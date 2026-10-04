from dataclasses import dataclass


@dataclass(slots=True)  # PERF (bubbywoodz): slots eliminate per-instance __dict__
class StatItem:
    cssclass: str
    text: str
    value: str | int
    image: str | None = None
