import os
import tempfile
import zipfile
import asyncio
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List
from fastapi import (
    FastAPI, Request, HTTPException, WebSocket,
    WebSocketDisconnect, UploadFile, Form
)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from supabase import create_client, Client
from git import Repo, GitCommandError
import git
import aiofiles
import logging

# ==============================
# SAFE DIRECTORY BOOTSTRAP (FIX)
# ==============================
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("/tmp/sessions", exist_ok=True)

# ==============================
# APP INIT
# ==============================
app = FastAPI(title="Mobile-Optimized Web IDE", debug=True)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

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
# LOGGING
# ==============================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webide")

# ==============================
# WEBSOCKET STATE
# ==============================
websocket_sessions: Dict[str, List[WebSocket]] = {}

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
            supabase.rpc("delete_old_projects").execute()
        except Exception as e:
            logger.warning(f"Supabase cleanup skipped: {e}")

# ==============================
# ROUTES
# ==============================
@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "page": "login"
    })

@app.post("/login")
async def login(
    secret_key: str = Form(...),
    project_name: str = Form(...)
):
    if not secret_key.isdigit() or len(secret_key) != 10:
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
        raise HTTPException(400, "Missing parameters")

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
    session_path = get_session_path(secret_key, project_name)
    os.makedirs(session_path, exist_ok=True)

    temp_zip = Path(tempfile.gettempdir()) / zip_file.filename

    async with aiofiles.open(temp_zip, "wb") as f:
        await f.write(await zip_file.read())

    try:
        with zipfile.ZipFile(temp_zip) as z:
            z.extractall(session_path)
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
    session_path = get_session_path(secret_key, project_name)
    os.makedirs(session_path, exist_ok=True)

    if github_token and repo_url.startswith("https://"):
        repo_url = repo_url.replace("https://", f"https://{github_token}@")

    try:
        Repo.clone_from(repo_url, session_path, depth=1)
        return {"success": True}
    except GitCommandError as e:
        raise HTTPException(500, str(e))

# ==============================
# FILE TREE
# ==============================
@app.get("/api/files")
async def file_tree(secret_key: str, project_name: str):
    base = get_session_path(secret_key, project_name)
    if not os.path.exists(base):
        raise HTTPException(404)

    def walk(path):
        node = {"name": os.path.basename(path), "children": []}
        for p in os.listdir(path):
            full = os.path.join(path, p)
            if os.path.isdir(full):
                node["children"].append(walk(full))
            else:
                node["children"].append({"name": p})
        return node

    return walk(base)

# ==============================
# FILE READ / WRITE
# ==============================
@app.get("/api/file")
async def read_file(secret_key: str, project_name: str, path: str):
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
    full = os.path.join(
        get_session_path(secret_key, project_name),
        sanitize_path(path)
    )
    os.makedirs(os.path.dirname(full), exist_ok=True)
    async with aiofiles.open(full, "w", encoding="utf-8") as f:
        await f.write(content)
    return {"success": True}

# ==============================
# WEBSOCKET TERMINAL
# ==============================
@app.websocket("/ws/terminal")
async def ws_terminal(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass

# ==============================
# GIT COMMIT / PUSH
# ==============================
@app.post("/api/git_commit")
async def git_commit(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    message: str = Form("update")
):
    repo_path = get_session_path(secret_key, project_name)
    repo = Repo(repo_path)

    repo.git.add(A=True)
    if repo.is_dirty(untracked_files=True):
        repo.index.commit(message)

    return {"success": True}

# ==============================
# ENTRYPOINT
# ==============================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
