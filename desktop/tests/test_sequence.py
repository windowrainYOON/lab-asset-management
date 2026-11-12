"""Sequence Map: SnapGene parsing, restriction sites and oligo matching.

The fixture plasmid is built here rather than copied from the lab's Drive
folder, so no lab sequence is kept in the repository. Sites and oligo binding
regions are planted at known positions, which is what the assertions check.
"""
from __future__ import annotations

import random
import struct

import pytest

from app.core.sequence import (
    SequenceLibrary, find_cuts, match_oligo, melting_temp, parse_cut_site, parse_dna,
)
from app.core.sequence.enzymes import cut_site_for, normalize_name, reverse_complement
from app.core.sequence.oligo_match import Oligo

LENGTH = 3000
XHOI_AT = 500        # 0-based index where CTCGAG is planted
NDEI_AT = 1200
ECORV_AT = 2000


def _random_seq(n: int, seed: int = 7) -> str:
    rnd = random.Random(seed)
    return "".join(rnd.choice("ACGT") for _ in range(n))


def _plant(seq: str, at: int, site: str) -> str:
    return seq[:at] + site + seq[at + len(site):]


def _clean(seq: str) -> str:
    """Remove accidental copies of the sites we plant, so counts are exact."""
    for site in ("CTCGAG", "CATATG", "GATATC", "GGATCC", "GGTCTC"):
        while True:
            i = seq.find(site)
            if i < 0:
                break
            seq = seq[:i] + "A" + seq[i + 1:]
        rc = reverse_complement(site)
        while rc != site:
            i = seq.find(rc)
            if i < 0:
                break
            seq = seq[:i] + "A" + seq[i + 1:]
    return seq


def _block(kind: int, payload: bytes) -> bytes:
    return bytes([kind]) + struct.pack(">I", len(payload)) + payload


def _dna_file(seq: str, circular: bool = True) -> bytes:
    features = (
        '<Features><Feature name="ori" type="rep_origin">'
        '<Segment range="100-700"/></Feature>'
        '<Feature name="TagX" type="CDS"><Segment range="1190-1260"/></Feature>'
        "</Features>"
    )
    header = b"SnapGene" + bytes([0, 1, 0, 1, 0, 0, 0, 13])  # type 9: the document header
    return (_block(9, header)
            + _block(0, bytes([1 if circular else 0]) + seq.encode())
            + _block(10, features.encode()))


def _fixture_seq() -> str:
    seq = _clean(_random_seq(LENGTH))
    seq = _plant(seq, XHOI_AT, "CTCGAG")
    seq = _plant(seq, NDEI_AT, "CATATG")
    seq = _plant(seq, ECORV_AT, "GATATC")
    assert len(seq) == LENGTH
    return seq


_PLASMID_SEQ = _fixture_seq()


@pytest.fixture()
def plasmid_bytes() -> bytes:
    return _dna_file(_PLASMID_SEQ)


# ---- the .dna format ------------------------------------------------------
def test_parses_sequence_topology_and_features(plasmid_bytes):
    rec = parse_dna(plasmid_bytes, "fixture")
    assert len(rec) == LENGTH
    assert rec.circular is True
    assert [f.name for f in rec.features] == ["ori", "TagX"]
    assert (rec.features[0].start, rec.features[0].end) == (100, 700)


def test_rejects_a_file_that_is_not_snapgene():
    with pytest.raises(ValueError):
        parse_dna(b"PK\x03\x04 not a map", "x")


def test_rejects_a_truncated_block():
    broken = _block(9, b"SnapGene") + bytes([0]) + struct.pack(">I", 999) + b"\x01ACGT"
    with pytest.raises(ValueError):
        parse_dna(broken, "x")


# ---- cut site notation ----------------------------------------------------
@pytest.mark.parametrize("notation, site, top, bottom, label", [
    ("G^GATC_C", "GGATCC", 1, 5, "5′ 돌출 4 nt"),
    ("G_GTAC^C", "GGTACC", 5, 1, "3′ 돌출 4 nt"),
    ("GAT^_ATC", "GATATC", 3, 3, "blunt"),
    ("GGTCTCN^NNNN_N", "GGTCTC", 7, 11, "5′ 돌출 4 nt"),
])
def test_reads_the_cut_site_column(notation, site, top, bottom, label):
    cut = parse_cut_site(notation)
    assert (cut.site, cut.top, cut.bottom, cut.end_label) == (site, top, bottom, label)


def test_an_empty_or_broken_cut_site_cell_is_ignored():
    assert parse_cut_site("") is None
    assert parse_cut_site("   ") is None
    assert parse_cut_site("GGATCC") is None      # no '^': nothing says where it cuts
    assert parse_cut_site("ZZ^ZZ") is None


def test_stock_names_with_hf_or_spaces_find_their_enzyme():
    assert normalize_name("BamHI / BamHI-HF") == "BamHI"
    assert normalize_name("Bgl II") == "BglII"
    assert normalize_name("Mfel-HF") == "MfeI"
    assert cut_site_for("EcoRI / EcoRI-HF").site == "GAATTC"


# ---- where enzymes cut ----------------------------------------------------
def test_finds_each_planted_site_once(plasmid_bytes):
    rec = parse_dna(plasmid_bytes)
    xhoi = find_cuts(rec.seq, cut_site_for("XhoI"), rec.circular, "XhoI")
    assert [h.pos for h in xhoi] == [XHOI_AT + 1]          # C^TCGAG cuts after the C
    assert [h.pos for h in find_cuts(rec.seq, cut_site_for("NdeI"), rec.circular)] == [NDEI_AT + 2]
    assert [h.pos for h in find_cuts(rec.seq, cut_site_for("EcoRV"), rec.circular)] == [ECORV_AT + 3]


def test_an_enzyme_with_no_site_does_not_cut(plasmid_bytes):
    rec = parse_dna(plasmid_bytes)
    assert find_cuts(rec.seq, cut_site_for("BamHI"), rec.circular) == []


def test_a_site_spanning_the_origin_of_a_circular_map_is_found():
    seq = _clean(_random_seq(400, seed=3))
    seq = "TCGAG" + seq[5:-1] + "C"               # CTCGAG wraps across position 1
    rec = parse_dna(_dna_file(seq, circular=True))
    hits = find_cuts(rec.seq, cut_site_for("XhoI"), rec.circular, "XhoI")
    assert [h.pos for h in hits] == [len(seq)]
    assert find_cuts(rec.seq, cut_site_for("XhoI"), False) == []  # linear: no wrap


def test_a_non_palindromic_enzyme_is_found_on_both_strands():
    site = cut_site_for("BsaI")                   # GGTCTC, cuts downstream
    seq = _clean(_random_seq(300, seed=11))
    seq = _plant(seq, 50, "GGTCTC")
    seq = _plant(seq, 150, reverse_complement("GGTCTC"))
    hits = find_cuts(seq, site, False, "BsaI")
    assert len(hits) == 2
    assert {h.strand for h in hits} == {"+", "-"}


# ---- melting temperature --------------------------------------------------
def test_tm_rises_with_length_and_gc():
    assert melting_temp("ATATATATATAT") < melting_temp("GCGCGCGCGCGC")
    assert melting_temp("ATGCATGCATGC") < melting_temp("ATGCATGCATGCATGC")
    assert melting_temp("") == 0.0
    assert melting_temp("ACGTN") == 0.0


# ---- oligo matching -------------------------------------------------------
def _record(plasmid_bytes):
    return parse_dna(plasmid_bytes, "fixture")


def test_a_forward_primer_whose_3_end_binds_is_read_as_annealing(plasmid_bytes):
    rec = _record(plasmid_bytes)
    oligo = Oligo("fwd", rec.seq[1190:1212])
    hit = match_oligo(oligo, rec, "p")
    assert (hit.start, hit.end, hit.strand) == (1191, 1212, "+")
    assert hit.full and hit.anneals and hit.kind == "anneal"
    assert "TagX" in hit.features


def test_a_reverse_primer_is_matched_on_the_other_strand(plasmid_bytes):
    rec = _record(plasmid_bytes)
    oligo = Oligo("rev", reverse_complement(rec.seq[1190:1212]))
    hit = match_oligo(oligo, rec, "p")
    assert (hit.start, hit.end, hit.strand) == (1191, 1212, "-")
    assert hit.anneals is True


def test_a_hit_on_only_the_5_prime_side_is_read_as_a_homology_arm(plasmid_bytes):
    """Gibson/restriction primer: its 5' half matches this vector, its 3' end
    belongs to the insert, so it cannot prime here."""
    rec = _record(plasmid_bytes)
    tail = "GTGGTGGTGGTGGTG"                       # not in the plasmid
    oligo = Oligo("arm", rec.seq[1190:1212] + tail)
    hit = match_oligo(oligo, rec, "p")
    assert hit.length == 22
    assert hit.anneals is False and hit.kind == "arm"
    assert hit.q_from == 1 and hit.q_to == 22


def test_a_reverse_oligo_keeps_annealing_when_only_its_5_prime_end_is_a_tail(plasmid_bytes):
    """A reverse primer with a 5' tail still primes: its 3' end is on the vector."""
    rec = _record(plasmid_bytes)
    oligo = Oligo("rev_tail", "GTGGTGGTGGTGGTG" + reverse_complement(rec.seq[1190:1212]))
    hit = match_oligo(oligo, rec, "p")
    assert hit.strand == "-" and hit.anneals is True


def test_a_reverse_oligo_whose_3_prime_end_is_off_the_vector_is_an_arm(plasmid_bytes):
    rec = _record(plasmid_bytes)
    oligo = Oligo("rev_arm", reverse_complement(rec.seq[1190:1212]) + "GTGGTGGTGGTGGTG")
    hit = match_oligo(oligo, rec, "p")
    assert hit.strand == "-" and hit.anneals is False and hit.kind == "arm"


def test_short_matches_need_the_melting_temperature(plasmid_bytes):
    rec = _record(plasmid_bytes)
    oligo = Oligo("short", rec.seq[1190:1202])     # 12 nt
    assert match_oligo(oligo, rec, "p", min_match=15) is None
    loose = match_oligo(oligo, rec, "p", min_match=10, full_length=15, min_tm=0.0)
    assert loose is not None and loose.length == 12
    strict = match_oligo(oligo, rec, "p", min_match=10, full_length=15, min_tm=90.0)
    assert strict is None


def test_an_oligo_that_is_nowhere_in_the_plasmid_has_no_hit(plasmid_bytes):
    rec = _record(plasmid_bytes)
    assert match_oligo(Oligo("none", "ACACACACACACACACACAC"), rec, "p") is None


# ---- the library ----------------------------------------------------------
def test_library_reads_a_folder_and_keeps_the_group(tmp_path, plasmid_bytes):
    common = tmp_path / "Common Plasmid"
    personal = tmp_path / "Personal Plasmid"
    common.mkdir()
    personal.mkdir()
    (common / "pFixture.dna").write_bytes(plasmid_bytes)
    (personal / "pMine.dna").write_bytes(plasmid_bytes)
    (common / "broken.dna").write_bytes(b"not a map")

    lib = SequenceLibrary()
    lib.load_folder(tmp_path)
    assert sorted(p.name for p in lib.plasmids.values()) == ["pFixture", "pMine"]
    assert lib.groups() == ["Common Plasmid", "Personal Plasmid"]
    assert [p.scope for p in lib.in_group("Personal Plasmid")] == ["personal"]
    assert lib.errors and "broken" in lib.errors[0]


def test_library_indexes_enzymes_and_oligos_together(tmp_path, plasmid_bytes):
    folder = tmp_path / "Common Plasmid"
    folder.mkdir()
    (folder / "pFixture.dna").write_bytes(plasmid_bytes)
    lib = SequenceLibrary()
    lib.load_folder(tmp_path)
    plasmid = next(iter(lib.plasmids.values()))

    from app.core.sequence.library import EnzymeStock
    lib.enzymes = {name: EnzymeStock(name, cut_site_for(name))
                   for name in ("XhoI", "NdeI", "BamHI")}
    lib.oligos = {"fwd": Oligo("fwd", plasmid.record.seq[1190:1212])}
    lib.reindex()

    assert plasmid.unique_cutters() == ["NdeI", "XhoI"]
    assert plasmid.non_cutters() == ["BamHI"]
    assert [h.oligo for h in lib.plasmid_hits(plasmid.key)] == ["fwd"]
    assert lib.oligo_hits("fwd")[0].anneals is True
    assert [n for n, _ in plasmid.cuts_near(NDEI_AT + 2, window=5)] == ["NdeI"]


# ---- the view -------------------------------------------------------------
def _qt_app():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _loaded_library(tmp_path, plasmid_bytes):
    from app.core.sequence.library import EnzymeStock
    folder = tmp_path / "Common Plasmid"
    folder.mkdir()
    (folder / "pFixture.dna").write_bytes(plasmid_bytes)
    lib = SequenceLibrary()
    lib.load_folder(tmp_path)
    plasmid = next(iter(lib.plasmids.values()))
    lib.enzymes = {name: EnzymeStock(name, cut_site_for(name)) for name in ("XhoI", "NdeI")}
    lib.oligos = {
        "fwd": Oligo("fwd", plasmid.record.seq[1190:1212], order="OL1", purification="HAP"),
        "rev": Oligo("rev", reverse_complement(plasmid.record.seq[1240:1262])),
    }
    lib.reindex()
    return lib, plasmid


def test_the_canvas_draws_and_picks_what_was_clicked(tmp_path, plasmid_bytes):
    _qt_app()
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QPixmap, QPainter
    from app.ui.sequence_view import Pick, SequenceCanvas

    lib, plasmid = _loaded_library(tmp_path, plasmid_bytes)
    canvas = SequenceCanvas()
    canvas.resize(900, 600)
    canvas.set_plasmid(lib, plasmid)
    assert canvas.sizeHint().height() > 0

    pixmap = QPixmap(QSize(900, canvas.sizeHint().height()))
    painter = QPainter(pixmap)
    painter.end()
    canvas.render(pixmap)                      # paints every row without raising
    kinds = {pick.kind for _, pick in canvas._hits}
    assert {"enzyme", "oligo", "feature"} <= kinds

    rect, pick = next((r, p) for r, p in canvas._hits if p.kind == "enzyme")
    canvas.select(pick)
    assert canvas.selection == Pick("enzyme", pick.name)


def test_the_inspector_shows_a_different_panel_for_each_kind(tmp_path, plasmid_bytes):
    _qt_app()
    from app.ui.sequence_view import SequenceInspector

    lib, plasmid = _loaded_library(tmp_path, plasmid_bytes)
    inspector = SequenceInspector()

    inspector.show_plasmid(lib, plasmid)
    assert "선택한 플라스미드" in _texts(inspector)

    inspector.show_enzyme(lib, plasmid, "XhoI")
    text = _texts(inspector)
    assert "restriction enzyme" in text and "C^TCGA_G" in text

    inspector.show_oligo(lib, plasmid, "fwd")
    text = _texts(inspector)
    assert "oligo" in text and "Oligos 재고 현황" in text and "OL1" in text


def _texts(widget) -> str:
    from PySide6.QtWidgets import QLabel
    return " ".join(child.text() for child in widget.findChildren(QLabel))


def test_an_empty_group_says_so_instead_of_failing(tmp_path):
    _qt_app()
    from app.ui.sequence_view import SequenceInspector
    inspector = SequenceInspector()
    inspector.show_plasmid(SequenceLibrary(), None)
    assert "플라스미드 없음" in _texts(inspector)


# ---- the stored index -----------------------------------------------------
class _Remote:
    """One .dna file in a pretend Drive folder."""

    def __init__(self, key: str, name: str, group: str, version: str):
        self.id = key
        self.name = name
        self.folder = group
        self.version = version
        self.modified_time = ""


def _stocked(lib):
    from app.core.sequence.library import EnzymeStock
    lib.enzymes = {name: EnzymeStock(name, cut_site_for(name)) for name in ("XhoI", "NdeI", "BamHI")}
    lib.oligos = {"fwd": Oligo("fwd", _PLASMID_SEQ[1190:1212], order="OL1")}


def _remote_library(tmp_path, plasmid_bytes, version="v1"):
    """A library over a fake folder, counting how often a file is fetched."""
    calls: list[str] = []

    def download(file_id, dest):
        calls.append(file_id)
        dest.write_bytes(plasmid_bytes)

    lib = SequenceLibrary(cache_dir=tmp_path / "cache")
    files = [_Remote("id-1", "pFixture.dna", "Common Plasmid", version)]
    lib.load_entries(files, download)
    _stocked(lib)
    return lib, calls, download, files


def test_listing_a_folder_downloads_nothing_until_it_is_indexed(tmp_path, plasmid_bytes):
    lib, calls, _, _ = _remote_library(tmp_path, plasmid_bytes)
    assert calls == []
    assert lib.plasmids == {}
    assert [e.name for e in lib.entries.values()] == ["pFixture"]
    assert len(lib.pending()) == 1

    assert lib.index() == 1
    assert calls == ["id-1"]
    summary = lib.summaries["id-1"]
    assert summary.length == LENGTH and summary.features
    assert summary.singles == ["NdeI", "XhoI"]
    assert summary.cutters == 2
    assert summary.anneal == 1
    assert lib.plasmids == {}          # the table never holds a sequence


def test_a_second_launch_reads_the_index_and_no_files(tmp_path, plasmid_bytes):
    lib, calls, download, files = _remote_library(tmp_path, plasmid_bytes)
    lib.index()
    assert len(calls) == 1

    again = SequenceLibrary(cache_dir=tmp_path / "cache")
    again.load_entries(files, download)
    _stocked(again)
    assert again.pending() == []
    assert again.index() == 0
    assert len(calls) == 1
    assert again.summaries["id-1"].singles == ["NdeI", "XhoI"]


def test_a_map_edited_in_snapgene_is_read_again(tmp_path, plasmid_bytes):
    lib, calls, download, _ = _remote_library(tmp_path, plasmid_bytes)
    lib.index()

    newer = SequenceLibrary(cache_dir=tmp_path / "cache")
    newer.load_entries([_Remote("id-1", "pFixture.dna", "Common Plasmid", "v2")], download)
    _stocked(newer)
    assert len(newer.pending()) == 1   # the revision moved, so the copy is stale
    newer.index()
    assert calls == ["id-1", "id-1"]


def test_changing_the_stock_makes_the_index_stale(tmp_path, plasmid_bytes):
    from app.core.sequence.library import EnzymeStock
    lib, _, _, _ = _remote_library(tmp_path, plasmid_bytes)
    lib.index()
    assert lib.pending() == []

    lib.enzymes["EcoRV"] = EnzymeStock("EcoRV", cut_site_for("EcoRV"))
    assert len(lib.pending()) == 1
    lib.index()
    assert "EcoRV" in lib.summaries["id-1"].cutting

    lib.set_limits(min_match=25)
    assert len(lib.pending()) == 1     # the thresholds are part of the stamp


def test_opening_a_map_is_what_reads_its_sequence(tmp_path, plasmid_bytes):
    lib, calls, _, _ = _remote_library(tmp_path, plasmid_bytes)
    lib.index()
    assert lib.plasmids == {}

    plasmid = lib.open("id-1")
    assert plasmid is not None
    assert plasmid.length == LENGTH
    assert plasmid.unique_cutters() == ["NdeI", "XhoI"]
    assert [h.oligo for h in lib.plasmid_hits("id-1")] == ["fwd"]
    assert calls == ["id-1"]           # the copy from indexing was reused

    assert lib.open("id-1") is plasmid


def test_only_the_last_few_maps_opened_are_kept(tmp_path, plasmid_bytes):
    from app.core.sequence.library import MAX_OPEN

    def download(file_id, dest):
        dest.write_bytes(plasmid_bytes)

    lib = SequenceLibrary(cache_dir=tmp_path / "cache")
    keys = [f"id-{n}" for n in range(MAX_OPEN + 2)]
    lib.load_entries([_Remote(k, f"p{k}.dna", "Common Plasmid", "v1") for k in keys], download)
    _stocked(lib)
    for key in keys:
        lib.open(key)
    assert len(lib.plasmids) == MAX_OPEN
    assert keys[0] not in lib.plasmids
    assert keys[-1] in lib.plasmids
    assert len(lib.summaries) == 0 or set(lib.summaries) <= set(keys)


def test_a_row_can_be_searched_by_enzyme_or_oligo(tmp_path, plasmid_bytes):
    lib, _, _, _ = _remote_library(tmp_path, plasmid_bytes)
    lib.index()
    summary = lib.summaries["id-1"]
    assert summary.matches("")
    assert summary.matches("pFix")
    assert summary.matches("ndei")      # an enzyme that cuts it
    assert summary.matches("fwd")       # an oligo that binds it
    assert not summary.matches("pUC19")


def test_the_table_lists_every_map_and_opening_one_reads_it(tmp_path, plasmid_bytes):
    _qt_app()
    from app.ui.sequence_view import SequenceView

    def download(file_id, dest):
        dest.write_bytes(plasmid_bytes)

    lib = SequenceLibrary(cache_dir=tmp_path / "cache")
    lib.load_entries([_Remote("id-1", "pFixture.dna", "Common Plasmid", "v1"),
                      _Remote("id-2", "pOther.dna", "Common Plasmid", "v1"),
                      _Remote("id-3", "pMine.dna", "Personal Plasmid", "v1")], download)
    _stocked(lib)
    lib.index()

    view = SequenceView(object(), "Common Plasmid")
    view.set_library(lib)
    assert view.pages.currentIndex() == 0            # the list comes first
    assert view.table.rowCount() == 2                # the other group is not here
    assert view.table.item(0, 0).text() == "pFixture"
    assert "NdeI" in view.table.item(0, 5).text()
    assert lib.plasmids == {}                        # still no sequence read

    view.search.setText("pOther")
    assert view.table.rowCount() == 1
    view.search.setText("")
    view._set_filter("single")
    assert view.table.rowCount() == 2                # both have unique cutters
    view._set_filter("all")

    view.table.selectRow(0)
    view._open_selected()
    assert view.pages.currentIndex() == 1
    assert view.plasmid is not None and view.plasmid.name == "pFixture"
    assert "bp" in view.map_sub.text()

    view.show_list()
    assert view.pages.currentIndex() == 0
