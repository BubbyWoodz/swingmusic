from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)  # PERF (bubbywoodz): slots eliminate per-instance __dict__
class SimilarArtistEntry:
    artisthash: str
    name: str
    weight: float
    scrobbles: int
    listeners: int


@dataclass(slots=True)  # PERF (bubbywoodz): slots eliminate per-instance __dict__
class SimilarArtist:
    artisthash: str
    similar_artists: list[SimilarArtistEntry]


    def get_artist_hash_set(self) -> set[str]:
        """
        Returns a set of similar artists.
        """
        if not self.similar_artists:
            return set()

        # INFO: 
        return set(a['artisthash'] for a in self.similar_artists)
