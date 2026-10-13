import asyncio
import shutil
import subprocess
import uuid

import pytest
from sqlalchemy import select

from app.core import storage
from app.core.config import get_settings
from app.crawler.domains import DomainEntry
from app.crawler.runner import ScanLimits
from app.db.session import SessionLocal
from app.matching.fingerprint import build_reference, load_image
from app.models import EvidenceFile, Match, Scan
from app.video.discover import find_video_urls
from app.video.keyframes import VideoError, extract_keyframes, probe, sample_frames
from app.video.matcher import KeyframeRef, VideoMatcher
from app.video.verify import compare_frames, longest_ordered_run, verify_video_url
from app.worker.main import claim_next_scan, process_scan
from tests.factories import make_photo, to_bytes
from tests.test_scan import FakeWeb, make_fetcher
from tests.test_scans_api import make_case, start_scan
from tests.video_factory import make_video

SCENES = [1, 2, 3, 4, 5]          # our video: five different scenes, 3 seconds each
OTHER_SCENES = [60, 61, 62, 63, 64]


def thumb(seed: int) -> bytes:
    """A web thumbnail taken from one scene of our video."""
    return to_bytes(make_photo(seed).resize((240, 180)), "JPEG", quality=85)


@pytest.fixture(scope="module")
def our_video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("videos") / "ours.mp4", SCENES)


@pytest.fixture(scope="module")
def other_video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("videos") / "other.mp4", OTHER_SCENES)


@pytest.fixture(scope="module")
def matcher(our_video):
    info = probe(our_video, 600)
    frames = extract_keyframes(our_video, info)
    return VideoMatcher([KeyframeRef(k.time_seconds, build_reference(k.image)) for k in frames])


# ============================================================ finding the video on a page

PAGE = """
<html><head>
  <meta property="og:video" content="/v/og.mp4">
  <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "VideoObject",
     "contentUrl": "https://cdn.test/ld.webm", "embedUrl": "https://player.test/embed/9"}
  </script>
</head><body>
  <video src="/v/tag.mp4"><source src="/v/live.m3u8" type="application/x-mpegURL"></video>
  <video><source src="/v/alt.mov" type="video/quicktime"></video>
  <iframe src="https://player.test/embed/7"></iframe>
  <video src="javascript:alert(1)"></video>
  <video src="data:video/mp4;base64,AAAA"></video>
  <a href="/v/not-a-video-tag.mp4">a link is not announced as a video</a>
</body></html>
"""


def test_it_finds_the_videos_a_page_announces_and_ignores_unsafe_addresses():
    refs = find_video_urls(PAGE, "https://site.test/watch/1", limit=20)
    kinds = {ref.url: ref.kind for ref in refs}
    assert kinds["https://site.test/v/og.mp4"] == "direct"
    assert kinds["https://site.test/v/tag.mp4"] == "direct"
    assert kinds["https://cdn.test/ld.webm"] == "direct"
    assert kinds["https://site.test/v/alt.mov"] == "direct"
    assert kinds["https://site.test/v/live.m3u8"] == "stream"
    assert kinds["https://player.test/embed/9"] == "embed"
    assert kinds["https://player.test/embed/7"] == "embed"
    assert not any(url.startswith(("javascript:", "data:")) for url in kinds)
    assert "https://site.test/v/not-a-video-tag.mp4" not in kinds
    assert [ref.kind for ref in refs][0] == "direct"  # files we can check come first


def test_a_page_cannot_make_us_look_at_endless_videos():
    page = "".join(f'<video src="/v/{i}.mp4"></video>' for i in range(50))
    assert len(find_video_urls(page, "https://site.test/")) == 6


def test_a_page_without_video_gives_nothing():
    assert find_video_urls("<p>hello</p><img src='/a.jpg'>", "https://site.test/") == []


# ============================================================ reading the user's video

def test_keyframes_cover_the_whole_video_and_skip_black_frames(tmp_path):
    path = make_video(tmp_path / "intro.mp4", SCENES, black_intro_seconds=2)
    info = probe(path, 600)
    assert 16.5 < info.duration_seconds < 17.5 and (info.width, info.height) == (320, 240)
    frames = extract_keyframes(path, info)
    times = [k.time_seconds for k in frames]
    assert 4 <= len(frames) <= 16
    assert times == sorted(times)
    assert min(times) >= 2.0                      # the black intro was dropped
    assert max(times) > info.duration_seconds - 4  # the end of the video is represented


def test_a_video_that_is_too_long_is_refused(our_video):
    with pytest.raises(VideoError, match="longer than"):
        probe(our_video, 5)


def test_something_that_is_not_a_video_is_refused(tmp_path):
    path = tmp_path / "x.mp4"
    path.write_bytes(b"this is only text " * 1000)
    with pytest.raises(VideoError):
        probe(path, 600)


def test_an_all_black_video_has_no_usable_frames(tmp_path):
    import cv2
    import numpy as np

    path = tmp_path / "black.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
    for _ in range(40):
        writer.write(np.zeros((240, 320, 3), dtype=np.uint8))
    writer.release()
    with pytest.raises(VideoError, match="no usable frames"):
        extract_keyframes(path, probe(path, 600))


def test_sampling_takes_one_frame_every_few_seconds(our_video):
    frames = sample_frames(our_video, step_seconds=2, max_frames=100)
    assert [round(t) for t, _ in frames] == [0, 2, 4, 6, 8, 10, 12, 14]
    assert len(sample_frames(our_video, step_seconds=2, max_frames=3)) == 3


# ============================================================ comparing a picture with the video

def test_a_picture_from_any_scene_matches_and_says_which_part(matcher):
    for seed in SCENES:
        decision = matcher.compare(load_image(thumb(seed)))
        assert decision is not None, f"scene {seed} was not recognised"
        assert decision.signals["reference"] == "video"
        assert decision.signals["keyframes_matched"] >= 1
    best_time, _ = matcher.best_keyframe(load_image(thumb(5)))
    assert best_time > 10  # scene 5 is at the end of the video


def test_an_unrelated_picture_does_not_match(matcher):
    assert matcher.compare(load_image(thumb(99))) is None


# ============================================================ order of scenes

def test_the_longest_ordered_run_wants_the_same_scenes_in_the_same_order():
    assert longest_ordered_run([(0, 1), (2, 5), (4, 9)]) == [(0, 1), (2, 5), (4, 9)]
    assert len(longest_ordered_run([(0, 9), (2, 5), (4, 1)])) == 1      # reversed order
    assert len(longest_ordered_run([(0, 3), (2, 3), (4, 3)])) == 1      # one scene repeated
    assert len(longest_ordered_run([(0, 1), (2, 9), (4, 2), (6, 5)])) == 3
    assert longest_ordered_run([]) == []


def test_a_copy_of_our_video_is_sequence_confirmed(matcher, our_video):
    result = compare_frames(matcher, our_video)
    assert result.status == "sequence_confirmed"
    assert result.ordered_frames >= 3 and result.segment["end"] > result.segment["start"]


def test_a_different_video_is_not_confirmed(matcher, other_video):
    result = compare_frames(matcher, other_video)
    assert result.status == "no_frame_match" and result.frames_matched == 0
    assert "does not rule out" in result.reason


def test_the_same_scenes_in_a_different_order_are_not_confirmed(matcher, tmp_path):
    shuffled = make_video(tmp_path / "shuffled.mp4", list(reversed(SCENES)))
    assert compare_frames(matcher, shuffled).status != "sequence_confirmed"


def test_a_part_that_cannot_be_decoded_is_reported_honestly(matcher, tmp_path):
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(b"\x00" * 2000)
    result = compare_frames(matcher, cut)
    assert result.status == "not_checked" and "could not be decoded" in result.reason


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is needed to make a streamable file")
def test_only_the_start_of_a_big_video_is_downloaded_and_that_is_enough(matcher, our_video, tmp_path):
    streamable = tmp_path / "streamable.mp4"  # index at the front, like most web videos
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(our_video), "-c", "copy",
                    "-movflags", "+faststart", str(streamable)], check=True)
    data = streamable.read_bytes()
    web = FakeWeb()
    web.add("cdn.test", "/clip.mp4", data, "video/mp4")
    limit = int(len(data) * 0.7)

    async def go():
        async with make_fetcher(web) as fetcher:
            return await verify_video_url(fetcher, matcher, "https://cdn.test/clip.mp4", limit)

    result = asyncio.run(go())
    assert result.status == "sequence_confirmed"
    assert result.seconds_checked < 14  # we did not look at the whole video


# ============================================================ uploading a video

def upload_video(client, user, case_id, data, name="clip.mp4", content_type="video/mp4"):
    return client.post(f"/api/cases/{case_id}/evidence/video",
                       files={"file": (name, data, content_type)}, headers=user["headers"])


@pytest.fixture
def video_bytes(our_video):
    return our_video.read_bytes()


def test_a_video_is_stored_privately_after_it_is_checked(client, make_user, video_bytes, private_evidence_folder):
    user = make_user()
    case = make_case(client, user)
    response = upload_video(client, user, case["id"], video_bytes, name="../../my clip.mp4")
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "reference_video" and body["size_bytes"] == len(video_bytes)
    assert body["original_filename"] == "my clip.mp4" and body["delete_after"]
    stored = list((private_evidence_folder / case["id"]).iterdir())
    assert len(stored) == 1 and stored[0].read_bytes() == video_bytes
    assert oct(stored[0].stat().st_mode & 0o777) == "0o600"
    assert not list((private_evidence_folder / ".incoming").iterdir())  # no temporary leftovers


def test_a_video_over_the_size_limit_is_refused_and_leaves_nothing_behind(client, make_user, video_bytes, monkeypatch, private_evidence_folder):
    monkeypatch.setattr(get_settings(), "max_video_bytes", 10_000)
    user = make_user()
    case = make_case(client, user)
    assert upload_video(client, user, case["id"], video_bytes).status_code == 413
    assert not list((private_evidence_folder / ".incoming").iterdir())
    assert not (private_evidence_folder / case["id"]).exists()


def test_a_video_that_is_too_long_or_not_a_video_is_refused(client, make_user, video_bytes, monkeypatch, private_evidence_folder):
    user = make_user()
    case = make_case(client, user)
    assert upload_video(client, user, case["id"], b"not a video at all " * 500).status_code == 422
    assert upload_video(client, user, case["id"], b"").status_code == 422
    monkeypatch.setattr(get_settings(), "max_video_seconds", 5)
    response = upload_video(client, user, case["id"], video_bytes)
    assert response.status_code == 422 and "longer than" in response.json()["detail"]
    assert not list((private_evidence_folder / ".incoming").iterdir())


def test_nobody_can_upload_a_video_into_someone_elses_case(client, make_user, video_bytes):
    owner, stranger = make_user(), make_user()
    case = make_case(client, owner)
    assert upload_video(client, stranger, case["id"], video_bytes).status_code == 404
    assert client.post(f"/api/cases/{case['id']}/evidence/video",
                       files={"file": ("a.mp4", video_bytes, "video/mp4")}).status_code in (401, 403)


# ============================================================ a whole video scan

def video_page(thumbnail: str, video: str | None = None, iframe: str | None = None) -> str:
    extra = f'<video src="{video}"></video>' if video else ""
    extra += f'<iframe src="{iframe}"></iframe>' if iframe else ""
    return f'<img src="{thumbnail}">{extra}'


def run_video_scan(client, user, video_bytes, web, hosts):
    case = make_case(client, user)
    evidence = upload_video(client, user, case["id"], video_bytes).json()
    scan = start_scan(client, user, case["id"], evidence["id"])
    assert scan.status_code == 201
    scan_id = uuid.UUID(scan.json()["id"])
    assert claim_next_scan(scan_id) == scan_id

    async def go():
        async with make_fetcher(web) as fetcher:
            await process_scan(scan_id, fetcher=fetcher, domains=[DomainEntry(domain=h) for h in hosts],
                               limits=ScanLimits(parallel_domains=1), poll_seconds=0.05)

    asyncio.run(go())
    return scan_id


def site(web, host, page_html):
    web.add(host, "/", '<a href="/watch/1">watch</a>')
    web.add(host, "/watch/1", page_html)
    web.add(host, "/t.jpg", thumb(3), "image/jpeg")


def test_a_video_scan_checks_the_real_video_behind_each_matching_thumbnail(client, make_user, video_bytes, our_video, other_video):
    user = make_user()
    web = FakeWeb()
    site(web, "real.test", video_page("/t.jpg", "/files/clip.mp4"))
    web.add("real.test", "/files/clip.mp4", our_video.read_bytes(), "video/mp4")
    site(web, "bait.test", video_page("/t.jpg", "/files/clip.mp4"))          # same thumbnail, other video
    web.add("bait.test", "/files/clip.mp4", other_video.read_bytes(), "video/mp4")
    site(web, "player.test", video_page("/t.jpg", iframe="https://embed.test/p/1"))
    site(web, "bare.test", video_page("/t.jpg"))

    scan_id = run_video_scan(client, user, video_bytes, web, ["real.test", "bait.test", "player.test", "bare.test"])

    with SessionLocal() as db:
        scan_row = db.get(Scan, scan_id)
        assert scan_row.status.value == "completed"
        assert scan_row.stats["reference"]["type"] == "video" and scan_row.stats["reference"]["keyframes"] >= 4
        matches = {m.domain: m for m in db.scalars(select(Match).where(Match.scan_id == scan_id))}
    assert set(matches) == {"real.test", "bait.test", "player.test", "bare.test"}  # every thumbnail matched

    video = {domain: m.signals["video"] for domain, m in matches.items()}
    assert video["real.test"]["verification"] == "sequence_confirmed"
    assert video["real.test"]["ordered_frames"] >= 3 and video["real.test"]["matched_segment"]
    assert video["bait.test"]["verification"] == "no_frame_match"       # a thumbnail alone proved nothing
    assert video["player.test"]["verification"] == "not_checked" and "embedded" in video["player.test"]["reason"]
    assert video["bare.test"]["verification"] == "not_checked" and "no video was found" in video["bare.test"]["reason"]
    for m in matches.values():
        assert m.review_status.value == "potential"                      # a machine result is never 'verified'
        assert m.signals["reference"] == "video" and m.signals["keyframe_times"]


def test_the_number_of_videos_downloaded_per_scan_is_limited(client, make_user, video_bytes, our_video, monkeypatch):
    monkeypatch.setattr(get_settings(), "video_verify_max_candidates", 1)
    user = make_user()
    web = FakeWeb()
    for host in ("a.test", "b.test"):
        site(web, host, video_page("/t.jpg", "/files/clip.mp4"))
        web.add(host, "/files/clip.mp4", our_video.read_bytes(), "video/mp4")
    scan_id = run_video_scan(client, user, video_bytes, web, ["a.test", "b.test"])
    with SessionLocal() as db:
        states = sorted(m.signals["video"]["verification"] for m in db.scalars(select(Match).where(Match.scan_id == scan_id)))
    assert states == ["not_checked", "sequence_confirmed"]


def test_a_scan_with_an_unusable_reference_video_fails_with_a_clear_reason(client, make_user, video_bytes, monkeypatch):
    user = make_user()
    case = make_case(client, user)
    evidence = upload_video(client, user, case["id"], video_bytes).json()
    scan_id = uuid.UUID(start_scan(client, user, case["id"], evidence["id"]).json()["id"])
    with SessionLocal() as db:  # the stored file is damaged afterwards
        row = db.get(EvidenceFile, uuid.UUID(evidence["id"]))
        storage.resolve(row.storage_path).write_bytes(b"damaged" * 100)
    assert claim_next_scan(scan_id) == scan_id
    web = FakeWeb()

    async def go():
        async with make_fetcher(web) as fetcher:
            await process_scan(scan_id, fetcher=fetcher, domains=[DomainEntry(domain="x.test")], poll_seconds=0.05)

    asyncio.run(go())
    scan = client.get(f"/api/scans/{scan_id}", headers=user["headers"]).json()
    assert scan["status"] == "failed" and "reference video could not be used" in scan["error"]
