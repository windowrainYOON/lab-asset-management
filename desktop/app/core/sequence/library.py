"""The Sequence Map library: plasmid maps, their cut sites and the oligos on them.

Built from the Drive folder's "Sequence Map" subfolder, whose children
("Common Plasmid", "Personal Plasmid") become the two tabs. Both are indexed
and shown to everyone.

Nothing here writes a .dna file. The only file this module can change is the
restriction enzyme stock sheet, and only to fill in its "cut site" column, which
`fill_cut_sites` does when asked.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .enzymes import CutSite, EnzymeHit, cut_site_for, find_cuts, normalize_name, parse_cut_site
from .oligo_match import MIN_MATCH, MIN_TM, FULL_LENGTH, Oligo, OligoHit, match_all
from .snapgene import DnaRecord, parse_dna

SEQ_FOLDER = "Sequence Map"
DNA_SUFFIX = ".dna"
CUT_SITE_COLUMN = "cut site"
OLIGO_CATEGORY = "oligos"
ENZYME_CATEGORY = "restriction enzyme"
SCOPE_OF = {"common plasmid": "common", "personal plasmid": "personal"}


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


@dataclass
class Plasmid:
    key: str                  # file id, or the path for a local folder
    name: str                 # file name without .dna
    group: str                # subfolder: "Common Plasmid" / "Personal Plasmid"
    record: DnaRecord
    cuts: dict[str, list[EnzymeHit]] = field(default_factory=dict)

    @property
    def scope(self) -> str:
        return SCOPE_OF.get(self.group.lower(), "common")

    @property
    def length(self) -> int:
        return len(self.record.seq)

    @property
    def topology(self) -> str:
        return "circular" if self.record.circular else "linear"

    def unique_cutters(self) -> list[str]:
        return sorted(n for n, hits in self.cuts.items() if len(hits) == 1)

    def cutters(self, times: int) -> list[str]:
        return sorted(n for n, hits in self.cuts.items() if len(hits) == times)

    def non_cutters(self) -> list[str]:
        return sorted(n for n, hits in self.cuts.items() if not hits)

    def cuts_near(self, pos: int, window: int = 60) -> list[tuple[str, EnzymeHit]]:
        out = []
        for name, hits in self.cuts.items():
            for hit in hits:
                if abs(hit.pos - pos) <= window:
                    out.append((name, hit))
        return sorted(out, key=lambda t: t[1].pos)


@dataclass
class EnzymeStock:
    name: str                 # as written in the stock file
    site: CutSite | None
    item_id: int | None = None
    notation: str = ""
    from_rebase: bool = False  # the cut site column was empty and we filled it in

    @property
    def display(self) -> str:
        return self.name


class SequenceLibrary:
    """Plasmid maps plus everything indexed against them.

    `load` only reads files; matching is done once by `reindex`, which the view
    calls after the stock categories are known.
    """

    def __init__(self, remote=None, cache_dir: Path | None = None) -> None:
        self.remote = remote
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.plasmids: dict[str, Plasmid] = {}
        self.oligos: dict[str, Oligo] = {}
        self.enzymes: dict[str, EnzymeStock] = {}
        self.hits: dict[str, list[OligoHit]] = {}        # oligo name -> hits
        self.by_plasmid: dict[str, list[OligoHit]] = {}  # plasmid key -> hits
        self.errors: list[str] = []
        self.min_match = MIN_MATCH
        self.full_length = FULL_LENGTH
        self.min_tm = MIN_TM

    # ---- reading maps ----------------------------------------------------
    def load_folder(self, folder: Path) -> None:
        """A plain folder on this Mac, used by the tests and by a local project."""
        folder = Path(folder)
        for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
            for path in sorted(sub.glob(f"*{DNA_SUFFIX}")):
                self._add(str(path), path.stem, sub.name, path.read_bytes())
        for path in sorted(folder.glob(f"*{DNA_SUFFIX}")):
            self._add(str(path), path.stem, "", path.read_bytes())

    def load_remote(self) -> None:
        """Every .dna file under the Drive folder's Sequence Map subfolder."""
        if self.remote is None or not hasattr(self.remote, "list_sequence_files"):
            return
        self.load_entries(self.remote.list_sequence_files(), self.remote.download)

    def load_entries(self, files, download) -> None:
        """Cache and read a list of remote files.

        Takes the listing and a `download(file_id, dest)` callable rather than a
        remote object, so it works with whichever remote the session has.
        """
        if self.cache_dir is None:
            return
        files = list(files)
        dest_dir = self.cache_dir / "sequences"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for rf in files:
            name = nfc(rf.name)
            if not name.lower().endswith(DNA_SUFFIX) or name.startswith("~$"):
                continue
            dest = dest_dir / f"{rf.id}{DNA_SUFFIX}"
            try:
                if not dest.exists():
                    download(rf.id, dest)
                self._add(rf.id, Path(name).stem, nfc(getattr(rf, "folder", "")), dest.read_bytes())
            except Exception as exc:  # noqa: BLE001  one bad file must not lose the rest
                self.errors.append(f"{name}: {exc}")

    def _add(self, key: str, name: str, group: str, data: bytes) -> None:
        try:
            record = parse_dna(data, name)
        except ValueError as exc:
            self.errors.append(f"{name}: {exc}")
            return
        self.plasmids[key] = Plasmid(key, name, group, record)

    def groups(self) -> list[str]:
        seen = []
        for p in self.plasmids.values():
            if p.group and p.group not in seen:
                seen.append(p.group)
        return seen

    def in_group(self, group: str) -> list[Plasmid]:
        return sorted((p for p in self.plasmids.values() if p.group == group),
                      key=lambda p: p.name.lower())

    # ---- stock ------------------------------------------------------------
    def read_oligos(self, project) -> None:
        self.oligos = {}
        cat = _category(project, OLIGO_CATEGORY)
        if cat is None:
            return
        cols = _columns(project, cat.id)
        name_f = _pick(cols, ("제품명", "name", "이름", "올리고"))
        seq_f = _pick(cols, ("contents", "서열", "sequence"))
        if seq_f is None:
            return
        for item in project.items(cat.id):
            seq = str(item.get(seq_f.id) or "")
            if not seq.strip():
                continue
            name = str(item.get(name_f.id) or "").strip() if name_f else ""
            oligo = Oligo(
                name=name or f"#{item.id}",
                seq=seq,
                order=_value(item, cols, ("주문번호", "order")),
                purification=_value(item, cols, ("purification", "정제")),
                scale=_value(item, cols, ("scale",)),
                ordered=_value(item, cols, ("주문일자", "주문일", "ordered")),
            )
            self.oligos.setdefault(oligo.name, oligo)

    def read_enzymes(self, project) -> None:
        """The stock list, reading the cut site column and falling back to REBASE."""
        self.enzymes = {}
        cat = _category(project, ENZYME_CATEGORY)
        if cat is None:
            for name in ():
                pass
            return
        cols = _columns(project, cat.id)
        name_f = _pick(cols, ("restriction enzyme stock", "효소", "enzyme", "name", "제품명"))
        cut_f = _pick(cols, (CUT_SITE_COLUMN, "cut_site", "절단 위치"))
        if name_f is None:
            return
        for item in project.items(cat.id):
            raw = str(item.get(name_f.id) or "").strip()
            if not raw or raw.lower().startswith(("최근", "배송", "box")):
                continue
            key = normalize_name(raw)
            if not key or key in self.enzymes:
                continue
            notation = str(item.get(cut_f.id) or "").strip() if cut_f else ""
            site = parse_cut_site(notation)
            from_rebase = False
            if site is None:
                site = cut_site_for(raw)
                from_rebase = site is not None
                notation = site.notation if site else ""
            if site is None:
                continue
            self.enzymes[key] = EnzymeStock(raw, site, item.id, notation, from_rebase)

    def missing_cut_sites(self) -> list[EnzymeStock]:
        return [e for e in self.enzymes.values() if e.from_rebase]

    def fill_cut_sites(self, project) -> int:
        """Write the cut site column into the restriction enzyme stock sheet.

        Adds the column when it is not there yet and fills only empty cells, so
        a notation someone typed by hand is never overwritten. Returns how many
        cells were written.
        """
        cat = _category(project, ENZYME_CATEGORY)
        if cat is None:
            return 0
        cols = _columns(project, cat.id)
        cut_f = _pick(cols, (CUT_SITE_COLUMN, "cut_site"))
        if cut_f is None:
            cut_f = project.add_field(cat.id, CUT_SITE_COLUMN, "text")
        written = 0
        for stock in self.enzymes.values():
            if stock.item_id is None or not stock.notation:
                continue
            item = project.item(stock.item_id)
            if item is None or str(item.get(cut_f.id) or "").strip():
                continue
            project.set_value(stock.item_id, cut_f.id, stock.notation)
            stock.from_rebase = False
            written += 1
        return written

    # ---- indexing ---------------------------------------------------------
    def set_limits(self, min_match: int | None = None, min_tm: float | None = None,
                   full_length: int | None = None) -> None:
        if min_match:
            self.min_match = max(4, int(min_match))
        if min_tm is not None:
            self.min_tm = float(min_tm)
        if full_length:
            self.full_length = int(full_length)

    def reindex(self) -> None:
        self._cut_all()
        self._match_all()

    def _cut_all(self) -> None:
        for plasmid in self.plasmids.values():
            cuts: dict[str, list[EnzymeHit]] = {}
            for key, stock in self.enzymes.items():
                if stock.site is None:
                    continue
                cuts[key] = find_cuts(plasmid.record.seq, stock.site,
                                      plasmid.record.circular, key)
            plasmid.cuts = cuts

    def _match_all(self) -> None:
        records = {key: p.record for key, p in self.plasmids.items()}
        self.hits = match_all(self.oligos.values(), records, min_match=self.min_match,
                              full_length=self.full_length, min_tm=self.min_tm)
        by: dict[str, list[OligoHit]] = {key: [] for key in records}
        for hits in self.hits.values():
            for hit in hits:
                by.setdefault(hit.plasmid, []).append(hit)
        for key in by:
            by[key].sort(key=lambda h: (h.start, h.oligo))
        self.by_plasmid = by

    # ---- lookups the view uses -------------------------------------------
    def oligo_hits(self, name: str) -> list[OligoHit]:
        return self.hits.get(name, [])

    def plasmid_hits(self, key: str) -> list[OligoHit]:
        return self.by_plasmid.get(key, [])

    def enzyme_cuts(self, name: str) -> dict[str, list[EnzymeHit]]:
        key = normalize_name(name)
        return {p.name: p.cuts.get(key, []) for p in self.plasmids.values()}


# ---- small helpers over the project ---------------------------------------
def _category(project, name: str):
    if project is None:
        return None
    want = name.lower()
    for scope in (None,):
        for cat in project.categories(scope):
            if cat.name.strip().lower().startswith(want):
                return cat
    return None


def _columns(project, category_id: int) -> dict[str, object]:
    return {f.name.strip().lower(): f for f in project.fields(category_id)}


def _pick(cols: dict, names: tuple[str, ...]):
    for want in names:
        for key, f in cols.items():
            if key == want.lower() or key.startswith(want.lower()):
                return f
    return None


def _value(item, cols: dict, names: tuple[str, ...]) -> str:
    f = _pick(cols, names)
    return str(item.get(f.id) or "").strip() if f is not None else ""
