# -*- mode: python ; coding: utf-8 -*-
"""py2exe 용 PyInstaller spec 템플릿.

이 파일을 빌드 대상 프로젝트 루트로 복사한 뒤 MAIN_SCRIPT / datas / hiddenimports 만 프로젝트에 맞게
수정하면 된다. 아래 값들은 빌드 시 py2exe 가 자동으로 채운다:

  - EXE 의 name      → APP_NAME (또는 EXE_NAME)
  - COLLECT 의 name  → <APP_NAME>_<버전>
  - APP_PATH         → PROJECT_DIR

즉 name 값에 무엇이 적혀 있든 덮어쓰므로 __APP_NAME__ 같은 토큰을 그대로 두어도 된다.
"""
import os
import platform

# py2exe 는 PROJECT_DIR 을 작업 디렉터리로 두고 환경변수 PY2EXE_APP_PATH 도 넘겨준다.
APP_PATH = os.environ.get("PY2EXE_APP_PATH") or os.path.abspath(os.getcwd())
ASSETS_PATH = os.path.join(APP_PATH, "assets")

ICON_FILE = "icon.ico" if platform.system() == "Windows" else "app_icon.icns"
ICON_PATH = os.path.join(ASSETS_PATH, "imgs", ICON_FILE)

MAIN_SCRIPT = os.path.join(APP_PATH, "main.py")


def _optional(src, dest):
    """존재하는 파일/폴더만 datas 에 포함한다."""
    return [(src, dest)] if os.path.exists(src) else []


a = Analysis(
    [MAIN_SCRIPT],
    pathex=[APP_PATH],
    binaries=[],
    datas=[
        # 예) 에셋 폴더 전체 포함:
        # *_optional(ASSETS_PATH, "assets"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="__APP_NAME__",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="__FOLDER_NAME__",
)
