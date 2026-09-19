"""멀티 앱 버전 관리 API 서버.

클라이언트 프로그램이 시작할 때 최신 버전을 조회하고, py2exe 빌드 도구가 배포 후 최신 버전을 등록한다.

    python run.py                       # 0.0.0.0:3009
    VERSION_API_PORT=8080 python run.py

환경 변수: VERSION_API_HOST, VERSION_API_PORT, VERSION_API_RELOAD(1/0), VERSIONS_FILE
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent
VERSION_FILE = Path(os.getenv("VERSIONS_FILE") or BASE_DIR / "versions.json")
APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

app = FastAPI(title="Multi-App Version Management API", version="2.0.0")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
_file_lock = threading.Lock()


# --------------------------------------------------------------------------
# 스키마
# --------------------------------------------------------------------------
class VersionUpdateSchema(BaseModel):
    app_name: str = Field(..., examples=["SkyboxAuto"], description="영문/숫자/._- 만 허용")
    version: str = Field(..., examples=["1.0.1"])
    base_url: Optional[str] = Field(
        None,
        examples=["https://pub-xxxx.r2.dev"],
        description="다운로드 서버 주소. 신규 앱은 필수, 기존 앱은 주면 갱신.",
    )

    @field_validator("app_name")
    @classmethod
    def _validate_app_name(cls, value: str) -> str:
        value = value.strip()
        if not APP_NAME_RE.match(value):
            raise ValueError("app_name 은 영문/숫자/._- 만 사용할 수 있습니다.")
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        try:
            Version(value.strip())
        except InvalidVersion as e:
            raise ValueError("올바른 버전 형식이 아닙니다. 예: 1.0.1") from e
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value:
            return None
        if not re.match(r"^https?://", value):
            raise ValueError("base_url 은 http:// 또는 https:// 로 시작해야 합니다.")
        return value


# --------------------------------------------------------------------------
# 저장소
# --------------------------------------------------------------------------
def read_version_file() -> dict:
    """항상 새 dict 를 돌려준다 (호출자가 수정해도 공유 상태가 오염되지 않도록)."""
    if not VERSION_FILE.exists():
        return {}
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write_version_file(versions: dict) -> None:
    """임시 파일에 쓴 뒤 교체해 쓰다 만 파일이 남지 않게 한다."""
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".versions-", suffix=".json", dir=VERSION_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(versions, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, VERSION_FILE)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def build_entry(app_name: str, version: str, base_url: str) -> dict:
    return {
        "latest_version": version,
        "base_url": base_url,
        "download_url": f"{base_url}/{app_name}_{version}.exe",
        "update_url": f"{base_url}/{app_name}_{version}_update.exe",
    }


def _get_or_404(versions: dict, app_name: str) -> dict:
    if app_name not in versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Application '{app_name}' not found.")
    return versions[app_name]


# --------------------------------------------------------------------------
# 엔드포인트
# --------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/health")
async def health():
    return {"status": "ok", "apps": len(read_version_file())}


@app.get("/api/versions")
async def get_all_versions():
    return read_version_file()


@app.get("/api/version/{app_name}")
async def get_latest_version(app_name: str):
    versions = read_version_file()
    return {"app_name": app_name, **_get_or_404(versions, app_name)}


@app.post("/api/version")
async def update_latest_version(payload: VersionUpdateSchema):
    version_str = str(Version(payload.version))

    with _file_lock:
        versions = read_version_file()
        existing = versions.get(payload.app_name)

        if existing is None:
            if not payload.base_url:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, detail="Base URL is required for new applications."
                )
            base_url = payload.base_url
        else:
            base_url = payload.base_url or existing.get("base_url")
            if not base_url:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="This application has no stored base_url; please provide one.",
                )

        previous_version = existing.get("latest_version") if existing else None
        versions[payload.app_name] = build_entry(payload.app_name, version_str, base_url)
        write_version_file(versions)

    return {
        "message": "Version and URLs updated successfully",
        "app_name": payload.app_name,
        "is_new_app": existing is None,
        "previous_version": previous_version,
        **versions[payload.app_name],
    }


@app.delete("/api/version/{app_name}")
async def delete_application(app_name: str):
    with _file_lock:
        versions = read_version_file()
        _get_or_404(versions, app_name)
        del versions[app_name]
        write_version_file(versions)
    return {"message": f"Application '{app_name}' deleted successfully."}


@app.get("/api/version/{app_name}/check")
async def check_update_required(app_name: str, client_version: str):
    try:
        client_v = Version(client_version)
    except InvalidVersion:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid client version format.")

    info = _get_or_404(read_version_file(), app_name)
    try:
        latest_v = Version(info.get("latest_version", ""))
    except InvalidVersion:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored version for '{app_name}' is invalid: {info.get('latest_version')!r}",
        )

    return {
        "app_name": app_name,
        "client_version": str(client_v),
        "latest_version": str(latest_v),
        "update_required": latest_v > client_v,
        "download_url": info.get("download_url", ""),
        "update_url": info.get("update_url", ""),
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def render_dashboard(request: Request):
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={"versions": read_version_file()}
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "run:app",
        host=os.getenv("VERSION_API_HOST", "0.0.0.0"),
        port=int(os.getenv("VERSION_API_PORT", "3009")),
        reload=os.getenv("VERSION_API_RELOAD", "0") in ("1", "true", "yes"),
    )
