import { useRef } from "react";
import { api } from "../api";
import { useAction } from "../hooks";
import { size, when } from "../labels";
import type { Evidence } from "../types";

export default function ReferencePanel({ caseId, evidence, onChange }: {
  caseId: string; evidence: Evidence[]; onChange: () => Promise<void>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const up = useAction();
  const remove = useAction();
  const references = evidence.filter((e) => e.kind !== "supporting");
  const copies = evidence.filter((e) => e.kind === "supporting").length;

  const choose = async (file: File | undefined) => {
    if (!file) return;
    const isVideo = file.type.startsWith("video/") || /\.(mp4|mov|m4v|webm|avi|mkv)$/i.test(file.name);
    await up.run(async () => { await api.upload(caseId, file, isVideo); await onChange(); });
    if (input.current) input.current.value = "";
  };

  return (
    <section className="card">
      <h2>1. Your reference file</h2>
      <p className="muted">
        Add a picture or a short video that you are entitled to use. It is stored privately, only you can open it,
        and it is deleted automatically after the retention period. It is never shown to anyone else.
      </p>
      {references.length === 0 && <p>No reference file yet.</p>}
      <ul className="list">
        {references.map((e) => (
          <li key={e.id} className="row">
            <div>
              <strong>{e.original_filename}</strong>{" "}
              <span className="tag">{e.kind === "reference_video" ? "video" : "picture"}</span>
              <div className="muted small">
                {size(e.size_bytes)} · added {when(e.created_at)} · delete after {when(e.delete_after)}
              </div>
              <div className="muted small mono" title="SHA-256 fingerprint of the file">SHA-256 {e.sha256.slice(0, 16)}…</div>
            </div>
            <button className="secondary" disabled={remove.busy}
              onClick={() => remove.run(async () => { await api.deleteEvidence(caseId, e.id); await onChange(); })}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      {remove.error && <p className="error" role="alert">{remove.error}</p>}
      <label className="file">
        <span className="button">{up.busy ? "Uploading and checking…" : "Choose a picture or video"}</span>
        <input ref={input} type="file" accept="image/jpeg,image/png,image/webp,video/*" disabled={up.busy}
               onChange={(e) => choose(e.target.files?.[0])} />
      </label>
      <p className="muted small">Pictures: JPEG, PNG or WebP up to 10 MB. Videos: up to 100 MB and 10 minutes.</p>
      {up.error && <p className="error" role="alert">{up.error}</p>}
      {copies > 0 && <p className="muted small">{copies} preserved cop{copies === 1 ? "y" : "ies"} of matched pictures are kept as evidence.</p>}
    </section>
  );
}
