from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from openwispr.instance import SessionInstanceGuard


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()

    state = args.state_dir
    state.mkdir(parents=True, exist_ok=True)
    guard = SessionInstanceGuard()

    if guard.acquire():
        (state / "instance.json").write_text(
            json.dumps({"pid": os.getpid(), "state": "running"}), encoding="utf-8"
        )
        stop = state / "stop"
        while not stop.exists():
            time.sleep(0.05)
        guard.release()
        return

    (state / "second.json").write_text(
        json.dumps({"pid": os.getpid(), "state": "focused", "focused": guard.focus_existing()}),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
