from __future__ import annotations

import sys


def write_stderr(message: str) -> None:
    if sys.stderr is not None:
        sys.stderr.write(message)
        sys.stderr.flush()
