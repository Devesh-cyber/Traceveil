"""Writes the first draft of a removal request. The user edits it and sends it themselves.

The draft states facts we really have (the page, the file, when it was found, the fingerprint
of our preserved copy). It does not claim anything we cannot know, it does not quote laws
(that is for the person, or their lawyer, to add), and it never attaches the intimate image.
"""

from __future__ import annotations

from app.models import Match

NAME = "[YOUR NAME]"
REPLY = "[YOUR CONTACT DETAILS]"


def build_notice(
    match: Match, *, snapshot_sha256: str | None, sender_name: str | None, reply_to: str | None
) -> tuple[str, str]:
    found = match.created_at.strftime("%d %B %Y")
    lines = [
        f"To the abuse / trust and safety team of {match.domain},",
        "",
        "I am asking you to urgently remove intimate content that is published on your website "
        "without my consent.",
        "",
        f"Page: {match.source_page_url}",
        f"File: {match.image_url}",
        f"First seen by me: {found} (UTC)",
    ]
    video = (match.signals or {}).get("video") or {}
    if video.get("verification") == "sequence_confirmed":
        lines.append("A video on this page also appears to contain the same footage.")
    lines += [
        "",
        "I did not consent to this content being shared. Please remove it, and any other copies "
        "you host, as soon as possible, and confirm by replying to this message.",
        "",
    ]
    if snapshot_sha256:
        lines += [
            f"I have kept a private copy of what was on the page. Its SHA-256 fingerprint is {snapshot_sha256}. "
            "I can provide further proof of my identity or of the content through a secure channel on request.",
            "",
        ]
    lines += ["Regards,", sender_name or NAME, reply_to or REPLY]
    subject = f"Removal request: non-consensual intimate content on {match.domain}"
    return subject, "\n".join(lines)
