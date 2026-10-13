import type {
  Case, Channels, DomainResult, Evidence, Match, ReviewStatus, Scan, Takedown, User,
} from "./types";

const BASE = "/api";
const KEY = "traceveil_token";

// The sign-in token lives in sessionStorage: it disappears when the tab is closed.
let token: string | null = null;
try { token = sessionStorage.getItem(KEY); } catch { /* storage blocked: stay signed out after reload */ }

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn; }
export function hasToken() { return token !== null; }
export function setToken(value: string | null) {
  token = value;
  try { value ? sessionStorage.setItem(KEY, value) : sessionStorage.removeItem(KEY); } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function messageFrom(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? "Invalid value").join(" ");
  return fallback;
}

async function raw(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response: Response;
  try {
    response = await fetch(BASE + path, { ...init, headers });
  } catch {
    throw new ApiError(0, "Could not reach the server. Is the backend running?");
  }
  if (response.status === 401 && token) { setToken(null); onUnauthorized(); }
  if (!response.ok) {
    let body: unknown = null;
    try { body = await response.json(); } catch { /* not json */ }
    throw new ApiError(response.status, messageFrom(body, `Request failed (${response.status}).`));
  }
  return response;
}

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await raw(path, init)).json() as Promise<T>;
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  async login(email: string, password: string) {
    const form = new URLSearchParams({ username: email, password });
    const data = await json<{ access_token: string }>("/auth/login", { method: "POST", body: form });
    setToken(data.access_token);
  },
  register: (email: string, password: string) => json<User>("/auth/register", send("POST", { email, password })),
  me: () => json<User>("/auth/me"),

  cases: () => json<Case[]>("/cases"),
  case: (id: string) => json<Case>(`/cases/${id}`),
  createCase: (title: string, description: string) =>
    json<Case>("/cases", send("POST", { title, description: description || null })),
  deleteCase: async (id: string) => { await raw(`/cases/${id}`, { method: "DELETE" }); },

  evidence: (caseId: string) => json<Evidence[]>(`/cases/${caseId}/evidence`),
  upload: (caseId: string, file: File, video: boolean) => {
    const form = new FormData();
    form.append("file", file);
    return json<Evidence>(`/cases/${caseId}/evidence${video ? "/video" : ""}`, { method: "POST", body: form });
  },
  deleteEvidence: async (caseId: string, id: string) => { await raw(`/cases/${caseId}/evidence/${id}`, { method: "DELETE" }); },

  scans: (caseId: string) => json<Scan[]>(`/cases/${caseId}/scans`),
  scan: (id: string) => json<Scan>(`/scans/${id}`),
  startScan: (caseId: string, evidenceId: string) => json<Scan>(`/cases/${caseId}/scans`, send("POST", { evidence_id: evidenceId })),
  cancelScan: (id: string) => json<Scan>(`/scans/${id}/cancel`, send("POST")),
  scanDomains: (id: string) => json<DomainResult[]>(`/scans/${id}/domains`),
  scanMatches: (id: string) => json<Match[]>(`/scans/${id}/matches`),

  review: (matchId: string, status: ReviewStatus, note?: string) =>
    json<Match>(`/matches/${matchId}/review`, send("POST", { status, note })),
  async snapshotUrl(matchId: string): Promise<string> {
    const blob = await (await raw(`/matches/${matchId}/snapshot`)).blob();
    return URL.createObjectURL(blob);
  },

  takedowns: (caseId: string) => json<Takedown[]>(`/cases/${caseId}/takedowns`),
  createTakedown: (matchId: string, senderName: string, replyTo: string) =>
    json<Takedown>(`/matches/${matchId}/takedown`, send("POST", { sender_name: senderName || null, reply_to: replyTo || null })),
  editTakedown: (id: string, changes: { subject?: string; body?: string; contact?: string }) =>
    json<Takedown>(`/takedowns/${id}`, send("PATCH", changes)),
  deleteTakedown: async (id: string) => { await raw(`/takedowns/${id}`, { method: "DELETE" }); },
  markSent: (id: string, via: string, sentAt?: string) =>
    json<Takedown>(`/takedowns/${id}/sent`, send("POST", { via, sent_at: sentAt || null })),
  takedownStatus: (id: string, status: string, note: string) =>
    json<Takedown>(`/takedowns/${id}/status`, send("POST", { status, note: note || null })),
  channels: () => json<Channels>("/reporting-channels"),

  async downloadReport(caseId: string, format: "json" | "markdown", includePotential: boolean) {
    const response = await raw(`/cases/${caseId}/report?format=${format}&include_potential=${includePotential}`);
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `evidence-report.${format === "json" ? "json" : "md"}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  },
};
