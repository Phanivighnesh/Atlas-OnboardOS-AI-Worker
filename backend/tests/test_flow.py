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
    cid = c.post("/onboarding", json={"goal": "Onboard Priya Sharma for Software Engineer, joining 20 October 2026."}, headers=admin).json()["id"]
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

def test_name_mismatch_blocks():
    cid = c.post("/onboarding", json={"goal": "Onboard Rahul Mehta as Product Designer"}, headers=hr).json()["id"]
    r = up(cid, "bank.txt", "BANK ACCOUNT PROOF\nAccount Holder: Someone Else\nAccount Number: 1234\nIFSC: EXMP0001234").json()
    assert r["documents"][0]["status"] == "NEEDS_REVIEW"
