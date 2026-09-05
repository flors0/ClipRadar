# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


root = Path(SPECPATH)
vendor = root / "vendor" / "bin"
binaries = []
for executable in ("ffmpeg.exe", "ffprobe.exe"):
    candidate = vendor / executable
    if candidate.exists():
        binaries.append((str(candidate), "bin"))

hiddenimports = (
    collect_submodules("keyring.backends")
    + collect_submodules("google.genai")
    + collect_submodules("googleapiclient")
    + collect_submodules("google_auth_oauthlib")
    + collect_submodules("google.oauth2")
    + collect_submodules("google_auth_httplib2")
    + collect_submodules("requests_oauthlib")
    + ["cv2", "yt_dlp", "yt_dlp.compat._legacy", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"]
)

a = Analysis(
    [str(root / "src" / "clipradar" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=[(str(root / "src" / "clipradar" / "resources"), "resources")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClipRadar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "src" / "clipradar" / "resources" / "clipradar.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ClipRadar",
)
