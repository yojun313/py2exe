"""설정 로딩 및 검증.

config.json(앱 목록/공통 설정)과 .env(Cloudflare R2 인증 정보)를 읽어
검증된 dataclass 로 변환한다. 모듈 import 시점에는 아무것도 읽지 않으므로
설정 파일이 없어도 import 자체는 실패하지 않는다. (호출자가 load_settings()
에서 ConfigError 를 잡아 사용자에게 안내한다.)

config.json 예시는 config.example.json, 각 키의 의미는 README 참고.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.json"

# Windows 에서 Inno Setup 컴파일러가 흔히 설치되는 위치
INNO_SETUP_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
)

APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ConfigError(Exception):
    """설정 파일/환경 변수 문제. 메시지는 사용자에게 그대로 보여줄 수 있게 작성한다."""


def _expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(str(value)))


def _resolve_path(value: str | None, base: str | None = None) -> str | None:
    """~, 환경변수 확장 후 상대 경로면 base 기준으로 절대 경로화."""
    if value is None or str(value).strip() == "":
        return None
    path = Path(_expand(str(value).strip()))
    if not path.is_absolute() and base:
        path = Path(base) / path
    return str(path)


def _find_venv_python(project_dir: str) -> str | None:
    """프로젝트 안의 가상환경 파이썬을 자동 탐색한다."""
    for venv_name in (".venv", "venv", "env"):
        for rel in ("Scripts/python.exe", "bin/python"):
            candidate = Path(project_dir) / venv_name / rel
            if candidate.exists():
                return str(candidate)
    return None


def find_inno_setup() -> str | None:
    """환경변수 → PATH → 기본 설치 위치 순으로 ISCC.exe 를 찾는다."""
    env_value = os.getenv("INNO_SETUP_EXE")
    if env_value and Path(_expand(env_value)).exists():
        return _expand(env_value)
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return found
    for candidate in INNO_SETUP_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


@dataclass(frozen=True)
class AppConfig:
    """빌드 대상 앱 하나의 설정. 모든 경로는 절대 경로."""

    app_name: str
    project_dir: str
    venv_python: str
    exe_directory: str
    output_directory: str
    spec_file: str
    iss_file: str
    version_pattern: str
    icon_path: str | None = None
    base_url: str | None = None
    upload_prefix: str = ""
    exe_name: str | None = None  # PyInstaller 가 만드는 실행 파일 이름(확장자 제외)

    # ---- 파생 값 -------------------------------------------------------
    @property
    def executable_name(self) -> str:
        return self.exe_name or self.app_name

    def build_folder_name(self, version: str) -> str:
        return f"{self.app_name}_{version}"

    def built_folder(self, version: str) -> str:
        return os.path.join(self.exe_directory, self.build_folder_name(version))

    def built_exe(self, version: str) -> str:
        return os.path.join(self.built_folder(version), f"{self.executable_name}.exe")

    def update_exe_name(self, version: str) -> str:
        return f"{self.app_name}_{version}_update.exe"

    def update_exe_path(self, version: str) -> str:
        return os.path.join(self.exe_directory, self.update_exe_name(version))

    def installer_name(self, version: str) -> str:
        return f"{self.app_name}_{version}.exe"

    def installer_path(self, version: str) -> str:
        return os.path.join(self.output_directory, self.installer_name(version))

    def upload_key(self, filename: str) -> str:
        prefix = self.upload_prefix.strip("/")
        return f"{prefix}/{filename}" if prefix else filename

    # ---- 생성 ----------------------------------------------------------
    @classmethod
    def from_dict(cls, raw: dict, index: int = 0) -> "AppConfig":
        if not isinstance(raw, dict):
            raise ConfigError(f"apps[{index}] 항목은 객체(dict)여야 합니다.")

        # 대소문자 구분 없이 키를 받는다 (APP_NAME / app_name 모두 허용)
        data = {str(k).upper(): v for k, v in raw.items()}

        app_name = str(data.get("APP_NAME", "")).strip()
        if not app_name:
            raise ConfigError(f"apps[{index}] 에 APP_NAME 이 없습니다.")
        if not APP_NAME_RE.match(app_name):
            raise ConfigError(
                f"apps[{index}] APP_NAME '{app_name}' 은 영문/숫자/._- 만 사용할 수 있습니다."
            )

        project_dir = _resolve_path(data.get("PROJECT_DIR"))
        if not project_dir:
            raise ConfigError(f"[{app_name}] PROJECT_DIR 이 비어 있습니다.")

        venv_python = _resolve_path(data.get("VENV_PYTHON"), project_dir)
        if not venv_python:
            venv_python = _find_venv_python(project_dir) or sys.executable

        exe_directory = _resolve_path(data.get("EXE_DIRECTORY"), project_dir) or os.path.join(
            project_dir, "exe"
        )
        output_directory = _resolve_path(
            data.get("OUTPUT_DIRECTORY"), project_dir
        ) or os.path.join(project_dir, "output")

        spec_file = _resolve_path(
            data.get("SPEC_FILE") or data.get("DEFAULT_SPEC_NAME") or "build.spec",
            project_dir,
        )
        iss_file = _resolve_path(
            data.get("ISS_FILE") or data.get("DEFAULT_ISS_NAME") or "setup.iss",
            project_dir,
        )

        version_pattern = str(
            data.get("VERSION_PATTERN") or rf"^{re.escape(app_name)}_(\d+(?:\.\d+)*)$"
        )
        try:
            re.compile(version_pattern)
        except re.error as e:
            raise ConfigError(f"[{app_name}] VERSION_PATTERN 정규식 오류: {e}") from e

        icon_path = _resolve_path(
            data.get("ICON_PATH") or "assets/imgs/icon.ico", project_dir
        )

        base_url = data.get("BASE_URL")
        base_url = str(base_url).strip().rstrip("/") if base_url else None

        exe_name = data.get("EXE_NAME")
        exe_name = str(exe_name).strip() or None if exe_name else None

        return cls(
            app_name=app_name,
            project_dir=project_dir,
            venv_python=venv_python,
            exe_directory=exe_directory,
            output_directory=output_directory,
            spec_file=spec_file,
            iss_file=iss_file,
            version_pattern=version_pattern,
            icon_path=icon_path,
            base_url=base_url,
            upload_prefix=str(data.get("UPLOAD_PREFIX") or ""),
            exe_name=exe_name,
        )


@dataclass(frozen=True)
class R2Config:
    """Cloudflare R2 (S3 호환) 인증 정보. .env 또는 환경 변수에서 읽는다."""

    access_key_id: str | None
    secret_access_key: str | None
    account_id: str | None
    bucket_name: str | None
    public_base_url: str | None = None

    @property
    def endpoint(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    def missing_fields(self) -> list[str]:
        names = {
            "ACCESS_KEY_ID": self.access_key_id,
            "SECRET_ACCESS_KEY": self.secret_access_key,
            "ACCOUNT_ID": self.account_id,
            "BUCKET_NAME": self.bucket_name,
        }
        return [k for k, v in names.items() if not v]

    @property
    def is_configured(self) -> bool:
        return not self.missing_fields()

    def require(self) -> None:
        missing = self.missing_fields()
        if missing:
            raise ConfigError(
                "Cloudflare R2 인증 정보가 부족합니다. .env 에 다음 값을 설정하세요: "
                + ", ".join(missing)
            )

    @classmethod
    def from_env(cls) -> "R2Config":
        def get(name: str) -> str | None:
            value = os.getenv(name)
            return value.strip() if value and value.strip() else None

        return cls(
            access_key_id=get("ACCESS_KEY_ID"),
            secret_access_key=get("SECRET_ACCESS_KEY"),
            account_id=get("ACCOUNT_ID"),
            bucket_name=get("BUCKET_NAME"),
            public_base_url=(get("R2_PUBLIC_BASE_URL") or "").rstrip("/") or None,
        )


@dataclass(frozen=True)
class Settings:
    apps: tuple[AppConfig, ...]
    r2: R2Config
    inno_setup_exe: str | None
    version_api_url: str | None
    config_path: str
    common: dict = field(default_factory=dict)

    def get_app(self, app_name: str) -> AppConfig:
        for app in self.apps:
            if app.app_name == app_name:
                return app
        names = ", ".join(a.app_name for a in self.apps) or "(없음)"
        raise ConfigError(f"'{app_name}' 앱을 찾을 수 없습니다. 등록된 앱: {names}")


def resolve_config_path(path: str | os.PathLike | None = None) -> Path:
    """우선순위: 인자 → PY2EXE_CONFIG 환경변수 → <프로젝트>/config.json"""
    if path:
        return Path(_expand(str(path)))
    env_path = os.getenv("PY2EXE_CONFIG")
    if env_path:
        return Path(_expand(env_path))
    return DEFAULT_CONFIG_PATH


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    """설정을 읽어 Settings 를 돌려준다. 문제가 있으면 ConfigError."""
    # .env 는 프로젝트 루트와 현재 디렉터리 양쪽에서 읽되 기존 환경 변수를 덮어쓰지 않는다.
    load_dotenv(BASE_DIR / ".env")
    load_dotenv()

    config_path = resolve_config_path(path)
    if not config_path.exists():
        raise ConfigError(
            f"config.json 파일을 찾을 수 없습니다: {config_path}\n"
            "config.example.json 을 복사해 config.json 을 만든 뒤 앱 정보를 입력하세요."
        )

    try:
        with open(config_path, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"config.json 파싱 실패 ({config_path}): {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError("config.json 최상위는 객체(dict)여야 합니다.")

    common = raw.get("common") or {}
    if not isinstance(common, dict):
        raise ConfigError("config.json 의 'common' 은 객체(dict)여야 합니다.")
    common = {str(k).upper(): v for k, v in common.items()}

    raw_apps = raw.get("apps") or []
    if not isinstance(raw_apps, list) or not raw_apps:
        raise ConfigError("config.json 의 'apps' 배열에 앱을 하나 이상 등록하세요.")

    apps = tuple(AppConfig.from_dict(a, i) for i, a in enumerate(raw_apps))
    duplicates = {a.app_name for a in apps if [x.app_name for x in apps].count(a.app_name) > 1}
    if duplicates:
        raise ConfigError(f"APP_NAME 이 중복되었습니다: {', '.join(sorted(duplicates))}")

    inno_from_config = _resolve_path(common.get("INNO_SETUP_EXE"))
    inno_setup_exe = (
        inno_from_config if inno_from_config and Path(inno_from_config).exists() else None
    ) or find_inno_setup() or inno_from_config

    version_api_url = os.getenv("VERSION_API_URL") or common.get("VERSION_API_URL")
    version_api_url = str(version_api_url).strip().rstrip("/") if version_api_url else None

    return Settings(
        apps=apps,
        r2=R2Config.from_env(),
        inno_setup_exe=inno_setup_exe,
        version_api_url=version_api_url,
        config_path=str(config_path),
        common=common,
    )
