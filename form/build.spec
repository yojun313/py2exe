import os, platform

APP_PATH = os.path.abspath(os.getcwd())
ASSETS_PATH = os.path.join(APP_PATH, "assets")

ICON_FILE = "icon.ico" if platform.system() == "Windows" else "app_icon.icns"
ICON_PATH = os.path.join(ASSETS_PATH, "imgs", ICON_FILE)

MAIN_SCRIPT = os.path.join(APP_PATH, "main.py")

a = Analysis(
    [MAIN_SCRIPT],
    pathex=[APP_PATH],
    binaries=[],
    datas=[
        (os.path.join(APP_PATH, 'external', 'move.bat'), 'external')
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SkyBoxAuto',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=ICON_PATH,
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
    name='SkyBoxAuto_VersionPlaceHolder'
)