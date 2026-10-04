from dataclasses import dataclass


@dataclass(slots=True)  # PERF (bubbywoodz): slots eliminate per-instance __dict__
class Plugin:
    name: str
    active: bool
    settings: dict
    extra: dict

