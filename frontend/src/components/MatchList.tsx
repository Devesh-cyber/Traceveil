import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useAction } from "../hooks";
import { MATCH_KIND, REVIEW, TAKEDOWN, VIDEO, minutes, when } from "../labels";
import type { Match, Scan, Takedown } from "../types";

export default function MatchList({ scan, matches, takedowns, onChanged }: {
  scan: Scan | null; matches: Match[]; takedowns: Takedown[]; onChanged: () => Promise<void>;
}) {
  return (
    <section className="card">
      <h2>3. Possible matches</h2>
      <p className="muted">
        These are pictures that <em>look similar</em> to your file. Similarity is not proof. Look at each one yourself and
        decide. These images may be upsetting, so they stay hidden until you choose to show them.
      </p>
      {!scan && <p>Start a search to see results here.</p>}
      {scan && matches.length === 0 && (
        <p>{scan.status === "queued" || scan.status === "running" ? "Nothing found so far." : "No possible matches in this search."}</p>
      )}
      <ul className="list">
        {matches.map((m) => (
          <MatchCard key={m.id} match={m} takedown={takedowns.find((t) => t.match_id === m.id)} onChanged={onChanged} />
        ))}
      </ul>
    </section>
  );
}

function MatchCard({ match, takedown, onChanged }: { match: Match; takedown?: Takedown; onChanged: () => Promise<void> }) {
  const [url, setUrl] = useState<string | null>(null);
  const [note, setNote] = useState(match.review_note ?? "");
  const [name, setName] = useState("");
  const [reply, setReply] = useState("");
  const viewing = useAction();
  const review = useAction();
  const draft = useAction();
  const current = useRef<string | null>(null);

  useEffect(() => () => { if (current.current) URL.revokeObjectURL(current.current); }, []);

  const show = () => viewing.run(async () => {
    const made = await api.snapshotUrl(match.id);
    current.current = made;
    setUrl(made);
  });
  const hide = () => { if (current.current) URL.revokeObjectURL(current.current); current.current = null; setUrl(null); };

  const set = (status: Match["review_status"]) =>
    review.run(async () => { await api.review(match.id, status, note.trim() || undefined); await onChanged(); });

  const s = match.review_status;
  const video = match.signals.video;
  const canDraft = s === "reviewed" || s === "verified";

  return (
    <li className={`card match ${s}`}>
      <div className="row wrap">
        <strong>{match.domain}</strong>
        <span className={`pill ${s}`}>{REVIEW[s]}</span>
      </div>
      <p className="small">{MATCH_KIND[match.kind]}</p>
      <p className="small">
        Page: <a href={match.source_page_url} target="_blank" rel="noopener noreferrer nofollow">{match.source_page_url}</a>
        <br /><span className="muted">Found {when(match.created_at)}. The link opens the website itself, so take care.</span>
      </p>

      {video && (
        <p className={`video ${video.verification}`}>
          <strong>Video: </strong>{VIDEO[video.verification] ?? video.verification}
          {video.verification === "sequence_confirmed" && video.matched_segment && (
            <> Matched around {minutes(video.matched_segment.start)}–{minutes(video.matched_segment.end)} of the page's video.</>
          )}
          {video.verification === "not_checked" && video.reason && <span className="muted"> ({video.reason})</span>}
        </p>
      )}

      {match.has_snapshot && (
        <div>
          {url ? (
            <>
              <img className="snap" src={url} alt="The picture found on the website" />
              <div><button className="link" onClick={hide}>Hide picture</button></div>
            </>
          ) : (
            <button className="secondary" onClick={show} disabled={viewing.busy}>
              {viewing.busy ? "Opening…" : "Show the preserved picture"}
            </button>
          )}
          {viewing.error && <p className="error" role="alert">{viewing.error}</p>}
        </div>
      )}
      {!match.has_snapshot && <p className="muted small">No preserved copy of this picture exists, so it cannot be verified.</p>}

      <label>Your note {s === "reviewed" && <span className="hint">(verifying needs a note of at least 10 characters about what you checked)</span>}
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
      </label>
      <div className="row wrap">
        {s === "potential" && <button className="primary" disabled={review.busy} onClick={() => set("reviewed")}>I looked at it</button>}
        {s === "reviewed" && (
          <button className="primary" disabled={review.busy || !match.has_snapshot || note.trim().length < 10}
                  title={!match.has_snapshot ? "Needs a preserved copy" : note.trim().length < 10 ? "Write a note first" : ""}
                  onClick={() => set("verified")}>I confirm it is my content</button>
        )}
        {(s === "potential" || s === "reviewed") && <button className="secondary" disabled={review.busy} onClick={() => set("dismissed")}>Not a match</button>}
        {s !== "potential" && <button className="link" disabled={review.busy} onClick={() => set("potential")}>Undo my review</button>}
      </div>
      {review.error && <p className="error" role="alert">{review.error}</p>}

      {canDraft && !takedown && (
        <details>
          <summary>Prepare a removal request</summary>
          <p className="muted small">This only writes a draft for you to read and edit. Nothing is sent.</p>
          <label>Your name (as you want it in the message)<input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} /></label>
          <label>How the website can reply to you<input value={reply} onChange={(e) => setReply(e.target.value)} maxLength={300} /></label>
          <button className="primary" disabled={draft.busy}
                  onClick={() => draft.run(async () => { await api.createTakedown(match.id, name, reply); await onChanged(); })}>
            Write the draft
          </button>
          {draft.error && <p className="error" role="alert">{draft.error}</p>}
        </details>
      )}
      {takedown && <p className="small">Removal request: <strong>{TAKEDOWN[takedown.status]}</strong> (see section 4).</p>}
    </li>
  );
}
