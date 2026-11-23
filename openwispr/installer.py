from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from openwispr import autostart, model
from openwispr.shortcut import create_shortcut, read_shortcut

APP_NAME = "OpenWispr"
APP_EXE = "OpenWispr.exe"
LAUNCHER_NAME = "OpenWispr.lnk"
MANIFEST_NAME = "install-manifest.json"


@dataclass(frozen=True)
class InstallerPaths:
    install_dir: Path
    start_menu_dir: Path
    models_dir: Path
    desktop_dir: Path
    startup_dir: Path
    payload_dir: Path


def _points_at_repository(info: dict) -> bool:
    target = str(info.get("TargetPath", "")).replace("/", "\\").lower()
    arguments = str(info.get("Arguments", "")).lower()
    return ".venv\\scripts" in target and (not arguments or "openwispr" in arguments)


def _remove_legacy_shortcuts(directory: Path) -> None:
    if not directory.exists():
        return
    for path in directory.glob("*.lnk"):
        try:
            info = read_shortcut(path)
        except Exception:
            continue
        if _points_at_repository(info):
            path.unlink(missing_ok=True)


def install(paths: InstallerPaths) -> Path:
    paths.install_dir.mkdir(parents=True, exist_ok=True)
    source = paths.payload_dir / APP_EXE
    target = paths.install_dir / APP_EXE
    if not source.is_file():
        raise RuntimeError(f"installer payload is missing {source}")
    target.write_bytes(source.read_bytes())
    paths.models_dir.mkdir(parents=True, exist_ok=True)
    installed_models = []
    for name in (model.file_name(model.DEFAULT_MODEL), model.VAD_FILE):
        source_model = paths.payload_dir / "models" / name
        if not source_model.is_file():
            raise RuntimeError(f"installer payload is missing {source_model}")
        installed_model = paths.models_dir / name
        shutil.copy2(source_model, installed_model)
        installed_models.append(str(installed_model))

    shortcut = create_shortcut(paths.start_menu_dir / LAUNCHER_NAME, target)
    manifest = {
        "version": 1,
        "start_menu": str(shortcut),
        "models": installed_models,
    }
    manifest_path = paths.install_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return shortcut


def uninstall(paths: InstallerPaths) -> None:
    manifest_path = paths.install_dir / MANIFEST_NAME
    manifest = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for value in manifest.get("models", []):
        Path(value).unlink(missing_ok=True)

    start_menu = Path(manifest.get("start_menu", paths.start_menu_dir / LAUNCHER_NAME))
    start_menu.unlink(missing_ok=True)

    if paths.install_dir.exists():
        shutil.rmtree(paths.install_dir)

    _remove_legacy_shortcuts(paths.desktop_dir)
    _remove_legacy_shortcuts(paths.startup_dir)


def bundled_payload_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS"))
    else:
        base = Path(__file__).resolve().parent.parent
    payload = base / "payload"
    if not payload.is_dir():
        raise RuntimeError(f"bundled installer payload not found: {payload}")
    return payload


def default_paths() -> InstallerPaths:
    local_app_data = os.environ.get("LOCALAPPDATA")
    local = Path(local_app_data) if local_app_data else (Path.home() / "AppData" / "Local")
    app_data = os.environ.get("APPDATA")
    roaming = Path(app_data) if app_data else (local.parent / "Roaming")
    programs = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return InstallerPaths(
        install_dir=local / APP_NAME / "app",
        start_menu_dir=programs,
        models_dir=model.models_dir(),
        desktop_dir=autostart.desktop_dir(),
        startup_dir=programs / "Startup",
        payload_dir=Path(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openwispr-installer")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("install", "uninstall"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--install-dir", type=Path)
        subparser.add_argument("--start-menu-dir", type=Path)
        subparser.add_argument("--models-dir", type=Path)
        subparser.add_argument("--desktop-dir", type=Path)
        subparser.add_argument("--startup-dir", type=Path)
        subparser.add_argument("--payload-dir", type=Path)

    args = parser.parse_args(argv)
    defaults = default_paths()
    override = {
        name: getattr(args, name)
        for name in (
            "install_dir",
            "start_menu_dir",
            "models_dir",
            "desktop_dir",
            "startup_dir",
        )
    }
    paths = InstallerPaths(
        **{name: value if value is not None else getattr(defaults, name) for name, value in override.items()},
        payload_dir=args.payload_dir or bundled_payload_dir(),
    )

    if args.command == "install":
        install(paths)
    else:
        uninstall(paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
