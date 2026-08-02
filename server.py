"""
Endure Quote Builder — single-host server.
Serves the builder page, the rates/save API (port of the old api/endure.js),
and the real WeasyPrint renderer (scripts/render_proposal.py) so the PDF a
client receives is pixel-identical to what the tool has always produced.

Run local:  uvicorn server:app --reload
Deploy:     Dockerfile (Railway / Render / Fly) → quote.enduredecks.com.au
"""
import base64
import io
import json
import os
import re
import smtplib
import sys
import tempfile
from email.message import EmailMessage

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
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
    return {"ok": True, "folderUrl": folder.get("webViewLink"), "folderId": folder["id"], "logged": logged}


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
@app.get("/ballpark")
async def ballpark():
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "ballpark.html"))


@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "index.html"))

app.mount("/assets", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")), name="assets")
