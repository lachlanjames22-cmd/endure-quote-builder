# Endure Proposal/Quote — Input Schema

One renderer (`render_proposal.py`), two document types, set by the top-level `"mode"` field:

| Business term | JSON `mode` | Sales-flow lane | What it is |
|---|---|---|---|
| **Proposal** | `"range"` | Black · Complex | Range pricing across 1+ priced options, no fixed number. Ends with a 3-tier design-service upsell (Working Drawings / Design Pack / Landscape Concept Design) that moves the client to a fixed price. |
| **Quote** | `"fixed"` | Red · Standard | Fixed, itemised, single price. Ends with accept + deposit. No design-service upsell. |

Both share: Cover, Approach, Finance (HandyPay), Project Run. They diverge on Cost Breakdown and Approval; Quote also adds a "Your Project" hero page.

## Shared top-level fields (every job)

`mode`, `client_name`, `cover_subtitle`, `prepared_for`, `location`, `proposal_date`, `status_label`, `eyebrow_tag`.

## `range` mode (Proposal) — `d["range"]`

- **`options`** — list of 1+ priced options. Each: `{label, image, size_label, price_low_inc_gst, price_high_inc_gst, price_low_ex_gst, price_high_ex_gst, spec_line, eyebrow?}`. 1 option renders as a single-page range card (the original Ray Frank look). 2+ options each get their own range card, stacked, and the Scope/Assumptions content moves to its own page since 3 full cards + scope won't fit one A4 page. Use `eyebrow` (e.g. `"Optional Add-on"`) to mark an option that isn't a board choice — e.g. a Patio alongside deck-material options — so it doesn't read as a 4th board.
- **`alt_materials`** — optional, 0-2 items, `{name, note, delta}`. This is for a same-option board *swap* shown as a small delta note (e.g. "Meridian instead of Vision, ~$5,515 lower") — NOT for independently-priced options. Independent options belong in `options`, not here.
- `scope_items[]` — `{label, value, strong?}` rows for the "Scope — included" table.
- `preliminary_note` — the "Preliminary — First pass" disclosure paragraph.
- `consult_fee_paid` — number, credited against tiers with `credit_consult: true`.
- `design_service_tiers[]` — `{name, description, price_ex_gst, credit_consult}` — normally 3 rows: Working Drawings ($800+GST, credit), Design Pack ($2,200+GST, no credit), Landscape Concept Design ($3,500+GST, credit).

## `fixed` mode (Quote) — `d["fixed"]`

- `your_project` — hero page: `summary_line`, `project_items[]` (`{label, image, price_inc_gst?, spec_label, description, bullets[]}`), `zones[]` (`{name, sub, body}`), `footnote`. One item renders as a single full-size card (the original Pantons look); 2+ items each get their own compact card, stacked on the page. `price_inc_gst` per item is optional — leave it out if the job is genuinely one all-up price shown only via `cost_lines`/`total_inc_gst` below. (Legacy: a flat `{image, price_inc_gst, spec_label, description, bullets}` directly on `your_project` — no `project_items` — is still accepted and treated as a single item, for backwards compatibility with older saved job data.)
- `package_label`, `cost_lines[]` (`{label, desc, amount}`), `subtotal_ex_gst`, `total_inc_gst`, `included`, `excluded`, `deposit_pct`.

## Optional extras — `optional_extras` (top level, both modes)
Optional list of upsells: `[{label, desc, price_inc_gst}]`. Renders as tickable "Optional upgrades — tick to add" cards on the approval page (both modes), priced individually and NEVER included in the project totals. The builder works each extra's cost/labour/margin ex GST internally; only the inc-GST sell price reaches job-data. The internal costing payload carries the full breakdown in its own `optional_extras` (with `gp_pct`), excluded from budget totals.

## Shared: `finance` and `project_run` blocks

- `finance`: `wk_low`, `wk_high`, `rows[]` (`{label, sub, wk3, wk5, wk7}`).
- `project_run`: `lead`, `steps[]` (`{num, title, body}`, normally 7), `payment_schedule[]` (`{pct, label}`, must sum to 100).

## Image fields — asset filename OR data URI
`range.options[].image` and `fixed.your_project.project_items[].image` accept either an asset filename (e.g. `merbau.jpg`, resolved against `assets/`) or an uploaded image as a `data:image/...;base64,` URI (the builder compresses uploads to ≤1200px JPEG). Empty string renders the cream placeholder box, unchanged.

## Fixed brand assets (never vary)

`assets/cover.jpg`, `logo.png`, `portrait.jpg`, `strip1.jpg`, `strip2.jpg`, `strip3.jpg`. The "About Endure" copy, the 20-Year Standard five points, and the Finance/HandyPay page copy are hard-coded in the renderer — never per-job.

## Known gaps

- No confirmed Quote-mode example was on hand when this was built (Pantons is labelled "First Pass Proposal" but structurally matches Quote — fixed price, no upsell). Confirm the intended status label wording for Quotes before relying on this for real client documents.
- The Proposal Inputs spreadsheet's Options/Scope/Extras tables cap at a fixed number of rows (5 spare option rows, 8 spare scope rows). That's not truly unlimited — insert more rows in Excel if a job needs more, and update the row ranges in `scripts/xlsx_to_json.py` to match.
- `render_proposal.py` depends on WeasyPrint: `pip install weasyprint --break-system-packages`.

## Optional section toggles — `d["include"]`

Optional top-level object controlling which fixed pages render. Any key omitted defaults to true.
`{"approach": bool, "finance": bool, "project_run": bool}` — e.g. `"include": {"finance": false}` drops the HandyPay page AND the "Refer me to HandyPay" tick-box from the Quote approval page. Cover, Your Project/Options, Cost Breakdown and Approval always render.

Exception: `"concept"` defaults to FALSE and also needs data — `"include": {"concept": true}` plus a top-level `"concept": {"pages": [{"layout": 1|2|4, "images": [{"image_data": "data:image/jpeg;base64,...", "caption": "..."}]}]}` renders one "This is your project." page per entry, between About Endure and the project pages (both modes). `layout` picks the page grid: 1 = single full-width image, 2 = two stacked, 4 = 2×2 grid; each image takes an optional `caption`. Images are data URIs (the builder compresses uploads to ≤1800px JPEG). The intro lead paragraph renders on the first page only. `titles.concept` overrides the headline. Legacy shape `"concept": {"image_data": ..., "caption": ...}` is still accepted as one single-image page.

## Fixed-cost separation — `cost_lines[].group`
In `fixed` mode, cost lines with `"group": "fixed"` (the builder tags per-job fixed costs and the small-job loading this way) render below a small black "FIXED COSTS" subhead in the Cost Breakdown, visually separated from the project items. Lines without the key render exactly as before.

## Editable section headlines — `d["titles"]`

Optional map overriding the big section titles per job. Keys: `approach`, `your_project`, `finance`, `approval`, `project_run`. Omitted keys keep the default mustard-accented headline. Override renders as plain text, e.g. `"titles": {"approval": "Let's build it."}`.

## Design-service tier photos

Each `design_service_tiers[]` entry may carry an `image` (asset filename). Defaults by name: Working Drawings → substr.jpg, Design Pack → render.jpg, Landscape Concept Design → finished.jpg.
