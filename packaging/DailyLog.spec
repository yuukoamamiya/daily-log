# PyInstaller one-folder build for the desktop client.
from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_data_files


ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from daily_log.version import __version__  # noqa: E402


datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    *collect_data_files("webview"),
]
hiddenimports = [
    "web_server",
    "web_data",
    "daily_log.desktop_app",
    "pystray._win32",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
]
runtime_hooks = []
if os.environ.get("DAILY_LOG_BUILD_PORTABLE") == "1":
    runtime_hooks.append(str(ROOT / "packaging" / "portable_runtime.py"))


a = Analysis(
    [str(ROOT / "daily_log" / "desktop_entry.py")],
    pathex=[str(ROOT), str(ROOT / "scripts")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DailyLog",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DailyLog",
)
