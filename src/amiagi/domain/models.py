from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class MemoryRecord:
    kind: str
    content: str
    source: str
    created_at: datetime
