import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import MatchList from "../components/MatchList";
import ReferencePanel from "../components/ReferencePanel";
import ReportPanel from "../components/ReportPanel";
import ScanPanel from "../components/ScanPanel";
import TakedownPanel from "../components/TakedownPanel";
import { useAction } from "../hooks";
import type { Case, DomainResult, Evidence, Match, Scan, Takedown } from "../types";

const ACTIVE = ["queued", "running"];

export default function CasePage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();
  const [theCase, setCase] = useState<Case | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [scans, setScans] = useState<Scan[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [domains, setDomains] = useState<DomainResult[]>([]);
  const [matches, setMatches] = useState<Match[]>([]);
  const [takedowns, setTakedowns] = useState<Takedown[]>([]);
  const load = useAction();
  const del = useAction();

  const scan = scans.find((s) => s.id === selected) ?? null;
  const active = scan !== null && ACTIVE.includes(scan.status);

  const refreshEvidence = useCallback(async () => setEvidence(await api.evidence(caseId)), [caseId]);
  const refreshTakedowns = useCallback(async () => setTakedowns(await api.takedowns(caseId)), [caseId]);
  const refreshScans = useCallback(async (select?: string) => {
    const list = await api.scans(caseId);
    setScans(list);
    setSelected((current) => select ?? current ?? list[0]?.id ?? null);
  }, [caseId]);

  useEffect(() => {
    load.run(async () => {
      setCase(await api.case(caseId));
      await Promise.all([refreshEvidence(), refreshScans(), refreshTakedowns()]);
    });
  }, [caseId]); // eslint-disable-line react-hooks/exhaustive-deps

  // What the selected scan has found so far. While it runs, ask again every 2 seconds.
  const refreshScanDetails = useCallback(async () => {
    if (!selected) { setDomains([]); setMatches([]); return; }
    const [fresh, d, m] = await Promise.all([api.scan(selected), api.scanDomains(selected), api.scanMatches(selected)]);
    setScans((all) => all.map((s) => (s.id === fresh.id ? fresh : s)));
    setDomains(d);
    setMatches(m);
  }, [selected]);

  useEffect(() => { refreshScanDetails().catch(() => {}); }, [refreshScanDetails]);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => { refreshScanDetails().catch(() => {}); }, 2000);
    return () => clearInterval(timer);
  }, [active, refreshScanDetails]);

  const removeCase = async () => {
    if (!window.confirm("Delete this case and everything in it (files, scans, findings, drafts)? This cannot be undone.")) return;
    if (await del.run(async () => { await api.deleteCase(caseId); return true; })) navigate("/");
  };

  if (load.error) return <p className="error" role="alert">{load.error} <Link to="/">Back</Link></p>;
  if (!theCase) return <p className="muted">Loading…</p>;

  return (
    <>
      <p><Link to="/">← All cases</Link></p>
      <h1>{theCase.title}</h1>
      {theCase.description && <p className="muted">{theCase.description}</p>}

      <ReferencePanel caseId={caseId} evidence={evidence} onChange={refreshEvidence} />
      <ScanPanel
        caseId={caseId} evidence={evidence} scans={scans} scan={scan} domains={domains}
        onSelect={setSelected}
        onStarted={async (s) => { await refreshScans(s.id); }}
        onChanged={async () => { await refreshScanDetails(); }}
      />
      <MatchList
        scan={scan} matches={matches} takedowns={takedowns}
        onChanged={async () => { await Promise.all([refreshScanDetails(), refreshTakedowns()]); }}
      />
      <TakedownPanel takedowns={takedowns} onChanged={refreshTakedowns} />
      <ReportPanel caseId={caseId} />

      <section className="card danger-zone">
        <h2>Delete this case</h2>
        <p className="muted">Removes the case, your uploaded files, every finding and every draft from this service.</p>
        {del.error && <p className="error" role="alert">{del.error}</p>}
        <button className="danger" onClick={removeCase} disabled={del.busy}>Delete case</button>
      </section>
    </>
  );
}
