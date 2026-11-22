from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from openwispr import model

ROOT = Path(__file__).resolve().parent
APP_EXE = "OpenWispr.exe"
INSTALLER_EXE = "OpenWisprInstaller.exe"
COMMON_FLAGS = ("--noconfirm", "--clean", "--onefile")


def pyinstaller_flags(
    *,
    name: str,
    windowed: bool,
    root: Path,
    payload: Path | None = None,
) -> list[str]:
    flags = [
        *COMMON_FLAGS,
        "--windowed" if windowed else "--console",
        "--name",
        name,
        "--distpath",
        str(root / "dist"),
        "--workpath",
        str(root / "build"),
        "--specpath",
        str(root / "build"),
    ]
    if payload is not None:
        flags.extend(["--add-data", f"{payload}{os.pathsep}payload"])
    return flags


def run_pyinstaller(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", *args], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, help="Use this payload instead of downloading models")
    parser.add_argument("--skip-app-build", action="store_true", help="Reuse the app exe already in the payload")
    args = parser.parse_args()

    payload = (args.payload or (ROOT / "payload")).resolve()
    models = payload / "models"
    models.mkdir(parents=True, exist_ok=True)

    required_models = [model.file_name(model.DEFAULT_MODEL), model.VAD_FILE]
    if args.payload is None:
        for source in (model.ensure(model.DEFAULT_MODEL), model.ensure_vad()):
            shutil.copy2(source, models / source.name)
    else:
        missing = [name for name in required_models if not (models / name).is_file()]
        if missing:
            raise RuntimeError(f"payload is missing models: {', '.join(missing)}")

    if not args.skip_app_build:
        run_pyinstaller(
            *pyinstaller_flags(name="OpenWispr", windowed=True, root=ROOT),
            str(ROOT / "openwispr" / "__main__.py"),
        )
        shutil.copy2(ROOT / "dist" / APP_EXE, payload / APP_EXE)
    elif not (payload / APP_EXE).is_file():
        raise RuntimeError(f"payload is missing {payload / APP_EXE}")

    run_pyinstaller(
        *pyinstaller_flags(name="OpenWisprInstaller", windowed=False, root=ROOT, payload=payload),
        str(ROOT / "openwispr" / "installer.py"),
    )
    print(f"Installer: {ROOT / 'dist' / INSTALLER_EXE}")


if __name__ == "__main__":
    main()
