"""What the plasmid table shows, and the file it is remembered in.

A folder can hold hundreds of maps, so reading every one of them to draw a list
is not worth it. Each map is read once; what the table needs — its length, how
many features it has, which stocked enzymes cut it and how often, which oligos
bind — is kept here and reused on the next launch. The sequence itself is never
kept: it is read again when the map is opened.

An entry is only used when three stamps still match: the file's own revision,
the restriction enzyme stock, and the oligo stock together with the matching
thresholds. Any of them moving means that entry is read again, and nothing else
is.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

CACHE_NAME = "sequence_index.json"
CACHE_VERSION = 1
SCOPE_OF = {"common plasmid": "common", "personal plasmid": "personal"}


def stamp_of(parts) -> str:
    """A short digest of whatever the index was built against."""
    digest = hashlib.sha1()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:16]


@dataclass
class PlasmidSummary:
    """One row of the table."""

    key: str
    name: str
    group: str
    stamp: str = ""            # the file's own revision when this was read
    length: int = 0
    circular: bool = True
    features: int = 0
    cutting: dict[str, int] = field(default_factory=dict)  # stock name -> cut count
    anneal: int = 0            # oligos whose 3' end sits on the map
    arm: int = 0               # oligos matching only on their 5' side
    oligos: list[str] = field(default_factory=list)
    enz_stamp: str = ""
    oligo_stamp: str = ""
    indexed_at: str = ""

    @property
    def topology(self) -> str:
        return "circular" if self.circular else "linear"

    @property
    def scope(self) -> str:
        return SCOPE_OF.get(self.group.lower(), "common")

    @property
    def singles(self) -> list[str]:
        return sorted(n for n, times in self.cutting.items() if times == 1)

    @property
    def cutters(self) -> int:
        return sum(1 for times in self.cutting.values() if times)

    def fresh(self, stamp: str, enz_stamp: str, oligo_stamp: str) -> bool:
        return (bool(self.stamp) and self.stamp == stamp
                and self.enz_stamp == enz_stamp and self.oligo_stamp == oligo_stamp)

    def matches(self, text: str) -> bool:
        """Does this row answer a search for `text`? Name, enzyme or oligo."""
        want = text.strip().lower()
        if not want:
            return True
        if want in self.name.lower():
            return True
        return (any(want in n.lower() for n in self.cutting)
                or any(want in n.lower() for n in self.oligos))


class IndexCache:
    """The summaries of one Drive folder, in one JSON file."""

    def __init__(self, directory: Path | None) -> None:
        self.path = Path(directory) / CACHE_NAME if directory else None
        self.entries: dict[str, PlasmidSummary] = {}
        self.load()

    def load(self) -> None:
        self.entries = {}
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return                      # a damaged cache just means reading again
        if data.get("version") != CACHE_VERSION:
            return
        for raw in data.get("plasmids", []):
            try:
                self.entries[raw["key"]] = PlasmidSummary(**raw)
            except (TypeError, KeyError):
                continue

    def save(self) -> None:
        if self.path is None:
            return
        payload = {"version": CACHE_VERSION,
                   "saved_at": datetime.now().isoformat(timespec="seconds"),
                   "plasmids": [asdict(s) for s in self.entries.values()]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
            tmp.replace(self.path)
        except OSError:
            pass                        # not being able to remember is not an error

    def get(self, key: str) -> PlasmidSummary | None:
        return self.entries.get(key)

    def put(self, summary: PlasmidSummary) -> None:
        summary.indexed_at = datetime.now().isoformat(timespec="seconds")
        self.entries[summary.key] = summary

    def keep_only(self, keys) -> None:
        """Forget maps that are no longer in the folder."""
        keep = set(keys)
        for gone in [k for k in self.entries if k not in keep]:
            del self.entries[gone]

    def clear(self) -> None:
        self.entries = {}
