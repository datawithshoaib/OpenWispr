from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(
    path: Path,
    target: Path,
    arguments: str = "",
    working_directory: Path | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    working = str(working_directory or target.parent)
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_quote(path)}); "
        f"$s.TargetPath = {_quote(target)}; "
        f"$s.Arguments = {_quote(arguments)}; "
        f"$s.WorkingDirectory = {_quote(working)}; $s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
    )
    if not path.exists():
        raise RuntimeError(f"shortcut was not created: {path}")
    return path


def read_shortcut(path: Path) -> dict:
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_quote(path)}); "
        "[pscustomobject]@{TargetPath=$s.TargetPath;Arguments=$s.Arguments;"
        "WorkingDirectory=$s.WorkingDirectory} | ConvertTo-Json -Compress"
    )
    output = subprocess.check_output(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        text=True,
    )
    return json.loads(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openwispr-shortcut")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    create.add_argument("path", type=Path)
    create.add_argument("target", type=Path)
    create.add_argument("--arguments", default="")
    create.add_argument("--working-directory", type=Path)

    read = commands.add_parser("read")
    read.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    if args.command == "create":
        create_shortcut(args.path, args.target, args.arguments, args.working_directory)
    else:
        print(json.dumps(read_shortcut(args.path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
