"""OnboardOS core: FastAPI + stateful onboarding agent. Demo-only credentials. See README."""
import os, re, json, time, sqlite3, hashlib, secrets, smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.security import HTTPBearer
from pydantic import BaseModel

DB = os.getenv("ONBOARDOS_DB", "onboardos.db")  # relative to cwd
SECRET = os.getenv("JWT_SECRET", "dev-only-secret")
FAIL_FIRST = int(os.getenv("DEMO_FAIL_FIRST", "0"))   # inject N failures into record creation
MAX_RETRIES = 3
EMAIL_RE = r"[\w.+-]+@[\w-]+\.[\w.-]+"
SMTP_HOST = os.getenv("SMTP_HOST")   # if unset, mail is written to the outbox table only

# ---------- config-driven requirements (not hardcoded in workflow) ----------
REQUIREMENTS = [
 {"key": "OFFER_LETTER", "name": "Offer Letter", "required": True, "group": "Employment", "kw": ["offer letter"]},
 {"key": "GOV_ID", "name": "Government ID", "required": True, "group": "Identity", "kw": ["government id"]},
 {"key": "ADDRESS_PROOF", "name": "Address Proof", "required": True, "group": "Identity", "kw": ["address proof"]},
 {"key": "DEGREE", "name": "Educational Certificate", "required": True, "group": "Education", "kw": ["degree certificate", "educational certificate"]},
 {"key": "BANK_PROOF", "name": "Bank Account Proof", "required": True, "group": "Payroll", "kw": ["bank account proof", "bank proof"]},
 {"key": "TAX_DOC", "name": "Tax/Payroll Document", "required": True, "group": "Payroll", "kw": ["tax form", "tax document"]},
]

# ---------- persistence ----------
def q(sql, *a):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    cur = c.execute(sql, a); c.commit(); rows = cur.fetchall(); lid = cur.lastrowid; c.close()
    return rows if sql.lstrip().upper().startswith("SELECT") else lid

def init_db():
    for s in ["CREATE TABLE IF NOT EXISTS users(email TEXT PRIMARY KEY, pw TEXT, role TEXT)",
     "CREATE TABLE IF NOT EXISTS cases(id INTEGER PRIMARY KEY, goal TEXT, name TEXT, role TEXT, joining TEXT, status TEXT, state TEXT)",
     "CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, case_id INT, filename TEXT, text TEXT, doc_type TEXT, status TEXT, fields TEXT, issues TEXT)",
     "CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, case_id INT, action TEXT, risk TEXT, changes TEXT, status TEXT, decided_by TEXT, reason TEXT)",
     "CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, case_id INT, actor TEXT, action TEXT, result TEXT, meta TEXT)",
     "CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, case_id INT, to_addr TEXT, subject TEXT, body TEXT, mode TEXT, ts TEXT)",
     "CREATE TABLE IF NOT EXISTS employees(case_id INT PRIMARY KEY, name TEXT, role TEXT, joining TEXT, status TEXT)"]:
        q(s)
    _c = sqlite3.connect(DB)
    for t, col in (("docs", "reviewed_by"), ("cases", "email")):
        if col not in [r[1] for r in _c.execute(f"PRAGMA table_info({t})")]:
            _c.execute(f"ALTER TABLE {t} ADD COLUMN {col} TEXT"); _c.commit()
    _c.close()
    if not q("SELECT 1 FROM users"):
        for e, p, r in [("admin@northstarlabs.demo", "AdminDemo123!", "ADMIN"),
                        ("hr@northstarlabs.demo", "HRDemo123!", "HR_OPERATOR"),
                        ("viewer@northstarlabs.demo", "ViewerDemo123!", "VIEWER")]:
            q("INSERT INTO users VALUES(?,?,?)", e, hash_pw(p), r)

def hash_pw(p, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 100_000).hex()
def check_pw(p, h): return hash_pw(p, h.split("$")[0]) == h

def audit(case_id, actor, action, result="ok", **meta):
    q("INSERT INTO audit(ts,case_id,actor,action,result,meta) VALUES(?,?,?,?,?,?)",
      datetime.now(timezone.utc).isoformat(timespec="seconds"), case_id, actor, action, result, json.dumps(meta))

# ---------- document service: deterministic validation + replaceable classifier ----------
class RuleClassifier:
    """Swap for an LLM-backed classifier/extractor implementing the same two methods."""
    PATS = {"name": r"^\s*(?:full name|name|account holder(?: name)?)\s*:\s*(.+)$", "account": r"^\s*account (?:number|no\.?)\s*:\s*(\d+)",
            "ifsc": r"^\s*ifsc(?: code)?\s*:\s*(\w+)", "bank": r"^\s*bank(?: name)?\s*:\s*(.+)$", "address": r"^\s*address\s*:\s*(.+)$",
            "email": r"^\s*(?:personal\s+)?e-?mail(?: id)?\s*:\s*(" + EMAIL_RE + ")", "id_type": r"^\s*id type\s*:\s*(.+)$",
            "id_number": r"^\s*id number\s*:\s*([\w-]+)", "degree": r"^\s*degree\s*:\s*(.+)$", "tax_id": r"^\s*tax id\s*:\s*([\w-]+)",
            "position": r"^\s*position\s*:\s*(.+)$", "joining_date": r"^\s*joining date\s*:\s*(.+)$"}
    def extract(self, text):
        return {k: (m.group(1).strip() if (m := re.search(p, text, re.I | re.M)) else None) for k, p in self.PATS.items()}
    def classify(self, text):
        low = text.lower()
        for r in REQUIREMENTS:
            if any(k in low for k in r["kw"]): return r["key"]
        f = self.extract(text)                                   # unlabeled content: infer from fields
        for t, k in (("BANK_PROOF", "ifsc"), ("ADDRESS_PROOF", "address"), ("TAX_DOC", "tax_id"), ("DEGREE", "degree"), ("GOV_ID", "id_number")):
            if f.get(k): return t
        return "UNKNOWN"
CLASSIFIER = RuleClassifier()

def norm(s): return set(re.sub(r"[^a-z ]", "", (s or "").lower()).split())
def mask(v): return "•" * max(len(v) - 4, 0) + v[-4:] if v else v

def validate(doc_type, f, employee_name):
    issues = []
    if doc_type == "UNKNOWN": issues.append("Document type not recognised")
    if not f.get("name"): issues.append("Holder name missing")
    elif norm(f["name"]) != norm(employee_name): issues.append(f"Name '{f['name']}' differs from '{employee_name}'")
    if doc_type == "BANK_PROOF":
        if not f.get("ifsc") or not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", f["ifsc"]): issues.append("IFSC format invalid")
        if not f.get("account"): issues.append("Account number missing")
    if doc_type == "ADDRESS_PROOF" and not f.get("address"): issues.append("Address missing")
    return issues

def split_sections(text):
    """A single file may hold several documents (a packet): split on heading lines."""
    lines = text.splitlines()
    heads = [i for i, l in enumerate(lines) if ":" not in l and 0 < len(l.strip()) < 45 and any(k in l.lower() for r in REQUIREMENTS for k in r["kw"])]
    if len(heads) < 2: return [text]
    pre = "\n".join(lines[:heads[0]]).strip()
    parts = [(pre + "\n" if pre else "") + "\n".join(lines[a:b]) for a, b in zip(heads, heads[1:] + [len(lines)])]
    return parts if len({CLASSIFIER.classify(p) for p in parts}) > 1 else [text]

def parse_and_validate(case_id, doc_id):
    d = q("SELECT * FROM docs WHERE id=?", doc_id)[0]; c = q("SELECT * FROM cases WHERE id=?", case_id)[0]
    t = CLASSIFIER.classify(d["text"]); f = CLASSIFIER.extract(d["text"]); issues = validate(t, f, c["name"])
    for k in ("account", "id_number", "tax_id"):                      # never store/display raw identifiers
        if f.get(k): f[k] = mask(f[k])
    status = "VERIFIED" if not issues else "NEEDS_REVIEW"
    q("UPDATE docs SET doc_type=?,status=?,fields=?,issues=? WHERE id=?", t, status, json.dumps(f), json.dumps(issues), doc_id)
    return {"doc_type": t, "status": status, "issues": issues}

# ---------- tool registry: the only way the agent touches state ----------
class NeedsApproval(Exception): ...
class Retryable(Exception): ...
TOOLS = {}
def tool(name, risk, roles, needs_approval=False):
    def deco(fn): TOOLS[name] = dict(fn=fn, risk=risk, roles=roles, needs_approval=needs_approval, doc=fn.__doc__); return fn
    return deco
def call_tool(name, actor, case_id, approved=False, **kw):
    t = TOOLS[name]
    if actor not in t["roles"]: raise PermissionError(f"{actor} may not call {name}")
    if t["needs_approval"] and not approved: raise NeedsApproval(name)
    out = t["fn"](case_id, **kw)
    audit(case_id, actor, f"tool:{name}", "ok", risk=t["risk"])
    return out

@tool("get_requirements", "LOW", {"AI"})
def _req(cid): "Return configured required documents" ; return REQUIREMENTS
@tool("list_documents", "LOW", {"AI"})
def _list(cid): "List case documents"; return q("SELECT * FROM docs WHERE case_id=?", cid)
@tool("parse_document", "LOW", {"AI"})
def _parse(cid, doc_id): "Classify, extract and validate a document"; return parse_and_validate(cid, doc_id)
@tool("create_employee_record", "HIGH", {"AI"}, needs_approval=True)
def _create(cid):
    "Create/finalize employee record (idempotent; requires admin approval)"
    c = q("SELECT * FROM cases WHERE id=?", cid)[0]
    q("INSERT OR REPLACE INTO employees VALUES(?,?,?,?,?)", cid, c["name"], c["role"], c["joining"], "ACTIVE")
    st = json.loads(c["state"] or "{}")
    if st.get("injected", 0) < FAIL_FIRST:                       # demo: record written, then API 'returns 500'
        st["injected"] = st.get("injected", 0) + 1; q("UPDATE cases SET state=? WHERE id=?", json.dumps(st), cid)
        raise Retryable("500 from HR system")
    return {"ok": True}
@tool("send_welcome_email", "HIGH", {"AI"}, needs_approval=True)
def _mail(cid):
    "Email the new hire that they are onboarded and when to start (external communication; requires admin approval)"
    c = q("SELECT * FROM cases WHERE id=?", cid)[0]
    if q("SELECT 1 FROM outbox WHERE case_id=?", cid): return {"already_sent": True}   # idempotent
    subject = "Welcome to Northstar Labs — you're onboarded"
    body = (f"Hi {c['name']},\n\nYour onboarding is complete. You're set up as {c['role']} and can start on {c['joining']}.\n"
            "Your manager and the People team will be in touch with next steps.\n\n— Northstar Labs People Team (sent by ATLAS)")
    mode = "smtp" if SMTP_HOST else "outbox"
    if SMTP_HOST:
        m = EmailMessage(); m["From"] = os.getenv("MAIL_FROM", "people@northstarlabs.demo"); m["To"] = c["email"]; m["Subject"] = subject; m.set_content(body)
        with smtplib.SMTP(SMTP_HOST, int(os.getenv("SMTP_PORT", "587"))) as sm:
            sm.starttls()
            if os.getenv("SMTP_USER"): sm.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS", ""))
            sm.send_message(m)
    q("INSERT INTO outbox(case_id,to_addr,subject,body,mode,ts) VALUES(?,?,?,?,?,?)", cid, c["email"], subject, body, mode, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return {"to": c["email"], "mode": mode}
@tool("verify_employee_record", "LOW", {"AI"})
def _verify(cid):
    "Read back record and compare expected vs actual"
    c = q("SELECT * FROM cases WHERE id=?", cid)[0]; e = q("SELECT * FROM employees WHERE case_id=?", cid)
    docs = q("SELECT doc_type FROM docs WHERE case_id=? AND status='VERIFIED'", cid); have = {d["doc_type"] for d in docs}
    checks = {"Employee record": bool(e), "Welcome email sent": bool(q("SELECT 1 FROM outbox WHERE case_id=?", cid)), "Name": bool(e) and e[0]["name"] == c["name"],
              "Joining date": bool(e) and e[0]["joining"] == c["joining"],
              "Required documents": all(r["key"] in have for r in REQUIREMENTS if r["required"])}
    return {"checks": checks, "passed": all(checks.values())}

# ---------- agent: explicit nodes, persisted state, pause/resume ----------
def set_case(cid, status, **state):
    st = json.loads(q("SELECT state FROM cases WHERE id=?", cid)[0]["state"] or "{}"); st.update(state)
    q("UPDATE cases SET status=?,state=? WHERE id=?", status, json.dumps(st), cid)

def run_agent(cid):
    audit(cid, "AI", "run_started")
    reqs = call_tool("get_requirements", "AI", cid)                                   # DetermineRequirements
    docs = call_tool("list_documents", "AI", cid)                                     # InspectDocuments
    for d in docs:
        if d["status"] == "UPLOADED":                                                 # ParseDocuments + Validate
            r = call_tool("parse_document", "AI", cid, doc_id=d["id"])
            audit(cid, "AI", "document_processed", r["status"], doc_type=r["doc_type"], issues=r["issues"])
    docs = call_tool("list_documents", "AI", cid)
    ok = {d["doc_type"] for d in docs if d["status"] == "VERIFIED"}
    review = [d for d in docs if d["status"] == "NEEDS_REVIEW"]
    missing = [r["name"] for r in reqs if r["required"] and r["key"] not in ok and r["key"] not in {d["doc_type"] for d in review}]
    email = q("SELECT email FROM cases WHERE id=?", cid)[0]["email"]
    if not email:                                                                     # look for it in verified documents
        for d in docs:
            e = d["status"] == "VERIFIED" and json.loads(d["fields"]).get("email")
            if e: email = e; q("UPDATE cases SET email=? WHERE id=?", e, cid); audit(cid, "AI", "email_resolved", "ok", source=d["filename"]); break
    if not email: missing.append("Employee email")
    plan = [{"step": r["name"], "state": "review" if r["key"] in {d["doc_type"] for d in review} else "done" if r["key"] in ok else "missing"} for r in reqs]
    if missing or review:                                                             # DetectMissing -> pause
        set_case(cid, "BLOCKED", plan=plan, missing=missing, review=[d["id"] for d in review])
        audit(cid, "AI", "workflow_paused", "waiting_for_human", missing=missing, needs_review=[d["filename"] for d in review]); return
    ap = q("SELECT * FROM approvals WHERE case_id=? AND action='CREATE_EMPLOYEE' ORDER BY id DESC", cid)
    if not ap:                                                                        # ApprovalCheck
        c = q("SELECT * FROM cases WHERE id=?", cid)[0]
        q("INSERT INTO approvals(case_id,action,risk,changes,status) VALUES(?,?,?,?,'PENDING')", cid, "CREATE_EMPLOYEE", "HIGH",
          json.dumps({"Employee": c["name"], "Role": c["role"], "Joining date": c["joining"], "Welcome email to": c["email"]}))
        set_case(cid, "AWAITING_ADMIN", plan=plan, missing=[]); audit(cid, "AI", "approval_requested", "pending", risk="HIGH"); return
    if ap[0]["status"] == "PENDING": set_case(cid, "AWAITING_ADMIN", plan=plan, missing=[], review=[]); return
    if ap[0]["status"] == "REJECTED": set_case(cid, "BLOCKED", plan=plan); audit(cid, "AI", "halted", "rejected_by_admin"); return
    if ap[0]["status"] == "APPROVED": execute_and_verify(cid, plan)

def execute_and_verify(cid, plan):
    set_case(cid, "AI_PROCESSING"); retries = 0
    for attempt in range(1, MAX_RETRIES + 1):                                         # Execute / Observe / Retry
        try: call_tool("create_employee_record", "AI", cid, approved=True); break
        except Retryable as e:
            retries += 1; audit(cid, "AI", "action_failed", "retryable", error=str(e), attempt=attempt)
            v = call_tool("verify_employee_record", "AI", cid)                        # check partial completion
            if all(v["checks"][k] for k in ("Employee record", "Name", "Joining date")): audit(cid, "AI", "recovered", "record_exists_and_verified"); break
            time.sleep(0.05 * 2 ** attempt)
    else:
        set_case(cid, "BLOCKED", retries=retries); audit(cid, "AI", "escalated_to_human", "max_retries"); return
    try: call_tool("send_welcome_email", "AI", cid, approved=True)                    # external communication, approved
    except Exception as e:
        set_case(cid, "BLOCKED", retries=retries); audit(cid, "AI", "email_failed", "escalated_to_human", error=str(e)[:120]); return
    v = call_tool("verify_employee_record", "AI", cid)                                # VerifyOutcome
    audit(cid, "AI", "verification", "PASS" if v["passed"] else "FAIL", **v["checks"])
    set_case(cid, "COMPLETED" if v["passed"] else "BLOCKED", plan=plan, retries=retries, verification=v)

# ---------- API with backend RBAC ----------
app = FastAPI(title="ATLAS"); bearer = HTTPBearer(auto_error=False); init_db()
class Login(BaseModel): email: str; password: str
class Goal(BaseModel): goal: str; email: str | None = None
class EmailIn(BaseModel): email: str
class Ids(BaseModel): ids: list[int]
class PwIn(BaseModel): password: str
class CaseEdit(BaseModel): name: str | None = None; role: str | None = None; joining: str | None = None; email: str | None = None
FAILS: dict = {}
class Reason(BaseModel): reason: str = ""

def user(cr=Depends(bearer)):
    if not cr: raise HTTPException(401, "Not authenticated")
    try: return jwt.decode(cr.credentials, SECRET, algorithms=["HS256"])
    except jwt.PyJWTError: raise HTTPException(401, "Invalid token")
def need(*roles):
    def dep(u=Depends(user)):
        if u["role"] not in roles: raise HTTPException(403, {"error": "ACCESS_RESTRICTED", "your_role": u["role"], "required": list(roles)})
        return u
    return dep
ANY = ("ADMIN", "HR_OPERATOR", "VIEWER"); WRITE = ("ADMIN", "HR_OPERATOR")

@app.post("/auth/login")
def login(b: Login):
    r = q("SELECT * FROM users WHERE email=?", b.email)
    if not r or not check_pw(b.password, r[0]["pw"]): raise HTTPException(401, "Invalid credentials")
    tok = jwt.encode({"sub": b.email, "role": r[0]["role"], "exp": datetime.now(timezone.utc) + timedelta(hours=8)}, SECRET)
    return {"token": tok, "role": r[0]["role"]}

@app.post("/onboarding")
def create_case(b: Goal, u=Depends(need(*WRITE))):
    found = re.search(EMAIL_RE, b.goal); em = b.email or (found[0] if found else None)
    g = re.sub(r",?\s*(?:(?:personal\s+)?e-?mail(?:\s+id)?\s*[:\-]?\s*)?" + EMAIL_RE, "", b.goal)
    if em and not re.fullmatch(EMAIL_RE, em): raise HTTPException(422, "Invalid email address")
    m = re.match(r"\s*onboard\s+(.+?)\s+(?:as|for)\s+(?:an?\s+)?(.+?)(?:,?\s*joining\s+(.+?))?\.?\s*$", g, re.I)
    if not m: raise HTTPException(422, "Could not understand goal. Try: 'Onboard <name> as <role>, joining <date>'")
    cid = q("INSERT INTO cases(goal,name,role,joining,status,state,email) VALUES(?,?,?,?,?,?,?)", b.goal, m[1], m[2], m[3] or "TBD", "DRAFT", "{}", em)
    audit(cid, u["role"], "case_created", goal=b.goal); return {"id": cid, "name": m[1], "role": m[2], "joining": m[3], "email": em}

@app.post("/onboarding/{cid}/start")
def start(cid: int, u=Depends(need(*WRITE))): audit(cid, u["role"], "start_requested"); run_agent(cid); return get_case(cid, u)

@app.post("/onboarding/{cid}/documents")
async def upload(cid: int, file: UploadFile = File(...), u=Depends(need(*WRITE))):
    raw = await file.read()
    if len(raw) > 2_000_000: raise HTTPException(413, "File too large")
    if not file.filename.lower().endswith((".txt", ".md")): raise HTTPException(415, "Prototype accepts text documents (PDF/DOCX/OCR parsers plug in via DocumentService)")
    parts = split_sections(raw.decode("utf-8", "ignore"))                              # one file may contain several documents
    for i, t in enumerate(parts, 1):
        name = file.filename if len(parts) == 1 else f"{file.filename} · part {i}"
        q("INSERT INTO docs(case_id,filename,text,doc_type,status,fields,issues) VALUES(?,?,?,?,?,?,?)", cid, name, t, None, "UPLOADED", "{}", "[]")
    audit(cid, u["role"], "document_uploaded", filename=file.filename, documents=len(parts))
    run_agent(cid)                                                                    # agent auto-resumes
    return get_case(cid, u)

@app.get("/onboarding/{cid}")
def get_case(cid: int, u=Depends(need(*ANY))):
    c = q("SELECT * FROM cases WHERE id=?", cid)
    if not c: raise HTTPException(404, "Case not found")
    docs = [{**dict(d), "fields": json.loads(d["fields"]), "issues": json.loads(d["issues"]), "text": None} for d in q("SELECT * FROM docs WHERE case_id=?", cid)]
    c = c[0]
    return {**dict(c), "state": json.loads(c["state"] or "{}"), "documents": docs,
            "approvals": [dict(a) for a in q("SELECT * FROM approvals WHERE case_id=?", cid)]}

@app.get("/onboarding/{cid}/activity")
def activity(cid: int, u=Depends(need(*ANY))): return [dict(r) for r in q("SELECT * FROM audit WHERE case_id=? ORDER BY id", cid)]
@app.get("/approvals")
def approvals(u=Depends(need(*ANY))): return [dict(r) for r in q("SELECT * FROM approvals WHERE status='PENDING'")]

@app.post("/approvals/{aid}/approve")
def approve(aid: int, u=Depends(need("ADMIN"))):
    a = q("SELECT * FROM approvals WHERE id=?", aid)
    if not a or a[0]["status"] != "PENDING": raise HTTPException(409, "Approval not pending")
    q("UPDATE approvals SET status='APPROVED',decided_by=? WHERE id=?", u["sub"], aid)
    audit(a[0]["case_id"], "ADMIN", "approval_granted", approved_action=a[0]["action"], by=u["sub"])
    run_agent(a[0]["case_id"]); return get_case(a[0]["case_id"], u)                  # approval resumes the workflow

@app.post("/approvals/{aid}/reject")
def reject(aid: int, b: Reason, u=Depends(need("ADMIN"))):
    a = q("SELECT * FROM approvals WHERE id=?", aid)
    if not a: raise HTTPException(404, "Not found")
    q("UPDATE approvals SET status='REJECTED',decided_by=?,reason=? WHERE id=?", u["sub"], b.reason, aid)
    audit(a[0]["case_id"], "ADMIN", "approval_rejected", reason=b.reason); run_agent(a[0]["case_id"]); return {"ok": True}

@app.get("/tools")
def tools(u=Depends(need(*ANY))): return {k: {"risk": v["risk"], "roles": sorted(v["roles"]), "needs_approval": v["needs_approval"], "description": v["doc"]} for k, v in TOOLS.items()}

@app.get("/onboarding")
def list_cases(u=Depends(need(*ANY))): return [dict(r) | {"state": json.loads(r["state"] or "{}")} for r in q("SELECT * FROM cases ORDER BY id DESC")]

@app.delete("/onboarding/{cid}/documents/{did}")
def remove_doc(cid: int, did: int, u=Depends(need(*WRITE))):
    d = q("SELECT filename FROM docs WHERE id=? AND case_id=?", did, cid)
    if not d: raise HTTPException(404, "Document not found")
    q("DELETE FROM docs WHERE id=?", did); audit(cid, u["role"], "document_removed", filename=d[0]["filename"])
    run_agent(cid); return get_case(cid, u)                                          # agent re-evaluates

@app.post("/onboarding/{cid}/documents/{did}/confirm")
def confirm_doc(cid: int, did: int, u=Depends(need(*WRITE))):
    d = q("SELECT filename,status FROM docs WHERE id=? AND case_id=?", did, cid)
    if not d: raise HTTPException(404, "Document not found")
    if d[0]["status"] != "VERIFIED": raise HTTPException(409, "Only AI-verified documents can be confirmed")
    q("UPDATE docs SET reviewed_by=? WHERE id=?", u["sub"], did)                      # human sign-off after AI check
    audit(cid, u["role"], "document_confirmed", filename=d[0]["filename"], by=u["sub"]); return get_case(cid, u)

@app.get("/onboarding/{cid}/documents/{did}/preview")
def preview(cid: int, did: int, u=Depends(need(*ANY))):
    r = q("SELECT * FROM docs WHERE id=? AND case_id=?", did, cid)
    if not r: raise HTTPException(404, "Document not found")
    d = r[0]; masked = re.sub(r"\d{5,}", lambda m: "•" * (len(m[0]) - 4) + m[0][-4:], d["text"])  # never return raw identifiers
    audit(cid, u["role"], "document_previewed", filename=d["filename"], by=u["sub"])
    return {"filename": d["filename"], "doc_type": d["doc_type"], "status": d["status"], "text": masked, "fields": json.loads(d["fields"])}

@app.post("/onboarding/{cid}/documents/confirm")
def confirm_many(cid: int, b: Ids, u=Depends(need(*WRITE))):
    for did in b.ids:
        d = q("SELECT filename,status FROM docs WHERE id=? AND case_id=?", did, cid)
        if d and d[0]["status"] == "VERIFIED":
            q("UPDATE docs SET reviewed_by=? WHERE id=?", u["sub"], did)
            audit(cid, u["role"], "document_confirmed", filename=d[0]["filename"], by=u["sub"])
    return get_case(cid, u)

@app.patch("/onboarding/{cid}/email")
def set_email(cid: int, b: EmailIn, u=Depends(need(*WRITE))):
    if not re.fullmatch(EMAIL_RE, b.email): raise HTTPException(422, "Invalid email address")
    q("UPDATE cases SET email=? WHERE id=?", b.email, cid); audit(cid, u["role"], "email_set", by=u["sub"])
    run_agent(cid); return get_case(cid, u)

@app.get("/onboarding/{cid}/emails")
def emails(cid: int, u=Depends(need(*ANY))): return [dict(r) for r in q("SELECT * FROM outbox WHERE case_id=? ORDER BY id", cid)]

@app.patch("/onboarding/{cid}")
def edit_case(cid: int, b: CaseEdit, u=Depends(need(*WRITE))):
    r = q("SELECT * FROM cases WHERE id=?", cid)
    if not r: raise HTTPException(404, "Case not found")
    c = r[0]
    if c["status"] == "COMPLETED": raise HTTPException(409, "Completed cases can't be edited")
    new = {k: v.strip() for k, v in b.__dict__.items() if v is not None}
    if any(not v for v in new.values()): raise HTTPException(422, "Fields can't be empty")
    if "email" in new and not re.fullmatch(EMAIL_RE, new["email"]): raise HTTPException(422, "Invalid email address")
    changes = {k: v for k, v in new.items() if v != c[k]}                              # keys are whitelisted by the model
    if not changes: return get_case(cid, u)
    q("UPDATE cases SET " + ",".join(f"{k}=?" for k in changes) + " WHERE id=?", *changes.values(), cid)
    q("DELETE FROM approvals WHERE case_id=?", cid)                                   # old approvals described the old details
    if "name" in changes: q("UPDATE docs SET status='UPLOADED', reviewed_by=NULL WHERE case_id=?", cid)   # re-validate against new name
    audit(cid, u["role"], "case_edited", by=u["sub"], changes=changes, approvals_reset=True, documents_revalidated="name" in changes)
    if c["status"] != "DRAFT": run_agent(cid)
    return get_case(cid, u)

@app.delete("/onboarding/{cid}")
def delete_case(cid: int, u=Depends(need("ADMIN"))):
    r = q("SELECT name FROM cases WHERE id=?", cid)
    if not r: raise HTTPException(404, "Case not found")
    for t in ("docs", "approvals", "employees"): q(f"DELETE FROM {t} WHERE case_id=?", cid)   # personal data removed; audit trail kept
    q("DELETE FROM cases WHERE id=?", cid)
    audit(cid, "ADMIN", "case_deleted", name=r[0]["name"], by=u["sub"]); return {"ok": True}

@app.post("/onboarding/{cid}/reveal")
def reveal(cid: int, b: PwIn, u=Depends(need("ADMIN"))):
    """Re-authenticate the admin, then return raw sensitive values for a short window. Every attempt is audited."""
    now = time.time(); recent = [t for t in FAILS.get(u["sub"], []) if now - t < 300]
    if len(recent) >= 5: raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    row = q("SELECT pw FROM users WHERE email=?", u["sub"])
    if not row or not check_pw(b.password, row[0]["pw"]):
        FAILS[u["sub"]] = recent + [now]; audit(cid, "ADMIN", "sensitive_reveal", "denied_bad_password", by=u["sub"])
        raise HTTPException(401, "Incorrect password")
    FAILS.pop(u["sub"], None); out = {}
    for d in q("SELECT text FROM docs WHERE case_id=? AND status='VERIFIED'", cid):
        f = CLASSIFIER.extract(d["text"])
        for k in ("account", "id_number", "tax_id"):
            if f.get(k) and k not in out: out[k] = f[k]
    audit(cid, "ADMIN", "sensitive_reveal", "granted", by=u["sub"], fields=sorted(out))
    return {"values": out, "expires_in": 30}
