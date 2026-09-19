"""빌드 파이프라인 (GUI 와 무관한 순수 로직).

PyInstaller → 업데이트용 exe 복사 → Inno Setup 인스톨러 → R2 업로드 → 버전 서버 등록
순서로 진행하며, 각 단계는 실패 시 어느 단계에서 왜 실패했는지 담은 BuildError 를 던진다.

GUI 에서는 BuildPipeline(...).run() 을 워커 스레드에서 호출하고, log 콜백과
cancel_event 로 진행 상황을 주고받는다. CLI 에서도 그대로 쓸 수 있다.
"""

from __future__ import annotations

import locale
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from packaging.version import InvalidVersion, Version

from config import AppConfig, ConfigError, Settings

LogFunc = Callable[[str], None]

VERSION_MODES = ("reuse", "next", "custom")
DEFAULT_INITIAL_VERSION = "1.0.0"

# 서브프로세스 콘솔 창 숨김 (Windows 전용 플래그, 다른 OS 에서는 0)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class BuildError(Exception):
    """파이프라인 단계 실패. stage 에 어느 단계인지 담는다."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.message = message


class BuildCancelled(Exception):
    """사용자가 취소함."""


@dataclass
class BuildOptions:
    version_mode: str = "next"  # reuse | next | custom
    custom_version: str = ""
    build_installer: bool = True  # Inno Setup 인스톨러 생성
    upload: bool = True  # R2 업로드
    installer_as_update: bool = False  # 인스톨러를 업데이트용 파일로도 업로드
    register_version: bool = False  # 버전 서버에 최신 버전 등록


@dataclass
class BuildResult:
    app_name: str
    version: str
    built_folder: str
    update_exe_path: str | None
    installer_path: str | None
    uploaded: list[str] = field(default_factory=list)
    registered: bool = False
    elapsed_seconds: float = 0.0


# --------------------------------------------------------------------------
# 버전 관련 유틸
# --------------------------------------------------------------------------
def parse_version(text: str) -> Version | None:
    try:
        return Version(text.strip())
    except (InvalidVersion, AttributeError):
        return None


def read_latest_built_version(exe_directory: str, version_pattern: str) -> str | None:
    """EXE_DIRECTORY 안의 `<App>_<version>` 폴더 이름에서 가장 높은 버전을 찾는다."""
    if not os.path.isdir(exe_directory):
        return None

    pattern = re.compile(version_pattern)
    found: list[tuple[Version, str]] = []
    for name in os.listdir(exe_directory):
        match = pattern.match(name)
        if not match or not match.groups():
            continue
        parsed = parse_version(match.group(1))
        if parsed is not None:
            found.append((parsed, match.group(1)))

    if not found:
        return None
    # 폴더 이름에 적힌 원래 문자열을 돌려준다 ("1.0.01" 같은 표기를 임의로 바꾸지 않음)
    return max(found, key=lambda item: item[0])[1]


def bump_patch(version: str) -> str:
    v = Version(version)
    release = list(v.release) + [0] * (3 - len(v.release))
    release[-1] += 1
    return ".".join(str(n) for n in release)


def resolve_target_version(current: str | None, mode: str, custom: str = "") -> str:
    """현재 버전과 선택 모드로 빌드할 버전 문자열을 정한다."""
    if mode not in VERSION_MODES:
        raise ValueError(f"알 수 없는 version_mode: {mode}")

    base = current or DEFAULT_INITIAL_VERSION
    if mode == "reuse":
        return base
    if mode == "next":
        # 이전 빌드가 없으면 굳이 1.0.1 로 올리지 않고 1.0.0 부터 시작
        return bump_patch(base) if current else base

    custom = custom.strip()
    if not custom:
        raise ValueError("직접 입력 버전이 비어 있습니다.")
    if parse_version(custom) is None:
        raise ValueError(f"올바른 버전 형식이 아닙니다: '{custom}' (예: 1.2.3)")
    return custom


# --------------------------------------------------------------------------
# 템플릿 치환
# --------------------------------------------------------------------------
_NAME_KW_RE = re.compile(r"(\bname\s*=\s*)(['\"])(.*?)\2")


def _find_call(content: str, call_name: str) -> re.Match | None:
    """`x = EXE(` 형태의 실제 호출을 찾는다 (주석/문서 문자열 속 언급은 건너뜀)."""
    assigned = re.compile(rf"^[ \t]*\w+[ \t]*=[ \t]*{call_name}[ \t]*\(", re.MULTILINE)
    match = None
    for match in assigned.finditer(content):
        pass  # 마지막 호출을 사용
    if match:
        return match
    # 대입 없이 호출만 있는 경우: 주석 줄(#)이 아닌 곳에서 찾는다
    for candidate in re.finditer(rf"\b{call_name}[ \t]*\(", content):
        line_start = content.rfind("\n", 0, candidate.start()) + 1
        if not content[line_start:candidate.start()].lstrip().startswith("#"):
            match = candidate
    return match


def _replace_name_in_call(content: str, call_name: str, new_name: str) -> tuple[str, bool]:
    """`EXE(` / `COLLECT(` 호출 안의 name='...' 값을 바꾼다. 호출이 없으면 그대로."""
    call = _find_call(content, call_name)
    if not call:
        return content, False
    # 호출 범위: 다음에 나오는 '열 0 에서 시작하는 줄' 직전까지 (닫는 괄호가 보통 열 0 에 온다)
    boundary = re.compile(r"\n(?=\S)").search(content, call.end())
    end = boundary.start() if boundary else len(content)
    match = _NAME_KW_RE.search(content, call.end(), end)
    if not match:
        return content, True
    replaced = f"{match.group(1)}{match.group(2)}{new_name}{match.group(2)}"
    return content[: match.start()] + replaced + content[match.end() :], True


def patch_spec(content: str, exe_name: str, folder_name: str, project_dir: str) -> tuple[str, bool]:
    """spec 템플릿에 실행 파일 이름/출력 폴더 이름/프로젝트 경로를 주입한다.

    돌려주는 bool 은 COLLECT (onedir) 호출이 있는지 여부.
    """
    escaped_dir = project_dir.replace("\\", "/")

    # 1) 토큰 방식 (__APP_NAME__, __FOLDER_NAME__, __APP_PATH__)
    content = (
        content.replace("__APP_NAME__", exe_name)
        .replace("__FOLDER_NAME__", folder_name)
        .replace("__APP_PATH__", escaped_dir)
    )
    # 2) 호출 기반 치환: 어떤 이름이 적혀 있든 EXE/COLLECT 의 name= 을 덮어쓴다
    content, _ = _replace_name_in_call(content, "EXE", exe_name)
    content, has_collect = _replace_name_in_call(content, "COLLECT", folder_name)
    # 3) APP_PATH = ... 줄이 있으면 프로젝트 경로로 고정
    content = re.sub(
        r"^(\s*APP_PATH\s*=\s*).*$",
        lambda m: f"{m.group(1)}{escaped_dir!r}",
        content,
        count=1,
        flags=re.MULTILINE,
    )
    return content, has_collect


_ISS_VERSION_RE = re.compile(r'^(\s*#define\s+MyAppVersion\s+)"[^"\r\n]*"', re.MULTILINE)


def patch_iss_version(content: str, version: str) -> str:
    """setup.iss 의 `#define MyAppVersion "..."` 을 새 버전으로 바꾼다. 없으면 맨 앞에 추가."""
    new_content, count = _ISS_VERSION_RE.subn(lambda m: f'{m.group(1)}"{version}"', content, count=1)
    if count == 0:
        return f'#define MyAppVersion "{version}"\n' + content
    return new_content


def iss_define_args(content: str, defines: dict[str, str]) -> list[str]:
    """ISCC 에 넘길 /D 인자를 만든다.

    스크립트 안에 `#ifndef NAME` 가드가 있거나 아예 정의가 없으면 /D 로 전달하고,
    가드 없이 하드코딩된 `#define NAME` 이 있으면 그 값을 존중해 전달하지 않는다.
    """
    args: list[str] = []
    for name, value in defines.items():
        if value is None:
            continue
        guarded = re.search(rf"^\s*#ifndef\s+{re.escape(name)}\b", content, re.MULTILINE)
        hard_defined = re.search(rf"^\s*#define\s+{re.escape(name)}\b", content, re.MULTILINE)
        if guarded or not hard_defined:
            args.append(f"/D{name}={value}")
    return args


# --------------------------------------------------------------------------
# 서브프로세스 실행 (로그 스트리밍 + 취소)
# --------------------------------------------------------------------------
def run_streamed(
    cmd: list[str],
    log: LogFunc,
    cancel_event: threading.Event | None = None,
    cwd: str | None = None,
    env: dict | None = None,
    encoding: str | None = None,
    on_start: Callable[[subprocess.Popen | None], None] | None = None,
) -> int:
    """명령을 실행하며 출력 한 줄씩 log 로 넘긴다. 취소 시 프로세스를 죽이고 BuildCancelled.

    on_start 에는 시작된 Popen 객체가(끝나면 None 이) 전달되어 외부에서 kill 할 수 있다.
    """
    encoding = encoding or "utf-8"
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding=encoding,
            errors="replace",
            cwd=cwd,
            env=env,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError as e:
        raise BuildError("실행", f"명령을 찾을 수 없습니다: {cmd[0]} ({e})") from e

    if on_start:
        on_start(process)

    if cancel_event is not None:
        # 자식이 출력을 내지 않아도 취소가 바로 먹도록 별도 스레드에서 감시한다
        def _watch() -> None:
            while process.poll() is None:
                if cancel_event.wait(0.25):
                    if process.poll() is None:
                        process.kill()
                    break

        threading.Thread(target=_watch, name="py2exe-cancel-watch", daemon=True).start()

    assert process.stdout is not None
    try:
        for line in process.stdout:
            if cancel_event is not None and cancel_event.is_set():
                raise BuildCancelled()
            line = line.rstrip("\r\n")
            if line:
                log(line)
        code = process.wait()
        if cancel_event is not None and cancel_event.is_set():
            raise BuildCancelled()
        return code
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if on_start:
            on_start(None)


# --------------------------------------------------------------------------
# 파이프라인
# --------------------------------------------------------------------------
class BuildPipeline:
    def __init__(
        self,
        app: AppConfig,
        settings: Settings,
        options: BuildOptions,
        log: LogFunc | None = None,
        cancel_event: threading.Event | None = None,
    ):
        self.app = app
        self.settings = settings
        self.options = options
        self._log = log or print
        self.cancel_event = cancel_event or threading.Event()
        self._process: subprocess.Popen | None = None
        self._process_lock = threading.Lock()

    # ---- 헬퍼 -----------------------------------------------------------
    def log(self, msg: str) -> None:
        self._log(msg)

    def cancel(self) -> None:
        """다른 스레드에서 호출: 취소 플래그를 세우고 실행 중인 서브프로세스를 죽인다."""
        self.cancel_event.set()
        with self._process_lock:
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.kill()
                except OSError:
                    pass

    def _track_process(self, process: subprocess.Popen | None) -> None:
        with self._process_lock:
            self._process = process
        if process is not None and self.cancel_event.is_set():
            process.kill()

    def _run(self, cmd: list[str], cwd: str, env: dict | None = None, encoding: str | None = None) -> int:
        return run_streamed(
            cmd, self.log, self.cancel_event, cwd=cwd, env=env, encoding=encoding, on_start=self._track_process
        )

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise BuildCancelled()

    # ---- 사전 검증 -------------------------------------------------------
    def preflight(self) -> None:
        """빌드에 시간을 쓰기 전에 필요한 파일/도구/인증 정보를 모두 확인한다."""
        app, opt = self.app, self.options
        problems: list[str] = []

        if not os.path.isdir(app.project_dir):
            problems.append(f"PROJECT_DIR 이 존재하지 않습니다: {app.project_dir}")
        if not os.path.isfile(app.spec_file):
            problems.append(
                f"spec 파일이 없습니다: {app.spec_file}\n"
                "  → form/build.spec 을 프로젝트로 복사해 수정하세요."
            )
        if not os.path.isfile(app.venv_python):
            problems.append(f"VENV_PYTHON 이 존재하지 않습니다: {app.venv_python}")

        if opt.build_installer:
            if not os.path.isfile(app.iss_file):
                problems.append(
                    f"Inno Setup 스크립트가 없습니다: {app.iss_file}\n"
                    "  → form/setup.iss 를 프로젝트로 복사해 수정하거나 '인스톨러 생성' 옵션을 끄세요."
                )
            inno = self.settings.inno_setup_exe
            if not inno or not os.path.isfile(inno):
                problems.append(
                    "Inno Setup 컴파일러(ISCC.exe)를 찾을 수 없습니다. "
                    "config.json 의 common.INNO_SETUP_EXE 또는 환경변수 INNO_SETUP_EXE 를 설정하세요."
                )
        elif opt.installer_as_update:
            problems.append("'인스톨러를 업데이트용으로 배포' 옵션은 인스톨러 생성이 켜져 있어야 합니다.")

        if opt.upload:
            missing = self.settings.r2.missing_fields()
            if missing:
                problems.append("R2 업로드에 필요한 .env 값이 없습니다: " + ", ".join(missing))

        if opt.register_version and not self.settings.version_api_url:
            problems.append(
                "버전 서버 주소가 없습니다. common.VERSION_API_URL 또는 환경변수 VERSION_API_URL 을 설정하세요."
            )

        if opt.version_mode == "custom":
            try:
                resolve_target_version(None, "custom", opt.custom_version)
            except ValueError as e:
                problems.append(str(e))

        if problems:
            raise BuildError("사전 검증", "\n".join(f"- {p}" for p in problems))

    # ---- 단계별 구현 -------------------------------------------------------
    def _determine_version(self) -> str:
        current = read_latest_built_version(self.app.exe_directory, self.app.version_pattern)
        if current:
            self.log(f"현재 빌드 버전: {current}")
        else:
            self.log(f"이전 빌드가 없어 기본 버전 {DEFAULT_INITIAL_VERSION} 부터 시작합니다.")
        try:
            target = resolve_target_version(current, self.options.version_mode, self.options.custom_version)
        except ValueError as e:
            raise BuildError("버전 결정", str(e)) from e
        self.log(f"빌드 대상 버전: {target}")
        return target

    def _clean_previous(self, version: str) -> None:
        folder = self.app.built_folder(version)
        if os.path.isdir(folder):
            shutil.rmtree(folder)
            self.log(f"동일 버전 빌드 폴더 삭제: {folder}")
        for path in (self.app.update_exe_path(version), self.app.installer_path(version)):
            if os.path.isfile(path):
                os.remove(path)
                self.log(f"동일 버전 산출물 삭제: {path}")

    def _run_pyinstaller(self, version: str) -> str:
        app = self.app
        folder_name = app.build_folder_name(version)
        os.makedirs(app.exe_directory, exist_ok=True)

        with open(app.spec_file, "r", encoding="utf-8") as f:
            content = f.read()
        patched, has_collect = patch_spec(content, app.executable_name, folder_name, app.project_dir)

        temp_spec = os.path.join(app.exe_directory, f"{folder_name}.spec")
        with open(temp_spec, "w", encoding="utf-8") as f:
            f.write(patched)

        work_path = os.path.join(app.exe_directory, "build")
        cmd = [
            app.venv_python,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            app.exe_directory,
            "--workpath",
            work_path,
            temp_spec,
        ]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PY2EXE_APP_PATH"] = app.project_dir
        env["PY2EXE_APP_NAME"] = app.executable_name
        env["PY2EXE_VERSION"] = version

        self.log("PyInstaller 실행: " + " ".join(cmd))
        try:
            code = self._run(cmd, cwd=app.project_dir, env=env)
        finally:
            for path, remover in ((temp_spec, os.remove), (work_path, shutil.rmtree)):
                try:
                    if os.path.exists(path):
                        remover(path)
                except OSError as e:
                    self.log(f"[경고] 임시 파일 정리 실패 ({path}): {e}")

        if code != 0:
            raise BuildError("PyInstaller", f"종료 코드 {code}. 위 로그를 확인하세요.")

        built_folder = app.built_folder(version)
        built_exe = app.built_exe(version)

        # onefile 스펙(COLLECT 없음)이면 exe 가 exe_directory 바로 아래에 생긴다 → 폴더 구조로 맞춘다
        if not has_collect or not os.path.isdir(built_folder):
            single = os.path.join(app.exe_directory, f"{app.executable_name}.exe")
            if os.path.isfile(single):
                os.makedirs(built_folder, exist_ok=True)
                shutil.move(single, built_exe)
                self.log(f"onefile 결과물을 {built_folder} 로 이동")

        if not os.path.isfile(built_exe):
            raise BuildError(
                "PyInstaller",
                f"빌드 결과 실행 파일을 찾을 수 없습니다: {built_exe}\n"
                "  spec 의 EXE(name=...) 값이 APP_NAME(또는 EXE_NAME)과 다르면 config.json 에 EXE_NAME 을 지정하세요.",
            )
        self.log(f"PyInstaller 완료: {built_folder}")
        return built_folder

    def _copy_update_exe(self, version: str) -> str:
        src = self.app.built_exe(version)
        dst = self.app.update_exe_path(version)
        shutil.copy2(src, dst)
        self.log(f"업데이트용 실행 파일 생성: {dst}")
        return dst

    def _run_inno_setup(self, version: str) -> str:
        app = self.app
        iscc = self.settings.inno_setup_exe
        assert iscc is not None
        os.makedirs(app.output_directory, exist_ok=True)

        with open(app.iss_file, "r", encoding="utf-8-sig") as f:
            content = f.read()
        patched = patch_iss_version(content, version)

        temp_iss = os.path.join(os.path.dirname(app.iss_file), f".{app.app_name}_{version}.tmp.iss")
        with open(temp_iss, "w", encoding="utf-8-sig") as f:
            f.write(patched)

        defines = {
            "MyAppName": app.app_name,
            "MyAppVersion": version,
            "MyAppExeName": f"{app.executable_name}.exe",
            "ProjectBaseDir": os.path.dirname(app.exe_directory),
            "BuildDir": app.built_folder(version),
            "OutputDir": app.output_directory,
            "SourceIconPath": app.icon_path if app.icon_path and os.path.isfile(app.icon_path) else "",
        }
        cmd = [
            iscc,
            f"/O{app.output_directory}",  # OutputDir 강제
            f"/F{app.app_name}_{version}",  # OutputBaseFilename 강제 (.exe 는 ISCC 가 붙임)
            *iss_define_args(patched, defines),
            temp_iss,
        ]

        self.log("Inno Setup 실행: " + " ".join(cmd))
        try:
            code = self._run(cmd, cwd=app.project_dir, encoding=locale.getpreferredencoding(False))
        finally:
            try:
                os.remove(temp_iss)
            except OSError as e:
                self.log(f"[경고] 임시 iss 삭제 실패: {e}")

        if code != 0:
            raise BuildError("Inno Setup", f"종료 코드 {code}. 위 로그를 확인하세요.")

        installer = app.installer_path(version)
        if not os.path.isfile(installer):
            raise BuildError("Inno Setup", f"인스톨러가 생성되지 않았습니다: {installer}")
        self.log(f"인스톨러 생성 완료: {installer}")
        return installer

    def _upload(self, paths: list[str]) -> list[str]:
        from cloudflare import R2Uploader  # boto3 는 필요할 때만 import

        uploader = R2Uploader(self.settings.r2, log=self.log)
        uploaded: list[str] = []
        for path in paths:
            self._check_cancelled()
            key = self.app.upload_key(os.path.basename(path))
            try:
                uploader.upload(path, key)
            except Exception as e:  # boto3 예외 종류가 다양하므로 포괄 처리
                raise BuildError("R2 업로드", f"{os.path.basename(path)} 업로드 실패: {e}") from e
            uploaded.append(key)
        return uploaded

    def _register_version(self, version: str) -> None:
        from version_client import register_version

        base_url = self.app.base_url or self.settings.r2.public_base_url
        try:
            info = register_version(self.settings.version_api_url, self.app.app_name, version, base_url)
        except Exception as e:
            raise BuildError("버전 서버 등록", str(e)) from e
        self.log(f"버전 서버 등록 완료: {info.get('app_name')} → {info.get('latest_version')}")
        if info.get("download_url"):
            self.log(f"  다운로드 URL: {info['download_url']}")

    # ---- 전체 실행 ---------------------------------------------------------
    def run(self) -> BuildResult:
        started = time.monotonic()
        app, opt = self.app, self.options

        self.log(f"=== [{app.app_name}] 빌드 시작 {datetime.now():%Y-%m-%d %H:%M:%S} ===")
        self.preflight()
        self._check_cancelled()

        version = self._determine_version()
        self._clean_previous(version)
        self._check_cancelled()

        self.log("--- 1/4 PyInstaller ---")
        built_folder = self._run_pyinstaller(version)
        update_exe = self._copy_update_exe(version)
        self._check_cancelled()

        installer: str | None = None
        if opt.build_installer:
            self.log("--- 2/4 Inno Setup ---")
            installer = self._run_inno_setup(version)
            if opt.installer_as_update:
                shutil.copy2(installer, update_exe)
                self.log("인스톨러를 업데이트용 파일로도 사용합니다 (전체 설치 방식 업데이트).")
        else:
            self.log("--- 2/4 Inno Setup (건너뜀) ---")
        self._check_cancelled()

        uploaded: list[str] = []
        if opt.upload:
            self.log("--- 3/4 Cloudflare R2 업로드 ---")
            targets = [update_exe] + ([installer] if installer else [])
            uploaded = self._upload(targets)
        else:
            self.log("--- 3/4 업로드 (건너뜀) ---")

        registered = False
        if opt.register_version:
            self.log("--- 4/4 버전 서버 등록 ---")
            self._register_version(version)
            registered = True
        else:
            self.log("--- 4/4 버전 서버 등록 (건너뜀) ---")

        elapsed = time.monotonic() - started
        self.log(f"=== 완료: {app.app_name} {version} ({format_elapsed(elapsed)}) ===")
        return BuildResult(
            app_name=app.app_name,
            version=version,
            built_folder=built_folder,
            update_exe_path=update_exe,
            installer_path=installer,
            uploaded=uploaded,
            registered=registered,
            elapsed_seconds=elapsed,
        )


def format_elapsed(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}분 {secs}초" if minutes else f"{secs}초"


# --------------------------------------------------------------------------
# CLI: python builder.py <APP_NAME> [--mode next|reuse|custom --version X] [--no-upload] ...
# --------------------------------------------------------------------------
def _cli() -> int:
    import argparse

    from config import load_settings

    parser = argparse.ArgumentParser(description="GUI 없이 빌드 파이프라인을 실행합니다.")
    parser.add_argument("app", nargs="?", help="config.json 의 APP_NAME (생략 시 목록 출력)")
    parser.add_argument("--mode", choices=VERSION_MODES, default="next")
    parser.add_argument("--version", dest="custom_version", default="", help="--mode custom 일 때 버전")
    parser.add_argument("--no-installer", action="store_true", help="Inno Setup 단계 생략")
    parser.add_argument("--no-upload", action="store_true", help="R2 업로드 생략")
    parser.add_argument("--installer-as-update", action="store_true")
    parser.add_argument("--register", action="store_true", help="버전 서버에 등록")
    parser.add_argument("--config", help="config.json 경로")
    args = parser.parse_args()

    try:
        settings = load_settings(args.config)
    except ConfigError as e:
        print(f"[설정 오류] {e}")
        return 2

    if not args.app:
        print("등록된 앱:")
        for a in settings.apps:
            print(f"  - {a.app_name}  ({a.project_dir})")
        return 0

    try:
        app = settings.get_app(args.app)
    except ConfigError as e:
        print(f"[오류] {e}")
        return 2

    options = BuildOptions(
        version_mode="custom" if args.custom_version else args.mode,
        custom_version=args.custom_version,
        build_installer=not args.no_installer,
        upload=not args.no_upload,
        installer_as_update=args.installer_as_update,
        register_version=args.register,
    )
    try:
        BuildPipeline(app, settings, options).run()
    except BuildCancelled:
        print("취소됨")
        return 130
    except BuildError as e:
        print(f"[오류] {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
