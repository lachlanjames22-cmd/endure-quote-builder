# Endure Quote Builder

Internal proposal/quote tool for Endure Decks, self-contained on one host:
the builder web page, the rates/save API, and the **real WeasyPrint renderer**
(`scripts/render_proposal.py` — the same script that has always produced the
client PDFs, so the output is identical to every document sent to date).
Deploys to **quote.enduredecks.com.au**.

## What's here
- `public/index.html` — the builder. Live preview mirrors the PDF page-for-page.
  **Generate PDF** renders and downloads the finished document; **Save to Drive**
  files the job folder (job-data.json, internal-costing.json, site photos).
- `server.py`   — FastAPI: serves the page, `GET/POST /api/endure`
  (`rates` · `save_job` · `render`), token-gated.
- `scripts/render_proposal.py` — the branded renderer (WeasyPrint). Unchanged.
- `assets/`     — brand images the renderer stamps into the document.
- `references/` — INPUT_SCHEMA + sample jobs (also used as render tests).
- `Dockerfile`  — python:3.12-slim + Pango libs. Any Docker host runs it.

## Deploy (Railway / Render / Fly — one-time)
1. Push this folder to a GitHub repo.
2. Create a new service from the repo (the Dockerfile is auto-detected).
3. Set env vars: `GOOGLE_SERVICE_ACCOUNT` (full JSON, one line),
   `RATE_SHEET_ID`, `INCOMING_JOBS_ID`, `TEAM_TOKEN`.
4. Add custom domain `quote.enduredecks.com.au` → CNAME at the registrar.

Google side (once): service account with Drive + Sheets APIs enabled, added as
Content Manager on the shared drive (covers the rate sheet + Incoming Jobs).

## How updates work
    edit (Cowork or anywhere) → git commit → git push → host auto-deploys
- `main` = production. Branches get preview deploys (Railway/Render support this).
- Rates change in the Google Sheet — no deploy needed.

## Test after deploy
1. Open the URL → rates load from the sheet.
2. Fake job → **Generate PDF** → compare against a known-good document.
3. **Save to Drive** → folder appears in Incoming Jobs.

## Notes
- `POST {action:'render', job_data}` returns the PDF; add `folder_id` to also
  file a copy into the job's Drive folder.
- Request body limit ~4.5MB applies to photo attachments (base64 inflates ~1.33×).
- The internal-costing JSON is never rendered into the client document.
