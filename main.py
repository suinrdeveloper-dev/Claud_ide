import os
import tempfile
import zipfile
import asyncio
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List

from fastapi import (
    FastAPI, Request, HTTPException, WebSocket,
    WebSocketDisconnect, UploadFile, Form
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from supabase import create_client, Client
from git import Repo, GitCommandError
import aiofiles
import logging

# ==============================
# SAFE BOOTSTRAP
# ==============================
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("/tmp/sessions", exist_ok=True)

# ==============================
# LOGGING (CI + LOCAL)
# ==============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("server.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("webide")

# ==============================
# APP INIT
# ==============================
app = FastAPI(title="Mobile-Optimized Web IDE", debug=True)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==============================
# REQUEST LOGGER (MOST IMPORTANT)
# ==============================
@app.middleware("http")
async def request_logger(request: Request, call_next):
    start = time.time()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception as e:
        logger.exception(
            f"❌ ERROR | {request.method} {request.url.path}"
        )
        raise
    finally:
        duration = round(time.time() - start, 3)
        logger.info(
            f"{request.method} {request.url.path} | "
            f"Status={status if 'status' in locals() else 500} | "
            f"Time={duration}s"
        )
    return response

# ==============================
# SUPABASE (OPTIONAL)
# ==============================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase: Client | None = (
    create_client(SUPABASE_URL, SUPABASE_KEY)
    if SUPABASE_URL and SUPABASE_KEY else None
)

# ==============================
# HELPERS
# ==============================
def sanitize_path(path: str) -> str:
    normalized = os.path.normpath(path)
    if normalized.startswith("..") or "/.." in normalized:
        raise ValueError("Directory traversal detected")
    return normalized

def get_session_path(secret_key: str, project_name: str) -> str:
    return f"/tmp/sessions/{secret_key}_{project_name}"

async def delete_old_projects():
    if supabase:
        try:
            logger.info("Supabase cleanup started")
            supabase.rpc("delete_old_projects").execute()
        except Exception as e:
            logger.warning(f"Supabase cleanup skipped: {e}")

# ==============================
# ROUTES
# ==============================
@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    logger.info("Landing page opened")
    return templates.TemplateResponse("index.html", {
        "request": request,
        "page": "login"
    })

@app.post("/login")
async def login(
    secret_key: str = Form(...),
    project_name: str = Form(...)
):
    logger.info(f"Login attempt | project={project_name}")

    if not secret_key.isdigit() or len(secret_key) != 10:
        logger.warning("Invalid secret key")
        raise HTTPException(400, "Secret key must be 10 digits")

    await delete_old_projects()

    return {
        "success": True,
        "redirect": f"/dashboard?secret_key={secret_key}&project_name={project_name}"
    }

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    secret_key = request.query_params.get("secret_key")
    project_name = request.query_params.get("project_name")

    if not secret_key or not project_name:
        logger.error("Dashboard missing params")
        raise HTTPException(400, "Missing parameters")

    logger.info(f"Dashboard opened | {project_name}")
    return templates.TemplateResponse("index.html", {
        "request": request,
        "page": "dashboard",
        "secret_key": secret_key,
        "project_name": project_name
    })

# ==============================
# ZIP UPLOAD
# ==============================
@app.post("/upload_zip")
async def upload_zip(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    zip_file: UploadFile = Form(...)
):
    logger.info(f"ZIP upload started | {project_name}")

    session_path = get_session_path(secret_key, project_name)
    os.makedirs(session_path, exist_ok=True)

    temp_zip = Path(tempfile.gettempdir()) / zip_file.filename

    async with aiofiles.open(temp_zip, "wb") as f:
        await f.write(await zip_file.read())

    try:
        with zipfile.ZipFile(temp_zip) as z:
            z.extractall(session_path)
        logger.info("ZIP extracted successfully")
    except Exception:
        logger.exception("ZIP extraction failed")
        raise HTTPException(500, "Invalid ZIP")
    finally:
        temp_zip.unlink(missing_ok=True)

    return {"success": True}

# ==============================
# GIT CLONE
# ==============================
@app.post("/clone_repo")
async def clone_repo(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    repo_url: str = Form(...),
    github_token: str = Form(None)
):
    logger.info(f"Git clone started | {repo_url}")

    session_path = get_session_path(secret_key, project_name)
    os.makedirs(session_path, exist_ok=True)

    if github_token and repo_url.startswith("https://"):
        repo_url = repo_url.replace("https://", f"https://{github_token}@")

    try:
        Repo.clone_from(repo_url, session_path, depth=1)
        logger.info("Git clone success")
        return {"success": True}
    except GitCommandError as e:
        logger.exception("Git clone failed")
        raise HTTPException(500, str(e))

# ==============================
# FILE APIs
# ==============================
@app.get("/api/file")
async def read_file(secret_key: str, project_name: str, path: str):
    logger.info(f"Read file | {path}")

    full = os.path.join(
        get_session_path(secret_key, project_name),
        sanitize_path(path)
    )
    async with aiofiles.open(full, "r", encoding="utf-8") as f:
        return {"content": await f.read()}

@app.post("/api/save")
async def save_file(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    path: str = Form(...),
    content: str = Form(...)
):
    logger.info(f"Save file | {path}")

    full = os.path.join(
        get_session_path(secret_key, project_name),
        sanitize_path(path)
    )
    os.makedirs(os.path.dirname(full), exist_ok=True)

    async with aiofiles.open(full, "w", encoding="utf-8") as f:
        await f.write(content)

    return {"success": True}

# ==============================
# WEBSOCKET
# ==============================
@app.websocket("/ws/terminal")
async def ws_terminal(ws: WebSocket):
    await ws.accept()
    logger.info("WebSocket connected")

    try:
        while True:
            data = await ws.receive_text()
            logger.info(f"WS message: {data}")
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")

# ==============================
# GIT COMMIT
# ==============================
@app.post("/api/git_commit")
async def git_commit(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    message: str = Form("update")
):
    logger.info("Git commit requested")

    repo_path = get_session_path(secret_key, project_name)
    repo = Repo(repo_path)

    repo.git.add(A=True)
    if repo.is_dirty(untracked_files=True):
        repo.index.commit(message)
        logger.info("Git commit created")
    else:
        logger.info("Nothing to commit")

    return {"success": True}

# ==============================
# ENTRYPOINT
# ==============================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
