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
import sys
import tempfile

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
SHEET = os.environ.get("RATE_SHEET_ID")
JOBS = os.environ.get("INCOMING_JOBS_ID")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
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
        if action == "img_lib_list":
            return JSONResponse(img_lib_list())
        if action == "img_lib_get":
            return JSONResponse(img_lib_get(body))
        if action == "img_lib_put":
            return JSONResponse(img_lib_put(body))
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
           "labour_day": None, "gp_target": None}
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
def save_job(body):
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    folder = drive.files().create(
        body={"name": body.get("folder_name"), "mimeType": "application/vnd.google-apps.folder",
              "parents": [JOBS]},
        fields="id, webViewLink", supportsAllDrives=True,
    ).execute()

    def put_json(name, obj):
        media = MediaIoBaseUpload(io.BytesIO(json.dumps(obj, indent=2).encode()), mimetype="application/json")
        drive.files().create(body={"name": name, "parents": [folder["id"]]},
                             media_body=media, supportsAllDrives=True).execute()

    put_json("job-data.json", body.get("job_data"))
    if body.get("internal"):
        put_json("internal-costing.json", body["internal"])
        try:  # also file the rendered Job Budget PDF; the save still succeeds without it
            pdf_bytes, pdf_name = _internal_pdf_bytes(body["internal"], body.get("job_data"))
            media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")
            drive.files().create(body={"name": pdf_name, "parents": [folder["id"]]},
                                 media_body=media, supportsAllDrives=True).execute()
        except Exception:  # noqa: BLE001
            pass
    for a in body.get("attachments") or []:
        media = MediaIoBaseUpload(io.BytesIO(base64.b64decode(a["b64"])), mimetype=a.get("mime") or "image/jpeg")
        drive.files().create(body={"name": a["name"], "parents": [folder["id"]]},
                             media_body=media, supportsAllDrives=True).execute()
    return {"ok": True, "folderUrl": folder.get("webViewLink"), "folderId": folder["id"]}


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
@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "index.html"))

app.mount("/assets", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")), name="assets")
