import os
import tempfile
import zipfile
import shutil
import asyncio
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import (
    FastAPI, Request, HTTPException,
    WebSocket, WebSocketDisconnect,
    UploadFile, Form
)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from supabase import create_client, Client
from git import Repo, GitCommandError
import git
import aiofiles
import logging

# -------------------- APP INIT --------------------

app = FastAPI(title="Mobile-Optimized Web IDE", debug=True)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# -------------------- LOGGING --------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("web-ide")

# -------------------- SUPABASE --------------------

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase: Optional[Client] = (
    create_client(SUPABASE_URL, SUPABASE_KEY)
    if SUPABASE_URL and SUPABASE_KEY else None
)

# -------------------- WEBSOCKET STATE --------------------

websocket_sessions: Dict[str, List[WebSocket]] = {}

# -------------------- HELPERS --------------------

BASE_SESSION_DIR = Path("/tmp/sessions")

def validate_secret(secret_key: str):
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Invalid secret key")

def get_session_path(secret_key: str, project_name: str) -> Path:
    path = BASE_SESSION_DIR / f"{secret_key}_{project_name}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()

def safe_join(base: Path, target: str) -> Path:
    resolved = (base / target).resolve()
    if not resolved.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Invalid path")
    return resolved

def safe_extract_zip(zip_path: Path, dest: Path):
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            member_path = safe_join(dest, member)
            if member.endswith("/"):
                member_path.mkdir(parents=True, exist_ok=True)
            else:
                member_path.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, open(member_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)

async def broadcast(session_id: str, message: str):
    sockets = websocket_sessions.get(session_id, [])
    dead = []
    for ws in sockets:
        try:
            await ws.send_text(message)
        except WebSocketDisconnect:
            dead.append(ws)
    for ws in dead:
        sockets.remove(ws)

# -------------------- ROUTES --------------------

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "page": "login"})

@app.post("/login")
async def login(
    secret_key: str = Form(...),
    project_name: str = Form(...)
):
    validate_secret(secret_key)

    if supabase:
        asyncio.create_task(delete_old_projects())

    return {
        "success": True,
        "redirect": f"/dashboard?secret_key={secret_key}&project_name={project_name}"
    }

@app.get("/dashboard")
async def dashboard(request: Request):
    secret_key = request.query_params.get("secret_key")
    project_name = request.query_params.get("project_name")

    validate_secret(secret_key)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "page": "dashboard",
            "secret_key": secret_key,
            "project_name": project_name
        }
    )

@app.post("/upload_zip")
async def upload_zip(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    zip_file: UploadFile = None
):
    validate_secret(secret_key)
    if not zip_file:
        raise HTTPException(400, "ZIP missing")

    session_path = get_session_path(secret_key, project_name)
    temp_zip = Path(tempfile.gettempdir()) / zip_file.filename

    async with aiofiles.open(temp_zip, "wb") as f:
        await f.write(await zip_file.read())

    try:
        safe_extract_zip(temp_zip, session_path)
    finally:
        temp_zip.unlink(missing_ok=True)

    return {"success": True}

@app.post("/clone_repo")
async def clone_repo(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    repo_url: str = Form(...),
    github_token: str = Form(None)
):
    validate_secret(secret_key)
    session_path = get_session_path(secret_key, project_name)

    if session_path.exists():
        shutil.rmtree(session_path)
    session_path.mkdir()

    if github_token and repo_url.startswith("https://"):
        repo_url = repo_url.replace("https://", f"https://{github_token}@")

    try:
        Repo.clone_from(repo_url, session_path, depth=1)
    except GitCommandError as e:
        raise HTTPException(500, str(e))

    return {"success": True}

@app.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):
    await websocket.accept()
    params = websocket.query_params
    session_id = f"{params.get('secret_key')}_{params.get('project_name')}"

    websocket_sessions.setdefault(session_id, []).append(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_sessions[session_id].remove(websocket)

@app.post("/api/git_commit")
async def git_commit(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    commit_message: str = Form("Update from Web IDE"),
    github_token: str = Form(None)
):
    validate_secret(secret_key)
    session_path = get_session_path(secret_key, project_name)
    session_id = f"{secret_key}_{project_name}"

    repo = Repo(session_path)

    if not repo.is_dirty(untracked_files=True):
        await broadcast(session_id, "No changes to commit")
        return {"success": True}

    repo.git.add(A=True)
    repo.index.commit(commit_message)

    if github_token:
        origin = repo.remote()
        origin.push()

    await broadcast(session_id, "Commit successful")
    return {"success": True}

# -------------------- BACKGROUND --------------------

async def delete_old_projects():
    try:
        supabase.rpc("delete_old_projects").execute()
    except Exception as e:
        logger.warning(f"Cleanup failed: {e}")

# -------------------- RUN --------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)