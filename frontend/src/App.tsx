import { useCallback, useEffect, useState } from "react";
import { api, setToken, ApiError } from "./api";

type Doc = { id: number; filename: string; doc_type: string | null; status: string; fields: Record<string, string | null>; issues: string[]; reviewed_by: string | null };
type Approval = { id: number; action: string; risk: string; changes: string; status: string };
type Mail = { id: number; to_addr: string; subject: string; body: string; mode: string; ts: string };
type Case = { id: number; email: string | null; name: string; role: string; joining: string; status: string; state: any; documents?: Doc[]; approvals?: Approval[] };
type Ev = { id: number; ts: string; actor: string; action: string; result: string; meta: string };
type Preview = { filename: string; doc_type: string | null; status: string; text: string; fields: Record<string, string | null> };
type Me = { role: string; email: string };
const LABEL: Record<string, string> = { DRAFT: "Draft", BLOCKED: "Blocked", AWAITING_ADMIN: "Awaiting Admin", AI_PROCESSING: "AI Processing", COMPLETED: "Completed", VERIFIED: "AI verified", NEEDS_REVIEW: "Needs review", UPLOADED: "Uploaded" };
const FIELD: Record<string, string> = { email: "Email", id_type: "ID type", id_number: "ID number", address: "Address", bank: "Bank", account: "Account number", ifsc: "IFSC", degree: "Degree", tax_id: "Tax ID", position: "Position", joining_date: "Joining date" };
const DOCNAME: Record<string, string> = { OFFER_LETTER: "Offer letter", GOV_ID: "Government ID", ADDRESS_PROOF: "Address proof", DEGREE: "Educational certificate", BANK_PROOF: "Bank proof", TAX_DOC: "Tax document" };
const norm = (s?: string | null) => (s ?? "").toLowerCase().replace(/[^a-z ]/g, "").split(" ").filter(Boolean).sort().join(" ");

const Logo = () => (<svg width="30" height="30" viewBox="0 0 28 28" aria-label="ATLAS"><rect width="28" height="28" rx="7" fill="var(--acc)" /><circle cx="8" cy="14" r="2.4" fill="#fff" /><path d="M10.4 14h5l3-5.2M15.4 14l3 5.2" stroke="#fff" strokeWidth="1.7" fill="none" strokeLinecap="round" /><circle cx="20" cy="8.5" r="2" fill="#fff" /><circle cx="20" cy="19.5" r="2" fill="#fff" /></svg>);
const Eye = () => (<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" /><circle cx="12" cy="12" r="3" /></svg>);

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [cases, setCases] = useState<Case[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const loadCases = useCallback(() => api<Case[]>("/onboarding").then(setCases).catch(() => {}), []);
  useEffect(() => { if (me) loadCases(); }, [me, loadCases]);
  if (!me) return <Login onDone={setMe} />;
  const fail = (e: unknown) => setMsg(e instanceof ApiError && e.status === 403 ? `ACCESS RESTRICTED — your role: ${e.detail.your_role}, required: ${e.detail.required?.join("/")}` : `Error: ${e instanceof ApiError ? JSON.stringify(e.detail) : e}`);
  return (
    <div className="shell">
      <aside>
        <div className="brand"><Logo /><div><h1>ATLAS</h1><small>Autonomous onboarding agent</small></div></div>
        <div className="me"><span className="avatar">{me.email.slice(0, 2).toUpperCase()}</span><div><b>{me.email}</b><small>{me.role}</small></div></div>
        <NewCase onCreated={(id) => { loadCases(); setSel(id); }} onError={fail} />
        <nav>{cases.map((c) => (
          <button key={c.id} className={c.id === sel ? "on" : ""} onClick={() => { setSel(c.id); setMsg(""); }}>
            <b>{c.name}</b><span>{c.role}</span><i className={`tag ${c.status}`}>{LABEL[c.status] ?? c.status}</i>
          </button>))}</nav>
        <footer><a href="#" onClick={() => { setToken(""); setMe(null); }}>Sign out</a></footer>
      </aside>
      <main>
        {msg && <div className="banner" onClick={() => setMsg("")}>{msg}</div>}
        {sel ? <Workspace id={sel} me={me} onChange={loadCases} onError={fail} onDeleted={() => { setSel(null); loadCases(); }} /> : <p className="empty">Select a case, or create one with a natural-language goal.</p>}
      </main>
    </div>
  );
}

function Login({ onDone }: { onDone: (m: Me) => void }) {
  const [email, setEmail] = useState("admin@northstarlabs.demo"); const [pw, setPw] = useState("AdminDemo123!"); const [err, setErr] = useState("");
  const go = async () => { try { const r = await api<{ token: string; role: string }>("/auth/login", { method: "POST", body: JSON.stringify({ email, password: pw }) }); setToken(r.token); onDone({ role: r.role, email }); } catch { setErr("Invalid credentials"); } };
  return <div className="login"><div className="brand"><Logo /><div><h1>ATLAS</h1><small>Autonomous AI onboarding agent</small></div></div>
    <input value={email} onChange={(e) => setEmail(e.target.value)} /><input type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
    <button className="primary" onClick={go}>Sign in</button>{err && <p className="err">{err}</p>}
    <p className="hint">Demo-only: admin@ / hr@ / viewer@northstarlabs.demo — AdminDemo123! / HRDemo123! / ViewerDemo123!</p></div>;
}

function NewCase({ onCreated, onError }: { onCreated: (id: number) => void; onError: (e: unknown) => void }) {
  const [goal, setGoal] = useState("Onboard Priya Sharma for Software Engineer, joining 20 October 2026."); const [email, setEmail] = useState("");
  return <div className="new"><textarea value={goal} onChange={(e) => setGoal(e.target.value)} rows={3} />
    <input placeholder="Employee personal email (optional)" value={email} onChange={(e) => setEmail(e.target.value)} />
    <button className="primary" onClick={() => api<{ id: number }>("/onboarding", { method: "POST", body: JSON.stringify({ goal, email: email || null }) }).then((r) => onCreated(r.id)).catch(onError)}>Create case</button></div>;
}

function PreviewModal({ cid, ids, onClose }: { cid: number; ids: number[]; onClose: () => void }) {
  const [p, setP] = useState<Preview[]>([]);
  useEffect(() => { Promise.all(ids.map((i) => api<Preview>(`/onboarding/${cid}/documents/${i}/preview`))).then(setP).catch(() => onClose()); }, []); // eslint-disable-line
  const shared = p.length === 2 ? Object.keys({ ...p[0].fields, ...p[1].fields }).filter((k) => p[0].fields[k] && p[1].fields[k]) : [];
  return <div className="overlay" onClick={onClose}><div className="modal" onClick={(e) => e.stopPropagation()}>
    <header><b>{p.length === 2 ? "Compare documents" : "Document preview"}</b><button onClick={onClose}>Close</button></header>
    {p.length === 2 && <div className="cmp">{shared.length === 0 ? "No shared fields to compare." : shared.map((k) => {
      const same = norm(p[0].fields[k]) === norm(p[1].fields[k]);
      return <div key={k} className={same ? "ok" : "issue"}>{same ? "✓" : "⚠"} {k}: {same ? "match" : `“${p[0].fields[k]}” vs “${p[1].fields[k]}”`}</div>; })}</div>}
    <div className={`cols c${p.length}`}>{p.map((d) => <div key={d.filename}><h4>{d.filename} <small>{d.doc_type} · {LABEL[d.status] ?? d.status}</small></h4><pre>{d.text}</pre></div>)}</div>
    <small className="hint">Long identifiers are masked. Viewing is recorded in the audit trail.</small></div></div>;
}

function PasswordModal({ cid, onClose, onOk }: { cid: number; onClose: () => void; onOk: (v: Record<string, string>, secs: number) => void }) {
  const [pw, setPw] = useState(""); const [err, setErr] = useState(""); const [b, setB] = useState(false);
  const go = async () => { setB(true); try { const r = await api<{ values: Record<string, string>; expires_in: number }>(`/onboarding/${cid}/reveal`, { method: "POST", body: JSON.stringify({ password: pw }) }); onOk(r.values, r.expires_in); }
    catch (e) { setErr(e instanceof ApiError && typeof e.detail === "string" ? e.detail : "Not allowed"); setPw(""); } setB(false); };
  return <div className="overlay" onClick={onClose}><div className="modal small" onClick={(e) => e.stopPropagation()}>
    <h4>Confirm your password</h4><p className="hint">Sensitive details (account, ID and tax numbers) are shown for 30 seconds. This access is recorded in the audit trail.</p>
    <input type="password" autoFocus placeholder="Admin password" value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => e.key === "Enter" && pw && go()} />
    {err && <p className="err">{err}</p>}<button className="primary" disabled={!pw || b} onClick={go}>Reveal</button><button onClick={onClose}>Cancel</button></div></div>;
}

function EditModal({ c, onClose, onSave }: { c: Case; onClose: () => void; onSave: (v: Record<string, string>) => void }) {
  const [f, setF] = useState({ name: c.name, role: c.role, joining: c.joining, email: c.email ?? "" });
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  return <div className="overlay" onClick={onClose}><div className="modal small" onClick={(e) => e.stopPropagation()}>
    <h4>Edit case</h4><label>Full name<input value={f.name} onChange={set("name")} /></label><label>Job title<input value={f.role} onChange={set("role")} /></label>
    <label>Joining date<input value={f.joining} onChange={set("joining")} /></label><label>Personal email<input value={f.email} onChange={set("email")} /></label>
    <p className="hint">Saving resets any pending approval. Changing the name re-validates every document against the new name.</p>
    <button className="primary" onClick={() => onSave(Object.fromEntries(Object.entries(f).filter(([, v]) => v.trim())))}>Save changes</button><button onClick={onClose}>Cancel</button></div></div>;
}

function Workspace({ id, me, onChange, onError, onDeleted }: { id: number; me: Me; onChange: () => void; onError: (e: unknown) => void; onDeleted: () => void }) {
  const role = me.role;
  const [c, setC] = useState<Case | null>(null); const [ev, setEv] = useState<Ev[]>([]); const [mails, setMails] = useState<Mail[]>([]); const [busy, setBusy] = useState(false);
  const [drawer, setDrawer] = useState(false); const [picked, setPicked] = useState<number[]>([]); const [view, setView] = useState<number[] | null>(null); const [mail, setMail] = useState("");
  const [ask, setAsk] = useState(false); const [edit, setEdit] = useState(false); const [del, setDel] = useState(false);
  const [rev, setRev] = useState<{ values: Record<string, string>; until: number } | null>(null); const [left, setLeft] = useState(0);
  useEffect(() => {                                                    // revealed values auto-hide after 30s or when the tab is hidden
    if (!rev) return;
    const t = setInterval(() => { const n = Math.ceil((rev.until - Date.now()) / 1000); if (n <= 0) setRev(null); else setLeft(n); }, 500);
    const vis = () => { if (document.hidden) setRev(null); }; document.addEventListener("visibilitychange", vis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [rev]);
  const load = useCallback(async () => { setC(await api<Case>(`/onboarding/${id}`)); setEv(await api<Ev[]>(`/onboarding/${id}/activity`)); setMails(await api<Mail[]>(`/onboarding/${id}/emails`)); onChange(); }, [id, onChange]);
  useEffect(() => { setC(null); setPicked([]); setDrawer(false); setRev(null); load().catch(onError); }, [id]); // eslint-disable-line
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); try { await fn(); await load(); } catch (e) { onError(e); } setBusy(false); };
  if (!c) return <p className="empty">Loading…</p>;
  const docs = c.documents ?? []; const plan: any[] = c.state.plan ?? []; const done = plan.filter((p) => p.state === "done").length;
  const confirmed = docs.filter((d) => d.reviewed_by).length; const review = docs.filter((d) => d.status === "NEEDS_REVIEW");
  const pending = c.approvals?.find((a) => a.status === "PENDING"); const ro = role === "VIEWER"; const missing: string[] = c.state.missing ?? [];
  const unconfirmed = docs.filter((d) => d.status === "VERIFIED" && !d.reviewed_by).map((d) => d.id);
  const toggle = (i: number) => setPicked((p) => (p.includes(i) ? p.filter((x) => x !== i) : [...p, i]));
  const upload = (files: FileList | null) => files && act(async () => { for (const f of Array.from(files)) { const fd = new FormData(); fd.append("file", f); await api(`/onboarding/${id}/documents`, { method: "POST", body: fd }); } });
  const confirmMany = (ids: number[]) => act(async () => { await api(`/onboarding/${id}/documents/confirm`, { method: "POST", body: JSON.stringify({ ids }) }); setPicked([]); });
  const tools: Record<string, number> = {}; ev.filter((e) => e.action.startsWith("tool:")).forEach((e) => { const k = e.action.slice(5); tools[k] = (tools[k] ?? 0) + 1; });
  const v = c.state.verification;
  const rows = docs.flatMap((d) => Object.entries(d.fields).filter(([k, x]) => x && FIELD[k]).map(([k, x]) => ({ k, x: x as string, d })));
  const changes: Record<string, string> = pending ? JSON.parse(pending.changes) : {};
  return (<>
    <header className="head"><div><h2>{c.name}</h2><p>{c.role} · Joining {c.joining}{c.email && <> · ✉ {c.email}</>}</p></div><div className="hbtns"><i className={`tag ${c.status}`}>{LABEL[c.status] ?? c.status}</i>
      <button disabled={ro || c.status === "COMPLETED"} onClick={() => setEdit(true)}>Edit</button>{role === "ADMIN" && <button className="danger" onClick={() => setDel(true)}>Delete</button>}</div></header>

    <section className="insights">
      <h3>AI insights</h3>
      {plan.length === 0 ? <p>The AI worker hasn't run yet. Upload documents, then click “Run AI worker”.</p> : <>
        <p className="sum">✓ {done} of {plan.length} required documents verified by AI{confirmed > 0 && <> · {confirmed} confirmed by a human reviewer</>}</p>
        {missing.filter((m) => m !== "Employee email").map((m) => <p key={m} className="issue">✗ Missing: {m}</p>)}
        {missing.includes("Employee email") && <p className="issue">✗ Employee email is needed to send the welcome message.
          <span className="inl"><input placeholder="name@example.com" value={mail} onChange={(e) => setMail(e.target.value)} />
            <button disabled={busy || ro || !mail} onClick={() => act(async () => { await api(`/onboarding/${id}/email`, { method: "PATCH", body: JSON.stringify({ email: mail }) }); setMail(""); })}>Save email</button></span></p>}
        {review.map((d) => <p key={d.id} className="issue">⚠ {d.filename}: {d.issues.join("; ")}</p>)}
        {pending && <><p>⏸ Waiting for human approval. Prepared changes:</p><ul className="chg">{Object.entries(changes).map(([k, x]) => <li key={k}><small>{k}</small> {x}</li>)}</ul></>}
        {mails.length > 0 && <p className="okline">✉ Welcome email sent to {mails[0].to_addr} — they can start on {c.joining}.</p>}
        {c.status === "COMPLETED" && <p className="okline">✓ Onboarding complete — final verification {v?.passed ? "passed" : "failed"}.</p>}</>}
      <div className="btns">
        {pending && <><button className="primary" disabled={busy || ro} onClick={() => act(() => api(`/approvals/${pending.id}/approve`, { method: "POST" }))}>Approve &amp; Continue</button>
          <button disabled={busy || ro} onClick={() => act(() => api(`/approvals/${pending.id}/reject`, { method: "POST", body: JSON.stringify({ reason: "Rejected in UI" }) }))}>Reject</button></>}
        <button onClick={() => setDrawer(true)}>View in detail</button>
        <button disabled={busy || ro || c.status === "COMPLETED"} onClick={() => act(() => api(`/onboarding/${id}/start`, { method: "POST" }))}>Run AI worker</button>
      </div>
    </section>

    <section><h3>Employee details</h3><div className="tw"><table className="tbl det"><thead><tr><th>Field</th><th>Value</th><th>Status</th></tr></thead><tbody>
      <tr><td>Full name</td><td><b>{c.name}</b> {role === "ADMIN" && (rev
        ? <button className="eye on" title="Hide sensitive details" onClick={() => setRev(null)}><Eye /> Hide ({left}s)</button>
        : <button className="eye" title="View sensitive details (password required)" aria-label="View sensitive details" onClick={() => setAsk(true)}><Eye /></button>)}</td><td>Case request</td></tr>
      {[{ label: "Job title", value: c.role }, { label: "Joining date", value: c.joining }, { label: "Personal email", value: c.email ?? "—" },
        ...(["id_type", "id_number", "address", "bank", "account", "ifsc", "degree", "tax_id"] as const).map((k) => { const d = docs.find((x) => x.fields[k]); return { label: FIELD[k], key: k as string, d, value: d ? (d.fields[k] as string) : "—" }; })]
        .map((r: { label: string; value: string; key?: string; d?: Doc }) => <tr key={r.label}><td>{r.label}</td>
          <td>{r.key && rev?.values[r.key] ? <b className="reveal">{rev.values[r.key]}</b> : r.value}</td>
          <td>{r.d ? (r.d.reviewed_by ? `✓ ${r.d.reviewed_by}` : r.d.status === "VERIFIED" ? "AI verified" : "Needs review") : r.key ? "Not provided" : "Case request"}</td></tr>)}
    </tbody></table></div></section>

    <section><h3>Documents</h3>
      {docs.length > 0 && <div className="cmpbar"><button disabled={unconfirmed.length === 0} onClick={() => setPicked(unconfirmed)}>Select all unconfirmed ({unconfirmed.length})</button>
        {picked.length > 0 && <> <b>{picked.length} selected</b>
          <button className="primary" disabled={busy || ro} onClick={() => confirmMany(picked)}>Confirm selected as reviewed</button>
          {picked.length === 2 && <button onClick={() => setView(picked)}>Compare side by side</button>}
          <button onClick={() => setPicked([])}>Clear</button></>}</div>}
      <div className="docs">{docs.map((d) => <div key={d.id} className={`doc ${picked.includes(d.id) ? "sel" : ""}`}>
        <div className="dh"><label><input type="checkbox" checked={picked.includes(d.id)} onChange={() => toggle(d.id)} /> <b>{d.filename}</b></label><i className={`tag ${d.status}`}>{LABEL[d.status] ?? d.status}</i></div>
        <small>{d.doc_type ? DOCNAME[d.doc_type] ?? d.doc_type : "unparsed"}</small>
        {Object.entries(d.fields).filter(([k, x]) => x && FIELD[k]).map(([k, x]) => <div key={k}><small>{FIELD[k]}</small> {x}</div>)}
        {d.issues.map((i) => <div key={i} className="issue">⚠ {i}</div>)}
        <div className="trust">
          {d.status === "VERIFIED" && <span className="chip ai">AI verified</span>}
          {d.reviewed_by ? <span className="chip human">✓ Confirmed by {d.reviewed_by}</span>
            : d.status === "VERIFIED" && <button disabled={busy || ro} onClick={() => confirmMany([d.id])}>Confirm</button>}
        </div>
        <div className="acts"><button title="Preview document" onClick={() => setView([d.id])}><Eye /> Preview</button>
          {d.status === "NEEDS_REVIEW" && <button disabled={busy || ro} onClick={() => act(() => api(`/onboarding/${id}/documents/${d.id}`, { method: "DELETE" }))}>Remove</button>}</div>
      </div>)}</div>
      <label className="drop">Upload documents (.txt) — one file can contain several documents<input type="file" multiple disabled={busy || ro} onChange={(e) => upload(e.target.files)} /></label>
    </section>

    {ask && <PasswordModal cid={id} onClose={() => setAsk(false)} onOk={(v, n) => { setRev({ values: v, until: Date.now() + n * 1000 }); setLeft(n); setAsk(false); }} />}
    {edit && <EditModal c={c} onClose={() => setEdit(false)} onSave={(v) => { setEdit(false); act(() => api(`/onboarding/${id}`, { method: "PATCH", body: JSON.stringify(v) })); }} />}
    {del && <div className="overlay" onClick={() => setDel(false)}><div className="modal small" onClick={(e) => e.stopPropagation()}><h4>Delete case for {c.name}?</h4>
      <p className="hint">This removes the case, its documents, extracted data, approvals and employee record. The audit trail is kept.</p>
      <button className="danger" disabled={busy} onClick={async () => { setBusy(true); try { await api(`/onboarding/${id}`, { method: "DELETE" }); onDeleted(); } catch (e) { onError(e); setBusy(false); setDel(false); } }}>Delete permanently</button>
      <button onClick={() => setDel(false)}>Cancel</button></div></div>}
    {view && <PreviewModal cid={id} ids={view} onClose={() => setView(null)} />}
    {drawer && <div className="overlay" onClick={() => setDrawer(false)}><aside className="drawer" onClick={(e) => e.stopPropagation()}>
      <header><b>AI work — details</b><button onClick={() => setDrawer(false)}>Close</button></header>
      <h3>Run summary</h3><div className="kv"><span>Status</span><b>{LABEL[c.status] ?? c.status}</b><span>Employee email</span><b>{c.email ?? "—"}</b><span>Retries</span><b>{c.state.retries ?? 0}</b>
        <span>Approvals</span><b>{c.approvals?.filter((a) => a.status === "APPROVED").length ?? 0} of {c.approvals?.length ?? 0} granted</b></div>
      <h3>Extracted data</h3>{rows.length === 0 ? <small>Nothing extracted yet</small> : <div className="tw"><table className="tbl"><thead><tr><th>Field</th><th>Value</th><th>Source</th><th>Status</th></tr></thead><tbody>
        {rows.map(({ k, x, d }) => <tr key={d.id + k}><td>{FIELD[k]}</td><td>{x}</td><td>{DOCNAME[d.doc_type ?? ""] ?? d.doc_type}</td>
          <td>{d.reviewed_by ? `✓ ${d.reviewed_by}` : d.status === "VERIFIED" ? "AI verified" : "Needs review"}</td></tr>)}</tbody></table></div>}
      <h3>Plan</h3>{plan.map((p) => <div key={p.step} className={`row ${p.state}`}><span>{p.state === "done" ? "✓" : p.state === "review" ? "⚠" : "✗"}</span>{p.step}</div>)}
      {v && <><h3>Verification</h3>{Object.entries(v.checks).map(([k, x]) => <div key={k}>{x ? "✓" : "✗"} {k}</div>)}</>}
      <h3>Emails</h3>{mails.length === 0 ? <small>None sent (sending requires admin approval)</small> : mails.map((m) => <details key={m.id}><summary>{m.subject} → {m.to_addr} ({m.mode})</summary><pre className="mailbody">{m.body}</pre></details>)}
      <h3>Tools used</h3>{Object.keys(tools).length === 0 ? <small>None yet</small> : Object.entries(tools).map(([k, n]) => <div key={k}><code>{k}</code> × {n}</div>)}
      <h3>Audit trail</h3><ol className="audit">{ev.map((e) => <li key={e.id}><time>{e.ts.slice(11, 19)}</time><span className={`actor ${e.actor}`}>{e.actor}</span> {e.action.replace("tool:", "tool · ")} <em>{e.result !== "ok" ? e.result : ""}</em>
        {e.meta !== "{}" && <details><summary>details</summary><code>{e.meta}</code></details>}</li>)}</ol>
    </aside></div>}
  </>);
}
