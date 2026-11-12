"""Read a SnapGene .dna file.

The format is a flat list of blocks: one byte of type, four bytes of length
(big-endian), then the payload. Only the blocks this app shows are read:

- 0  DNA: one byte of flags (bit 0 = circular) then the sequence, ASCII.
- 5  Primers, as XML.
- 10 Features, as XML. A feature's range comes from its <Segment range="a-b">.

The first byte of the file is 0x09 (SnapGene's own magic). Anything else, or a
truncated block, raises ValueError so the caller can skip that file.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

MAGIC = 0x09
DNA_BLOCK, PRIMER_BLOCK, NOTES_BLOCK, FEATURE_BLOCK = 0, 5, 6, 10


@dataclass
class Feature:
    name: str
    start: int           # 1-based, inclusive
    end: int             # 1-based, inclusive
    ftype: str = ""
    strand: str = "+"

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass
class DnaRecord:
    name: str = ""
    seq: str = ""
    circular: bool = False
    features: list[Feature] = field(default_factory=list)
    primers: list[Feature] = field(default_factory=list)
    description: str = ""

    def __len__(self) -> int:
        return len(self.seq)


def _blocks(data: bytes):
    i = 0
    while i + 5 <= len(data):
        kind = data[i]
        (size,) = struct.unpack(">I", data[i + 1:i + 5])
        end = i + 5 + size
        if end > len(data):
            raise ValueError("truncated SnapGene block")
        yield kind, data[i + 5:end]
        i = end


def _segments(node) -> list[tuple[int, int]]:
    out = []
    for seg in node.findall("Segment"):
        rng = seg.get("range") or ""
        m = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*$", rng)
        if m:
            out.append((int(m.group(1)), int(m.group(2))))
    return out


def _parse_features(xml: str) -> list[Feature]:
    out: list[Feature] = []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return out
    for node in root.iter("Feature"):
        spans = _segments(node)
        if not spans:
            continue
        name = node.get("name") or node.get("type") or ""
        strand = "-" if node.get("directionality") == "2" else "+"
        out.append(Feature(name, min(a for a, _ in spans), max(b for _, b in spans),
                           node.get("type") or "", strand))
    return out


def _parse_primers(xml: str) -> list[Feature]:
    out: list[Feature] = []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return out
    for node in root.iter("Primer"):
        name = node.get("name") or ""
        for site in node.iter("BindingSite"):
            rng = site.get("location") or ""
            m = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*$", rng)
            if m:
                strand = "-" if site.get("strand") == "0" else "+"
                out.append(Feature(name, int(m.group(1)) + 1, int(m.group(2)) + 1, "primer", strand))
    return out


def parse_dna(data: bytes, name: str = "") -> DnaRecord:
    if not data or data[0] != MAGIC:
        raise ValueError("not a SnapGene .dna file")
    rec = DnaRecord(name=name)
    for kind, payload in _blocks(data):
        if kind == DNA_BLOCK and payload:
            rec.circular = bool(payload[0] & 1)
            rec.seq = payload[1:].decode("ascii", "ignore").upper()
        elif kind == FEATURE_BLOCK:
            rec.features = _parse_features(payload.decode("utf-8", "ignore"))
        elif kind == PRIMER_BLOCK:
            rec.primers = _parse_primers(payload.decode("utf-8", "ignore"))
        elif kind == NOTES_BLOCK:
            text = payload.decode("utf-8", "ignore")
            m = re.search(r"<Description>(.*?)</Description>", text, re.S)
            if m:
                rec.description = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    if not rec.seq:
        raise ValueError("no DNA block in .dna file")
    rec.features.sort(key=lambda f: (f.start, -f.length))
    return rec


def read_dna(path: str | Path) -> DnaRecord:
    path = Path(path)
    return parse_dna(path.read_bytes(), path.stem)
