import { Link, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import CasePage from "./pages/CasePage";
import Cases from "./pages/Cases";
import Login from "./pages/Login";

export default function App() {
  const { user, loading, signOut } = useAuth();
  if (loading) return <p className="center muted">Loading…</p>;

  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">Traceveil</Link>
        {user && (
          <div className="who">
            <span className="muted">{user.email}</span>
            <button className="link" onClick={signOut}>Sign out</button>
          </div>
        )}
      </header>
      <main className="page">
        <Routes>
          <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/" element={user ? <Cases /> : <Navigate to="/login" replace />} />
          <Route path="/cases/:caseId" element={user ? <CasePage /> : <Navigate to="/login" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <footer className="footer muted">
        Your files stay private to your account. A similarity result is not proof, and nothing here is sent to anyone
        unless you send it yourself.
      </footer>
    </>
  );
}
