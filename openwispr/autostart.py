"""
Windows launch shortcuts for OpenWispr.

The Startup-folder shortcut launches the app at login; the Desktop shortcut is
an on-demand launcher. Both use the virtualenv's windowless Python interpreter,
so no console appears.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

STARTUP_RELATIVE = Path("Microsoft") / "Windows" / "Start Menu" / "Programs" / "Startup"
LAUNCHER_NAME = "OpenWispr.lnk"
CSIDL_DESKTOPDIRECTORY = 0x0010


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; autostart is Windows-only")
    return Path(appdata) / STARTUP_RELATIVE


def launcher_path() -> Path:
    return startup_dir() / LAUNCHER_NAME


def desktop_dir() -> Path:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, 0, buffer)
        if result == 0 and buffer.value:
            return Path(buffer.value)
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def desktop_launcher_path() -> Path:
    return desktop_dir() / LAUNCHER_NAME


def project_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def pythonw_path() -> Path:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    if not candidate.exists():
        raise RuntimeError(f"windowless Python not found at {candidate}")
    return candidate


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def shortcut_script(path: Path) -> str:
    return (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(path))}); "
        f"$s.TargetPath = {_ps_quote(str(pythonw_path()))}; "
        "$s.Arguments = '-m openwispr'; "
        f"$s.WorkingDirectory = {_ps_quote(str(project_dir()))}; "
        "$s.Save()"
    )


def _create_shortcut(path: Path, kind: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", shortcut_script(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"could not create {kind} shortcut: {e.stderr.strip()}") from e
    if not path.exists():
        raise RuntimeError(f"{kind} shortcut was not created")
    return path


def install() -> Path:
    return _create_shortcut(launcher_path(), "autostart")


def uninstall() -> bool:
    path = launcher_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def install_desktop() -> Path:
    return _create_shortcut(desktop_launcher_path(), "desktop")


def uninstall_desktop() -> bool:
    path = desktop_launcher_path()
    if not path.exists():
        return False
    path.unlink()
    return True
