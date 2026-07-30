# -*- coding: utf-8 -*-
"""ENDURE PROPOSAL RENDERER (v2 — "Preliminary Range" / "First Pass Fixed")

Renders the current Endure Decks sales-proposal layout (as seen in the
Ray Frank preliminary-range proposal and the Daniel Pantons first-pass
fixed proposal) from a single JSON data file to a branded PDF.

Usage:
    python render_proposal.py path/to/job.json [output.pdf]

If output.pdf is omitted, writes "<ClientSlug>_Proposal.pdf" next to the JSON file.

See references/INPUT_SCHEMA.md (one level up from this script) for the full field reference.
Two modes, set by top-level "mode": "range" (Proposal) or "fixed" (Quote).
"""
import sys, os, re, json
from weasyprint import HTML

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.environ.get('ENDURE_ASSETS') or os.path.join(SKILL_ROOT, 'assets')
I = "file://" + ASSETS.replace(os.sep, "/") + "/"

def fmt_money(n, decimals=0):
    if n is None or n == '':
        return '$0'
    try:
        n = float(n)
        return f'${n:,.{decimals}f}' if decimals else f'${int(round(n)):,}'
    except (ValueError, TypeError):
        return f'${n}'

def esc(v):
    return '' if v is None else str(v)

def title_html(d, key, default_inner):
    """Per-job editable section headline. d['titles'][key] overrides the default;
    override renders plain, default keeps its mustard-accented markup."""
    t = (d.get('titles') or {}).get(key)
    return f'<div class="title">{esc(t)}</div>' if t else f'<div class="title">{default_inner}</div>'

# ---------------------------------------------------------------- CSS ----
CSS = r'''<style>@import url('https://fonts.googleapis.com/css2?family=EB+Garamond:wght@500;600;700&family=Inter:wght@400;500;600&display=swap');
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
html, body { margin:0; padding:0; font-family:'Inter',Arial,sans-serif; font-size:10.5pt; color:#1A1815; line-height:1.5; }
:root{--ink:#1A1815;--grey:#6e6e6e;--rule:#e3dccf;--mustard:#C09A45;--mustard-deep:#B7872F;
--dark:#1A1815;--darker:#0E0D0B;--cream:#F7F1E4;--white:#fff;--green:#2f8e4e;}
.page{width:210mm;height:297mm;page-break-after:always;position:relative;overflow:hidden;background:var(--white);}
.page:last-child{page-break-after:auto;}
.phead{display:flex;align-items:center;justify-content:space-between;padding:13mm 16mm 0;}
.logo-badge{width:26mm;height:16mm;background:var(--dark);display:flex;align-items:center;justify-content:center;padding:2mm 3mm;}
.logo-badge img{max-width:100%;max-height:100%;object-fit:contain;}
.phead-right{font-size:9pt;font-weight:500;letter-spacing:.18em;color:var(--mustard-deep);text-transform:uppercase;}
.pbody{padding:6mm 16mm 0;height:calc(297mm - 13mm - 16mm - 14mm);position:relative;overflow:hidden;}
.pbody::before{content:"";position:absolute;top:0;left:16mm;right:16mm;height:1px;background:var(--rule);}
.pfoot{position:absolute;bottom:9mm;left:16mm;right:16mm;display:grid;grid-template-columns:1fr 1fr 1fr;align-items:center;
font-size:8pt;letter-spacing:.18em;color:var(--mustard-deep);text-transform:uppercase;border-top:1px solid var(--rule);padding-top:2.5mm;}
.pfoot-c{text-align:center;color:var(--grey);} .pfoot-r{text-align:right;color:var(--ink);}
.eyebrow{font-size:8.5pt;font-weight:500;letter-spacing:.22em;color:var(--mustard-deep);text-transform:uppercase;margin-top:5mm;}
.title{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:30pt;line-height:1.05;margin:2mm 0 5mm;}
.mustard{color:var(--mustard-deep);}
p{margin:0 0 2.5mm 0;}
.lead{font-size:10pt;}
.page-cover{padding:0;display:flex;flex-direction:column;}
.cover-image{height:48%;overflow:hidden;position:relative;}
.cover-image img{width:100%;height:100%;object-fit:cover;display:block;}
.cover-logo{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:52mm;}
.cover-logo .lb{background:var(--darker);padding:7mm 8mm;display:flex;align-items:center;justify-content:center;}
.cover-logo img{width:100%;display:block;}
.cover-dark{flex:1;background:var(--darker);color:var(--cream);padding:14mm 16mm;display:flex;flex-direction:column;justify-content:space-between;}
.cover-eyebrow{font-size:9pt;font-weight:500;letter-spacing:.22em;color:var(--mustard);text-transform:uppercase;margin-bottom:5mm;}
.cover-title{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:42pt;line-height:1;color:var(--white);margin:0 0 4mm;}
.cover-subtitle{font-size:11pt;color:#bdb8a8;margin-bottom:8mm;}
.cover-meta{display:grid;grid-template-columns:repeat(var(--meta-cols,3),1fr);gap:8mm;padding-top:4mm;border-top:1px solid #3a3631;}
.cover-meta .lab{font-size:8pt;letter-spacing:.22em;color:var(--mustard);text-transform:uppercase;margin-bottom:1.5mm;}
.cover-meta .val{font-size:11pt;color:var(--white);}
.intro-grid{display:grid;grid-template-columns:1fr 1fr;gap:6mm;margin-top:4mm;}
.director-row{display:grid;grid-template-columns:18mm 1fr;gap:4mm;align-items:start;}
.director-portrait{width:18mm;height:18mm;border-radius:50%;overflow:hidden;background:var(--cream);
border:1.5px solid var(--mustard-deep);display:flex;align-items:center;justify-content:center;}
.director-portrait img{width:100%;height:100%;object-fit:cover;}
.intro-letter h3{font-family:'EB Garamond',Georgia,serif;font-size:13pt;font-weight:600;margin:0 0 2mm;}
.intro-letter p{font-size:9.5pt;}
.intro-signoff{font-family:'EB Garamond',Georgia,serif;font-style:italic;margin-top:3mm;font-size:10pt;}
.thread-lab{font-size:8.5pt;font-weight:500;letter-spacing:.22em;color:var(--mustard-deep);text-transform:uppercase;margin-bottom:3mm;}
.std-list{margin-top:2mm;}
.std-item{margin-bottom:3mm;}
.std-headrow{display:flex;align-items:baseline;gap:2mm;}
.std-num{font-family:'EB Garamond',Georgia,serif;color:var(--mustard-deep);font-weight:700;font-size:12pt;min-width:6mm;}
.std-head{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:11.5pt;}
.std-body{font-size:8.5pt;color:var(--grey);margin-left:8mm;margin-top:0.3mm;}
.image-strip{display:grid;grid-template-columns:1fr 1fr 1fr;gap:3mm;margin-top:6mm;}
.image-strip-cell{height:42mm;overflow:hidden;background:var(--cream);}
.image-strip-cell img{width:100%;height:100%;object-fit:cover;}
/* Range hero (preliminary pricing card) */
.range-card{border:1px solid var(--rule);border-top:3px solid var(--mustard-deep);margin-top:3mm;}
.range-card-head{text-align:center;padding:3mm 4mm 0;}
.range-eyebrow{font-size:8pt;letter-spacing:.2em;color:var(--mustard-deep);text-transform:uppercase;font-weight:500;}
.range-title{font-family:'EB Garamond',Georgia,serif;font-size:16pt;font-weight:600;margin:1.5mm 0 3mm;}
.range-body{display:grid;grid-template-columns:34mm 1fr;gap:5mm;padding:0 6mm 5mm;align-items:center;}
.range-card.compact{margin-top:2.5mm;}
.range-card.compact .range-card-head{padding:2.5mm 4mm 0;}
.range-card.compact .range-title{font-size:13pt;margin:1mm 0 2mm;}
.range-card.compact .range-body{padding:0 5mm 3.5mm;gap:4mm;}
.range-card.compact .range-img{height:18mm;}
.range-card.compact .range-price{font-size:15pt;}
.range-card.compact .range-spec{margin-top:1mm;}
.range-img{height:26mm;background:var(--cream);overflow:hidden;}
.range-img img{width:100%;height:100%;object-fit:cover;}
.range-price{font-family:'EB Garamond',Georgia,serif;font-size:20pt;font-weight:600;}
.range-price-sub{font-size:8pt;color:var(--grey);letter-spacing:.1em;text-transform:uppercase;margin-top:1mm;}
.range-price-sub2{font-size:8.5pt;color:var(--grey);}
.range-spec{font-size:8.5pt;color:var(--grey);margin-top:2mm;}
.alt-callout{background:var(--cream);border-left:3px solid var(--mustard-deep);padding:3.5mm 6mm;margin-top:3mm;font-size:9.5pt;}
.alt-callout .h{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:11.5pt;color:var(--ink);margin-bottom:1.5mm;}
.scope-h{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:12.5pt;margin-top:4.5mm;}
.scope-table{border:1px solid var(--rule);margin-top:2mm;}
.scope-header{display:grid;grid-template-columns:1fr 40mm;background:var(--cream);font-size:8pt;letter-spacing:.12em;
text-transform:uppercase;color:var(--grey);padding:2.2mm 5mm;font-weight:500;}
.scope-header>div:last-child{text-align:right;}
.scope-row{display:grid;grid-template-columns:1fr 40mm;padding:2.2mm 5mm;border-top:1px solid var(--rule);font-size:9.5pt;}
.scope-row.strong{font-weight:600;}
.scope-row>div:last-child{text-align:right;color:var(--grey);}
.scope-row.strong>div:last-child{color:var(--ink);}
.prelim-note{background:var(--cream);border-left:3px solid var(--mustard-deep);padding:3.5mm 6mm;margin-top:4mm;font-size:9pt;}
.prelim-note .h{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:11.5pt;margin-bottom:1.5mm;}
/* Your Project (fixed-mode hero) */
.yp-card{display:grid;grid-template-columns:64mm 1fr;gap:4mm;margin-top:4mm;border:1px solid var(--rule);}
.yp-img{background:var(--cream);min-height:64mm;overflow:hidden;}
.yp-img img{width:100%;height:100%;object-fit:cover;}
.yp-body{padding:5mm 6mm;}
.yp-price{font-family:'EB Garamond',Georgia,serif;font-size:22pt;font-weight:600;}
.yp-price .lab{font-size:8.5pt;font-family:'Inter',Arial,sans-serif;color:var(--grey);letter-spacing:.12em;margin-left:2mm;font-weight:500;}
.yp-spec{font-size:9pt;color:var(--grey);font-style:italic;margin:1mm 0 3mm;}
.yp-desc{font-size:9.5pt;margin-bottom:2.5mm;}
.yp-bul{margin:0;padding-left:5mm;font-size:9pt;}
.yp-bul li{margin-bottom:1mm;}
.yp-zones{display:grid;grid-template-columns:1fr 1fr;gap:4mm;margin-top:4mm;}
.yp-zone{border:1px solid var(--rule);padding:4mm 5mm;}
.yp-zone .nm{font-family:'EB Garamond',Georgia,serif;font-size:13pt;font-weight:600;}
.yp-zone .sub{font-size:8.5pt;color:var(--grey);font-style:italic;margin:0.5mm 0 2mm;}
.yp-note{font-size:8.5pt;color:var(--grey);font-style:italic;margin-top:3mm;}
.yp-card.compact{grid-template-columns:44mm 1fr;margin-top:3mm;}
.yp-card.compact .yp-img{min-height:38mm;}
.yp-card.compact .yp-body{padding:3.5mm 5mm;}
.yp-card.compact .yp-price{font-size:16pt;}
.yp-card.compact .yp-desc{font-size:8.5pt;margin-bottom:1.5mm;}
.yp-card.compact .yp-bul{font-size:8pt;}
.yp-item-label{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:12.5pt;margin-bottom:0;}

/* Cost breakdown — fixed mode */
.cb2-block{border:1px solid var(--rule);margin-top:3mm;}
.cb2-head{display:grid;grid-template-columns:1fr 30mm;background:var(--dark);color:var(--white);padding:3mm 6mm;font-size:10pt;}
.cb2-head>div:last-child{text-align:right;font-size:8pt;letter-spacing:.15em;}
.cb2-subhead{padding:3.5mm 6mm 1mm;font-size:8pt;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--ink);border-top:1.5px solid var(--ink);margin-top:1.5mm;}
.cb2-line{display:grid;grid-template-columns:1fr 30mm;padding:3mm 6mm;border-bottom:1px solid var(--rule);font-size:10pt;}
.concept-grid{margin-top:4mm;display:grid;gap:4mm;}
.concept-grid.l1{grid-template-columns:1fr;}
.concept-grid.l2{grid-template-columns:1fr;}
.concept-grid.l4{grid-template-columns:1fr 1fr;}
.concept-cell{border:1px solid var(--rule);background:var(--cream);padding:3mm;text-align:center;}
.concept-grid.l1 .concept-cell img{max-width:100%;max-height:168mm;object-fit:contain;}
.concept-grid.l2 .concept-cell img{max-width:100%;max-height:78mm;object-fit:contain;}
.concept-grid.l4 .concept-cell img{max-width:100%;max-height:74mm;object-fit:contain;}
.concept-grid.tall.l1 .concept-cell img{max-height:180mm;}
.concept-grid.tall.l2 .concept-cell img{max-height:84mm;}
.concept-grid.tall.l4 .concept-cell img{max-height:80mm;}
.concept-cap{font-size:9pt;color:var(--grey);margin-top:2mm;font-style:italic;}
.cb2-line .desc{color:var(--grey);font-size:8.5pt;margin-top:0.5mm;}
.cb2-line .amount{text-align:right;}
.cb2-total{display:grid;grid-template-columns:1fr 50mm;background:var(--dark);color:var(--white);padding:4mm 6mm;align-items:center;}
.cb2-total .name{font-family:'EB Garamond',Georgia,serif;font-size:15pt;font-weight:600;}
.cb2-total .ex{text-align:right;font-size:8pt;color:#bdb8a8;}
.cb2-total .inc{text-align:right;font-family:'EB Garamond',Georgia,serif;font-size:15pt;font-weight:600;}
.incl-box{background:var(--cream);border-left:3px solid var(--mustard-deep);padding:4mm 6mm;margin-top:4mm;font-size:9pt;}
.incl-box .h{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:11.5pt;margin-bottom:1.5mm;}
/* Cost breakdown — range mode (reuses old cb-table look, simplified) */
.cb-table{margin-top:3mm;border:1px solid var(--rule);}
.cb-header{display:grid;grid-template-columns:1fr 50mm;background:var(--dark);color:var(--white);padding:3mm 6mm;font-size:9pt;letter-spacing:.18em;}
.cb-header>div:last-child{text-align:right;}
.cb-line{display:grid;grid-template-columns:1fr 50mm;padding:2.0mm 6mm;border-bottom:1px solid var(--rule);font-size:10pt;}
.cb-line .amount{text-align:right;}
.cb-line .desc{color:var(--grey);font-size:8.5pt;margin-top:.5mm;}
/* Finance page (shared) */
.fin-hero{background:var(--darker);color:var(--cream);border-radius:1mm;padding:6mm 8mm;margin-top:4mm;
display:grid;grid-template-columns:1fr auto;align-items:center;gap:6mm;}
.fin-hero-h{font-family:'EB Garamond',Georgia,serif;font-size:20pt;font-weight:600;color:var(--white);}
.fin-hero-sub{font-size:9.5pt;color:#bdb8a8;margin-top:1mm;}
.fin-hero-right{text-align:right;}
.fin-hero-eyebrow{font-size:8pt;letter-spacing:.18em;color:var(--mustard);text-transform:uppercase;margin-bottom:1.5mm;}
.fin-hero-range{font-family:'EB Garamond',Georgia,serif;font-size:26pt;font-weight:600;color:var(--white);}
.fin-hero-range span{font-size:11pt;font-family:'Inter',Arial,sans-serif;color:#bdb8a8;}
.fin-hero-note{font-size:7.5pt;letter-spacing:.1em;color:var(--mustard);text-transform:uppercase;margin-top:1mm;}
.fin-table{margin-top:5mm;border:1px solid var(--rule);}
.fin-table-head{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;background:var(--cream);font-size:9pt;font-weight:500;padding:3mm 6mm;}
.fin-table-row{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;padding:4mm 6mm;border-top:1px solid var(--rule);align-items:center;font-size:9pt;}
.fin-table-row .bl{font-weight:600;} .fin-table-row .bl .sub{display:block;font-weight:400;color:var(--grey);font-size:8.5pt;margin-top:0.5mm;}
.fin-table-row .wk{text-align:center;}
.fin-next{background:var(--cream);border-left:3px solid var(--mustard-deep);padding:3.5mm 6mm;margin-top:4mm;font-size:9.5pt;}
.fin-next .h{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:12pt;margin-bottom:1.5mm;}
.fin-disclaimer{font-size:7.5pt;color:var(--grey);font-style:italic;margin-top:4mm;}
/* Approval */
.appr-cards{display:grid;grid-template-columns:1fr 1fr;gap:4mm;margin-top:4mm;}
.appr-card{border:1px solid var(--rule);padding:3.5mm 5mm;display:flex;gap:2.5mm;}
.appr-card.sel{background:var(--cream);border-color:var(--mustard-deep);}
.appr-card .cbx{font-size:11pt;color:var(--mustard-deep);line-height:1.3;}
.appr-card .nm{font-weight:600;font-size:9.5pt;} .appr-card .pr{font-size:8.5pt;color:var(--grey);margin-top:0.5mm;}
.fee-box{background:var(--cream);border-left:3px solid var(--mustard-deep);padding:4mm 6mm;margin-top:4mm;}
.fee-box .h{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:12.5pt;margin-bottom:2mm;}
.fee-row{display:flex;justify-content:space-between;font-size:9.5pt;padding:1mm 0;}
.fee-row.total{border-top:1px solid var(--mustard-deep);margin-top:1.5mm;padding-top:2mm;font-weight:700;font-size:11pt;}
.fee-row .neg{color:var(--green);}
.fee-note{font-size:8pt;color:var(--grey);font-style:italic;margin-top:1.5mm;}
.ds-h{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:12.5pt;margin-top:3.5mm;}
.ds-sub{font-size:9pt;color:var(--grey);margin:1mm 0 2mm;}
.ds-cards{display:grid;grid-template-columns:1fr 1fr 1fr;gap:3mm;}
.ds-card{border:1px solid var(--rule);display:flex;flex-direction:column;overflow:hidden;}
.ds-card .ds-img{height:21mm;background:var(--cream);overflow:hidden;}
.ds-card .ds-img img{width:100%;height:100%;object-fit:cover;display:block;}
.ds-card .ds-inner{padding:3mm 3.5mm;display:flex;flex-direction:column;flex:1;}
.ds-card .cbx{font-size:11pt;color:var(--mustard-deep);line-height:1;margin-bottom:1mm;}
.ds-card .nm{font-weight:600;font-size:9.5pt;}
.ds-card .desc{font-size:8pt;color:var(--grey);margin-top:0.5mm;line-height:1.3;min-height:10mm;}
.ds-card .pr{font-size:9.5pt;font-weight:600;margin-top:auto;padding-top:2mm;white-space:nowrap;}
.ds-card .pr .credit{display:block;font-weight:400;font-size:7pt;color:var(--green);margin-top:0.5mm;}
.accept-block{display:flex;align-items:flex-start;background:var(--darker);color:var(--cream);padding:3.5mm 6mm;margin-top:4mm;border-left:4px solid var(--mustard);}
.accept-block .paper-tick{font-size:13pt;color:var(--mustard);margin-right:3mm;}
.accept-block .accept-text{font-size:10pt;font-weight:500;line-height:1.4;color:var(--white);}
.sign-grid{display:grid;grid-template-columns:1fr 8mm 1fr;margin-top:5mm;}
.sign-label{font-weight:600;font-size:9pt;}
.sign-line{border-bottom:1.5px solid var(--ink);height:9mm;margin-top:1.5mm;}
/* Project run */
.proc-row{display:grid;grid-template-columns:26mm 1fr;gap:4mm;margin-bottom:3mm;}
.proc-num{background:var(--dark);color:var(--mustard);padding:4mm 3mm;text-align:center;}
.proc-num .n{font-family:'EB Garamond',Georgia,serif;font-size:18pt;font-weight:600;line-height:1;}
.proc-content .head{font-family:'EB Garamond',Georgia,serif;font-size:12pt;font-weight:600;}
.proc-content .body{font-size:9.5pt;margin-top:1mm;}
.pay-sched{background:var(--cream);padding:4mm 6mm;margin-top:4mm;display:grid;grid-template-columns:repeat(var(--sched-cols,5),1fr);gap:3mm;}
.pay-sched .pct{font-family:'EB Garamond',Georgia,serif;font-size:16pt;font-weight:600;color:var(--mustard-deep);}
.pay-sched .lab{font-size:8pt;color:var(--ink);margin-top:0.5mm;}
.pay-sched-h{font-family:'EB Garamond',Georgia,serif;font-size:13pt;font-weight:600;margin-top:5mm;}
.stepper{display:flex;align-items:flex-start;margin:5mm 0 6mm;}
.stepper .step{display:flex;flex-direction:column;align-items:center;flex:0 0 auto;width:20mm;}
.stepper .dot{width:4.5mm;height:4.5mm;border-radius:50%;background:var(--mustard-deep);border:1.5px solid var(--mustard-deep);}
.stepper .dot.upcoming{background:var(--white);border:1.5px solid var(--rule);}
.stepper .label{font-size:7.5pt;margin-top:2mm;color:var(--ink);text-transform:uppercase;letter-spacing:.06em;text-align:center;font-weight:600;}
.stepper .label.upcoming{color:var(--grey);font-weight:400;}
.stepper .line{flex:1;height:2px;margin-top:2.25mm;display:flex;align-self:flex-start;}
.stepper .line .fill{background:var(--mustard-deep);height:100%;}
.stepper .line .empty{background:var(--rule);height:100%;}
.stepper .line{position:relative;}
.stepper .youhere{position:absolute;top:-6.5mm;left:80%;transform:translateX(-50%);white-space:nowrap;
font-size:6.5pt;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--mustard-deep);}
.stepper .youhere::after{content:"";position:absolute;left:50%;top:3.4mm;transform:translateX(-50%);
width:0;height:0;border-left:1.3mm solid transparent;border-right:1.3mm solid transparent;border-top:1.6mm solid var(--mustard-deep);}
</style>'''


def progress_track_html():
    """Fixed 'how far through the process' stepper shown on both Approval
    pages. Steps only, no dollar figures or percentages — the ~80%-filled
    final connector line is the only signal of how close they are."""
    def dot(label, done):
        cls = '' if done else ' upcoming'
        return f'''<div class="step"><div class="dot{cls}"></div><div class="label{cls}">{label}</div></div>'''
    def line(pct_filled, here=False):
        marker = '<div class="youhere">You are here</div>' if here else ''
        return (f'<div class="line">{marker}<div class="fill" style="width:{pct_filled}%"></div>'
                f'<div class="empty" style="width:{100-pct_filled}%"></div></div>')
    return (f'<div class="stepper">'
            f'{dot("Enquiry", True)}{line(100)}'
            f'{dot("Site Visit", True)}{line(100)}'
            f'{dot("Planning", True)}{line(80, here=True)}'
            f'{dot("Deposit &amp; Build", False)}'
            f'</div>')

def head(label):
    return f'<div class="phead"><div class="logo-badge"><img src="{I}logo.png"></div><div class="phead-right">{label}</div></div>'

def foot(n):
    return f'<div class="pfoot"><div>Endure Decks</div><div class="pfoot-c">Built for the long horizon</div><div class="pfoot-r">{n:02d}</div></div>'

# ------------------------------------------------------------ SHARED PAGES ----
def page_cover(d, n):
    return f'''<div class="page page-cover">
  <div class="cover-image"><img src="{I}cover.jpg">
    <div class="cover-logo"><div class="lb"><img src="{I}logo.png"></div></div></div>
  <div class="cover-dark">
    <div>
      <div class="cover-eyebrow">{d["eyebrow_tag"]}</div>
      <div class="cover-title">{d["client_name"]}</div>
      <div class="cover-subtitle">{d["cover_subtitle"]}</div>
    </div>
    <div class="cover-meta" style="--meta-cols:4">
      <div><div class="lab">Prepared for</div><div class="val">{d["prepared_for"]}</div></div>
      <div><div class="lab">Location</div><div class="val">{d["location"]}</div></div>
      <div><div class="lab">Date</div><div class="val">{d["proposal_date"]}</div></div>
      <div><div class="lab">Status</div><div class="val">{d["status_label"]}</div></div>
    </div>
  </div>
</div>'''

STANDARDS = [
    ('01', 'Water Management', 'No horizontal surfaces. Joist protection. Clear drainage.'),
    ('02', 'Durability Class &amp; Material Match', 'Correct treatment for actual exposure conditions.'),
    ('03', 'Connections &amp; Corrosion Control', 'Rated fasteners. No mixed metals. No missing fixings.'),
    ('04', 'Ventilation &amp; Drying Capacity', 'Adequate clearance. No sealed voids. Drainage maintained.'),
    ('05', 'Load Path &amp; Structural Continuity', 'Clean transfer from deck surface to footings.'),
]

def page_approach(d, n, status_label):
    std_html = ''.join(
        f'<div class="std-item"><div class="std-headrow"><span class="std-num">{num}</span>'
        f'<span class="std-head">{h}</span></div><div class="std-body">{b}</div></div>'
        for num, h, b in STANDARDS)
    return f'''<div class="page">{head(f"{status_label} &middot; {n:02d}")}<div class="pbody">
  <div class="eyebrow">About Endure</div>
  {title_html(d, "approach", 'Our <span class="mustard">approach.</span>')}
  <div class="intro-grid">
    <div>
      <div class="thread-lab">From the Director</div>
      <div class="director-row">
        <div class="director-portrait"><img src="{I}portrait.jpg"></div>
        <div class="intro-letter">
          <h3>A quick note.</h3>
          <p>I built Endure Decks to be the decking company I would want to hire for my own
          project. Our business is built on doing trades work properly or not at all, and every
          deck we lay carries our 20-Year Standard. I trust you&rsquo;ll find your experience
          with us more than you expected. Throughout the project you&rsquo;ll be able to contact
          your Project Consultant and Project Manager for any queries. Thank you for
          considering us.</p>
          <div class="intro-signoff">&mdash; Lachlan | Director</div>
        </div>
      </div>
    </div>
    <div>
      <div class="thread-lab">The 20 Year Standard</div>
      <p style="font-size:9.5pt;margin-bottom:2mm;">An internal build philosophy applied on every Endure build. Five points, every job, no exceptions.</p>
      <div class="std-list">{std_html}</div>
    </div>
  </div>
  <div class="image-strip">
    <div class="image-strip-cell"><img src="{I}strip1.jpg"></div>
    <div class="image-strip-cell"><img src="{I}strip2.jpg"></div>
    <div class="image-strip-cell"><img src="{I}strip3.jpg"></div>
  </div>
{foot(n)}</div></div>'''

def page_finance(d, n, status_label):
    f = d['finance']
    rows = ''
    for r in f['rows']:
        rows += (f'<div class="fin-table-row"><div class="bl">{r["label"]}<span class="sub">{r["sub"]}</span></div>'
                  f'<div class="wk">~${r["wk3"]}/wk</div><div class="wk">~${r["wk5"]}/wk</div><div class="wk">~${r["wk7"]}/wk</div></div>')
    return f'''<div class="page">{head(f"Finance &middot; {n:02d}")}<div class="pbody">
  <div class="eyebrow">Flexible Payment</div>
  {title_html(d, "finance", 'Pay it <span class="mustard">your way.</span>')}
  <p class="lead">Not paying upfront? We work with HandyPay. We make the intro and they quote you direct.</p>
  <div class="fin-hero">
    <div>
      <div class="fin-hero-eyebrow">Finance via HandyPay</div>
      <div class="fin-hero-h">Build now. Pay weekly.</div>
      <div class="fin-hero-sub">Spread the cost over a term that suits you.</div>
    </div>
    <div class="fin-hero-right">
      <div class="fin-hero-eyebrow">Indicative Range</div>
      <div class="fin-hero-range">${f["wk_low"]}&ndash;${f["wk_high"]}<span>/wk</span></div>
      <div class="fin-hero-note">Depending on term</div>
    </div>
  </div>
  <div style="margin-top:5mm;font-family:'EB Garamond',Georgia,serif;font-size:13pt;font-weight:600;">How the ranges look</div>
  <p style="font-size:9pt;color:var(--grey);margin:1mm 0 0;">Rough weekly figures across different terms. Indicative only.</p>
  <div class="fin-table">
    <div class="fin-table-head"><div>Build</div><div style="text-align:center">3-year term</div><div style="text-align:center">5-year term</div><div style="text-align:center">7-year term</div></div>
    {rows}
  </div>
  <div class="fin-next"><div class="h">Next step on finance.</div>
  <p style="margin:0;font-size:9.5pt;">Tick the finance box on the Approval page and we refer you to HandyPay. They handle the rest. No commitment until you&rsquo;ve seen their numbers.</p></div>
  <div class="fin-disclaimer">Weekly figures above are indicative. Actual rate, term and repayments are set by HandyPay subject to credit assessment. Terms, conditions, fees and charges apply.</div>
{foot(n)}</div></div>'''

# ------------------------------------------------------------ RANGE MODE ----
def _option_card_html(opt, compact):
    cls = ' compact' if compact else ''
    return f'''<div class="range-card{cls}">
    <div class="range-card-head">
      <div class="range-eyebrow">{opt.get("eyebrow", "Preliminary Range")}</div>
      <div class="range-title">{opt["label"]}</div>
    </div>
    <div class="range-body">
      <div class="range-img"><img src="{I}{opt["image"]}"></div>
      <div>
        <div class="range-price">{fmt_money(opt["price_low_inc_gst"])} &ndash; {fmt_money(opt["price_high_inc_gst"])}</div>
        <div class="range-price-sub">Inc GST &middot; {opt.get("size_label", "")}</div>
        <div class="range-price-sub2">{fmt_money(opt["price_low_ex_gst"])} &ndash; {fmt_money(opt["price_high_ex_gst"])} ex GST</div>
        <div class="range-spec">{opt["spec_line"]}</div>
      </div>
    </div>
  </div>'''

def pages_cost_range(d, n, status_label):
    """Returns a LIST of page HTML strings — one page if there's a single
    option, two pages (option cards, then scope/note) if there are several,
    since 3+ full-size range cards plus the scope table won't fit one A4 page."""
    r = d['range']
    options = r['options']
    multi = len(options) > 1
    option_cards = ''.join(_option_card_html(opt, compact=multi) for opt in options)
    alt_html = ''.join(f'<div class="alt-callout"><div class="h">Alternative material &middot; {a["name"]}</div>'
                        f'<div>{a["note"]} <strong>{a["delta"]}</strong></div></div>' for a in r.get('alt_materials', []))
    scope_rows = ''.join(
        f'<div class="scope-row{" strong" if it.get("strong") else ""}"><div>{it["label"]}</div><div>{it["value"]}</div></div>'
        for it in r['scope_items'])
    scope_block = f'''<div class="scope-h">Scope &mdash; included</div>
  <div class="scope-table">
    <div class="scope-header"><div>Item</div><div>Approx (inc GST)</div></div>
    {scope_rows}
  </div>
  <div class="prelim-note"><div class="h">Preliminary &mdash; First pass</div>
  <p style="margin:0;">{r["preliminary_note"]}</p></div>'''

    if not multi:
        return [f'''<div class="page">{head(f"Cost Breakdown &middot; {n:02d}")}<div class="pbody">
  <div class="eyebrow">Pricing Breakdown</div>
  <div class="title">Cost <span class="mustard">breakdown.</span></div>
  <p class="lead">Your full project, line by line. All amounts inc GST.</p>
  {option_cards}
  {alt_html}
  {scope_block}
{foot(n)}</div></div>''']

    page1 = f'''<div class="page">{head(f"Cost Breakdown &middot; {n:02d}")}<div class="pbody">
  <div class="eyebrow">Pricing Breakdown</div>
  <div class="title">Your <span class="mustard">options.</span></div>
  <p class="lead">Same site, priced a few ways. All ranges inc/ex GST as shown &mdash; pick a starting point, refine at approval.</p>
  {option_cards}
  {alt_html}
{foot(n)}</div></div>'''
    page2 = f'''<div class="page">{head(f"Cost Breakdown &middot; {n+1:02d}")}<div class="pbody">
  <div class="eyebrow">Pricing Breakdown</div>
  <div class="title">Scope <span class="mustard">&amp; assumptions.</span></div>
  <p class="lead">What&rsquo;s covered across the options above.</p>
  {scope_block}
{foot(n+1)}</div></div>'''
    return [page1, page2]

def page_approval_range(d, n, status_label):
    r = d['range']
    consult_fee = r.get('consult_fee_paid', 0)
    tier_default_img = {'working drawings':'substr.jpg','design pack':'render.jpg','landscape concept design':'finished.jpg'}
    tier_cards = ''
    for t in r['design_service_tiers']:
        price_ex = fmt_money(t['price_ex_gst'], 0)
        credit_html = ''
        if t.get('credit_consult'):
            net = t['price_ex_gst'] - consult_fee
            credit_html = f'<span class="credit">less {fmt_money(consult_fee)} consult fee &rarr; {fmt_money(net)}+GST</span>'
        img = t.get('image') or tier_default_img.get(str(t.get('name','')).strip().lower(), 'render.jpg')
        tier_cards += (f'<div class="ds-card"><div class="ds-img"><img src="{I}{img}"></div>'
                        f'<div class="ds-inner"><div class="cbx">&#9744;</div>'
                        f'<div class="nm">{t["name"]}</div><div class="desc">{t["description"]}</div>'
                        f'<div class="pr">{price_ex}+GST{credit_html}</div></div></div>')
    option_check_cards = ''.join(
        f'<div class="appr-card"><div class="cbx">&#9744;</div><div><div class="nm">{opt["label"]} &middot; {opt.get("size_label","")}</div>'
        f'<div class="pr">{fmt_money(opt["price_low_inc_gst"])} &ndash; {fmt_money(opt["price_high_inc_gst"])} inc GST</div></div></div>'
        for opt in r['options'])
    alt_check_cards = ''.join(
        f'<div class="appr-card"><div class="cbx">&#9744;</div><div><div class="nm">Substitute with {a["name"]}</div>'
        f'<div class="pr">{a["delta"]}</div></div></div>'
        for a in r.get('alt_materials', []))
    return f'''<div class="page">{head(f"Approval &middot; {n:02d}")}<div class="pbody">
  <div class="eyebrow">Approval</div>
  {title_html(d, "approval", 'Next <span class="mustard">step.</span>')}
  <p class="lead">Sign below to authorise Endure to proceed. Tick the option(s) you want, and select the design service that takes your project from this range to a fixed, locked-scope price.</p>
  {progress_track_html()}
  <div style="font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:12.5pt;margin-top:3mm;">Your project</div>
  <div class="appr-cards">
    {option_check_cards}
    {alt_check_cards}
  </div>
  <div class="ds-h">Next step &middot; design service</div>
  <div class="ds-sub">Choose one. This purchase is separate from the build and moves you to a fixed price.</div>
  <div class="ds-cards">{tier_cards}</div>
  <div class="accept-block"><div class="paper-tick">&#9744;</div><div class="accept-text">I accept the preliminary range above and instruct Endure to proceed with the design service selected.</div></div>
  <div class="sign-grid">
    <div><div class="sign-label">Client Name(s)</div><div class="sign-line"></div></div>
    <div></div>
    <div><div class="sign-label">Signature</div><div class="sign-line"></div></div>
  </div>
  <div class="sign-grid" style="margin-top:5mm">
    <div><div class="sign-label">Date</div><div class="sign-line"></div></div>
    <div></div>
    <div><div class="sign-label">Range Accepted (inc GST)</div><div class="sign-line"></div></div>
  </div>
{foot(n)}</div></div>'''

# ------------------------------------------------------------ FIXED MODE ----
def _your_project_items(fp):
    """Normalises fixed.your_project to a list of items. Supports the current
    project_items[] shape, and falls back to treating the legacy single-object
    fields (image/price_inc_gst/spec_label/description/bullets) as one item,
    so older saved job data still renders."""
    items = fp.get('project_items')
    if items:
        return items
    if fp.get('image') or fp.get('price_inc_gst') or fp.get('description'):
        return [{
            'label': 'Endure Install', 'image': fp.get('image', ''),
            'price_inc_gst': fp.get('price_inc_gst'), 'spec_label': fp.get('spec_label', ''),
            'description': fp.get('description', ''), 'bullets': fp.get('bullets', []),
        }]
    return []

def page_your_project(d, n, status_label):
    fp = d['fixed']['your_project']
    items = _your_project_items(fp)
    compact = ' compact' if len(items) > 1 else ''
    cards = []
    for it in items:
        bul = ''.join(f'<li>{b}</li>' for b in it.get('bullets', []))
        label_row = f'<div class="yp-item-label">{it["label"]}</div>' if it.get('label') and len(items) > 1 else ''
        price_html = (f'<div class="yp-price">{fmt_money(it["price_inc_gst"], 2)} <span class="lab">INC GST</span></div>'
                      if it.get('price_inc_gst') not in (None, '') else '')
        cards.append(f'''<div class="yp-card{compact}">
    <div class="yp-img"><img src="{I}{it.get("image","")}"></div>
    <div class="yp-body">
      {label_row}
      {price_html}
      <div class="yp-spec">{it.get("spec_label","")}</div>
      <p class="yp-desc">{it.get("description","")}</p>
      <ul class="yp-bul">{bul}</ul>
    </div>
  </div>''')
    cards_html = ''.join(cards)
    zones = ''.join(
        f'<div class="yp-zone"><div class="nm">{z["name"]}</div><div class="sub">{z.get("sub","")}</div><div>{z["body"]}</div></div>'
        for z in fp.get('zones', []))
    return f'''<div class="page">{head(f"Your Project &middot; {n:02d}")}<div class="pbody">
  <div class="eyebrow">Your Project</div>
  {title_html(d, "your_project", 'Your <span class="mustard">deck.</span>')}
  <p class="lead">{fp["summary_line"]}</p>
  {cards_html}
  <div class="yp-zones">{zones}</div>
  <div class="yp-note">Built to the Endure 20-Year Standard. {fp.get("footnote","")}</div>
{foot(n)}</div></div>'''
def concept_pages_data(d):
    """Normalised list of concept pages: new-style concept.pages (each with a
    layout of 1/2/4 and its images), or a legacy single image_data as one page."""
    cp = d.get('concept') or {}
    if cp.get('pages'):
        return [p for p in cp['pages'] if p.get('images')]
    if cp.get('image_data'):
        return [{'layout': 1, 'images': [{'image_data': cp['image_data'], 'caption': cp.get('caption', '')}]}]
    return []


def page_concept(d, n, page, first):
    """Optional concept-plan page: preset layout (single / 2-grid / 4-grid),
    per-job uploaded images. One document page per entry in concept.pages."""
    layout = int(page.get('layout') or 1)
    cells = ''.join(
        f'<div class="concept-cell"><img src="{img.get("image_data", "")}">'
        + (f'<div class="concept-cap">{img["caption"]}</div>' if img.get('caption') else '')
        + '</div>'
        for img in page.get('images') or [])
    lead = ('<p class="lead">The concept drawn for your space &mdash; the design the numbers in this '
            'document are built around.</p>') if first else ''
    return f'''<div class="page">{head(f"Concept &middot; {n:02d}")}<div class="pbody">
  <div class="eyebrow">Your Concept</div>
  {title_html(d, "concept", 'This is your <span class="mustard">project.</span>')}
  {lead}
  <div class="concept-grid l{layout}{"" if first else " tall"}">{cells}</div>
{foot(n)}</div></div>'''


def page_cost_fixed(d, n, status_label):
    fx = d['fixed']
    lines, saw_fixed = '', False
    for l in fx['cost_lines']:
        # black "Fixed costs" subhead separates per-job fixed lines from the project items
        if l.get('group') == 'fixed' and not saw_fixed:
            saw_fixed = True
            lines += '<div class="cb2-subhead">Fixed costs</div>'
        lines += (f'<div class="cb2-line"><div>{l["label"]}<div class="desc">{l["desc"]}</div></div>'
                  f'<div class="amount">{fmt_money(l["amount"], 2)}</div></div>')
    return f'''<div class="page">{head(f"Cost Breakdown &middot; {n:02d}")}<div class="pbody">
  <div class="eyebrow">Pricing Breakdown</div>
  <div class="title">Cost <span class="mustard">breakdown.</span></div>
  <p class="lead">Your full project, line by line. All amounts inc GST.</p>
  <div class="cb2-block">
    <div class="cb2-head"><div>{fx["package_label"]}</div><div>INC GST</div></div>
    {lines}
    <div class="cb2-total"><div class="name">Total Project Price</div>
      <div class="ex">{fmt_money(fx["subtotal_ex_gst"], 2)} ex GST</div>
      <div class="inc">{fmt_money(fx["total_inc_gst"], 2)} inc GST</div>
    </div>
  </div>
  <div class="incl-box"><div class="h">What&rsquo;s included &middot; what&rsquo;s not</div>
  <p style="margin:0 0 1.5mm;"><strong>Included:</strong> {fx["included"]}</p>
  <p style="margin:0;"><strong>Not included:</strong> {fx["excluded"]}</p></div>
{foot(n)}</div></div>'''

def page_approval_fixed(d, n, status_label):
    fx = d['fixed']
    include_finance = d.get('include', {}).get('finance', True) is not False
    handypay_card = ('<div class="appr-card"><div class="cbx">&#9744;</div><div><div class="nm">Refer me to HandyPay for finance</div>'
                     '<div class="pr">we make the intro</div></div></div>') if include_finance else ''
    return f'''<div class="page">{head(f"Approval &middot; {n:02d}")}<div class="pbody">
  <div class="eyebrow">Approval</div>
  {title_html(d, "approval", 'Next <span class="mustard">step.</span>')}
  <p class="lead">Ready to start? Sign below. A {fx["deposit_pct"]}% deposit confirms your project and locks the build window.</p>
  {progress_track_html()}
  <div style="font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:12.5pt;margin-top:3mm;">Your project</div>
  <div class="appr-cards">
    <div class="appr-card sel"><div class="cbx">&#9744;</div><div><div class="nm">{fx["package_label"]}</div>
    <div class="pr">{fmt_money(fx["total_inc_gst"], 2)} inc GST</div></div></div>
    {handypay_card}
  </div>
  <div class="accept-block"><div class="paper-tick">&#9744;</div><div class="accept-text">I accept the project above and instruct Endure to proceed on payment of the {fx["deposit_pct"]}% deposit.</div></div>
  <div class="sign-grid">
    <div><div class="sign-label">Client Name</div><div class="sign-line"></div></div>
    <div></div>
    <div><div class="sign-label">Signature</div><div class="sign-line"></div></div>
  </div>
  <div class="sign-grid" style="margin-top:5mm">
    <div><div class="sign-label">Date</div><div class="sign-line"></div></div>
    <div></div>
    <div><div class="sign-label">Accepted Amount (inc GST)</div><div class="sign-line"></div></div>
  </div>
{foot(n)}</div></div>'''

# ------------------------------------------------------------ PROJECT RUN ----
def page_project_run(d, n, status_label):
    steps = d['project_run']['steps']
    sched = d['project_run']['payment_schedule']  # list of {pct, label}
    steps_html = ''.join(
        f'<div class="proc-row"><div class="proc-num"><div class="n">{s["num"]}</div></div>'
        f'<div class="proc-content"><div class="head">{s["title"]}</div><div class="body">{s["body"]}</div></div></div>'
        for s in steps)
    sched_html = ''.join(f'<div><div class="pct">{p["pct"]}%</div><div class="lab">{p["label"]}</div></div>' for p in sched)
    return f'''<div class="page">{head(f"Project Run &middot; {n:02d}")}<div class="pbody">
  <div class="eyebrow">Project Run</div>
  {title_html(d, "project_run", 'How your project <span class="mustard">runs.</span>')}
  <p class="lead">{d["project_run"]["lead"]}</p>
  {steps_html}
  <div class="pay-sched-h">Payment schedule at a glance</div>
  <div class="pay-sched" style="--sched-cols:{len(sched)}">{sched_html}</div>
{foot(n)}</div></div>'''

# ------------------------------------------------------------ RENDER ----
def render(d, out_pdf):
    mode = d['mode']
    status_label = d['status_label']
    inc = d.get('include', {})
    def on(key):
        return inc.get(key, True) is not False
    n = 1
    pages = [page_cover(d, n)]; n += 1
    if on('approach'):
        pages.append(page_approach(d, n, status_label)); n += 1
    if inc.get('concept'):
        for i, cp in enumerate(concept_pages_data(d)):
            pages.append(page_concept(d, n, cp, i == 0)); n += 1
    if mode == 'fixed':
        pages.append(page_your_project(d, n, status_label)); n += 1
        pages.append(page_cost_fixed(d, n, status_label)); n += 1
    else:
        cost_pages = pages_cost_range(d, n, status_label)
        pages.extend(cost_pages); n += len(cost_pages)
    if on('finance'):
        pages.append(page_finance(d, n, status_label)); n += 1
    if mode == 'fixed':
        pages.append(page_approval_fixed(d, n, status_label)); n += 1
    else:
        pages.append(page_approval_range(d, n, status_label)); n += 1
    if on('project_run'):
        pages.append(page_project_run(d, n, status_label)); n += 1

    DOC = "<!DOCTYPE html><html><head><meta charset='utf-8'>" + CSS + "</head><body>" + "".join(pages) + "</body></html>"
    HTML(string=DOC).write_pdf(out_pdf)
    return out_pdf

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    json_path = sys.argv[1]
    with open(json_path, 'r', encoding='utf-8') as f:
        d = json.load(f)
    if len(sys.argv) >= 3:
        out_pdf = sys.argv[2]
    else:
        slug = re.sub(r'[^A-Za-z0-9_-]+', '_', d['client_name']).strip('_') or 'Client'
        out_pdf = os.path.join(os.path.dirname(os.path.abspath(json_path)), f'{slug}_Proposal.pdf')
    render(d, out_pdf)
    print(f'rendered: {out_pdf} ({os.path.getsize(out_pdf)/1024:.0f}KB)')

if __name__ == '__main__':
    main()
