import { useState } from "react";
import { api } from "../api";
import { useAction } from "../hooks";

export default function ReportPanel({ caseId }: { caseId: string }) {
  const [includePotential, setIncludePotential] = useState(false);
  const download = useAction();

  return (
    <section className="card">
      <h2>5. Evidence report</h2>
      <p className="muted">
        A record of what was searched (including what could not be), what you reviewed, the fingerprints of your preserved
        copies, your removal requests and an audit trail. It is made for you, a lawyer or the police if you choose to go
        to them. Bring it yourself; Traceveil never sends it anywhere.
      </p>
      <label className="check">
        <input type="checkbox" checked={includePotential} onChange={(e) => setIncludePotential(e.target.checked)} />
        Also list findings that no person has reviewed yet (they are clearly marked as unreviewed)
      </label>
      <div className="row wrap">
        <button className="primary" disabled={download.busy} onClick={() => download.run(() => api.downloadReport(caseId, "markdown", includePotential))}>
          Download readable report (.md)
        </button>
        <button className="secondary" disabled={download.busy} onClick={() => download.run(() => api.downloadReport(caseId, "json", includePotential))}>
          Download data (.json)
        </button>
      </div>
      {download.error && <p className="error" role="alert">{download.error}</p>}
    </section>
  );
}
