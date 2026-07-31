# -*- coding: utf-8 -*-
"""ENDURE INTERNAL JOB BUDGET RENDERER

Renders the PM-only internal costing document (red-banner Job Budget) from
internal-costing.json (+ optionally job-data.json for context, totals
reconciliation and the other quoted options). This document is NEVER sent to
a client — the banner, filename and footer all say so.

Usage:
    python render_internal.py internal-costing.json [job-data.json] [output.pdf]
"""
import json
import os
import re
import sys

from weasyprint import HTML

RED = "#b3392c"


def money(n, dec=0):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "$0"
    return f"${n:,.{dec}f}" if dec else f"${int(round(n)):,}"


def esc(v):
    return ("" if v is None else str(v)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


CSS = r"""<style>
@import url('https://fonts.googleapis.com/css2?family=EB+Garamond:wght@500;600;700&family=Inter:wght@400;500;600&display=swap');
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
html, body { margin:0; padding:0; font-family:'Inter',Arial,sans-serif; font-size:9.5pt; color:#1A1815; line-height:1.45; }
:root{--ink:#1A1815;--grey:#6e6e6e;--rule:#e3dccf;--mustard-deep:#B7872F;--cream:#F7F1E4;--green:#2f8e4e;--red:#b3392c;}
.banner{background:var(--red);color:#fff;font-weight:600;letter-spacing:.14em;text-align:center;
  padding:4mm;font-size:10pt;text-transform:uppercase;}
.body{padding:8mm 14mm 10mm;}
.jtitle{font-family:'EB Garamond',Georgia,serif;font-weight:600;font-size:22pt;line-height:1.1;margin:0 0 1.5mm;}
.jsub{font-size:9pt;color:var(--grey);margin-bottom:5mm;}
.h{font-size:8.5pt;font-weight:600;letter-spacing:.18em;color:var(--mustard-deep);text-transform:uppercase;
  margin:5mm 0 1.5mm;border-bottom:1px solid var(--rule);padding-bottom:1mm;}
table{width:100%;border-collapse:collapse;font-size:9pt;}
th{font-size:7.5pt;font-weight:600;letter-spacing:.1em;color:var(--grey);text-transform:uppercase;
  text-align:left;padding:1mm 2mm;border-bottom:1px solid var(--rule);}
td{padding:1.2mm 2mm;border-bottom:1px solid #f0ebdd;vertical-align:top;}
td.r, th.r{text-align:right;}
tr.sub td{font-weight:600;color:var(--grey);border-bottom:1px solid var(--rule);}
tr.tot td{font-weight:700;border-bottom:none;border-top:1.5px solid var(--ink);}
.empty{font-style:italic;color:var(--grey);padding:1.5mm 2mm;font-size:9pt;}
.strip{display:flex;gap:0;margin-top:6mm;border:1.5px solid var(--ink);}
.strip .cell{flex:1;padding:2.5mm 3mm;border-right:1px solid var(--rule);}
.strip .cell:last-child{border-right:none;}
.strip .lab{font-size:6.8pt;font-weight:600;letter-spacing:.1em;color:var(--grey);text-transform:uppercase;}
.strip .val{font-size:12pt;font-weight:700;margin-top:.5mm;}
.strip .val small{font-size:8pt;font-weight:500;color:var(--grey);}
.val.bad{color:var(--red);} .val.good{color:var(--green);}
.watch{background:#fdeaea;border:1.5px solid var(--red);color:#7a271d;padding:3mm;margin-top:5mm;font-size:9pt;}
.watch b{color:var(--red);}
.okbox{background:#eef7f0;border:1.5px solid var(--green);color:#22603a;padding:3mm;margin-top:5mm;font-size:9pt;}
.opts{margin-top:4mm;font-size:9pt;color:var(--ink);background:var(--cream);padding:3mm;border:1px solid var(--rule);}
.ops li{margin-bottom:1mm;}
.foot{font-size:7.5pt;color:#999;font-style:italic;margin-top:6mm;border-top:1px solid var(--rule);padding-top:2mm;}
</style>"""


def build_watch(summary, targets, passes, job_data, sell_override):
    """Rule-based commentary. The AI polish layer (v2) may reword this later,
    but the numbers and the trigger rules are deterministic here."""
    warns, notes = [], []
    gp_pct = num(summary.get("gp_pct"))
    gp_day = summary.get("gp_per_install_day")
    gp_min = num(targets.get("gp_pct_min")) or 35
    gp_goal = num(targets.get("gp_per_day_goal")) or 1500
    if summary.get("sell_ex_gst") and num(summary.get("total_cost")) > 0:
        if gp_pct < gp_min:
            warns.append(f"GP {gp_pct:.1f}% (under {gp_min:.0f}% goal)")
        if gp_day is not None:
            gp_day = num(gp_day)
            if gp_day < 1000:
                warns.append(f"GP/day {money(gp_day)} (below the $1,000/day floor)")
            elif gp_day < gp_goal:
                warns.append(f"GP/day {money(gp_day)} (above $1,000 floor, under {money(gp_goal)} aim)")
    if sell_override:
        notes.append("Sell price hand-set (overrides the calculator auto figure)")
    if not passes:
        notes.append("NO fixed costs added (no delivery/PM/bins) — confirm that's intended")
    recon = None
    if job_data and job_data.get("mode") == "fixed":
        client_total = num((job_data.get("fixed") or {}).get("total_inc_gst"))
        expected = num(summary.get("sell_ex_gst")) * 1.1
        if client_total > 0 and expected > 0:
            drift = abs(client_total - expected) / expected
            if drift > 0.01:
                warns.append(f"Client total {money(client_total)} inc GST does NOT reconcile with this budget "
                             f"(expected ~{money(expected)} from sell ex GST — {drift * 100:.1f}% off)")
            else:
                recon = f"Client total {money(client_total)} inc GST reconciles to the cost breakdown."
    return warns, notes, recon


def options_on_table(job_data, summary):
    if not job_data:
        return []
    out = []
    if job_data.get("mode") == "range":
        for o in (job_data.get("range") or {}).get("options") or []:
            lo, hi = num(o.get("price_low_inc_gst")), num(o.get("price_high_inc_gst"))
            price = money(lo) if lo == hi else f"{money(lo)}–{money(hi)}"
            out.append(f"{esc(o.get('label'))} {price} inc GST")
    else:
        items = ((job_data.get("fixed") or {}).get("your_project") or {}).get("project_items") or []
        priced = [i for i in items if num(i.get("price_inc_gst")) > 0]
        if len(priced) > 1:
            for i in priced:
                out.append(f"{esc(i.get('label'))} {money(num(i.get('price_inc_gst')))} inc GST")
    return out


def render(internal, job_data, out_pdf):
    summary = internal.get("summary") or {}
    targets = internal.get("targets") or {}
    comm = internal.get("commission") or {}
    mats = internal.get("materials") or []
    labs = internal.get("labour") or []
    passes = internal.get("pass_throughs") or []

    client = esc(internal.get("client_name") or "Client")
    jd = job_data or {}
    sub_bits = [esc(internal.get("project_type") or jd.get("project_type") or "Job"),
                esc(internal.get("location") or jd.get("location") or ""),
                esc(internal.get("proposal_date") or jd.get("proposal_date") or ""),
                "Quote (fixed)" if internal.get("mode") == "fixed" else "Proposal (range)"]
    ctx = esc(internal.get("context_line") or (jd.get("fixed") or {}).get("package_label") or "")
    if ctx:
        sub_bits.append("In costings: " + ctx)
    sub = " · ".join(b for b in sub_bits if b) + " · all figures ex GST"

    # materials grouped by stream, subtotals when more than one stream
    streams = []
    for m in mats:
        s = m.get("stream") or "Other"
        if s not in streams:
            streams.append(s)
    mat_rows = ""
    for s in streams:
        grp = [m for m in mats if (m.get("stream") or "Other") == s]
        for m in grp:
            line = num(m.get("qty")) * num(m.get("unit"))
            mat_rows += (f'<tr><td>{esc(m.get("item"))}</td><td>{esc(m.get("supplier"))}</td>'
                         f'<td>{esc(s)}</td><td class="r">{esc(m.get("qty"))}</td>'
                         f'<td class="r">{money(num(m.get("unit")), 2)}</td><td class="r">{money(line, 2)}</td></tr>')
        if len(streams) > 1:
            sub_t = sum(num(m.get("qty")) * num(m.get("unit")) for m in grp)
            mat_rows += (f'<tr class="sub"><td colspan="5">{esc(s)} — subtotal</td>'
                         f'<td class="r">{money(sub_t, 2)}</td></tr>')
    mat_rows += (f'<tr class="tot"><td colspan="5">Materials total</td>'
                 f'<td class="r">{money(num(summary.get("materials_total")), 2)}</td></tr>')

    lab_rows = "".join(
        f'<tr><td>{esc(l.get("task"))}</td><td>{esc(l.get("stream"))}</td><td class="r">{esc(l.get("days"))}</td>'
        f'<td class="r">{money(num(l.get("rate")))}</td><td class="r">{money(num(l.get("days")) * num(l.get("rate")), 2)}</td></tr>'
        for l in labs)
    lab_rows += (f'<tr class="tot"><td colspan="4">Labour total ({esc(summary.get("labour_days"))} days)</td>'
                 f'<td class="r">{money(num(summary.get("labour_total")), 2)}</td></tr>')
    lab_html = (f'<table><tr><th>Task</th><th>Stream</th><th class="r">Days</th><th class="r">Day rate</th>'
                f'<th class="r">Cost</th></tr>{lab_rows}</table>') if labs else \
        '<div class="empty">None entered.</div>'

    if passes:
        pass_rows = ""
        for p in passes:
            charge = num(p.get("cost")) * (1 + num(p.get("margin")) / 100) if p.get("type") == "Subbie" else num(p.get("cost"))
            margin = f'{esc(p.get("margin"))}%' if p.get("type") == "Subbie" else "—"
            pass_rows += (f'<tr><td>{esc(p.get("label"))}</td><td>{esc(p.get("type"))}</td>'
                          f'<td class="r">{money(num(p.get("cost")), 2)}</td><td class="r">{margin}</td>'
                          f'<td class="r">{money(charge, 2)}</td></tr>')
        pass_html = (f'<table><tr><th>Item</th><th>Type</th><th class="r">Our cost</th><th class="r">Margin</th>'
                     f'<th class="r">Client charge</th></tr>{pass_rows}</table>')
    else:
        pass_html = '<div class="empty">None entered — no delivery / PM / bins on this job.</div>'

    gp_pct = num(summary.get("gp_pct"))
    gp_cls = "bad" if gp_pct < 30 else "good"
    gp_day = summary.get("gp_per_install_day")
    gp_day_html = money(num(gp_day)) if gp_day is not None else "—"
    strip = f"""<div class="strip">
      <div class="cell"><div class="lab">Total cost</div><div class="val">{money(num(summary.get("total_cost")))}</div></div>
      <div class="cell"><div class="lab">Sell ex GST</div><div class="val">{money(num(summary.get("sell_ex_gst")))}</div></div>
      <div class="cell"><div class="lab">Gross profit</div><div class="val {gp_cls}">{money(num(summary.get("gp")))} <small>{gp_pct:.1f}%</small></div></div>
      <div class="cell"><div class="lab">GP / install day</div><div class="val">{gp_day_html}</div></div>
      <div class="cell"><div class="lab">Commission ({num(comm.get("pct")):.0f}% GP)</div><div class="val">{money(num(comm.get("amount")))}</div></div>
      <div class="cell"><div class="lab">Net retained</div><div class="val">{money(num(summary.get("net_retained")))}</div></div>
    </div>"""

    xs = internal.get("optional_extras") or []
    extras_html = ""
    if xs:
        xrows = "".join(
            f'<tr><td>{esc(x.get("label"))}</td><td class="r">{money(num(x.get("cost_ex")), 2)}</td>'
            f'<td class="r">{money(num(x.get("margin_ex")), 2)}</td><td class="r">{money(num(x.get("sell_inc")), 2)}</td>'
            f'<td class="r">{num(x.get("gp_pct")):.1f}%</td></tr>'
            for x in xs)
        extras_html = ('<div class="h">Optional upsells offered (not in totals — awaiting client tick)</div>'
                       '<table><tr><th>Extra</th><th class="r">Cost ex</th><th class="r">Margin ex</th>'
                       f'<th class="r">Client price inc</th><th class="r">GP%</th></tr>{xrows}</table>')

    warns, notes, recon = build_watch(summary, targets, passes, job_data, internal.get("sell_override"))
    watch_html = ""
    if warns or notes:
        bits = " · ".join(warns + notes)
        tail = f" {recon}" if recon else ""
        watch_html = f'<div class="watch"><b>Watch:</b> {bits}.{tail}</div>'
    elif recon:
        watch_html = f'<div class="okbox">✓ {recon} No flags on this budget.</div>'

    opts = options_on_table(job_data, summary)
    opts_html = ""
    if opts:
        opts_html = ('<div class="opts"><b>Options on the table (not in this budget):</b> ' + " · ".join(opts) +
                     ". If the client picks another, flip it to in-costings and regenerate — the budget recomputes.</div>")

    ops_html = ""
    ops = (internal.get("ops_notes") or "").strip()
    if ops:
        items = "".join(f"<li>{esc(line.lstrip('-• ').strip())}</li>" for line in ops.splitlines() if line.strip())
        ops_html = f'<div class="h">Ops notes (from the quote — for the crew/PM)</div><ul class="ops">{items}</ul>'

    sheet = internal.get("costings_sheet_url")
    sheet_html = f'<div class="jsub">Working costings sheet: {esc(sheet)}</div>' if sheet else ""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">{CSS}</head><body>
    <div class="banner">Internal — not for client distribution — PM budget reference only</div>
    <div class="body">
      <div class="jtitle">{client} — Job Budget</div>
      <div class="jsub">{sub}</div>
      {sheet_html}
      <div class="h">Materials — by type</div>
      <table><tr><th>Item</th><th>Supplier</th><th>Type</th><th class="r">Qty</th><th class="r">Unit cost</th>
      <th class="r">Cost</th></tr>{mat_rows}</table>
      <div class="h">Labour</div>
      {lab_html}
      <div class="h">Fixed costs &amp; pass-throughs</div>
      {pass_html}
      {extras_html}
      {strip}
      {watch_html}
      {opts_html}
      {ops_html}
      <div class="foot">Generated from the Endure Quote Builder job data. Costs ex GST.
      Never include this document in anything a client receives.</div>
    </div></body></html>"""
    HTML(string=html).write_pdf(out_pdf)
    return out_pdf


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    internal = json.load(open(sys.argv[1]))
    job_data = None
    out = None
    for arg in sys.argv[2:]:
        if arg.endswith(".json"):
            job_data = json.load(open(arg))
        elif arg.endswith(".pdf"):
            out = arg
    if not out:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", internal.get("client_name") or "Client").strip("_")
        out = os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), f"INTERNAL_Job_Budget_{slug}.pdf")
    render(internal, job_data, out)
    print(f"rendered: {out} ({os.path.getsize(out) // 1024}KB)")


if __name__ == "__main__":
    main()
