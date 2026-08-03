"""
Endure Quote Builder — single-host server.
Serves the builder page, the rates/save API (port of the old api/endure.js),
and the real WeasyPrint renderer (scripts/render_proposal.py) so the PDF a
client receives is pixel-identical to what the tool has always produced.

Run local:  uvicorn server:app --reload
Deploy:     Dockerfile (Railway / Render / Fly) → quote.enduredecks.com.au
"""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import smtplib
import sys
import tempfile
from email.message import EmailMessage

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import render_internal  # noqa: E402  (imports weasyprint)
import render_proposal  # noqa: E402  (imports weasyprint)

from google.oauth2 import service_account  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaIoBaseUpload  # noqa: E402

TOKEN = os.environ.get("TEAM_TOKEN")
SEND_USER = os.environ.get("SEND_EMAIL_USER")
SEND_PASS = os.environ.get("SEND_EMAIL_APP_PASSWORD")
SEND_BCC = os.environ.get("SEND_BCC", "")
SEND_NAME = os.environ.get("SEND_EMAIL_NAME", "Matt — Endure Decks")
SHEET = os.environ.get("RATE_SHEET_ID")
JOBS = os.environ.get("INCOMING_JOBS_ID")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _creds():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "")
    return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)


app = FastAPI()


def bad(err):
    # Same contract as the old endpoint: always 200, ok:false on error.
    return JSONResponse({"ok": False, "error": str(err)})


@app.get("/api/endure")
async def endure_get(request: Request):
    if not TOKEN:
        return JSONResponse({"ok": False, "error": "server not configured"}, status_code=500)
    q = request.query_params
    if q.get("token") != TOKEN:
        return bad("bad token")
    if q.get("action") == "rates":
        try:
            return JSONResponse(read_rates())
        except Exception as e:  # noqa: BLE001
            return bad(e)
    return bad("unknown action")


@app.post("/api/endure")
async def endure_post(request: Request):
    if not TOKEN:
        return JSONResponse({"ok": False, "error": "server not configured"}, status_code=500)
    try:
        body = json.loads((await request.body()) or b"{}")
    except Exception:  # noqa: BLE001
        body = {}
    if body.get("token") != TOKEN:
        return bad("bad token")
    action = body.get("action")
    try:
        if action == "save_job":
            return JSONResponse(save_job(body))
        if action == "render":
            return render_pdf(body)
        if action == "render_internal":
            return render_internal_pdf(body)
        if action == "log_url":
            drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
            sheets = build("sheets", "v4", credentials=_creds(), cache_discovery=False)
            return JSONResponse({"ok": True,
                                 "url": f"https://docs.google.com/spreadsheets/d/{_quote_log_id(drive, sheets)}/edit"})
        if action == "draft_save":
            return JSONResponse(draft_save(body))
        if action == "draft_list":
            return JSONResponse(draft_list())
        if action == "draft_get":
            return JSONResponse(draft_get(body))
        if action == "img_lib_list":
            return JSONResponse(img_lib_list())
        if action == "img_lib_get":
            return JSONResponse(img_lib_get(body))
        if action == "img_lib_put":
            return JSONResponse(img_lib_put(body))
        if action == "send_ballpark":
            return JSONResponse(send_ballpark(body))
        if action == "send_quote":
            return JSONResponse(send_quote(body))
        if action == "accept_links_get":
            return JSONResponse({"ok": True, "enabled": accept_links_enabled()})
        if action == "accept_links_set":
            return JSONResponse(accept_links_set(body))
    except Exception as e:  # noqa: BLE001
        return bad(e)
    return bad("unknown action")


# ── rates (straight port of readRates in api/endure.js) ─────────────────────
def read_rates():
    sheets = build("sheets", "v4", credentials=_creds(), cache_discovery=False)
    vals = sheets.spreadsheets().values()
    rows = vals.get(spreadsheetId=SHEET, range="Shopping List!A1:H60").execute().get("values", [])
    asm = vals.get(spreadsheetId=SHEET, range="Assumptions!A1:B30").execute().get("values", [])
    out = {"ok": True, "boards": {}, "subframe_items": {}, "linear": {}, "perjob": {},
           "labour_day": None, "gp_target": None,
           "sheet_url": f"https://docs.google.com/spreadsheets/d/{SHEET}/edit"}
    oil = 0.0
    for r in rows:
        section = (r[0] if len(r) > 0 else "").strip()
        item = (r[1] if len(r) > 1 else "").strip()
        try:
            cost = float(r[5]) if len(r) > 5 and r[5] not in ("", None) else 0.0
        except (TypeError, ValueError):
            cost = 0.0
        if not item or cost <= 0:
            continue
        if section == "Boards" and re.search(r"oil", item, re.I):
            oil = cost
            continue
        if section == "Boards":
            out["boards"][item] = cost
        elif section == "Subframe":
            out["subframe_items"][item] = cost
        elif section == "Linear":
            out["linear"][item] = cost
        elif section == "Labour":
            out["labour_day"] = cost
        elif section == "Per-job":
            out["perjob"][item] = cost
    if oil:
        comp = ["eva-last", "evalast", "moistureshield", "moisture shield", "trex", "modwood", "composite"]
        for name in list(out["boards"].keys()):
            if not any(k in name.lower() for k in comp):
                out["boards"][name] = round((out["boards"][name] + oil) * 100) / 100
    for r in asm:
        k = r[0] if len(r) > 0 else ""
        if re.search(r"IDEAL|north star", str(k), re.I):
            try:
                out["gp_target"] = float(str(r[1]).replace(",", ""))
            except (TypeError, ValueError, IndexError):
                out["gp_target"] = None
    return out


# ── save job (straight port of saveJob in api/endure.js) ────────────────────
def _upsert_file(drive, folder_id, name, media):
    """Same name in the same folder = new version, not a duplicate — saves are idempotent."""
    safe = json.dumps(name)[1:-1]
    res = drive.files().list(q=f"'{folder_id}' in parents and name = '{safe}' and trashed = false",
                             fields="files(id)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True, corpora="allDrives").execute()
    hits = res.get("files", [])
    if hits:
        drive.files().update(fileId=hits[0]["id"], media_body=media, supportsAllDrives=True).execute()
    else:
        drive.files().create(body={"name": name, "parents": [folder_id]}, media_body=media,
                             supportsAllDrives=True).execute()


def save_job(body):
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    fname = body.get("folder_name")
    safe = json.dumps(fname or "")[1:-1]
    existing = drive.files().list(
        q=f"'{JOBS}' in parents and name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        fields="files(id, webViewLink)", supportsAllDrives=True,
        includeItemsFromAllDrives=True, corpora="allDrives").execute().get("files", [])
    if existing:
        folder = existing[0]
    else:
        folder = drive.files().create(
            body={"name": fname, "mimeType": "application/vnd.google-apps.folder", "parents": [JOBS]},
            fields="id, webViewLink", supportsAllDrives=True).execute()

    def put_json(name, obj):
        media = MediaIoBaseUpload(io.BytesIO(json.dumps(obj, indent=2).encode()), mimetype="application/json")
        _upsert_file(drive, folder["id"], name, media)

    put_json("job-data.json", body.get("job_data"))
    if body.get("internal"):
        put_json("internal-costing.json", body["internal"])
        try:  # also file the rendered Job Budget PDF; the save still succeeds without it
            pdf_bytes, pdf_name = _internal_pdf_bytes(body["internal"], body.get("job_data"))
            media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")
            _upsert_file(drive, folder["id"], pdf_name, media)
        except Exception:  # noqa: BLE001
            pass
    for a in body.get("attachments") or []:
        media = MediaIoBaseUpload(io.BytesIO(base64.b64decode(a["b64"])), mimetype=a.get("mime") or "image/jpeg")
        _upsert_file(drive, folder["id"], a["name"], media)
    try:  # also file the final client PDF into the folder — the CEO/paper trail
        jd = body.get("job_data") or {}
        client = (jd.get("client_name") or "Client").strip()
        slug = re.sub(r"[^A-Za-z0-9]+", "_", client).strip("_") or "Client"
        doc_name = f"{slug}_{'Proposal' if jd.get('mode') == 'range' else 'Quote'}.pdf"
        with tempfile.TemporaryDirectory() as td:
            out_pdf = os.path.join(td, doc_name)
            render_proposal.render(jd, out_pdf)
            media = MediaIoBaseUpload(io.BytesIO(open(out_pdf, "rb").read()), mimetype="application/pdf")
            _upsert_file(drive, folder["id"], doc_name, media)
    except Exception:  # noqa: BLE001
        pass
    logged = False
    try:  # quote-log append is best-effort — a log hiccup must never fail the save
        log_quote(body, folder.get("webViewLink"))
        logged = True
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "folderUrl": folder.get("webViewLink"), "folderId": folder["id"], "logged": logged,
            "accept_token": accept_token_for(folder["id"])}


# ── render (the real renderer — same code path as the skill) ────────────────
def render_pdf(body):
    """POST {action:'render', job_data:{...}} → the client PDF, exactly as
    render_proposal.py has always produced it. Optional folder_id uploads a
    copy into the job's Drive folder as well."""
    data = body.get("job_data")
    if not data:
        return bad("job_data required")
    client = (data.get("client_name") or "Client").strip()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", client).strip("_") or "Client"
    with tempfile.TemporaryDirectory() as td:
        out_pdf = os.path.join(td, f"{slug}_Proposal.pdf")
        render_proposal.render(data, out_pdf)
        pdf_bytes = open(out_pdf, "rb").read()
    if body.get("folder_id"):
        try:
            drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
            media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")
            drive.files().create(
                body={"name": body.get("pdf_name") or f"{slug}_Proposal.pdf",
                      "parents": [body["folder_id"]]},
                media_body=media, supportsAllDrives=True).execute()
        except Exception:  # noqa: BLE001
            pass  # PDF still returns to the browser even if Drive filing fails
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{slug}_Proposal.pdf"'})


# ── project drafts (named work-in-progress saves, live in Drive) ────────────
DRAFTS_NAME = "_Project Drafts"


def _drafts_folder(drive):
    q = (f"'{JOBS}' in parents and name = '{DRAFTS_NAME}' "
         "and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    res = drive.files().list(q=q, fields="files(id)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True, corpora="allDrives").execute()
    hits = res.get("files", [])
    if hits:
        return hits[0]["id"]
    made = drive.files().create(
        body={"name": DRAFTS_NAME, "mimeType": "application/vnd.google-apps.folder", "parents": [JOBS]},
        fields="id", supportsAllDrives=True).execute()
    return made["id"]


def draft_save(body):
    name, data = (body.get("name") or "").strip(), body.get("data")
    if not name or data is None:
        return {"ok": False, "error": "name and data required"}
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    folder = _drafts_folder(drive)
    fname = re.sub(r'[\\/:*?"<>|]', "_", name) + ".json"
    media = MediaIoBaseUpload(io.BytesIO(json.dumps(data).encode()), mimetype="application/json")
    q = f"'{folder}' in parents and name = '{json.dumps(fname)[1:-1]}' and trashed = false"
    res = drive.files().list(q=q, fields="files(id)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True, corpora="allDrives").execute()
    hits = res.get("files", [])
    if hits:  # same name = overwrite, so a project keeps one current save
        drive.files().update(fileId=hits[0]["id"], media_body=media, supportsAllDrives=True).execute()
        return {"ok": True, "updated": True}
    drive.files().create(body={"name": fname, "parents": [folder]}, media_body=media,
                         supportsAllDrives=True).execute()
    return {"ok": True, "updated": False}


def draft_list():
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    folder = _drafts_folder(drive)
    res = drive.files().list(
        q=f"'{folder}' in parents and trashed = false and mimeType = 'application/json'",
        fields="files(id, name, modifiedTime)", orderBy="modifiedTime desc", pageSize=100,
        supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives").execute()
    drafts = [{"id": f["id"], "name": f["name"].rsplit(".json", 1)[0], "modified": f.get("modifiedTime", "")}
              for f in res.get("files", [])]
    return {"ok": True, "drafts": drafts}


def draft_get(body):
    file_id = body.get("id")
    if not file_id:
        return {"ok": False, "error": "id required"}
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    data = drive.files().get_media(fileId=file_id).execute()
    return {"ok": True, "data": json.loads(data)}


# ── quote log (every saved quote appends a row; Dashboard tab = CEO view) ───
QUOTE_LOG_NAME = "_Quote Log"
LOG_HEADERS = ["Date", "Client", "Location", "Lane", "Total inc GST", "Sell ex GST",
               "Cost ex GST", "GP $ ex", "GP %", "GP/day", "Install days",
               "Extras offered $ inc", "Status", "Job folder"]
DASH_ROWS = [
    ["ENDURE — QUOTE PIPELINE  (auto-updates from the Log tab; set Status to Won or Lost as jobs land)"],
    [],
    ["This month — quotes sent", "=COUNTIFS(Log!A:A,\">=\"&EOMONTH(TODAY(),-1)+1)"],
    ["This month — value sent (inc GST)", "=SUMIFS(Log!E:E,Log!A:A,\">=\"&EOMONTH(TODAY(),-1)+1)"],
    ["Pipeline (Sent) — value inc GST", "=SUMIFS(Log!E:E,Log!M:M,\"Sent\")"],
    ["Pipeline (Sent) — GP forecast ex GST", "=SUMIFS(Log!H:H,Log!M:M,\"Sent\")"],
    ["Won — value inc GST (all time)", "=SUMIFS(Log!E:E,Log!M:M,\"Won\")"],
    ["Conversion (won / decided)", "=IFERROR(COUNTIF(Log!M:M,\"Won\")/(COUNTIF(Log!M:M,\"Won\")+COUNTIF(Log!M:M,\"Lost\")),\"—\")"],
    ["Avg GP % (won jobs)", "=IFERROR(AVERAGEIF(Log!M:M,\"Won\",Log!I:I),\"—\")"],
    [],
    ["BY MONTH"],
    ["=QUERY(Log!A2:N,\"select year(A), month(A)+1, count(B), sum(E), sum(H) where A is not null "
     "group by year(A), month(A)+1 order by year(A) desc, month(A)+1 desc "
     "label year(A) 'Year', month(A)+1 'Month', count(B) 'Quotes', sum(E) 'Sent $ inc', sum(H) 'GP $ ex'\",0)"],
]


def _quote_log_id(drive, sheets):
    q = (f"'{JOBS}' in parents and name = '{QUOTE_LOG_NAME}' "
         "and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false")
    res = drive.files().list(q=q, fields="files(id)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True, corpora="allDrives").execute()
    hits = res.get("files", [])
    if hits:
        return hits[0]["id"]
    made = drive.files().create(
        body={"name": QUOTE_LOG_NAME, "mimeType": "application/vnd.google-apps.spreadsheet",
              "parents": [JOBS]},
        fields="id", supportsAllDrives=True).execute()
    sid = made["id"]
    meta = sheets.spreadsheets().get(spreadsheetId=sid).execute()
    first = meta["sheets"][0]["properties"]["sheetId"]
    sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
        {"updateSheetProperties": {"properties": {"sheetId": first, "title": "Log"},
                                   "fields": "title"}},
        {"addSheet": {"properties": {"title": "Dashboard"}}},
    ]}).execute()
    vals = sheets.spreadsheets().values()
    vals.update(spreadsheetId=sid, range="Log!A1", valueInputOption="USER_ENTERED",
                body={"values": [LOG_HEADERS]}).execute()
    vals.update(spreadsheetId=sid, range="Dashboard!A1", valueInputOption="USER_ENTERED",
                body={"values": DASH_ROWS}).execute()
    return sid


def log_quote(body, folder_url):
    """Append one row per saved quote. Never raises into the save path."""
    from datetime import date
    jd = body.get("job_data") or {}
    internal = body.get("internal") or {}
    summ = internal.get("summary") or {}
    lane = "Proposal (range)" if jd.get("mode") == "range" else "Quote (fixed)"
    sell_ex = summ.get("sell_ex_gst") or 0
    if jd.get("mode") == "fixed":
        total_inc = (jd.get("fixed") or {}).get("total_inc_gst") or 0
    else:
        total_inc = round(float(sell_ex) * 1.1) if sell_ex else 0
    extras_inc = sum(float(x.get("sell_inc") or 0) for x in internal.get("optional_extras") or [])
    row = [date.today().isoformat(), jd.get("client_name") or "", jd.get("location") or "", lane,
           total_inc, sell_ex, summ.get("total_cost") or 0, summ.get("gp") or 0,
           summ.get("gp_pct") or 0, summ.get("gp_per_install_day") or "",
           summ.get("labour_days") or "", extras_inc, "Sent", folder_url or ""]
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    sheets = build("sheets", "v4", credentials=_creds(), cache_discovery=False)
    sid = _quote_log_id(drive, sheets)
    vals = sheets.spreadsheets().values()
    # one row per job: match by client name, update in place (keeps Status); else append
    clients = vals.get(spreadsheetId=sid, range="Log!B:B").execute().get("values", [])
    hit = None
    for i, r in enumerate(clients):
        if r and r[0] == row[1] and i > 0:
            hit = i + 1
            break
    if hit:
        status = vals.get(spreadsheetId=sid, range=f"Log!M{hit}").execute().get("values", [[row[12]]])
        row[12] = (status[0][0] if status and status[0] else row[12]) or row[12]
        vals.update(spreadsheetId=sid, range=f"Log!A{hit}:N{hit}", valueInputOption="USER_ENTERED",
                    body={"values": [row]}).execute()
    else:
        vals.append(spreadsheetId=sid, range="Log!A1", valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS", body={"values": [row]}).execute()


# ── ballpark email send (from Matt's address, BCC to the shared inbox) ──────
def _gmail_send(msg):
    # HTTPS path — the only one that works on hosts that block outbound SMTP
    # (Railway does). Needs domain-wide delegation: the Workspace admin
    # authorises this service account's client ID for the gmail.send scope,
    # then it can send as SEND_USER.
    info = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT", ""))
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/gmail.send"], subject=SEND_USER)
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    gmail.users().messages().send(
        userId="me",
        body={"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}).execute()


def _gmail_err(e):
    s = str(e)
    try:
        info = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT", ""))
    except Exception:  # noqa: BLE001
        info = {}
    cid = info.get("client_id", "")
    proj = info.get("project_id", "")
    if "accessNotConfigured" in s or "has not been used" in s or "is disabled" in s:
        return ("Gmail API isn't enabled in the service account's own project"
                + (f" ('{proj}')" if proj else "") + " — enable it at "
                "https://console.cloud.google.com/apis/library/gmail.googleapis.com"
                + (f"?project={proj}" if proj else "") +
                f" then wait 2–3 minutes and retry. Google's exact words: {s[:300]}")
    if "unauthorized_client" in s or "access_denied" in s or "Precondition" in s:
        return (f"the service account isn't authorised to send as {SEND_USER}. In "
                "admin.google.com → Security → Access and data control → API controls → "
                f"Domain-wide delegation, add client ID {cid or '(the client_id field in the service-account JSON)'} "
                "with scope https://www.googleapis.com/auth/gmail.send")
    if "invalid_grant" in s:
        return (f"{SEND_USER} doesn't look like a mailbox on the Google Workspace domain the "
                "delegation was granted in — SEND_EMAIL_USER must be a Workspace address "
                "(a personal @gmail.com can't be delegated to)")
    return s


def _send_message(msg):
    # Returns the method that worked; raises with an instructive combined
    # error if every path fails.
    errors = []
    try:
        _gmail_send(msg)
        return "gmail_api"
    except Exception as e:  # noqa: BLE001
        errors.append(f"Gmail API: {_gmail_err(e)}")
    if SEND_PASS:
        try:
            _smtp_send(msg)
            return "smtp"
        except smtplib.SMTPAuthenticationError:
            errors.append("SMTP: Gmail rejected the app-password login")
        except (TimeoutError, OSError) as e:
            errors.append(f"SMTP: mail ports blocked or unreachable ({e})")
        except smtplib.SMTPException as e:
            errors.append(f"SMTP: {e}")
    raise RuntimeError(" | ".join(errors))


def _smtp_send(msg):
    # 465/SSL first; if that port is filtered (connection hangs/refused), retry
    # on 587/STARTTLS before giving up. Timeouts keep a blocked port from
    # hanging the request forever.
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
            smtp.login(SEND_USER, SEND_PASS)
            smtp.send_message(msg)
            return
    except smtplib.SMTPAuthenticationError:
        raise
    except (TimeoutError, OSError, smtplib.SMTPException):
        pass
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SEND_USER, SEND_PASS)
        smtp.send_message(msg)


def send_ballpark(body):
    if not SEND_USER:
        return {"ok": False, "error": "Email sending isn't configured — add SEND_EMAIL_USER "
                                      "(Matt's address) in Railway, plus SEND_BCC for the "
                                      "shared-inbox copy."}
    to = (body.get("to") or "").strip()
    if not to or "@" not in to:
        return {"ok": False, "error": "recipient email required"}
    subject = (body.get("subject") or "Your ballpark from Endure Decks").strip()
    text = body.get("body") or ""
    jd = body.get("job_data")

    msg = EmailMessage()
    msg["From"] = f"{SEND_NAME} <{SEND_USER}>"
    msg["To"] = to
    msg["Subject"] = subject
    if SEND_BCC:
        msg["Bcc"] = SEND_BCC
    msg.set_content(text)

    attached = False
    if jd:
        client = (jd.get("client_name") or "Client").strip()
        slug = re.sub(r"[^A-Za-z0-9]+", "_", client).strip("_") or "Client"
        pdf_name = f"{slug}_Ballpark.pdf"
        with tempfile.TemporaryDirectory() as td:
            out_pdf = os.path.join(td, pdf_name)
            render_proposal.render(jd, out_pdf)
            msg.add_attachment(open(out_pdf, "rb").read(), maintype="application",
                               subtype="pdf", filename=pdf_name)
        attached = True

    try:
        via = _send_message(msg)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{e}. Fallback: Copy email + Generate PDF and send it yourself."}

    logged = False
    try:  # lead log is best-effort — the send already happened
        log_ballpark_lead(jd, to)
        logged = True
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "from": SEND_USER, "attached": attached, "logged": logged, "via": via}


def log_ballpark_lead(jd, to):
    from datetime import date
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    sheets = build("sheets", "v4", credentials=_creds(), cache_discovery=False)
    sid = _quote_log_id(drive, sheets)
    meta = sheets.spreadsheets().get(spreadsheetId=sid).execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    if "Ballparks" not in titles:
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
            {"addSheet": {"properties": {"title": "Ballparks"}}}]}).execute()
        sheets.spreadsheets().values().update(
            spreadsheetId=sid, range="Ballparks!A1", valueInputOption="USER_ENTERED",
            body={"values": [["Date", "Name", "Email", "Lane", "Range low inc", "Range high inc", "Status"]]}).execute()
    opt = ((jd or {}).get("range", {}).get("options") or [{}])[0]
    row = [date.today().isoformat(), (jd or {}).get("client_name", ""), to,
           ((jd or {}).get("ballpark_next") or {}).get("lane", ""),
           opt.get("price_low_inc_gst", ""), opt.get("price_high_inc_gst", ""), "Ballpark sent"]
    sheets.spreadsheets().values().append(
        spreadsheetId=sid, range="Ballparks!A1", valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS", body={"values": [row]}).execute()


# ── image library (shared bank of reusable images, lives in Drive) ──────────
IMG_LIB_NAME = "_Image Library"


def _img_lib_folder(drive):
    q = (f"'{JOBS}' in parents and name = '{IMG_LIB_NAME}' "
         "and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    res = drive.files().list(q=q, fields="files(id)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True, corpora="allDrives").execute()
    hits = res.get("files", [])
    if hits:
        return hits[0]["id"]
    made = drive.files().create(
        body={"name": IMG_LIB_NAME, "mimeType": "application/vnd.google-apps.folder", "parents": [JOBS]},
        fields="id", supportsAllDrives=True).execute()
    return made["id"]


def img_lib_list():
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    folder = _img_lib_folder(drive)
    res = drive.files().list(
        q=f"'{folder}' in parents and trashed = false and mimeType contains 'image/'",
        fields="files(id, name, mimeType)", orderBy="name", pageSize=200,
        supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives").execute()
    return {"ok": True, "images": res.get("files", [])}


def img_lib_get(body):
    file_id = body.get("id")
    if not file_id:
        return {"ok": False, "error": "id required"}
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    meta = drive.files().get(fileId=file_id, fields="mimeType", supportsAllDrives=True).execute()
    data = drive.files().get_media(fileId=file_id).execute()
    return {"ok": True, "mime": meta.get("mimeType") or "image/jpeg",
            "b64": base64.b64encode(data).decode()}


def img_lib_put(body):
    name, b64 = body.get("name"), body.get("b64")
    if not name or not b64:
        return {"ok": False, "error": "name and b64 required"}
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    folder = _img_lib_folder(drive)
    media = MediaIoBaseUpload(io.BytesIO(base64.b64decode(b64)), mimetype=body.get("mime") or "image/jpeg")
    made = drive.files().create(body={"name": name, "parents": [folder]}, media_body=media,
                                fields="id, name", supportsAllDrives=True).execute()
    return {"ok": True, "image": {"id": made["id"], "name": made["name"]}}


# ── online quote acceptance (/q/<token>, beta — toggled from the builder) ───
def _accept_key():
    try:
        pk = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT", ""))["private_key"]
    except Exception:  # noqa: BLE001
        return None
    return hashlib.sha256(("accept-links:" + pk).encode()).digest()


def accept_token_for(folder_id):
    key = _accept_key()
    if not key or not folder_id:
        return None
    sig = hmac.new(key, folder_id.encode(), hashlib.sha256).hexdigest()[:20]
    return f"{folder_id}~{sig}"


def _accept_folder_id(token):
    if "~" not in (token or ""):
        return None
    fid, sig = token.rsplit("~", 1)
    key = _accept_key()
    if not key:
        return None
    good = hmac.new(key, fid.encode(), hashlib.sha256).hexdigest()[:20]
    return fid if hmac.compare_digest(sig, good) else None


def _find_in_folder(drive, folder_id, name):
    safe = json.dumps(name)[1:-1]
    res = drive.files().list(q=f"'{folder_id}' in parents and name = '{safe}' and trashed = false",
                             fields="files(id)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True, corpora="allDrives").execute().get("files", [])
    return res[0]["id"] if res else None


_ACCEPT_FLAG = {"ts": 0.0, "val": False}


def accept_links_enabled():
    import time
    now = time.time()
    if now - _ACCEPT_FLAG["ts"] < 30:
        return _ACCEPT_FLAG["val"]
    val = _ACCEPT_FLAG["val"]  # keep last known state if Drive hiccups
    try:
        drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
        fid = _find_in_folder(drive, JOBS, "_Accept Links.json")
        val = bool(fid and json.loads(drive.files().get_media(fileId=fid).execute()).get("enabled"))
    except Exception:  # noqa: BLE001
        pass
    _ACCEPT_FLAG["ts"] = now
    _ACCEPT_FLAG["val"] = val
    return val


def accept_links_set(body):
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    enabled = bool(body.get("enabled"))
    media = MediaIoBaseUpload(io.BytesIO(json.dumps({"enabled": enabled}).encode()),
                              mimetype="application/json")
    _upsert_file(drive, JOBS, "_Accept Links.json", media)
    import time
    _ACCEPT_FLAG["ts"] = time.time()
    _ACCEPT_FLAG["val"] = enabled
    return {"ok": True, "enabled": enabled}


def _read_job_folder(token):
    """token → (drive, folder_id, job_data, acceptance|None, filed_pdf_id|None).
    One folder list + minimal reads — this is the accept page's hot path."""
    fid = _accept_folder_id(token)
    if not fid:
        raise ValueError("bad link")
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    res = drive.files().list(q=f"'{fid}' in parents and trashed = false",
                             fields="files(id, name)", pageSize=100, supportsAllDrives=True,
                             includeItemsFromAllDrives=True, corpora="allDrives").execute().get("files", [])
    byname = {f["name"]: f["id"] for f in res}
    jd_id = byname.get("job-data.json")
    if not jd_id:
        raise ValueError("quote not found")
    jd = json.loads(drive.files().get_media(fileId=jd_id).execute())
    acc = None
    if byname.get("acceptance.json"):
        try:
            acc = json.loads(drive.files().get_media(fileId=byname["acceptance.json"]).execute())
        except Exception:  # noqa: BLE001
            acc = None
    pdf_id = next((i for nm, i in byname.items()
                   if nm.lower().endswith(("_quote.pdf", "_proposal.pdf"))), None)
    return drive, fid, jd, acc, pdf_id


_ACCEPT_CSS = """
:root{--cream:#F7F1E4;--ink:#1A1815;--grey:#6e6e6e;--rule:#e3dccf;--mdeep:#B7872F;}
*{box-sizing:border-box;}body{margin:0;background:var(--cream);color:var(--ink);
font-family:'Segoe UI',Arial,sans-serif;font-size:14px;}
.top{background:var(--ink);color:#F7F1E4;display:flex;align-items:center;gap:12px;padding:14px 22px;}
.top .badge{border:1px solid var(--mdeep);color:var(--mdeep);padding:4px 10px;font-size:11px;font-weight:700;letter-spacing:.14em;}
.wrap{max-width:720px;margin:0 auto;padding:26px 18px 70px;}
.card{background:#fffdf8;border:1px solid var(--rule);border-radius:10px;padding:26px 28px;margin-bottom:16px;}
h1{font-family:Georgia,serif;font-size:26px;margin:0 0 6px;}
.eyebrow{font-size:11px;letter-spacing:.18em;color:var(--mdeep);text-transform:uppercase;font-weight:700;}
.total{font-family:Georgia,serif;font-size:38px;margin:10px 0 2px;}
.sub{color:var(--grey);font-size:12.5px;}
.btn{display:inline-block;border:none;border-radius:6px;padding:12px 20px;font-size:14px;font-weight:700;
cursor:pointer;font-family:inherit;text-decoration:none;}
.btn.primary{background:var(--mdeep);color:#fff;}
.btn.dark{background:var(--ink);color:#fff;}
label{display:block;font-weight:600;font-size:12px;margin:12px 0 4px;}
input[type=text],input[type=email]{width:100%;padding:10px;border:1px solid var(--rule);border-radius:6px;font-size:14px;font-family:inherit;}
.chk{display:flex;gap:8px;align-items:flex-start;margin:14px 0;font-size:12.5px;color:var(--ink);}
.status{font-size:13px;margin-top:10px;min-height:18px;color:var(--grey);}
.status.ok{color:#2f8e4e;font-weight:700;}
.done{background:#eaf6ee;border:1px solid #bfe3cb;border-radius:8px;padding:16px 18px;font-size:14px;}
.fine{font-size:11px;color:var(--grey);margin-top:14px;line-height:1.6;}
.item{display:flex;gap:14px;border:1px solid var(--rule);border-radius:8px;padding:14px;margin-top:12px;background:#fff;}
.item img{width:110px;height:82px;object-fit:cover;border-radius:6px;flex:none;}
.item .nm{font-weight:700;font-size:15px;}
.item .spec{font-size:11px;color:var(--mdeep);font-weight:700;letter-spacing:.04em;text-transform:uppercase;margin:2px 0;}
.item .desc{font-size:12.5px;color:var(--grey);margin:4px 0 6px;line-height:1.5;}
.item ul{margin:0;padding-left:16px;font-size:12px;color:var(--ink);}
.item li{margin-bottom:2px;}
.item .pr{font-family:Georgia,serif;font-size:17px;margin-top:6px;}
.lines{border:1px solid var(--rule);border-radius:8px;overflow:hidden;margin-top:12px;background:#fff;}
.lhead{display:flex;justify-content:space-between;background:var(--ink);color:#fff;padding:9px 14px;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;}
.line{display:flex;justify-content:space-between;gap:12px;padding:10px 14px;border-top:1px solid var(--rule);font-size:13px;}
.line:first-of-type{border-top:none;}
.line .desc{font-size:11px;color:var(--grey);margin-top:1px;}
.line .amt{font-weight:600;white-space:nowrap;}
.ltotal{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:12px 14px;border-top:2px solid var(--ink);background:var(--cream);}
.ltotal .nm{font-weight:700;}
.ltotal .inc{font-family:Georgia,serif;font-size:20px;}
.ltotal .ex{font-size:11px;color:var(--grey);}
.sched{display:flex;gap:6px;margin-top:10px;}
.sched>div{flex:1;background:var(--ink);border-radius:6px;text-align:center;padding:9px 2px 7px;}
.sched .pct{color:var(--mdeep);font-weight:800;font-size:15px;}
.sched .lab{color:#fff;font-size:9.5px;margin-top:1px;}
.sect{font-size:12px;font-weight:800;color:var(--mdeep);letter-spacing:.08em;text-transform:uppercase;margin:22px 0 4px;}
.incl{font-size:12.5px;line-height:1.6;border:1px solid var(--rule);border-radius:8px;padding:12px 14px;background:#fff;margin-top:8px;}
.rangecard{border:1px solid var(--rule);border-radius:8px;padding:14px;margin-top:12px;background:#fff;}
.rangecard .nm{font-weight:700;font-size:15px;}
.rangecard .pr{font-family:Georgia,serif;font-size:19px;margin:4px 0 1px;}
.rangecard .sub2{font-size:11px;color:var(--grey);}
@media (max-width:520px){.item{flex-direction:column;}.item img{width:100%;height:150px;}.sched{flex-wrap:wrap;}.sched>div{min-width:30%;}}
"""


def _accept_shell(inner):
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<meta name='robots' content='noindex,nofollow'>"
            "<title>Endure Decks — Your quote</title><style>" + _ACCEPT_CSS + "</style></head><body>"
            "<div class='top'><span class='badge'>ENDURE DECKS</span>"
            "<span style='font-family:Georgia,serif;font-size:17px;'>Built for the long horizon</span></div>"
            "<div class='wrap'>" + inner + "</div></body></html>")


def _fmt_inc(v):
    try:
        return "${:,.0f}".format(float(v))
    except Exception:  # noqa: BLE001
        return ""


def _esc_h(s):
    return str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _img_src_web(img):
    s = str(img or "")
    if not s:
        return ""
    return s if s.startswith("data:") else "/assets/" + s


def _quote_body_html(jd):
    """The quote itself, as scrollable HTML — same data the PDF renders from."""
    fx = jd.get("fixed") or {}
    parts = []
    fp = fx.get("your_project") or {}
    items = render_proposal._your_project_items(fp)
    if fp.get("summary_line"):
        parts.append(f"<p style='font-size:13.5px;color:var(--grey);line-height:1.6;margin:6px 0 0;'>{_esc_h(fp['summary_line'])}</p>")
    if items:
        parts.append("<div class='sect'>Your project</div>")
        for it in items:
            img = _img_src_web(it.get("image"))
            img_html = f"<img src='{img}' alt=''>" if img else ""
            bul = "".join(f"<li>{_esc_h(b)}</li>" for b in it.get("bullets", []) if b)
            price = ""
            if it.get("price_inc_gst") not in (None, ""):
                price = f"<div class='pr'>{_fmt_inc(it['price_inc_gst'])} <span style='font-size:11px;color:var(--grey);'>inc GST</span></div>"
            parts.append(
                f"<div class='item'>{img_html}<div>"
                f"<div class='nm'>{_esc_h(it.get('label') or 'Endure Install')}</div>"
                f"<div class='spec'>{_esc_h(it.get('spec_label'))}</div>"
                f"<div class='desc'>{_esc_h(it.get('description'))}</div>"
                f"<ul>{bul}</ul>{price}</div></div>")
    lines = fx.get("cost_lines") or []
    if lines:
        parts.append("<div class='sect'>Cost breakdown</div><div class='lines'>")
        parts.append(f"<div class='lhead'><div>{_esc_h(fx.get('package_label', 'Your project'))}</div><div>inc GST</div></div>")
        for l in lines:
            desc = f"<div class='desc'>{_esc_h(l.get('desc'))}</div>" if l.get("desc") else ""
            parts.append(f"<div class='line'><div>{_esc_h(l.get('label'))}{desc}</div>"
                         f"<div class='amt'>{_fmt_inc(l.get('amount'))}</div></div>")
        parts.append(f"<div class='ltotal'><div><div class='nm'>Total project price</div>"
                     f"<div class='ex'>{_fmt_inc(fx.get('subtotal_ex_gst'))} ex GST</div></div>"
                     f"<div class='inc'>{_fmt_inc(fx.get('total_inc_gst'))} inc GST</div></div></div>")
    if fx.get("included") or fx.get("excluded"):
        parts.append("<div class='sect'>What's included &middot; what's not</div><div class='incl'>")
        if fx.get("included"):
            parts.append(f"<p style='margin:0 0 6px;'><strong>Included:</strong> {_esc_h(fx['included'])}</p>")
        if fx.get("excluded"):
            parts.append(f"<p style='margin:0;'><strong>Not included:</strong> {_esc_h(fx['excluded'])}</p>")
        parts.append("</div>")
    sched = (jd.get("project_run") or {}).get("payment_schedule") or []
    if sched:
        parts.append("<div class='sect'>Payment schedule</div><div class='sched'>")
        for p in sched:
            parts.append(f"<div><div class='pct'>{_esc_h(p.get('pct'))}%</div><div class='lab'>{_esc_h(p.get('label'))}</div></div>")
        parts.append("</div>")
    parts.append("<p class='fine'>Built to the Endure 20-Year Standard. The attached/linked PDF is the formal document; this page presents the same quote for easy reading.</p>")
    return "".join(parts)


def _range_body_html(jd):
    r = jd.get("range") or {}
    parts = []
    for opt in r.get("options") or []:
        parts.append(
            f"<div class='rangecard'><div class='nm'>{_esc_h(opt.get('label'))}</div>"
            f"<div class='pr'>{_fmt_inc(opt.get('price_low_inc_gst'))} &ndash; {_fmt_inc(opt.get('price_high_inc_gst'))}</div>"
            f"<div class='sub2'>inc GST &middot; {_esc_h(opt.get('size_label'))}</div>"
            f"<div class='desc' style='font-size:12.5px;color:var(--grey);margin-top:6px;line-height:1.5;'>{_esc_h(opt.get('spec_line'))}</div></div>")
    return "".join(parts)


def accept_page(token):
    if not accept_links_enabled():
        return _accept_shell("<div class='card'><h1>Online acceptance isn't available right now</h1>"
                             "<p class='sub'>Reply to the email your quote came with and we'll take it from there.</p></div>")
    try:
        drive, fid, jd, acc, pdf_id = _read_job_folder(token)
    except Exception:  # noqa: BLE001
        return _accept_shell("<div class='card'><h1>This link isn't valid</h1>"
                             "<p class='sub'>Check the link in your email, or reply to it and we'll resend.</p></div>")
    client = _esc_h(jd.get("client_name") or "your project")
    pdf_btn = f"<a class='btn dark' href='/q/{token}/pdf' target='_blank'>Open the PDF version</a>"
    if jd.get("mode") != "fixed":
        inner = (f"<div class='card'><div class='eyebrow'>Proposal</div><h1>Proposal for {client}</h1>"
                 "<p class='sub'>A priced range and the options for your project — the full detail is in the document. "
                 "The next step is agreeing the option and scope together: reply to the email this came with.</p>"
                 + _range_body_html(jd) +
                 "<div style='margin-top:16px;'>" + pdf_btn + "</div></div>")
        return _accept_shell(inner)
    total = _fmt_inc((jd.get("fixed") or {}).get("total_inc_gst"))
    doc_html = _quote_body_html(jd)
    head_card = (f"<div class='card'><div class='eyebrow'>Fixed quote</div><h1>Quote for {client}</h1>"
                 f"<div class='total'>{total}</div><div class='sub'>inc GST &middot; fixed price &middot; "
                 "everything below is the full quote</div>"
                 f"<div style='margin-top:12px;'>{pdf_btn}</div>{doc_html}</div>")
    if acc:
        inner = (head_card +
                 f"<div class='card'><div class='done'><strong>Accepted</strong> by {_esc_h(acc.get('name'))} "
                 f"on {_esc_h(str(acc.get('ts'))[:10])}. We'll be in touch about the deposit and start date &mdash; "
                 "nothing more to do here.</div></div>")
        return _accept_shell(inner)
    form = (
        "<label>Your full name</label><input type='text' id='accName' placeholder='Full name'>"
        "<label>Your email</label><input type='email' id='accEmail' placeholder='you@email.com'>"
        "<div class='chk'><input type='checkbox' id='accChk' style='margin-top:2px;'>"
        "<span>I accept this quote and the terms set out in the quote document.</span></div>"
        "<button class='btn primary' id='accBtn'>Accept this quote</button>"
        "<div class='status' id='accSt'></div>"
        "<div class='fine'>Accepting records your name, the date and time. It doesn't take any payment &mdash; "
        "we'll confirm the deposit and start date with you by email. Questions first? Just reply to the "
        "email this quote came with.</div>")
    js = """<script>
document.getElementById('accBtn').onclick = async function(){
  var st = document.getElementById('accSt');
  var name = document.getElementById('accName').value.trim();
  var email = document.getElementById('accEmail').value.trim();
  if(!name){ st.textContent='Please enter your full name.'; return; }
  if(!document.getElementById('accChk').checked){ st.textContent='Please tick the acceptance box.'; return; }
  st.className='status'; st.textContent='Recording your acceptance\u2026';
  this.disabled = true;
  try{
    var res = await fetch(location.pathname + '/accept', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:name, email:email})});
    var j = await res.json();
    if(j && j.ok){ st.className='status ok';
      st.textContent='Accepted \u2014 thank you! A confirmation email is on its way.';
      setTimeout(function(){ location.reload(); }, 1800); return; }
    st.textContent = (j && j.error) || 'Something went wrong \u2014 reply to the email instead.';
    this.disabled = false;
  }catch(e){ st.textContent='Something went wrong \u2014 reply to the email instead.'; this.disabled = false; }
};
</script>"""
    inner = (head_card +
             "<div class='card'><div class='eyebrow'>Ready to go ahead?</div>" + form + "</div>" + js)
    return _accept_shell(inner)


def _log_mark_accepted(client_name):
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    sheets = build("sheets", "v4", credentials=_creds(), cache_discovery=False)
    sid = _quote_log_id(drive, sheets)
    vals = sheets.spreadsheets().values()
    clients = vals.get(spreadsheetId=sid, range="Log!B:B").execute().get("values", [])
    for i, r in enumerate(clients):
        if r and r[0] == client_name and i > 0:
            vals.update(spreadsheetId=sid, range=f"Log!M{i + 1}", valueInputOption="USER_ENTERED",
                        body={"values": [["Accepted"]]}).execute()
            return True
    return False


def record_acceptance(token, body, ip, ua):
    if not accept_links_enabled():
        return {"ok": False, "error": "Online acceptance isn't available right now — reply to the email instead."}
    try:
        drive, fid, jd, acc, _pdf = _read_job_folder(token)
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "This link isn't valid."}
    if jd.get("mode") != "fixed":
        return {"ok": False, "error": "This document is a proposal — reply to the email to take the next step."}
    if acc:
        return {"ok": True, "already": True}
    from datetime import datetime, timezone
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    record = {"name": name, "email": email, "ts": datetime.now(timezone.utc).isoformat(),
              "ip": ip or "", "user_agent": (ua or "")[:300],
              "total_inc_gst": (jd.get("fixed") or {}).get("total_inc_gst")}
    media = MediaIoBaseUpload(io.BytesIO(json.dumps(record, indent=2).encode()), mimetype="application/json")
    _upsert_file(drive, fid, "acceptance.json", media)
    try:  # best-effort — acceptance stands even if the log write hiccups
        _log_mark_accepted(jd.get("client_name") or "")
    except Exception:  # noqa: BLE001
        pass
    try:  # confirmation emails, best-effort
        client = jd.get("client_name") or "Client"
        total = _fmt_inc((jd.get("fixed") or {}).get("total_inc_gst"))
        msg = EmailMessage()
        msg["From"] = f"{SEND_NAME} <{SEND_USER}>"
        msg["To"] = email or SEND_USER
        bcc = [x for x in [SEND_BCC, SEND_USER if email else ""] if x]
        if bcc:
            msg["Bcc"] = ", ".join(bcc)
        msg["Subject"] = f"Quote accepted — {client}"
        msg.set_content(
            f"Hi {name.split(' ')[0]},\n\n"
            f"Thanks — your quote ({total} inc GST) is accepted as of today.\n\n"
            "Next step: we'll be in touch shortly to confirm the deposit and lock in your start date. "
            "Nothing more you need to do right now.\n\n"
            f"{SEND_NAME}\nEndure Decks — built for the long horizon")
        if SEND_USER:
            _send_message(msg)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


def accept_pdf(token):
    drive, fid, jd, acc, pdf_id = _read_job_folder(token)
    client = (jd.get("client_name") or "Client").strip()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", client).strip("_") or "Client"
    doc_name = f"{slug}_{'Proposal' if jd.get('mode') == 'range' else 'Quote'}.pdf"
    if pdf_id:  # the PDF filed at save time — instant, and identical to what was sent
        pdf_bytes = drive.files().get_media(fileId=pdf_id).execute()
    else:  # fallback: fresh render
        with tempfile.TemporaryDirectory() as td:
            out_pdf = os.path.join(td, doc_name)
            render_proposal.render(jd, out_pdf)
            pdf_bytes = open(out_pdf, "rb").read()
    return Response(pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{doc_name}"'})


# ── send the quote/proposal from the builder (same path as the ballpark) ─────
def send_quote(body):
    if not SEND_USER:
        return {"ok": False, "error": "Email sending isn't configured — add SEND_EMAIL_USER in Railway."}
    to = (body.get("to") or "").strip()
    if not to or "@" not in to:
        return {"ok": False, "error": "recipient email required"}
    jd = body.get("job_data")
    if not jd:
        return {"ok": False, "error": "job_data required"}
    client = (jd.get("client_name") or "Client").strip()
    kind = "Proposal" if jd.get("mode") == "range" else "Quote"
    subject = (body.get("subject") or f"Your {kind.lower()} from Endure Decks").strip()
    text = body.get("body") or ""
    msg = EmailMessage()
    msg["From"] = f"{SEND_NAME} <{SEND_USER}>"
    msg["To"] = to
    msg["Subject"] = subject
    if SEND_BCC:
        msg["Bcc"] = SEND_BCC
    msg.set_content(text)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", client).strip("_") or "Client"
    pdf_name = f"{slug}_{kind}.pdf"
    with tempfile.TemporaryDirectory() as td:
        out_pdf = os.path.join(td, pdf_name)
        render_proposal.render(jd, out_pdf)
        msg.add_attachment(open(out_pdf, "rb").read(), maintype="application",
                           subtype="pdf", filename=pdf_name)
    try:
        via = _send_message(msg)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{e}. Fallback: Generate PDF and send it from Gmail yourself."}
    return {"ok": True, "from": SEND_USER, "attached": True, "via": via}


# ── render internal (PM-only Job Budget — never for clients) ────────────────
def _internal_pdf_bytes(internal, job_data):
    client = (internal.get("client_name") or "Client").strip()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", client).strip("_") or "Client"
    with tempfile.TemporaryDirectory() as td:
        out_pdf = os.path.join(td, "internal.pdf")
        render_internal.render(internal, job_data, out_pdf)
        return open(out_pdf, "rb").read(), f"INTERNAL_Job_Budget_{slug}.pdf"


def render_internal_pdf(body):
    """POST {action:'render_internal', internal:{...}, job_data:{...}} → the
    red-banner Job Budget PDF. job_data is optional but enables the totals
    reconciliation check and the options-on-the-table line."""
    internal = body.get("internal")
    if not internal:
        return bad("internal required")
    pdf_bytes, pdf_name = _internal_pdf_bytes(internal, body.get("job_data"))
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{pdf_name}"'})


# ── static: the builder page ────────────────────────────────────────────────
@app.get("/q/{token}")
async def quote_accept_page(token: str):
    return HTMLResponse(accept_page(token))


@app.get("/q/{token}/pdf")
async def quote_accept_pdf(token: str):
    if not accept_links_enabled():
        return HTMLResponse(accept_page(token))
    try:
        return accept_pdf(token)
    except Exception:  # noqa: BLE001
        return HTMLResponse(accept_page(token))


@app.post("/q/{token}/accept")
async def quote_accept_post(token: str, request: Request):
    try:
        body = json.loads((await request.body()) or b"{}")
    except Exception:  # noqa: BLE001
        body = {}
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
         (request.client.host if request.client else "")
    return JSONResponse(record_acceptance(token, body, ip, request.headers.get("user-agent")))


@app.get("/ballpark")
async def ballpark():
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "ballpark.html"))


@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "index.html"))

app.mount("/assets", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")), name="assets")
