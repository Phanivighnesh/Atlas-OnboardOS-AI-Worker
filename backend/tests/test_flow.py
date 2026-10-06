import os, tempfile
os.environ["ONBOARDOS_DB"] = tempfile.mktemp(suffix=".db"); os.environ["DEMO_FAIL_FIRST"] = "1"
from onboardos import main as A
from fastapi.testclient import TestClient
c = TestClient(A.app)
def tok(e, p): return {"Authorization": "Bearer " + c.post("/auth/login", json={"email": e, "password": p}).json()["token"]}
admin, hr, viewer = tok("admin@northstarlabs.demo", "AdminDemo123!"), tok("hr@northstarlabs.demo", "HRDemo123!"), tok("viewer@northstarlabs.demo", "ViewerDemo123!")
D = {"offer.txt": "OFFER LETTER\nName: Priya Sharma", "id.txt": "GOVERNMENT ID\nName: Priya Sharma", "degree.txt": "DEGREE CERTIFICATE\nName: Priya Sharma",
     "bank.txt": "BANK ACCOUNT PROOF\nAccount Holder: Priya Sharma\nAccount Number: 000012344821\nIFSC: EXMP0001234", "tax.txt": "TAX FORM\nName: Priya Sharma"}
def up(cid, n, t, h=admin): return c.post(f"/onboarding/{cid}/documents", files={"file": (n, t)}, headers=h)

def test_full_flow():
    cid = c.post("/onboarding", json={"goal": "Onboard Priya Sharma for Software Engineer, joining 20 October 2026.", "email": "priya.personal@example.test"}, headers=admin).json()["id"]
    assert up(cid, "x.txt", "x", viewer).status_code == 403
    for n, t in D.items(): up(cid, n, t)
    r = c.post(f"/onboarding/{cid}/start", headers=admin).json()
    assert r["status"] == "BLOCKED" and r["state"]["missing"] == ["Address Proof"]
    r = up(cid, "addr.txt", "ADDRESS PROOF\nName: Priya Sharma\nAddress: 12 Lake Road").json()
    assert r["status"] == "AWAITING_ADMIN"
    aid = r["approvals"][0]["id"]
    assert c.post(f"/approvals/{aid}/approve", headers=hr).status_code == 403
    r = c.post(f"/approvals/{aid}/approve", headers=admin).json()
    assert r["status"] == "COMPLETED" and r["state"]["retries"] == 1 and r["state"]["verification"]["passed"]
    acts = [e["action"] for e in c.get(f"/onboarding/{cid}/activity", headers=admin).json()]
    assert "recovered" in acts and "verification" in acts
    assert "000012344821" not in str(c.get(f"/onboarding/{cid}", headers=admin).json())
    assert c.patch(f"/onboarding/{cid}", json={"role": "X"}, headers=hr).status_code == 409   # completed cases are locked

def test_name_mismatch_blocks():
    cid = c.post("/onboarding", json={"goal": "Onboard Rahul Mehta as Product Designer"}, headers=hr).json()["id"]
    r = up(cid, "bank.txt", "BANK ACCOUNT PROOF\nAccount Holder: Someone Else\nAccount Number: 1234\nIFSC: EXMP0001234").json()
    assert r["documents"][0]["status"] == "NEEDS_REVIEW"

def test_remove_conflicting_doc_unblocks():
    cid = c.post("/onboarding", json={"goal": "Onboard Priya Sharma as Software Engineer", "email": "priya.personal@example.test"}, headers=admin).json()["id"]
    for n, t in D.items(): up(cid, n, t)
    up(cid, "addr.txt", "ADDRESS PROOF\nName: Priya Sharma\nAddress: 12 Lake Road")
    bad = up(cid, "bad.txt", "BANK ACCOUNT PROOF\nAccount Holder: Someone Else\nAccount Number: 9999\nIFSC: EXMP0001234").json()
    assert bad["status"] == "BLOCKED" and any(s["state"] == "review" for s in bad["state"]["plan"])
    bad_id = [d["id"] for d in bad["documents"] if d["status"] == "NEEDS_REVIEW"][0]
    assert c.delete(f"/onboarding/{cid}/documents/{bad_id}", headers=viewer).status_code == 403
    assert c.delete(f"/onboarding/{cid}/documents/{bad_id}", headers=hr).json()["status"] == "AWAITING_ADMIN"

def test_confirm_and_preview():
    cid = c.post("/onboarding", json={"goal": "Onboard Priya Sharma as Software Engineer", "email": "priya.personal@example.test"}, headers=admin).json()["id"]
    did = up(cid, "bank.txt", D["bank.txt"]).json()["documents"][0]["id"]
    p = c.get(f"/onboarding/{cid}/documents/{did}/preview", headers=viewer).json()
    assert "000012344821" not in p["text"] and "4821" in p["text"]
    assert c.post(f"/onboarding/{cid}/documents/{did}/confirm", headers=viewer).status_code == 403
    r = c.post(f"/onboarding/{cid}/documents/{did}/confirm", headers=hr).json()
    assert r["documents"][0]["reviewed_by"] == "hr@northstarlabs.demo"

def test_packet_split_and_welcome_email():
    cid = c.post("/onboarding", json={"goal": "Onboard Priya Sharma as Software Engineer, joining 20 October 2026"}, headers=admin).json()["id"]
    packet = "\n\n".join(D.values()) + "\n\nADDRESS PROOF\nName: Priya Sharma\nAddress: 12 Lake Road\nPersonal Email: priya.packet@example.test"
    r = up(cid, "packet.txt", packet).json()
    assert len(r["documents"]) == 6 and r["status"] == "AWAITING_ADMIN" and r["email"] == "priya.packet@example.test"
    assert "priya.packet@example.test" in r["approvals"][0]["changes"]
    assert c.get(f"/onboarding/{cid}/emails", headers=viewer).json() == []            # nothing sent before approval
    r = c.post(f"/approvals/{r['approvals'][0]['id']}/approve", headers=admin).json()
    assert r["status"] == "COMPLETED" and r["state"]["verification"]["checks"]["Welcome email sent"]
    assert c.get(f"/onboarding/{cid}/emails", headers=viewer).json()[0]["to_addr"] == "priya.packet@example.test"

def test_missing_email_blocks_then_resolves():
    cid = c.post("/onboarding", json={"goal": "Onboard Priya Sharma as Software Engineer"}, headers=admin).json()["id"]
    for n, t in {**D, "addr.txt": "ADDRESS PROOF\nName: Priya Sharma\nAddress: 12 Lake Road"}.items(): r = up(cid, n, t).json()
    assert r["status"] == "BLOCKED" and "Employee email" in r["state"]["missing"]
    assert c.patch(f"/onboarding/{cid}/email", json={"email": "bad"}, headers=hr).status_code == 422
    assert c.patch(f"/onboarding/{cid}/email", json={"email": "x@example.test"}, headers=hr).json()["status"] == "AWAITING_ADMIN"

def test_bulk_confirm():
    cid = c.post("/onboarding", json={"goal": "Onboard Priya Sharma as Software Engineer"}, headers=admin).json()["id"]
    for n, t in D.items(): r = up(cid, n, t).json()
    ids = [d["id"] for d in r["documents"]]
    r = c.post(f"/onboarding/{cid}/documents/confirm", json={"ids": ids}, headers=hr).json()
    assert all(d["reviewed_by"] for d in r["documents"])

def _ready_case():
    cid = c.post("/onboarding", json={"goal": "Onboard Priya Sharma as Software Engineer", "email": "p@example.test"}, headers=admin).json()["id"]
    for n, t in {**D, "addr.txt": "ADDRESS PROOF\nName: Priya Sharma\nAddress: 12 Lake Road"}.items(): r = up(cid, n, t).json()
    return cid, r

def test_edit_resets_approval_and_revalidates():
    cid, r = _ready_case(); assert r["status"] == "AWAITING_ADMIN"
    r = c.patch(f"/onboarding/{cid}", json={"role": "Senior Engineer"}, headers=hr).json()
    assert r["status"] == "AWAITING_ADMIN" and len(r["approvals"]) == 1 and "Senior Engineer" in r["approvals"][0]["changes"]
    assert c.patch(f"/onboarding/{cid}", json={"role": "X"}, headers=viewer).status_code == 403
    r = c.patch(f"/onboarding/{cid}", json={"name": "Priya Kumar"}, headers=hr).json()
    assert r["status"] == "BLOCKED" and r["approvals"] == []                          # docs no longer match the name

def test_reveal_requires_admin_password_and_is_audited():
    cid, _ = _ready_case()
    assert c.post(f"/onboarding/{cid}/reveal", json={"password": "AdminDemo123!"}, headers=hr).status_code == 403
    assert c.post(f"/onboarding/{cid}/reveal", json={"password": "wrong"}, headers=admin).status_code == 401
    ok = c.post(f"/onboarding/{cid}/reveal", json={"password": "AdminDemo123!"}, headers=admin).json()
    assert ok["values"]["account"] == "000012344821"
    acts = [(e["action"], e["result"]) for e in c.get(f"/onboarding/{cid}/activity", headers=admin).json()]
    assert ("sensitive_reveal", "denied_bad_password") in acts and ("sensitive_reveal", "granted") in acts

def test_delete_case_admin_only_keeps_audit():
    cid, _ = _ready_case()
    assert c.delete(f"/onboarding/{cid}", headers=hr).status_code == 403
    assert c.delete(f"/onboarding/{cid}", headers=admin).status_code == 200
    assert c.get(f"/onboarding/{cid}", headers=admin).status_code == 404
    assert "case_deleted" in [e["action"] for e in c.get(f"/onboarding/{cid}/activity", headers=admin).json()]
