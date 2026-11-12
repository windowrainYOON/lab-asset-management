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
from .summary import SCOPE_OF, IndexCache, PlasmidSummary, stamp_of

SEQ_FOLDER = "Sequence Map"
DNA_SUFFIX = ".dna"
CUT_SITE_COLUMN = "cut site"
OLIGO_CATEGORY = "oligos"
ENZYME_CATEGORY = "restriction enzyme"

# How many opened maps to keep in memory. A map plus its lookup index is about
# a megabyte, so holding every one of a few hundred would cost hundreds; the
# table runs off the summaries instead and only what the user looks at is kept.
MAX_OPEN = 3


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
class FileEntry:
    """One .dna file the folder holds, before anything has been read from it."""

    key: str                  # Drive file id, or the path for a local folder
    name: str                 # file name without .dna
    group: str                # subfolder: "Common Plasmid" / "Personal Plasmid"
    stamp: str = ""           # the file's revision, so a changed map is read again
    path: Path | None = None  # set when the file is already on this Mac


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
        self.entries: dict[str, FileEntry] = {}          # every map the folder holds
        self.summaries: dict[str, PlasmidSummary] = {}   # what the table draws
        self.cache = IndexCache(self.cache_dir)
        self._download = None
        self._open_order: list[str] = []
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
        """A plain folder on this Mac, used by the tests and by a local project.

        Small enough to read whole, so every map is parsed here and the table
        has its rows without a second pass.
        """
        folder = Path(folder)
        paths = [(p, sub.name) for sub in sorted(q for q in folder.iterdir() if q.is_dir())
                 for p in sorted(sub.glob(f"*{DNA_SUFFIX}"))]
        paths += [(p, "") for p in sorted(folder.glob(f"*{DNA_SUFFIX}"))]
        for path, group in paths:
            key = str(path)
            try:
                stamp = str(path.stat().st_mtime_ns)
            except OSError:
                stamp = ""
            self.entries[key] = FileEntry(key, path.stem, group, stamp, path)
            self._add(key, path.stem, group, path.read_bytes())
        self.index()

    def load_remote(self) -> None:
        """Every .dna file under the Drive folder's Sequence Map subfolder."""
        if self.remote is None or not hasattr(self.remote, "list_sequence_files"):
            return
        self.load_entries(self.remote.list_sequence_files(), self.remote.download)

    def load_entries(self, files, download) -> None:
        """Note which maps the folder holds, without reading any of them.

        Takes the listing and a `download(file_id, dest)` callable rather than a
        remote object, so it works with whichever remote the session has. A file
        is only fetched when `index` or `open` actually needs its sequence.
        """
        if self.cache_dir is None:
            return
        self._download = download
        for rf in files:
            name = nfc(rf.name)
            if not name.lower().endswith(DNA_SUFFIX) or name.startswith("~$"):
                continue
            stamp = str(getattr(rf, "version", "") or getattr(rf, "modified_time", "") or "")
            self.entries[rf.id] = FileEntry(rf.id, Path(name).stem,
                                            nfc(getattr(rf, "folder", "")), stamp)

    def _local_path(self, entry: FileEntry) -> Path:
        if entry.path is not None:
            return entry.path
        return self.cache_dir / "sequences" / f"{entry.key}{DNA_SUFFIX}"

    def _bytes(self, entry: FileEntry) -> bytes:
        """The file's content, fetching it when this Mac has no copy or a stale one.

        The stamp is kept beside the download. Without it a map edited in
        SnapGene and put back in the folder would keep showing the old version,
        since the file would already be there.
        """
        path = self._local_path(entry)
        if entry.path is not None:
            return path.read_bytes()
        mark = path.with_suffix(".stamp")
        have = mark.read_text("utf-8").strip() if mark.exists() else ""
        if not path.exists() or (entry.stamp and have != entry.stamp):
            if self._download is None:
                raise RuntimeError("파일을 받을 수 없습니다")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._download(entry.key, path)
            if entry.stamp:
                mark.write_text(entry.stamp, "utf-8")
        return path.read_bytes()

    def _fail(self, name: str, exc: object) -> None:
        """Report a file we could not read, once, however often it is retried."""
        line = f"{name}: {exc}"
        if line not in self.errors:
            self.errors.append(line)

    def _add(self, key: str, name: str, group: str, data: bytes) -> None:
        try:
            record = parse_dna(data, name)
        except ValueError as exc:
            self._fail(name, exc)
            return
        self.plasmids[key] = Plasmid(key, name, group, record)

    def groups(self) -> list[str]:
        seen = []
        for entry in self.entries.values():
            if entry.group and entry.group not in seen:
                seen.append(entry.group)
        return seen

    def in_group(self, group: str) -> list[PlasmidSummary]:
        """The table's rows: summaries, not sequences."""
        return sorted((s for s in self.summaries.values() if s.group == group),
                      key=lambda s: s.name.lower())

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

    def enzyme_stamp(self) -> str:
        return stamp_of(sorted(f"{k}|{e.notation}" for k, e in self.enzymes.items()))

    def oligo_stamp(self) -> str:
        return stamp_of(sorted(f"{k}|{o.seq}" for k, o in self.oligos.items())
                        + [self.min_match, self.full_length, self.min_tm])

    def pending(self) -> list[FileEntry]:
        """The maps whose summary has to be built or built again."""
        enz, olig = self.enzyme_stamp(), self.oligo_stamp()
        out = []
        for entry in self.entries.values():
            known = self.cache.get(entry.key)
            if known is None or not known.fresh(entry.stamp, enz, olig):
                out.append(entry)
        return out

    def index(self, progress=None) -> int:
        """Bring every summary up to date, reading only the maps that need it.

        `progress(done, total, name)` is called before each file so a caller can
        show how far along it is. Returns how many maps were read.
        """
        enz, olig = self.enzyme_stamp(), self.oligo_stamp()
        todo = self.pending()
        total = len(todo)
        for done, entry in enumerate(todo, 1):
            if progress is not None:
                progress(done, total, entry.name)
            record = self._record_of(entry)
            if record is None:
                continue
            self.cache.put(self._summarize(entry, record, enz, olig))
        self.cache.keep_only(self.entries)
        if total or self.cache.entries:
            self.cache.save()
        self.summaries = {k: s for k, s in self.cache.entries.items() if k in self.entries}
        return total

    def _record_of(self, entry: FileEntry) -> DnaRecord | None:
        plasmid = self.plasmids.get(entry.key)
        if plasmid is not None:
            return plasmid.record
        try:
            return parse_dna(self._bytes(entry), entry.name)
        except Exception as exc:  # noqa: BLE001  one bad file must not lose the rest
            self._fail(entry.name, exc)
            return None

    def _summarize(self, entry: FileEntry, record: DnaRecord,
                   enz: str, olig: str) -> PlasmidSummary:
        cutting = {stock.display: len(find_cuts(record.seq, stock.site, record.circular, key))
                   for key, stock in self.enzymes.items() if stock.site is not None}
        hits = match_all(self.oligos.values(), {entry.key: record}, min_match=self.min_match,
                         full_length=self.full_length, min_tm=self.min_tm)
        anneal = arm = 0
        names: list[str] = []
        for name, found in hits.items():
            for hit in found:
                names.append(name)
                if hit.anneals:
                    anneal += 1
                else:
                    arm += 1
        return PlasmidSummary(entry.key, entry.name, entry.group, entry.stamp,
                              len(record.seq), record.circular, len(record.features),
                              cutting, anneal, arm, sorted(set(names)), enz, olig)

    # ---- opening one map --------------------------------------------------
    def open(self, key: str) -> Plasmid | None:
        """Read one map in full: its sequence, its cut sites and the oligos on it.

        Only this is expensive, and only this many maps are kept: the table
        itself never needs a sequence.
        """
        plasmid = self.plasmids.get(key)
        if plasmid is None:
            entry = self.entries.get(key)
            if entry is None:
                return None
            try:
                record = parse_dna(self._bytes(entry), entry.name)
            except Exception as exc:  # noqa: BLE001
                self._fail(entry.name, exc)
                return None
            plasmid = Plasmid(key, entry.name, entry.group, record)
            self.plasmids[key] = plasmid
            self._cut_one(plasmid)
            self._match_one(plasmid)
        if key in self._open_order:
            self._open_order.remove(key)
        self._open_order.append(key)
        self._evict()
        return plasmid

    def _evict(self) -> None:
        """Let go of maps the user has moved on from, keeping their summaries."""
        while len(self._open_order) > MAX_OPEN:
            gone = self._open_order.pop(0)
            if self.entries.get(gone) is None or self.entries[gone].path is not None:
                continue            # a local folder keeps everything anyway
            self.plasmids.pop(gone, None)
            self.by_plasmid.pop(gone, None)
            for name in list(self.hits):
                self.hits[name] = [h for h in self.hits[name] if h.plasmid != gone]

    def reindex(self) -> None:
        self._cut_all()
        self._match_all()
        self.index()

    def _cut_one(self, plasmid: Plasmid) -> None:
        cuts: dict[str, list[EnzymeHit]] = {}
        for key, stock in self.enzymes.items():
            if stock.site is None:
                continue
            cuts[key] = find_cuts(plasmid.record.seq, stock.site,
                                  plasmid.record.circular, key)
        plasmid.cuts = cuts

    def _match_one(self, plasmid: Plasmid) -> None:
        found = match_all(self.oligos.values(), {plasmid.key: plasmid.record},
                          min_match=self.min_match, full_length=self.full_length,
                          min_tm=self.min_tm)
        here: list[OligoHit] = []
        for name, hits in found.items():
            self.hits.setdefault(name, [])
            self.hits[name] = [h for h in self.hits[name] if h.plasmid != plasmid.key] + hits
            here += hits
        here.sort(key=lambda h: (h.start, h.oligo))
        self.by_plasmid[plasmid.key] = here

    def _cut_all(self) -> None:
        for plasmid in self.plasmids.values():
            self._cut_one(plasmid)

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
