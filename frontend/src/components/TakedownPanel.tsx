import { useEffect, useState } from "react";
import { api } from "../api";
import { useAction } from "../hooks";
import { TAKEDOWN, when } from "../labels";
import type { Channels, Takedown, TakedownStatus } from "../types";

// What may be recorded next. This mirrors the rules the server enforces.
const NEXT: Record<TakedownStatus, TakedownStatus[]> = {
  draft: [],
  sent: ["acknowledged", "removed", "rejected", "no_response", "still_live"],
  acknowledged: ["removed", "rejected", "no_response", "still_live"],
  no_response: ["sent", "removed", "rejected", "still_live"],
  still_live: ["sent", "removed", "rejected", "no_response"],
  rejected: ["sent", "still_live"],
  removed: ["still_live"],
};
const NEXT_LABEL: Partial<Record<TakedownStatus, string>> = {
  sent: "I sent it again (follow-up)", acknowledged: "The website replied", removed: "It was removed",
  rejected: "The website refused", no_response: "No response", still_live: "It is still online",
};

export default function TakedownPanel({ takedowns, onChanged }: { takedowns: Takedown[]; onChanged: () => Promise<void> }) {
  const [channels, setChannels] = useState<Channels | null>(null);
  const load = useAction();
  useEffect(() => { load.run(async () => setChannels(await api.channels())); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="card" id="takedowns">
      <h2>4. Removal requests</h2>
      <p className="muted">
        Traceveil writes the draft. <strong>You</strong> send it, by email or on the website's own form, and then record
        here what happened. Nothing is ever sent for you, and nothing is reported to anyone automatically.
      </p>
      {takedowns.length === 0 && <p>No drafts yet. Mark a match as reviewed, then choose "Prepare a removal request".</p>}
      <ul className="list">
        {takedowns.map((t) => <TakedownCard key={t.id} item={t} onChanged={onChanged} />)}
      </ul>

      <details>
        <summary>Other places you can report to</summary>
        {channels && <p className="warn">{channels.note}</p>}
        <ul>
          {channels?.channels.map((c) => (
            <li key={c.id}>
              <strong>{c.url ? <a href={c.url} target="_blank" rel="noopener noreferrer">{c.name}</a> : c.name}</strong>
              <div className="small">{c.what}</div>
              <div className="muted small">{c.who_decides} {c.last_checked ? `Last checked ${c.last_checked}.` : "Not checked yet."}</div>
            </li>
          ))}
        </ul>
        {load.error && <p className="error" role="alert">{load.error}</p>}
      </details>
    </section>
  );
}

function TakedownCard({ item, onChanged }: { item: Takedown; onChanged: () => Promise<void> }) {
  const [subject, setSubject] = useState(item.subject);
  const [body, setBody] = useState(item.body);
  const [contact, setContact] = useState(item.contact ?? "");
  const [via, setVia] = useState("email");
  const [sentAt, setSentAt] = useState("");
  const [next, setNext] = useState<TakedownStatus | "">("");
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);
  const save = useAction();
  const sent = useAction();
  const move = useAction();
  const remove = useAction();

  const isDraft = item.status === "draft";
  const options = NEXT[item.status];
  useEffect(() => { setNext(options[0] ?? ""); }, [item.status]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = subject !== item.subject || body !== item.body || contact !== (item.contact ?? "");

  const saveChanges = () => save.run(async () => {
    await api.editTakedown(item.id, { subject, body, contact });
    await onChanged();
  });
  const copy = async () => {
    try { await navigator.clipboard.writeText(`${subject}\n\n${body}`); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { /* ignore */ }
  };

  return (
    <li className="card takedown">
      <div className="row wrap">
        <strong>{item.domain}</strong>
        <span className={`pill td-${item.status}`}>{TAKEDOWN[item.status]}</span>
      </div>

      {item.warnings.map((w) => <p key={w} className="warn">{w}</p>)}

      <label>To {item.contact_verified
        ? <span className="tag ok">verified contact</span>
        : <span className="tag">not verified</span>}
        <input value={contact} onChange={(e) => setContact(e.target.value)} disabled={!isDraft} placeholder="abuse email or the website's report page" />
      </label>
      <label>Subject<input value={subject} onChange={(e) => setSubject(e.target.value)} disabled={!isDraft} /></label>
      <label>Message<textarea rows={12} value={body} onChange={(e) => setBody(e.target.value)} disabled={!isDraft} /></label>

      <div className="row wrap">
        <button className="secondary" onClick={copy}>{copied ? "Copied" : "Copy message"}</button>
        {isDraft && <button className="secondary" disabled={!dirty || save.busy} onClick={saveChanges}>Save changes</button>}
        {isDraft && (
          <button className="link danger-text" disabled={remove.busy}
                  onClick={() => remove.run(async () => { await api.deleteTakedown(item.id); await onChanged(); })}>
            Delete draft
          </button>
        )}
      </div>
      {(save.error || remove.error) && <p className="error" role="alert">{save.error || remove.error}</p>}

      {isDraft && (
        <div className="stack boxed">
          <strong>After you have sent it yourself</strong>
          <label>How did you send it?
            <select value={via} onChange={(e) => setVia(e.target.value)}>
              <option value="email">By email</option><option value="web_form">Through the website's form</option><option value="other">Another way</option>
            </select>
          </label>
          <label>When (leave empty for now)<input type="datetime-local" value={sentAt} onChange={(e) => setSentAt(e.target.value)} /></label>
          <button className="primary" disabled={sent.busy || dirty || item.placeholders_left.length > 0}
                  title={dirty ? "Save your changes first" : item.placeholders_left.length ? "Fill in the bracketed parts first" : ""}
                  onClick={() => sent.run(async () => { await api.markSent(item.id, via, sentAt ? new Date(sentAt).toISOString() : undefined); await onChanged(); })}>
            I sent it: record this
          </button>
          {sent.error && <p className="error" role="alert">{sent.error}</p>}
        </div>
      )}

      {options.length > 0 && (
        <div className="stack boxed">
          <strong>What happened next?</strong>
          <label>Update
            <select value={next} onChange={(e) => setNext(e.target.value as TakedownStatus)}>
              {options.map((o) => <option key={o} value={o}>{NEXT_LABEL[o]}</option>)}
            </select>
          </label>
          <label>Note (optional)<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} /></label>
          <button className="primary" disabled={move.busy || !next}
                  onClick={() => move.run(async () => { await api.takedownStatus(item.id, next, note); setNote(""); await onChanged(); })}>
            Record
          </button>
          {move.error && <p className="error" role="alert">{move.error}</p>}
        </div>
      )}

      <details>
        <summary>History ({item.history.length})</summary>
        <ol className="small">
          {item.history.map((h, i) => (
            <li key={i}>{when(h.at)}: {h.from ? `${TAKEDOWN[h.from as TakedownStatus]} → ` : ""}{TAKEDOWN[h.to as TakedownStatus]}{h.note ? ` (${h.note})` : ""}</li>
          ))}
        </ol>
      </details>
    </li>
  );
}
