# Endure Quote Builder — project context

Read this first. It carries the full context of the session that built this repo,
so any new Claude session can pick up exactly where it left off.

## What this is
Internal proposal/quote tool for Endure Decks (Perth decking builder, owner
Lachlan — "Lachy"). Sales rep **Matt** uses it from anywhere: build a job in the
browser, see a live page-for-page preview, **Generate PDF** (the finished branded
client document), **Save to Drive** (job folder with job-data.json,
internal-costing.json, site photos). Deploys to **quote.enduredecks.com.au**.

## Architecture (all one host — do not split)
- `public/index.html` — the builder SPA (~2,400 lines, vanilla JS). The live
  preview (#pvRoot) mirrors the PDF page-for-page. `buildJobData()` is the single
  source for job data — both Save and Generate PDF use it.
- `server.py` — FastAPI. Serves the page, `/assets`, and `GET/POST /api/endure`
  with actions: `rates` (Google Sheet), `save_job` (Drive), `render` (PDF).
  Token-gated (TEAM_TOKEN). Always returns 200 + {ok:false,error} on failure —
  that contract is intentional, the frontend depends on it.
- `scripts/render_proposal.py` — THE renderer. WeasyPrint. This exact script
  produced every client PDF to date. **Never replace it with a browser/Puppeteer
  renderer** — different engine = different pixels. The preview imitates it, not
  the other way round.
- `assets/` — brand images the renderer stamps in. Board name → image mapping
  lives in the builder (MATERIALS list + rate_card boards): merbau.jpg,
  jarrah.jpg, blackbutt.jpg, spottedgum.jpg, evainf.jpg, evapio.jpg, trex.jpg,
  moisture.jpg (shared by both MoistureShield lines). Custom boards have no
  image → hero renders as an empty cream box (known, acceptable for now).
- `Dockerfile` — python:3.12-slim + Pango libs (WeasyPrint needs them). This is
  why the host is Railway/Render (Docker), NOT Vercel.

## Key decisions already made (don't relitigate)
- Separate repo + subdomain (quote.enduredecks.com.au), fully separate from the
  marketing site (enduredecks-website, hosted on Vercel — untouched by this).
- Payment schedule default: **5 / 35 / 25 / 25 / 10** (deposit / pre-start /
  materials / structural / handover), including the step-body copy percentages.
- Three builder lanes: Proposal (`mode:"range"`, priced range + design-service
  upsell), Quote (`mode:"fixed"`, itemised fixed price + accept/deposit), and
  Manual (Quote) — a line-item grid (`S.manualOnly`, Cowork-built 2026-07-29)
  for jobs the rate card can't price. Manual emits standard `mode:"fixed"`
  job-data; the renderer/schema are untouched. Each manual line carries a
  `cat` type (mat-board / mat-sub / mat-other / labour / subbie / fixed) that
  routes it into the internal costing doc's Materials (by stream, with
  per-type subtotals) / Labour / Pass-through tables via `autoFillInternal()`.
  `sample_manual.json` is the render test for this lane.
- Internal costing (internal-costing.json / the red-banner page) is PM-only and
  NEVER merged into the client document.
- Rates live in a Google Sheet — editing the sheet updates the tool, no deploy.

## Deploy state / the journey (where we got to)
1. ✅ Code built, tested end-to-end IN-SESSION: sample renders (Pantons, and a
   Merbau-hero variant proving per-product hero images), server boot, token
   gate, render-over-API returns application/pdf, page + assets serve.
2. ⏳ GitHub: repo `lachlanjames22-cmd/endure-quote-builder` created by Lachy,
   but the Claude GitHub App connection was stale — push was pending a
   reconnect (claude.ai → Settings → Connectors → GitHub) at handover time.
3. ⏳ Railway: not yet created. Steps: New Project → Deploy from GitHub repo →
   Dockerfile auto-detected → set env vars → get *.up.railway.app test URL.
4. ⏳ Env vars (names in .env.example): GOOGLE_SERVICE_ACCOUNT (full JSON, one
   line), RATE_SHEET_ID, INCOMING_JOBS_ID, TEAM_TOKEN. Values live in Lachy's
   old Vercel setup / README history — do not commit them.
5. ⏳ DNS: quote.enduredecks.com.au CNAME → Railway (registrar side).
6. ⏳ Test run: rates load · fake job → Generate PDF → compare vs a known-good
   document · Save to Drive → folder in Operations → Incoming Jobs.

## v2 (agreed, not built): the intelligence layer
The old workflow had Claude "generate the proposal" from a saved job folder and
it did real work — the spec is documented in the endure-decks-proposals skill
(SKILL.md): fill empty bullets/included/excluded, polish copy to brand voice,
fix typos, catch wrong hero images, reconcile figures vs the costing sheet
(flag >~1% mismatch), warn GP% < 30% and GP/install-day < $1,000. Build as a
server-side `action:'polish'` calling the Claude API. Hard rule: **the AI may
rewrite words and raise flags but must never change a number on its own.**

## Google side (one-time, Lachy's accounts)
Service account with Drive + Sheets APIs enabled, added as Content Manager on
the "Endure Carpentry and Constructions" shared drive (covers rate sheet +
Incoming Jobs). Vercel body-size lesson kept: photo attachments ~4.5MB limit.

## Update loop once live
Edit (Cowork or any session) → commit → push to main → Railway auto-deploys
(~1 min). Branches = preview deploys for testing before merge.
