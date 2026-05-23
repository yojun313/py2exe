import os
import json
from typing import Optional
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
    base_url: Optional[str] = Field(None)


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
    return {"app_name": app_name, **versions[app_name]}


@app.post("/api/version", status_code=status.HTTP_200_OK)
async def update_latest_version(payload: VersionUpdateSchema):
    try:
        validated_version = Version(payload.version)
    except InvalidVersion:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid version format. Use semantic versioning (e.g., 1.0.1)."
        )

    v_str = str(validated_version)
    versions = read_version_file()

    is_existing_app = payload.app_name in versions

    if is_existing_app:
        # 기존 앱: 요청에 base_url이 있더라도 무시하고 저장된 base_url을 그대로 사용합니다.
        clean_base_url = versions[payload.app_name]["base_url"]
    else:
        # 신규 앱: base_url이 반드시 필요합니다.
        if not payload.base_url or not payload.base_url.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Base URL is required for new applications."
            )
        clean_base_url = payload.base_url.strip().rstrip("/")

    download_url = f"{clean_base_url}/{payload.app_name}_{v_str}.exe"
    update_url = f"{clean_base_url}/{payload.app_name}_{v_str}_update.exe"

    versions[payload.app_name] = {
        "latest_version": v_str,
        "base_url": clean_base_url,
        "download_url": download_url,
        "update_url": update_url
    }
    write_version_file(versions)

    return {
        "message": "Version and URLs generated successfully",
        "app_name": payload.app_name,
        "is_new_app": not is_existing_app,
        **versions[payload.app_name]
    }


@app.delete("/api/version/{app_name}", status_code=status.HTTP_200_OK)
async def delete_application(app_name: str):
    versions = read_version_file()
    if app_name not in versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{app_name}' not found."
        )

    del versions[app_name]
    write_version_file(versions)
    return {"message": f"Application '{app_name}' deleted successfully."}


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

    app_info = versions[app_name]
    latest_v = Version(app_info["latest_version"])
    update_required = latest_v > client_v

    return {
        "app_name": app_name,
        "client_version": str(client_v),
        "latest_version": str(latest_v),
        "update_required": update_required,
        "download_url": app_info.get("download_url", ""),
        "update_url": app_info.get("update_url", "")
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