import os
import tempfile
import zipfile
import shutil
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from git import Repo, GitCommandError
import git
import aiofiles
import logging

# Initialize FastAPI app
app = FastAPI(title="Mobile-Optimized Web IDE", debug=True)

# Configure templates
templates = Jinja2Templates(directory="templates")

# Configure static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Session storage for WebSocket connections
websocket_sessions: Dict[str, List[WebSocket]] = {}  # Track connections by session

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def sanitize_path(path: str) -> str:
    """Sanitize file paths to prevent directory traversal attacks"""
    # Normalize the path
    normalized = os.path.normpath(path)
    # Ensure it doesn't start with parent directory references
    if normalized.startswith("..") or "/.." in normalized:
        raise ValueError("Invalid path: Directory traversal detected")
    return normalized

def get_session_path(secret_key: str, project_name: str) -> str:
    """Get the temporary session path for a user's project"""
    return f"/tmp/sessions/{secret_key}_{project_name}"

@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Landing page with login form"""
    return templates.TemplateResponse("index.html", {"request": request, "page": "login"})

@app.post("/login")
async def login(
    request: Request,
    secret_key: str = Form(...),
    project_name: str = Form(...)
):
    """Handle user login and session setup"""
    # Validate secret key format (10 digits)
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Secret key must be 10 digits")
    
    # For now, just return success - in a real implementation, you'd connect to Supabase
    return JSONResponse({
        "success": True,
        "redirect": f"/dashboard?secret_key={secret_key}&project_name={project_name}"
    })

@app.get("/dashboard")
async def dashboard(request: Request):
    """Dashboard page for project setup"""
    secret_key = request.query_params.get("secret_key")
    project_name = request.query_params.get("project_name")
    
    if not secret_key or not project_name:
        raise HTTPException(status_code=400, detail="Missing secret key or project name")
    
    # Validate secret key format
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Invalid secret key")
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "page": "dashboard",
        "secret_key": secret_key,
        "project_name": project_name
    })

@app.post("/upload_zip")
async def upload_zip(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    zip_file: UploadFile = None
):
    """Handle ZIP file upload and extraction"""
    if not zip_file:
        raise HTTPException(status_code=400, detail="No file uploaded")
    
    # Validate secret key
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Invalid secret key")
    
    session_path = get_session_path(secret_key, project_name)
    os.makedirs(session_path, exist_ok=True)
    
    # Save uploaded file temporarily
    temp_file_path = os.path.join(tempfile.gettempdir(), f"{secret_key}_{zip_file.filename}")
    async with aiofiles.open(temp_file_path, 'wb') as temp_file:
        content = await zip_file.read()
        await temp_file.write(content)
    
    try:
        # Extract ZIP file
        with zipfile.ZipFile(temp_file_path, 'r') as zip_ref:
            zip_ref.extractall(session_path)
        
        # Clean up temp file
        os.remove(temp_file_path)
        
        return JSONResponse({
            "success": True,
            "message": "ZIP file uploaded and extracted successfully",
            "session_path": session_path
        })
    except Exception as e:
        # Clean up temp file even if extraction fails
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(status_code=500, detail=f"Failed to extract ZIP file: {str(e)}")

@app.post("/clone_repo")
async def clone_repo(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    repo_url: str = Form(...),
    github_token: str = Form(None)
):
    """Clone a GitHub repository to the session directory"""
    # Validate secret key
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Invalid secret key")
    
    session_path = get_session_path(secret_key, project_name)
    os.makedirs(session_path, exist_ok=True)
    
    try:
        # If token is provided, use it for authentication
        if github_token:
            # Replace https:// with token authentication
            if repo_url.startswith("https://"):
                repo_url = repo_url.replace("https://", f"https://{github_token}@")
        
        # Clone the repository
        repo = git.Repo.clone_from(repo_url, session_path, depth=1)
        
        return JSONResponse({
            "success": True,
            "message": "Repository cloned successfully",
            "session_path": session_path
        })
    except GitCommandError as e:
        raise HTTPException(status_code=500, detail=f"Git error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clone repository: {str(e)}")

@app.get("/ide")
async def ide_interface(request: Request):
    """IDE interface page"""
    secret_key = request.query_params.get("secret_key")
    project_name = request.query_params.get("project_name")
    
    if not secret_key or not project_name:
        raise HTTPException(status_code=400, detail="Missing secret key or project name")
    
    # Validate secret key
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Invalid secret key")
    
    session_path = get_session_path(secret_key, project_name)
    if not os.path.exists(session_path):
        raise HTTPException(status_code=404, detail="Project session not found")
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "page": "ide",
        "secret_key": secret_key,
        "project_name": project_name,
        "session_path": session_path
    })

@app.get("/api/files")
async def get_file_tree(secret_key: str, project_name: str):
    """Get the file tree for the project"""
    # Validate secret key
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Invalid secret key")
    
    session_path = get_session_path(secret_key, project_name)
    if not os.path.exists(session_path):
        raise HTTPException(status_code=404, detail="Project session not found")
    
    def build_tree(path):
        tree = {"name": os.path.basename(path), "path": path, "type": "directory", "children": []}
        try:
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                if os.path.isdir(item_path):
                    tree["children"].append(build_tree(item_path))
                else:
                    tree["children"].append({
                        "name": item,
                        "path": item_path,
                        "type": "file"
                    })
        except PermissionError:
            pass  # Skip directories we don't have access to
        
        return tree
    
    return build_tree(session_path)

@app.get("/api/file_content")
async def get_file_content(secret_key: str, project_name: str, file_path: str):
    """Get content of a specific file"""
    # Validate secret key
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Invalid secret key")
    
    session_path = get_session_path(secret_key, project_name)
    full_path = os.path.join(session_path, sanitize_path(file_path))
    
    # Ensure the file is within the session directory
    if not full_path.startswith(session_path):
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    if os.path.isdir(full_path):
        raise HTTPException(status_code=400, detail="Path is a directory, not a file")
    
    try:
        async with aiofiles.open(full_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        return {"content": content}
    except UnicodeDecodeError:
        # Handle binary files
        try:
            async with aiofiles.open(full_path, 'rb') as f:
                content = await f.read()
            return {"content": content.decode('latin-1')}  # Fallback encoding for binary
        except:
            raise HTTPException(status_code=500, detail="Could not read file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")

@app.post("/api/save_file")
async def save_file(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    file_path: str = Form(...),
    content: str = Form(...)
):
    """Save content to a specific file"""
    # Validate secret key
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Invalid secret key")
    
    session_path = get_session_path(secret_key, project_name)
    full_path = os.path.join(session_path, sanitize_path(file_path))
    
    # Ensure the file is within the session directory
    if not full_path.startswith(session_path):
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    
    try:
        async with aiofiles.open(full_path, 'w', encoding='utf-8') as f:
            await f.write(content)
        return {"success": True, "message": "File saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving file: {str(e)}")

@app.post("/api/delete_file")
async def delete_file(secret_key: str, project_name: str, file_path: str):
    """Delete a specific file"""
    # Validate secret key
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Invalid secret key")
    
    session_path = get_session_path(secret_key, project_name)
    full_path = os.path.join(session_path, sanitize_path(file_path))
    
    # Ensure the file is within the session directory
    if not full_path.startswith(session_path):
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        os.remove(full_path)
        return {"success": True, "message": "File deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting file: {str(e)}")

@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket, request: Request):
    """WebSocket endpoint for terminal output"""
    await websocket.accept()
    
    # Get session info from query params
    secret_key = request.query_params.get("secret_key", "unknown")
    project_name = request.query_params.get("project_name", "unknown")
    session_id = f"{secret_key}_{project_name}"
    
    # Store connection by session
    if session_id not in websocket_sessions:
        websocket_sessions[session_id] = []
    websocket_sessions[session_id].append(websocket)
    
    try:
        while True:
            # Wait for messages (we won't receive any in this implementation,
            # but keeping connection alive)
            data = await websocket.receive_text()
            # Process any commands if needed
    except WebSocketDisconnect:
        # Remove connection when disconnected
        if session_id in websocket_sessions:
            try:
                websocket_sessions[session_id].remove(websocket)
            except ValueError:
                pass  # Connection already removed

async def broadcast_to_websocket(message: str, secret_key: str = None, project_name: str = None):
    """Broadcast message to WebSocket connections for a specific session"""
    if secret_key and project_name:
        session_id = f"{secret_key}_{project_name}"
        if session_id in websocket_sessions:
            disconnected_clients = []
            for websocket in websocket_sessions[session_id]:
                try:
                    await websocket.send_text(message)
                except WebSocketDisconnect:
                    disconnected_clients.append(websocket)
            
            # Remove disconnected clients
            for websocket in disconnected_clients:
                try:
                    websocket_sessions[session_id].remove(websocket)
                except ValueError:
                    pass
    else:
        # Broadcast to all connections if no specific session
        for session_id, websockets in websocket_sessions.items():
            disconnected_clients = []
            for websocket in websockets:
                try:
                    await websocket.send_text(message)
                except WebSocketDisconnect:
                    disconnected_clients.append(websocket)
            
            # Remove disconnected clients
            for websocket in disconnected_clients:
                try:
                    websocket_sessions[session_id].remove(websocket)
                except ValueError:
                    pass

@app.post("/api/git_commit")
async def git_commit(
    secret_key: str = Form(...),
    project_name: str = Form(...),
    commit_message: str = Form("Update from Web IDE"),
    github_token: str = Form(None)
):
    """Perform git operations: add, commit, and push"""
    # Validate secret key
    if not secret_key.isdigit() or len(secret_key) != 10:
        raise HTTPException(status_code=400, detail="Invalid secret key")
    
    session_path = get_session_path(secret_key, project_name)
    if not os.path.exists(session_path):
        raise HTTPException(status_code=404, detail="Project session not found")
    
    try:
        # Initialize git repo if not already initialized
        if not os.path.exists(os.path.join(session_path, ".git")):
            repo = git.Repo.init(session_path)
        else:
            repo = git.Repo(session_path)

        # Set git config
        with repo.config_writer() as git_config:
            git_config.set_value("user", "name", "Web IDE User")
            git_config.set_value("user", "email", "webide@example.com")
            git_config.release()

        # Add all changes
        repo.git.add(A=True)

        # Commit changes
        if repo.is_dirty(untracked_files=True) or repo.head.commit:
            repo.git.commit(m=commit_message)

            # Push to remote if token is available
            if github_token:
                # Update remote URL with token
                origin = repo.remote(name='origin')
                repo_url = origin.url

                # Replace https:// with token authentication
                if repo_url.startswith("https://"):
                    authenticated_url = repo_url.replace("https://", f"https://{github_token}@")
                    origin.set_url(authenticated_url)

                # Perform push
                origin.push()

                await broadcast_to_websocket(f"Successfully committed and pushed changes: {commit_message}", secret_key, project_name)
            else:
                await broadcast_to_websocket(f"Committed changes locally: {commit_message} (no token for push)", secret_key, project_name)
        else:
            await broadcast_to_websocket("No changes to commit", secret_key, project_name)

        return {"success": True, "message": "Git operations completed successfully"}
    except GitCommandError as e:
        error_msg = f"Git error: {str(e)}"
        await broadcast_to_websocket(error_msg, secret_key, project_name)
        raise HTTPException(status_code=500, detail=error_msg)
    except Exception as e:
        error_msg = f"Error performing git operations: {str(e)}"
        await broadcast_to_websocket(error_msg, secret_key, project_name)
        raise HTTPException(status_code=500, detail=error_msg)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)