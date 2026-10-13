import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";
import { useAction } from "../hooks";

export default function Login() {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<"in" | "up">("in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, run, clear } = useAction();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(() => (mode === "in" ? signIn(email, password) : signUp(email, password)));
  };

  return (
    <section className="card narrow">
      <h1>{mode === "in" ? "Sign in" : "Create an account"}</h1>
      <p className="muted">
        Traceveil helps you find and report intimate images or videos of you that were shared without your consent.
        Everything you add is private to your account.
      </p>
      <form onSubmit={submit} className="stack">
        <label>Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
        </label>
        <label>Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
                 minLength={mode === "up" ? 10 : undefined}
                 autoComplete={mode === "in" ? "current-password" : "new-password"} />
          {mode === "up" && <span className="hint">At least 10 characters.</span>}
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "Please wait…" : mode === "in" ? "Sign in" : "Create account"}</button>
      </form>
      <p>
        <button className="link" onClick={() => { clear(); setMode(mode === "in" ? "up" : "in"); }}>
          {mode === "in" ? "I need an account" : "I already have an account"}
        </button>
      </p>
    </section>
  );
}
