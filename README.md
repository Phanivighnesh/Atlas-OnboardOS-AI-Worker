# ATLAS

> Atlas is an autonomous AI onboarding agent that executes employee workflows with minimal human intervention. It identifies requirements, validates documents, detects missing/inconsistent data, plans and executes approved tasks, requests approval for sensitive actions, recovers from failures, verifies outcomes, resumes paused workflows, and maintains audit logs.

**What's in this version:** employee email capture (from the request, a document, or entered by a human), a welcome email sent only after admin approval (written to an `outbox` table, or sent via SMTP if `SMTP_HOST` is set), per-document human confirmation (single and bulk), document preview/compare, an extracted-data table, and single-file packets that contain several documents.

```
onboardos/
├── backend/            FastAPI + stateful onboarding agent
│   ├── src/onboardos/main.py
│   ├── tests/test_flow.py
│   └── pyproject.toml
├── frontend/           React + TypeScript + Vite
│   └── src/ (App.tsx, api.ts, styles.css)
└── sample_docs/        fictional documents for the demo
```

## Run
```
# terminal 1
cd backend && pip install -e . pytest httpx && uvicorn onboardos.main:app --reload
# terminal 2
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxies /api -> :8000)
cd backend && pytest -q                         # end-to-end agent + RBAC tests
```
Demo path: sign in as admin → create the Priya case → upload the 5 files in `sample_docs/` (or just `priya_full_packet.txt`, which holds all six documents in one file; avoid `_MISMATCH`) → Run AI worker (blocks on Address Proof) → upload `address_proof_UPLOAD_LATER.txt` → approve → verification passes. Sign in as hr@ to see the approve button return 403. Set `DEMO_FAIL_FIRST=1` to watch retry and recovery.

Demo-only credentials: admin@northstarlabs.demo / AdminDemo123!, hr@northstarlabs.demo / HRDemo123!, viewer@northstarlabs.demo / ViewerDemo123!

## Architecture
- **Agent** (`run_agent`): explicit nodes — requirements → inspect → parse → validate → detect missing → plan → approval check → execute → observe/retry → verify. State is persisted per case, so the workflow pauses and resumes (on upload or approval).
- **LLM boundary**: `RuleClassifier` is the only classification/extraction component; swap in an LLM provider with the same `classify`/`extract` interface. Deterministic code owns validation, RBAC, approvals, retry limits, verification, audit.
- **Tools** (`TOOLS`): allowlisted functions with risk, allowed actors, approval flag. `create_employee_record` is HIGH risk and raises `NeedsApproval` unless an ADMIN approval exists — the agent cannot bypass it.
- **RBAC**: enforced in FastAPI dependencies (`need(...)`). Viewer read-only; HR can upload/start but approve/reject → 403 `ACCESS_RESTRICTED`.
- **Recovery**: max 3 attempts, exponential backoff, on failure the agent reads the record back; if it exists and verifies, it continues; otherwise escalates to a human.
- **Sensitive data**: account numbers are masked before storage; raw text is never returned by the API.

## Known limitations / not yet built
React frontend, LangGraph wiring (nodes map 1:1 to graph nodes), Postgres/SQLAlchemy, PDF/DOCX/OCR parsers, Playwright mock HR app, settings UI, custom error pages. Uploads are .txt/.md only.

## Sensitive data, editing and deletion
- **Employee details table** on every case. Account, ID and tax numbers are masked. An ADMIN can click the eye icon beside the employee name, re-enter their password, and see the raw values for 30 seconds (auto-hides on timeout or when the tab is hidden). Wrong passwords are audited and limited to 5 attempts per 5 minutes. HR operators and viewers get 403 from the backend.
- **Edit**: ADMIN/HR can change name, title, joining date, email (not once COMPLETED). Edits reset approvals (they described the old details) and a name change re-validates all documents.
- **Delete**: ADMIN only. Removes documents, extracted data, approvals and the employee record; the audit trail is retained.
