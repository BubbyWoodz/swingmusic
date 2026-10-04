from dataclasses import dataclass
from typing import Any, Literal


@dataclass(slots=True)  # PERF (bubbywoodz): slots eliminate per-instance __dict__
class Favorite:
    hash: str
    type: Literal["album", "track", "artist"]
    timestamp: int
    userid: int
    extra: dict[str, Any]

    def __post_init__(self):
        # remove the type prefix from the hash
        self.hash = self.hash.replace(f"{self.type}_", "")
