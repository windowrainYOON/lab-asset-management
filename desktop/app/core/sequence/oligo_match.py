"""Match the lab's ordered oligos against a plasmid map.

What counts as a match (the user's rule, which is also SnapGene's default):
the longest run of bases that is identical on either strand, at least
`min_match` long, and a run shorter than `full_length` only counts when its
melting temperature reaches `min_tm`.

A hit is read differently depending on whether the oligo's 3' end is inside it:

- 3' end included: the oligo anneals and a polymerase can extend it.
- only the 5' side matches: a homology arm for Gibson assembly, or a tail
  carrying a restriction site. It will not prime on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .enzymes import reverse_complement
from .snapgene import DnaRecord
from .tm import melting_temp

MIN_MATCH = 10        # nt, the shortest run that can count
FULL_LENGTH = 15      # nt, at or above this a run counts on length alone
MIN_TM = 40.0         # °C, what a shorter run has to reach
K = 8                 # seed length of the lookup index


@dataclass
class Oligo:
    name: str
    seq: str
    order: str = ""
    length: int = 0
    purification: str = ""
    scale: str = ""
    ordered: str = ""

    def __post_init__(self) -> None:
        self.seq = "".join((self.seq or "").upper().split())
        self.length = self.length or len(self.seq)


@dataclass
class OligoHit:
    oligo: str                  # oligo name
    plasmid: str                # plasmid key
    start: int                  # 1-based on the plasmid, inclusive
    end: int
    strand: str                 # '+' matches the top strand as written
    q_from: int                 # 1-based on the oligo as it is drawn (see `aligned`)
    q_to: int
    length: int                 # matched length
    oligo_len: int
    tm: float = 0.0
    anneals: bool = False       # the oligo's own 3' end is inside the match
    features: list[str] = field(default_factory=list)

    @property
    def full(self) -> bool:
        return self.length == self.oligo_len

    @property
    def kind(self) -> str:
        return "anneal" if self.anneals else "arm"

    @property
    def anchor(self) -> int:
        """Plasmid position the first drawn base of the oligo sits on."""
        return self.start - (self.q_from - 1)

    def aligned(self, oligo_seq: str) -> str:
        """The oligo written left to right along the plasmid's top strand."""
        s = (oligo_seq or "").upper()
        return s if self.strand == "+" else reverse_complement(s)

    def drawn(self, oligo_seq: str) -> str:
        """What is shown on the oligo's own row: a reverse oligo is drawn under
        the bottom strand, so its letters are the complement of the aligned
        sequence (its own 5'→3' sequence read right to left)."""
        aligned = self.aligned(oligo_seq)
        if self.strand == "+":
            return aligned
        return aligned.translate(str.maketrans("ACGTN", "TGCAN"))


def _index(seq: str, circular: bool, k: int = K) -> tuple[str, dict[str, list[int]]]:
    text = seq + seq[:k - 1] if circular and len(seq) > k else seq
    table: dict[str, list[int]] = {}
    for i in range(len(text) - k + 1):
        table.setdefault(text[i:i + k], []).append(i)
    return text, table


def _extend(text: str, ti: int, query: str, qi: int, n: int, k: int) -> tuple[int, int, int]:
    """Grow a k-mer seed both ways. Returns (text start, query start, length)."""
    a, b = ti, qi
    while a > 0 and b > 0 and text[a - 1] == query[b - 1]:
        a -= 1
        b -= 1
    e, f = ti + k, qi + k
    while e < len(text) and f < len(query) and text[e] == query[f]:
        e += 1
        f += 1
    return a, b, e - a


def _best(text: str, table: dict[str, list[int]], query: str, k: int) -> tuple[int, int, int]:
    best = (0, 0, 0)
    for qi in range(len(query) - k + 1):
        for ti in table.get(query[qi:qi + k], ()):  # noqa: B020
            a, b, length = _extend(text, ti, query, qi, len(query), k)
            if length > best[2]:
                best = (a, b, length)
    return best


def _accept(run: str, min_match: int, full_length: int, min_tm: float) -> bool:
    if len(run) < min_match:
        return False
    if len(run) >= full_length:
        return True
    return melting_temp(run) >= min_tm


def match_oligo(oligo: Oligo, record: DnaRecord, key: str = "",
                min_match: int = MIN_MATCH, full_length: int = FULL_LENGTH,
                min_tm: float = MIN_TM, index=None) -> OligoHit | None:
    """The oligo's best hit on this plasmid, or None."""
    seq = record.seq
    n = len(seq)
    if not oligo.seq or n < min_match:
        return None
    k = min(K, min_match)
    text, table = index if index is not None else _index(seq, record.circular, k)
    best: tuple[int, int, int, str] = (0, 0, 0, "+")
    for strand, query in (("+", oligo.seq), ("-", reverse_complement(oligo.seq))):
        a, b, length = _best(text, table, query, k)
        if length > best[2]:
            best = (a, b, length, strand)
    a, b, length, strand = best
    if length == 0:
        return None
    run = text[a:a + length]
    if not _accept(run, min_match, full_length, min_tm):
        return None
    start = (a % n) + 1
    end = ((a + length - 1) % n) + 1
    q_from, q_to = b + 1, b + length
    anneals = (q_to == oligo.length) if strand == "+" else (q_from == 1)
    names = [f.name for f in record.features
             if _overlaps(f.start, f.end, start, end, n)]
    return OligoHit(oligo.name, key or record.name, start, end, strand,
                    q_from, q_to, length, oligo.length, melting_temp(run), anneals, names)


def _overlaps(a1: int, a2: int, b1: int, b2: int, n: int) -> bool:
    """Ranges on a circle: either may wrap past the end."""
    def spans(x1: int, x2: int) -> list[tuple[int, int]]:
        return [(x1, x2)] if x1 <= x2 else [(x1, n), (1, x2)]
    return any(p1 <= q2 and q1 <= p2 for p1, p2 in spans(a1, a2) for q1, q2 in spans(b1, b2))


def match_all(oligos, records: dict[str, DnaRecord], **limits) -> dict[str, list[OligoHit]]:
    """Every oligo's best hit on every plasmid, keyed by oligo name."""
    k = min(K, limits.get("min_match", MIN_MATCH))
    indexes = {key: _index(rec.seq, rec.circular, k) for key, rec in records.items()}
    out: dict[str, list[OligoHit]] = {}
    for oligo in oligos:
        hits = []
        for key, rec in records.items():
            hit = match_oligo(oligo, rec, key, index=indexes[key], **limits)
            if hit is not None:
                hits.append(hit)
        if hits:
            out[oligo.name] = hits
    return out
