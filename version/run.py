import os
import json
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from packaging.version import Version, InvalidVersion

app = FastAPI(title="Multi-App Version Management API")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(BASE_DIR, "versions.json")
DEFAULT_VERSIONS = {}

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


class VersionUpdateSchema(BaseModel):
    app_name: str = Field(..., example="SkyboxAuto")
    version: str = Field(..., example="1.0.1")


def read_version_file() -> dict:
    if not os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_VERSIONS, f, ensure_ascii=False, indent=4)
        return DEFAULT_VERSIONS
    
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return DEFAULT_VERSIONS


def write_version_file(versions: dict):
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(versions, f, ensure_ascii=False, indent=4)


@app.get("/api/versions", status_code=status.HTTP_200_OK)
async def get_all_versions():
    return read_version_file()


@app.get("/api/version/{app_name}", status_code=status.HTTP_200_OK)
async def get_latest_version(app_name: str):
    versions = read_version_file()
    if app_name not in versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_name}' not found."
        )
    return {"app_name": app_name, "latest_version": versions[app_name]}


@app.post("/api/version", status_code=status.HTTP_200_OK)
async def update_latest_version(payload: VersionUpdateSchema):
    try:
        validated_version = Version(payload.version)
    except InvalidVersion:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid version format. Use semantic versioning (e.g., 1.0.1)."
        )
    
    versions = read_version_file()
    versions[payload.app_name] = str(validated_version)
    write_version_file(versions)
    
    return {
        "message": "Version updated successfully",
        "app_name": payload.app_name,
        "latest_version": str(validated_version)
    }


@app.get("/api/version/{app_name}/check", status_code=status.HTTP_200_OK)
async def check_update_required(app_name: str, client_version: str):
    try:
        client_v = Version(client_version)
    except InvalidVersion:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid client version format."
        )
        
    versions = read_version_file()
    if app_name not in versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_name}' not found."
        )
        
    latest_v = Version(versions[app_name])
    update_required = latest_v > client_v
    
    return {
        "app_name": app_name,
        "client_version": str(client_v),
        "latest_version": str(latest_v),
        "update_required": update_required
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def render_dashboard(request: Request):
    versions = read_version_file()
    return templates.TemplateResponse(
        request=request, 
        name="dashboard.html", 
        context={"versions": versions}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("run:app", host="0.0.0.0", port=3009, reload=True)