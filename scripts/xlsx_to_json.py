# -*- coding: utf-8 -*-
"""Reads a filled Endure-Proposal-Quote-Inputs-TEMPLATE.xlsx and produces the
JSON dict render_proposal.py needs — so the agent can go straight from the
sales-filled spreadsheet to the rendered PDF, no hand-authored JSON required.

IMPORTANT: the workbook must be recalculated first (openpyxl writes formulas
but not their cached values), otherwise formula cells read back as None:
    python <xlsx-skill>/scripts/recalc.py path/to/Inputs.xlsx
If recalc.py hangs, check for a stale ".~lock.<file>#" file next to the
workbook — LibreOffice will wait on it. Recalculate a copy elsewhere and
copy it back over the original rather than fighting the lock file.

Usage:
    python xlsx_to_json.py path/to/Inputs.xlsx [out.json]

Cell addresses below are fixed to the layout produced by build_inputs_template.py
and patch_proposal_options.py. If someone reorganises the Inputs workbook, update
the row numbers here to match — this script is a direct mirror of that layout,
not a general-purpose xlsx parser.
"""
import sys, os, json
import openpyxl

def val(ws, addr):
    v = ws[addr].value
    return v.strip() if isinstance(v, str) else v

def read_inputs(xlsx_path):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)

    js = wb['Job Setup']
    mode_label = (val(js, 'B3') or 'Proposal').strip().lower()
    mode = 'fixed' if mode_label.startswith('quote') else 'range'
    d = {
        'mode': mode,
        'client_name': val(js, 'B4'),
        'cover_subtitle': val(js, 'B5'),
        'prepared_for': val(js, 'B6'),
        'location': val(js, 'B7'),
        'proposal_date': val(js, 'B8'),
        'status_label': val(js, 'B9'),
        'eyebrow_tag': val(js, 'B10'),
    }

    if mode == 'range':
        pr = wb['Proposal Inputs (Range)']

        options = []
        for r in range(6, 12):  # 1 pre-filled + 5 spare rows
            label = val(pr, f'A{r}')
            if not label:
                continue
            opt = {
                'label': label,
                'image': val(pr, f'B{r}'),
                'size_label': val(pr, f'C{r}') or '',
                'price_low_inc_gst': val(pr, f'D{r}'),
                'price_high_inc_gst': val(pr, f'E{r}'),
                'price_low_ex_gst': val(pr, f'F{r}'),
                'price_high_ex_gst': val(pr, f'G{r}'),
                'spec_line': val(pr, f'H{r}') or '',
            }
            eyebrow = val(pr, f'I{r}')
            if eyebrow:
                opt['eyebrow'] = eyebrow
            options.append(opt)

        alt_materials = []
        for r in range(15, 17):  # up to 2 alt-material rows
            name = val(pr, f'A{r}')
            if not name:
                continue
            alt_materials.append({
                'name': name, 'note': val(pr, f'B{r}') or '', 'delta': val(pr, f'E{r}') or '',
            })

        scope_items = []
        for r in range(20, 32):  # 4 pre-filled + 8 spare rows
            label = val(pr, f'A{r}')
            if not label:
                continue
            scope_items.append({
                'label': label, 'value': val(pr, f'B{r}'),
                'strong': (val(pr, f'C{r}') or '').upper() == 'Y',
            })

        tiers = []
        for r in range(39, 42):  # 3 design-service tiers
            name = val(pr, f'A{r}')
            if not name:
                continue
            tiers.append({
                'name': name, 'description': val(pr, f'B{r}'),
                'price_ex_gst': val(pr, f'C{r}'),
                'credit_consult': (val(pr, f'D{r}') or '').upper() == 'Y',
            })

        d['range'] = {
            'options': options,
            'alt_materials': alt_materials,
            'scope_items': scope_items,
            'preliminary_note': val(pr, 'B34'),
            'consult_fee_paid': val(pr, 'B37') or 0,
            'design_service_tiers': tiers,
        }
    else:
        qf = wb['Quote Inputs (Fixed)']
        bullets = [val(qf, f'A{r}') for r in range(11, 17) if val(qf, f'A{r}')]
        zones = []
        for r in range(19, 21):
            name = val(qf, f'A{r}')
            if not name:
                continue
            zones.append({'name': name, 'sub': val(qf, f'B{r}') or '', 'body': val(qf, f'C{r}') or ''})
        cost_lines = []
        for r in range(26, 36):  # 2 pre-filled + 8 spare rows
            label = val(qf, f'A{r}')
            amt = val(qf, f'C{r}')
            if not label or amt in (None, ''):
                continue
            cost_lines.append({'label': label, 'desc': val(qf, f'B{r}') or '', 'amount': amt})
        d['fixed'] = {
            'your_project': {
                'summary_line': val(qf, 'B4'),
                'image': val(qf, 'B5'),
                'price_inc_gst': val(qf, 'B6'),
                'spec_label': val(qf, 'B7'),
                'description': val(qf, 'B8'),
                'bullets': bullets,
                'zones': zones,
                'footnote': val(qf, 'B21') or '',
            },
            'package_label': val(qf, 'B24'),
            'cost_lines': cost_lines,
            'subtotal_ex_gst': val(qf, 'C36'),
            'total_inc_gst': val(qf, 'C38'),
            'included': val(qf, 'B41'),
            'excluded': val(qf, 'B42'),
            'deposit_pct': val(qf, 'B43'),
        }

    fin = wb['Finance']
    fin_rows = []
    for r in range(7, 10):  # 1 pre-filled + 2 spare rows
        label = val(fin, f'A{r}')
        if not label:
            continue
        fin_rows.append({'label': label, 'sub': val(fin, f'B{r}') or '',
                          'wk3': val(fin, f'C{r}'), 'wk5': val(fin, f'D{r}'), 'wk7': val(fin, f'E{r}')})
    d['finance'] = {'wk_low': val(fin, 'B3'), 'wk_high': val(fin, 'B4'), 'rows': fin_rows}

    pj = wb['Project Run']
    steps = []
    for r in range(6, 13):  # 7 steps
        num = val(pj, f'A{r}')
        if not num:
            continue
        steps.append({'num': str(num), 'title': val(pj, f'B{r}'), 'body': val(pj, f'C{r}')})
    sched = []
    for r in range(15, 20):  # up to 5 payment-schedule rows
        pct = val(pj, f'A{r}')
        if pct in (None, ''):
            continue
        sched.append({'pct': pct, 'label': val(pj, f'B{r}')})
    d['project_run'] = {'lead': val(pj, 'B3'), 'steps': steps, 'payment_schedule': sched}

    total_pct = sum(s['pct'] for s in sched)
    if abs(total_pct - 100) > 0.01:
        print(f'WARNING: payment schedule sums to {total_pct}, not 100 — check Project Run tab.', file=sys.stderr)

    return d

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    xlsx_path = sys.argv[1]
    d = read_inputs(xlsx_path)
    out_path = sys.argv[2] if len(sys.argv) >= 3 else os.path.splitext(xlsx_path)[0] + '.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    print(f'wrote: {out_path}')

if __name__ == '__main__':
    main()
