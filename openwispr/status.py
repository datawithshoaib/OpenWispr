from __future__ import annotations

from enum import Enum, auto


class Status(Enum):
    Idle = auto()
    Listening = auto()
    Transcribing = auto()
    Finishing = auto()

    @property
    def label(self) -> str:
        return self.name
