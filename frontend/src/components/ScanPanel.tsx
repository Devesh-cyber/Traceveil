import { useEffect, useState } from "react";
import { api } from "../api";
import { useAction } from "../hooks";
import { SCAN, SITE, minutes, when } from "../labels";
import type { DomainResult, Evidence, Scan } from "../types";

export default function ScanPanel({ caseId, evidence, scans, scan, domains, onSelect, onStarted, onChanged }: {
  caseId: string; evidence: Evidence[]; scans: Scan[]; scan: Scan | null; domains: DomainResult[];
  onSelect: (id: string) => void; onStarted: (s: Scan) => Promise<void>; onChanged: () => Promise<void>;
}) {
  const references = evidence.filter((e) => e.kind !== "supporting");
  const [chosen, setChosen] = useState("");
  const start = useAction();
  const cancel = useAction();

  useEffect(() => {
    if (!references.some((r) => r.id === chosen)) setChosen(references[0]?.id ?? "");
  }, [references, chosen]);

  const running = scan !== null && (scan.status === "queued" || scan.status === "running");
  const anyActive = scans.some((s) => s.status === "queued" || s.status === "running");
  const stats = scan?.stats ?? {};
  const done = stats.sites_done ?? 0;
  const total = stats.sites_total ?? 0;
  const coverage = stats.coverage;
  const notSearched = coverage ? coverage.blocked + coverage.unreachable + coverage.skipped + coverage.no_images : 0;

  return (
    <section className="card">
      <h2>2. Search</h2>
      <p className="muted">
        Traceveil looks through a fixed list of websites that the team has researched. It does <strong>not</strong> search
        the whole internet, and websites that block it or cannot be reached are listed as not searched.
      </p>

      <div className="row wrap">
        <select value={chosen} onChange={(e) => setChosen(e.target.value)} aria-label="Reference file"
                disabled={references.length === 0}>
          {references.length === 0 && <option>Add a reference file first</option>}
          {references.map((r) => <option key={r.id} value={r.id}>{r.original_filename}</option>)}
        </select>
        <button className="primary" disabled={!chosen || anyActive || start.busy}
          onClick={() => start.run(async () => onStarted(await api.startScan(caseId, chosen)))}>
          {anyActive ? "A search is in progress" : "Start a search"}
        </button>
      </div>
      {start.error && <p className="error" role="alert">{start.error}</p>}

      {scans.length > 1 && (
        <label className="inline">Earlier searches
          <select value={scan?.id ?? ""} onChange={(e) => onSelect(e.target.value)}>
            {scans.map((s) => <option key={s.id} value={s.id}>{when(s.created_at)}: {SCAN[s.status]}</option>)}
          </select>
        </label>
      )}

      {scan && (
        <div className="scan" aria-live="polite">
          <h3>Search from {when(scan.created_at)}</h3>
          <p>
            <span className={`pill ${scan.status}`}>{SCAN[scan.status]}</span>{" "}
            {scan.status === "queued" && <span className="muted">The background worker has not picked it up yet. Make sure it is running.</span>}
          </p>
          {stats.reference?.type === "video" && (
            <p className="muted small">
              Comparing against {stats.reference.keyframes} key moments of your video
              {stats.reference.duration_seconds ? ` (${minutes(stats.reference.duration_seconds)} long)` : ""}.
            </p>
          )}
          {scan.status !== "queued" && total > 0 && (
            <>
              <progress max={total} value={done} aria-label="Websites finished" />
              <p className="small">
                {done} of {total} websites finished · {stats.pages_scanned ?? 0} pages read · {stats.images_compared ?? 0} pictures compared ·{" "}
                {stats.matches_found ?? 0} possible match{stats.matches_found === 1 ? "" : "es"}
              </p>
            </>
          )}
          {scan.error && <p className="error" role="alert">{scan.error}</p>}
          {scan.truncated && <p className="warn">Stopped early: {scan.truncation_reason}. Websites not reached are listed as skipped.</p>}
          {(scan.status === "completed" || scan.status === "failed") && coverage && (
            <p className="coverage">
              <strong>What this search covered:</strong> {coverage.checked} website{coverage.checked === 1 ? "" : "s"} searched.
              {notSearched > 0 && <> <strong>{notSearched}</strong> could not be searched (see below).</>}{" "}
              {scan.status === "completed" && "Finding nothing does not prove the content is not elsewhere."}
            </p>
          )}
          {running && (
            <button className="secondary" disabled={cancel.busy}
              onClick={() => cancel.run(async () => { await api.cancelScan(scan.id); await onChanged(); })}>
              Cancel this search
            </button>
          )}
          {cancel.error && <p className="error" role="alert">{cancel.error}</p>}

          {domains.length > 0 && (
            <details open={!running}>
              <summary>Website by website ({domains.length})</summary>
              <table>
                <thead><tr><th>Website</th><th>Result</th><th>Pages</th><th>Pictures</th></tr></thead>
                <tbody>
                  {domains.map((d) => (
                    <tr key={d.domain} className={SITE[d.status].searched ? "" : "notsearched"}>
                      <td>{d.domain}</td>
                      <td>{SITE[d.status].label}{d.detail ? <span className="muted small"> ({d.detail})</span> : null}</td>
                      <td>{d.pages_scanned}</td><td>{d.images_compared}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
