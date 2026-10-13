import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { when } from "../labels";
import { useAction } from "../hooks";
import type { Case } from "../types";

export default function Cases() {
  const [cases, setCases] = useState<Case[] | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const load = useAction();
  const create = useAction();

  const refresh = () => load.run(async () => setCases(await api.cases()));
  useEffect(() => { refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const made = await create.run(() => api.createCase(title, description));
    if (made) { setTitle(""); setDescription(""); refresh(); }
  };

  return (
    <>
      <h1>Your cases</h1>
      {load.error && <p className="error" role="alert">{load.error}</p>}
      {cases === null && !load.error && <p className="muted">Loading…</p>}
      {cases?.length === 0 && <p className="muted">No cases yet. A case keeps everything about one situation together.</p>}
      <ul className="list">
        {cases?.map((c) => (
          <li key={c.id} className="card row">
            <div>
              <Link to={`/cases/${c.id}`}><strong>{c.title}</strong></Link>
              <div className="muted small">Created {when(c.created_at)}</div>
            </div>
            <Link className="button" to={`/cases/${c.id}`}>Open</Link>
          </li>
        ))}
      </ul>

      <form className="card stack" onSubmit={submit}>
        <h2>Start a new case</h2>
        <label>A name only you will recognise
          <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} />
        </label>
        <label>Notes (optional)
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} maxLength={5000} />
        </label>
        {create.error && <p className="error" role="alert">{create.error}</p>}
        <button className="primary" disabled={create.busy}>Create case</button>
      </form>
    </>
  );
}
