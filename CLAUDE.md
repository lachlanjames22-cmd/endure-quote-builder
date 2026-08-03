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
- Recipe builder (Cowork-built, integrated 2026-07-29): compose a hero item
  from ingredients (materials by item, labour by task, margin as % of sell or
  $/day × days). Emits ONE cost line + hero bundle; ingredients live on
  `b.recipe` and expand into the internal costing via `autoFillInternal()`.
  Client document only ever sees the sell price. `S.recipeLib` (library)
  survives New job and travels in the setup export.
- Internal costing (internal-costing.json / the red-banner page) is PM-only and
  NEVER merged into the client document.
- GST convention (2026-07-30): ALL working figures on Matt's side are ex GST —
  costs, rates, margins, fixed job costs (matches the rate sheet). GST is added
  once, at the client-facing sell/render price, and every money field is
  labelled ex/inc. Fixed-cost entries are ×1.1 onto the client cost line;
  internal pass-throughs carry the ex figure untouched.
- Rates live in a Google Sheet — editing the sheet updates the tool, no deploy.
- Quote Log (2026-07-30): every Save to Drive appends a row to the "_Quote Log"
  spreadsheet (auto-created in Incoming Jobs; Log tab + Dashboard tab with
  pipeline/conversion/GP formulas). Status column (Sent/Won/Lost) is updated
  by hand as jobs land — that drives the conversion metrics. Best-effort:
  a log failure never fails the save (resp.logged flags it in the UI).
- Project library (2026-07-30): "Save project" files the stripped builder
  state as JSON into "_Project Drafts" in Incoming Jobs (same name =
  overwrite); Library → Saved projects lists by name + modified date and
  loads via the endureBuilderDraft + reload path. Save to Drive also files
  the rendered client PDF into the job folder now.
- Header bar (2026-07-30): sticky black topbar — logo, Build / Library /
  Contracts & templates dropdowns, Save project button.
- Ballpark lane (Cowork-built, ported 2026-08-02): /ballpark calculator page
  (🚧 banner removed 2026-08-02 — live for real use) + `ballpark:true` job-data flag
  routing render_proposal.py to a range-estimate doc: cover → why-a-range →
  ballpark range card(s) ("A project like this typically lands…" + five-factor
  strip) → Site Assessment agenda page → design-path page (complex lane only).
  Cowork iteration applied 2026-08-02 (endureballpark20260802_1.zip — its
  handoff notes about stubbing email/keeping the 🚧 banner were STALE and
  ignored): the ballpark deliberately stays broad — the Site Assessment is
  sold as a product ($250 inc GST flat, `ballpark_next.consult_fee_inc_gst`,
  older consult_fee_ex_gst read as ×1.1 fallback), with a 10-point on-site
  agenda (ASSESSMENT_AGENDA) as the sales device and a no-chase slow-mover
  footer. Nav (2026-08-02): Ballpark is a topbar dropdown in the builder;
  /ballpark carries the same black topbar with "← Quote builder" home link.
  Email send
  (`action:'send_ballpark'`, wired 2026-08-02 with Lachy): sends FROM Matt's
  address (SEND_EMAIL_USER) with the ballpark PDF attached, BCC to SEND_BCC,
  logs a lead row to the "Ballparks" tab of _Quote Log. Railway BLOCKS
  outbound SMTP, so the primary path is the Gmail API over HTTPS via the
  service account with domain-wide delegation (admin.google.com → API
  controls → Domain-wide delegation → SA client_id + gmail.send scope; Gmail
  API enabled in the SA's Cloud project). SMTP (app password) is only a
  fallback for SMTP-capable hosts. Pricing TABLE in ballpark.html mirrors the
  published site pricing page — keep them in sync. Fixtures:
  sample_ballpark_*.json.
- Save-gate (2026-08-02): hosted Generate PDF is DISABLED until the current
  quote is saved (S.savedClean; any edit re-locks). Saves are idempotent:
  job folder reused by name, files upsert by name, Quote Log upserts one row
  per client (preserving hand-set Status) — re-saving never duplicates.
- Drive-backed image library: "_Image Library" folder in Incoming Jobs,
  actions img_lib_list/get/put, overlay picker on hero/recipe/concept images.
- Online accept links (BETA, 2026-08-02): every Save to Drive mints an accept
  token (`accept_token` in the save response; HMAC of the folder id keyed off
  the SA private key — no new env vars, no database, Drive stays the store).
  Public routes `/q/<token>` (branded accept page; fixed quotes get an accept
  form, proposals get view-only), `/q/<token>/pdf` (fresh render),
  `POST /q/<token>/accept` (writes acceptance.json to the job folder, flips
  Quote Log Status→Accepted, sends confirmation email best-effort). Feature is
  OFF until toggled: "_Accept Links.json" in Incoming Jobs, flipped from the
  builder's Contracts & templates menu (actions accept_links_get/set) — while
  testing, Lachy turns it on/off there. Click-accept legality is pending the
  lawyer's blessing (asked alongside the contract-terms review). Stripe
  deposit-at-accept is the agreed part 2b, not built.
- Site Assessment booking (BUILT 2026-08-02, 🚧 NOT LIVE — Lachy's call, revisit
  after Bali): public /book page (branded, banner'd) + POST /book/submit.
  Personal prefill links are signed tokens (name/email/lane/range in the URL,
  HMAC'd — no storage until submit). Submit files a JSON record to
  "_Assessment Bookings" in Incoming Jobs, flips the Ballparks-tab Status to
  "Assessment booked" (match by email), and fires three emails: client
  confirmation, Matt's action email, accounts invoice request (ACCOUNTS_EMAIL
  env, noted in Matt's email when unset). `BOOKING_LIVE = False` in server.py
  is the master switch — while False, NO client-facing surface references
  /book (ballpark emails and the PDF booking strip stay clean); flip it to
  start injecting the personal link into sent ballparks + the PDF's "book
  online" strip. Roadmap owner-approved next steps: online acceptance
  hardening, soft client portal for sales (questions/design/link sharing),
  ops portal much later.
- Send to client from the builder (2026-08-02): `action:'send_quote'` — same
  Gmail path as the ballpark; card above the save bar, gated like Generate
  PDF (must save first), renders the quote/proposal PDF and emails it from
  Matt's address, BCC shared inbox; prefill includes the accept link when
  links are ON and mode is fixed.

## Deploy state (LIVE as of 2026-07-29)
1. ✅ GitHub: `lachlanjames22-cmd/endure-quote-builder`, default flow is
   branch → PR → merge to `main` → Railway auto-deploys (~1 min). Lachy's
   standing instruction: Claude merges and pushes on his behalf.
2. ✅ Railway: live on Hobby plan, deploys from `main`, env vars set
   (names in .env.example). TEAM_TOKEN must equal the token hardcoded in
   public/index.html (`endure-decks-2026` at time of writing) — mismatch
   shows as "bad token" on every action.
3. ✅ Service account: lives in Lachy's PERSONAL gmail Google Cloud (work
   Workspace org blocks key creation — deliberate workaround). Invited as
   Content Manager on the shared drive + Editor on the rate sheet.
4. ✅ Verified in production: rates load, Generate PDF, Save to Drive.
5. 🟡 DNS: quote.enduredecks.com.au — domain's nameservers are Vercel's
   (marketing site), so the CNAME lives in Vercel → Domains → DNS Records,
   NOT the registrar. Record added 2026-07-29, awaiting Railway's green tick.
6. Rate sheet gaps: only Merbau has a real board price — Jarrah, Spotted Gum,
   Eva-Last ×2, MoistureShield ×2, aluminium system and linear extras are
   FILL. Two duplicate rate-card sheets exist in Drive — the live one is
   RATE_SHEET_ID; the others should be renamed/binned.

## v2 (agreed, not built): the intelligence layer
The old workflow had Claude "generate the proposal" from a saved job folder and
it did real work — the spec is documented in the endure-decks-proposals skill
(SKILL.md): fill empty bullets/included/excluded, polish copy to brand voice,
fix typos, catch wrong hero images, reconcile figures vs the costing sheet
(flag >~1% mismatch), warn GP% < 30% and GP/install-day < $1,000. Build as a
server-side `action:'polish'` calling the Claude API. Hard rule: **the AI may
rewrite words and raise flags but must never change a number on its own.**

## Roadmap (Lachy, 2026-07-29 — agreed direction, build "another time")
1. **Similar projects page** — a bank of past projects (photos + short blurb)
   the rep can pick from per job; selected ones render as a "similar projects
   we've built" page in the client document. Bank should live in Drive (rep
   adds projects without a deploy), loaded via the API like rates.
2. **Onsite companion flow** — a simplified mobile-first lane for site visits:
   preloaded site-visit questions, draw/sketch on canvas, attach photos, then
   either (a) capture everything to the job's Drive folder ready for an
   office-built proposal, or (b) onsite fixed quote → generate the PDF and
   send it to the client while still on site. Same host, same job-data
   contract, same renderer — it's a new front door, not a new system. Note:
   "send to client on site" is the tool's first direct client touch — sending
   mechanism (email from whose address, wording) needs Lachy's sign-off.
3. Ongoing small tweaks as Matt uses it.

## Google side (one-time, Lachy's accounts)
Service account with Drive + Sheets APIs enabled, added as Content Manager on
the "Endure Carpentry and Constructions" shared drive (covers rate sheet +
Incoming Jobs). Vercel body-size lesson kept: photo attachments ~4.5MB limit.

## Update loop once live
Edit (Cowork or any session) → commit → push to main → Railway auto-deploys
(~1 min). Branches = preview deploys for testing before merge.
