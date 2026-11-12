"""Restriction enzymes: the lab's own cut sites, and where they cut a plasmid.

The lab's restriction enzyme stock file carries one "cut site" column written in
REBASE notation: the recognition sequence with '^' where the top strand is cut
and '_' where the bottom strand is cut, for example

    EcoRI     G^AATT_C        5' overhang, 4 nt
    KpnI      G_GTAC^C        3' overhang, 4 nt
    EcoRV     GAT^_ATC        blunt
    BsaI      GGTCTCN^NNNN_N  cuts outside its recognition sequence

That column is the only thing the vector map reads, so adding an enzyme or
fixing a notation needs no change here. REBASE below is only used to fill the
column in for enzymes that do not have one yet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

IUPAC = {
    "A": "A", "C": "C", "G": "G", "T": "T",
    "R": "AG", "Y": "CT", "S": "CG", "W": "AT", "K": "GT", "M": "AC",
    "B": "CGT", "D": "AGT", "H": "ACT", "V": "ACG", "N": "ACGT",
}
COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN")


def reverse_complement(seq: str) -> str:
    return (seq or "").upper().translate(COMPLEMENT)[::-1]


@dataclass(frozen=True)
class CutSite:
    """One enzyme's recognition sequence and where it cuts."""
    site: str        # recognition sequence only, IUPAC letters
    top: int         # bases before the cut on the top strand, from the site's start
    bottom: int      # bases before the cut on the bottom strand
    notation: str = ""

    @property
    def overhang(self) -> int:
        """Positive: 5' overhang of that many nt. Negative: 3'. Zero: blunt."""
        return self.bottom - self.top

    @property
    def end_label(self) -> str:
        n = self.overhang
        if n > 0:
            return f"5′ 돌출 {n} nt"
        if n < 0:
            return f"3′ 돌출 {-n} nt"
        return "blunt"

    @property
    def palindromic(self) -> bool:
        return self.site == reverse_complement(self.site)

    def regex(self) -> re.Pattern:
        return re.compile("".join(f"[{IUPAC.get(c, c)}]" for c in self.site))


def parse_cut_site(text: str) -> CutSite | None:
    """Read one 'cut site' cell. Returns None when it is empty or unreadable."""
    raw = (text or "").strip().upper().replace(" ", "")
    if not raw or "^" not in raw:
        return None
    body = raw.replace("_", "")
    top = raw.index("^")
    # '_' may come before or after '^'; count letters before each marker
    bottom = len(re.sub(r"[\^]", "", raw[:raw.index("_")])) if "_" in raw else top
    top = len(raw[:top].replace("_", ""))
    site = body.replace("^", "").rstrip("N")
    if not site or any(c not in IUPAC for c in site):
        return None
    return CutSite(site, top, bottom, raw)


@dataclass
class EnzymeHit:
    enzyme: str
    pos: int            # 1-based position on the top strand, cut is AFTER this base
    site_start: int     # 1-based start of the recognition sequence
    site_end: int
    strand: str = "+"


# REBASE notation for the enzymes the lab stocks, used to fill an empty cut site
# cell. Source: REBASE (rebase.neb.com), the same data NEB's EnzymeFinder shows.
REBASE: dict[str, str] = {
    "AccI": "GT^MK_AC", "Acc65I": "G^GTAC_C", "AflII": "C^TTAA_G", "AscI": "GG^CGCG_CC",
    "AvrII": "C^CTAG_G", "BamHI": "G^GATC_C", "BbsI": "GAAGACNN^NNNN_N",
    "BsaI": "GGTCTCN^NNNN_N", "BspMI": "ACCTGCNNNN^NNNN_N", "BsiWI": "C^GTAC_G",
    "BsrGI": "T^GTAC_A", "BglII": "A^GATC_T", "ClaI": "AT^CG_AT", "DpnI": "GA^_TC",
    "EcoRI": "G^AATT_C", "EcoRV": "GAT^_ATC", "HindIII": "A^AGCT_T", "HpaI": "GTT^_AAC",
    "KpnI": "G_GTAC^C", "MfeI": "C^AATT_G", "MscI": "TGG^_CCA", "NlaIII": "_CATG^",
    "NcoI": "C^CATG_G", "NdeI": "CA^TA_TG", "NheI": "G^CTAG_C", "NotI": "GC^GGCC_GC",
    "PacI": "TTA_AT^TAA", "PmeI": "GTTT^_AAAC", "PstI": "C_TGCA^G", "PvuI": "CG_AT^CG",
    "SacI": "G_AGCT^C", "SacII": "CC_GC^GG", "SalI": "G^TCGA_C", "ScaI": "AGT^_ACT",
    "SmaI": "CCC^_GGG", "SpeI": "A^CTAG_T", "StuI": "AGG^_CCT", "XbaI": "T^CTAG_A",
    "XhoI": "C^TCGA_G",
    # common neighbours of the stock list, so a newly bought enzyme is recognised
    "ApaI": "G_GGCC^C", "BclI": "T^GATC_A", "BsmBI": "CGTCTCN^NNNN_N",
    "DraI": "TTT^_AAA", "EagI": "C^GGCC_G", "HpaII": "C^CG_G", "MluI": "A^CGCG_T",
    "NruI": "TCG^_CGA", "PmlI": "CAC^_GTG", "PvuII": "CAG^_CTG", "SphI": "G_CATG^C",
    "SspI": "AAT^_ATT", "XmaI": "C^CCGG_G",
}

_HF_SUFFIX = re.compile(r"[-\s]*(hf|v2|hfv2)$", re.I)
_ALIASES = {"mfel": "MfeI", "bsll": "BglII", "bglii": "BglII", "bgl ii": "BglII"}


def normalize_name(name: str) -> str:
    """'BamHI / BamHI-HF', 'Bgl II', 'Mfel-HF' all mean one enzyme."""
    first = (name or "").split("/")[0].strip()
    first = _HF_SUFFIX.sub("", first).strip()
    key = first.replace(" ", "").lower()
    if key in _ALIASES:
        return _ALIASES[key]
    for known in REBASE:
        if known.lower() == key:
            return known
    return first.replace(" ", "")


def cut_site_for(name: str) -> CutSite | None:
    """The REBASE cut site for a stock name, when it is one we know."""
    notation = REBASE.get(normalize_name(name))
    return parse_cut_site(notation) if notation else None


def find_cuts(seq: str, site: CutSite, circular: bool = False, enzyme: str = "") -> list[EnzymeHit]:
    """Every cut this enzyme makes, 1-based, wrapping around a circular map.

    `pos` is the base after which the top strand is cut, so a cut at the very
    end of a circular plasmid comes back as a position inside it.
    """
    s = (seq or "").upper()
    n = len(s)
    if not s or not site.site:
        return []
    pad = len(site.site) + max(site.top, site.bottom) + 4
    text = s + s[:pad - 1] if circular and n > pad else s
    pattern = site.regex()
    found: dict[int, EnzymeHit] = {}

    def add(start: int, offset: int, strand: str) -> None:
        cut = start + offset                       # 0-based count of bases before the cut
        pos = ((cut - 1) % n) + 1 if n else 0
        if pos not in found:
            found[pos] = EnzymeHit(enzyme, pos, ((start) % n) + 1,
                                   ((start + len(site.site) - 1) % n) + 1, strand)

    for m in pattern.finditer(text):
        if m.start() >= n:
            continue
        add(m.start(), site.top, "+")
    if not site.palindromic:
        rc_pattern = CutSite(reverse_complement(site.site), 0, 0).regex()
        for m in rc_pattern.finditer(text):
            if m.start() >= n:
                continue
            # on the bottom strand the enzyme reads right to left: the top-strand
            # cut sits `bottom` bases to the left of the site's right-hand end
            add(m.start() + len(site.site), -site.bottom, "-")
    return sorted(found.values(), key=lambda h: h.pos)
