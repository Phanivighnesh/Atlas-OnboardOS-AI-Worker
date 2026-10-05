import { useCallback, useEffect, useState } from "react";
import { api, setToken, ApiError } from "./api";

type Doc = { id: number; filename: string; doc_type: string | null; status: string; fields: Record<string, string | null>; issues: string[] };
type Approval = { id: number; action: string; risk: string; changes: string; status: string };
type Case = { id: number; name: string; role: string; joining: string; status: string; state: any; documents?: Doc[]; approvals?: Approval[] };
type Ev = { id: number; ts: string; actor: string; action: string; result: string; meta: string };
const LABEL: Record<string, string> = { DRAFT: "Draft", BLOCKED: "Blocked", AWAITING_ADMIN: "Awaiting Admin", AI_PROCESSING: "AI Processing", COMPLETED: "Completed" };

export default function App() {
  const [role, setRole] = useState<string | null>(null);
  const [cases, setCases] = useState<Case[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const loadCases = useCallback(() => api<Case[]>("/onboarding").then(setCases).catch(() => {}), []);
  useEffect(() => { if (role) loadCases(); }, [role, loadCases]);

  if (!role) return <Login onDone={setRole} />;
  const fail = (e: unknown) => setMsg(e instanceof ApiError && e.status === 403 ? `ACCESS RESTRICTED — your role: ${e.detail.your_role}, required: ${e.detail.required?.join("/")}` : `Error: ${e instanceof ApiError ? JSON.stringify(e.detail) : e}`);
  return (
    <div className="shell">
      <aside>
        <h1>OnboardOS</h1><small>From paperwork to ready-to-start.</small>
        <NewCase onCreated={(id) => { loadCases(); setSel(id); }} onError={fail} />
        <nav>{cases.map((c) => (
          <button key={c.id} className={c.id === sel ? "on" : ""} onClick={() => { setSel(c.id); setMsg(""); }}>
            <b>{c.name}</b><span>{c.role}</span><i className={`tag ${c.status}`}>{LABEL[c.status] ?? c.status}</i>
          </button>))}</nav>
        <footer>{role} · <a href="#" onClick={() => { setToken(""); setRole(null); }}>sign out</a></footer>
      </aside>
      <main>
        {msg && <div className="banner" onClick={() => setMsg("")}>{msg}</div>}
        {sel ? <Workspace id={sel} role={role} onChange={loadCases} onError={fail} /> : <p className="empty">Select a case, or create one with a natural-language goal.</p>}
      </main>
    </div>
  );
}

function Login({ onDone }: { onDone: (r: string) => void }) {
  const [email, setEmail] = useState("admin@northstarlabs.demo"); const [pw, setPw] = useState("AdminDemo123!"); const [err, setErr] = useState("");
  const go = async () => { try { const r = await api<{ token: string; role: string }>("/auth/login", { method: "POST", body: JSON.stringify({ email, password: pw }) }); setToken(r.token); onDone(r.role); } catch { setErr("Invalid credentials"); } };
  return <div className="login"><h1>OnboardOS</h1><small>Autonomous Employee Onboarding Operations</small>
    <input value={email} onChange={(e) => setEmail(e.target.value)} /><input type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
    <button className="primary" onClick={go}>Sign in</button>{err && <p className="err">{err}</p>}
    <p className="hint">Demo-only: admin@ / hr@ / viewer@northstarlabs.demo — AdminDemo123! / HRDemo123! / ViewerDemo123!</p></div>;
}

function NewCase({ onCreated, onError }: { onCreated: (id: number) => void; onError: (e: unknown) => void }) {
  const [goal, setGoal] = useState("Onboard Priya Sharma for Software Engineer, joining 20 October 2026.");
  return <div className="new"><textarea value={goal} onChange={(e) => setGoal(e.target.value)} rows={3} />
    <button className="primary" onClick={() => api<{ id: number }>("/onboarding", { method: "POST", body: JSON.stringify({ goal }) }).then((r) => onCreated(r.id)).catch(onError)}>Create case</button></div>;
}

function Workspace({ id, role, onChange, onError }: { id: number; role: string; onChange: () => void; onError: (e: unknown) => void }) {
  const [c, setC] = useState<Case | null>(null); const [ev, setEv] = useState<Ev[]>([]); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { setC(await api<Case>(`/onboarding/${id}`)); setEv(await api<Ev[]>(`/onboarding/${id}/activity`)); onChange(); }, [id, onChange]);
  useEffect(() => { setC(null); load().catch(onError); }, [id]); // eslint-disable-line
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); try { await fn(); await load(); } catch (e) { onError(e); } setBusy(false); };
  if (!c) return <p className="empty">Loading…</p>;
  const pending = c.approvals?.find((a) => a.status === "PENDING");
  const upload = (files: FileList | null) => files && act(async () => { for (const f of Array.from(files)) { const fd = new FormData(); fd.append("file", f); await api(`/onboarding/${id}/documents`, { method: "POST", body: fd }); } });
  return (<>
    <header className="head"><div><h2>{c.name}</h2><p>{c.role} · Joining {c.joining}</p></div><i className={`tag ${c.status}`}>{LABEL[c.status] ?? c.status}</i></header>
    <div className="grid">
      <section><h3>Documents</h3>
        {(c.state.plan ?? []).map((p: any) => <div key={p.step} className={`row ${p.state}`}><span>{p.state === "done" ? "✓" : p.state === "review" ? "⚠" : "✗"}</span>{p.step}<em>{p.state === "missing" ? "Missing" : p.state === "review" ? "Needs review" : "Verified"}</em></div>)}
        {c.documents?.map((d) => <div key={d.id} className="doc"><b>{d.filename}</b> → {d.doc_type ?? "unparsed"} ({d.status})
          {Object.entries(d.fields).filter(([, v]) => v).map(([k, v]) => <div key={k}><small>{k}</small> {v}</div>)}
          {d.issues.map((i) => <div key={i} className="issue">⚠ {i}</div>)}</div>)}
        <label className="drop">Upload documents (.txt)<input type="file" multiple disabled={busy || role === "VIEWER"} onChange={(e) => upload(e.target.files)} /></label>
        <button disabled={busy || role === "VIEWER" || c.status === "COMPLETED"} onClick={() => act(() => api(`/onboarding/${id}/start`, { method: "POST" }))}>Run AI worker</button>
      </section>
      <section><h3>AI worker</h3>
        {pending && <div className="rec"><b>ADMIN APPROVAL REQUIRED</b><p>{pending.action.replace("_", " ")} · risk {pending.risk}</p>
          {Object.entries(JSON.parse(pending.changes)).map(([k, v]) => <div key={k}><small>{k}</small> {String(v)}</div>)}
          <button className="primary" disabled={busy} onClick={() => act(() => api(`/approvals/${pending.id}/approve`, { method: "POST" }))}>Approve &amp; Continue</button>
          <button disabled={busy} onClick={() => act(() => api(`/approvals/${pending.id}/reject`, { method: "POST", body: JSON.stringify({ reason: "Rejected in UI" }) }))}>Reject</button></div>}
        {c.state.verification && <div className="rec ok"><b>VERIFICATION {c.state.verification.passed ? "PASSED" : "FAILED"}</b>
          {Object.entries(c.state.verification.checks).map(([k, v]) => <div key={k}>{v ? "✓" : "✗"} {k}</div>)}</div>}
        <h3>Audit trail</h3>
        <ol className="audit">{ev.map((e) => <li key={e.id}><time>{e.ts.slice(11, 19)}</time><span className={`actor ${e.actor}`}>{e.actor}</span> {e.action.replace("tool:", "tool · ")} <em>{e.result !== "ok" ? e.result : ""}</em></li>)}</ol>
      </section></div></>);
}
