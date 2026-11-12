"""Build data/sample.labproj from the example spreadsheets in data/samples/.

    python scripts/build_sample.py

Every example file is lab-shared stock, so everything goes under Common Asset.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.importer import Table, import_table, plan_columns, read_tables  # noqa: E402
from app.core.project import Project  # noqa: E402

SAMPLES = ROOT / "data" / "samples"
OUT = ROOT / "data" / "sample.labproj"

# (file, sheets or None for all, category name, quantity column or None)
SPEC = [
    ("Antibody_PBC new.xlsx", None, "항체 (Antibody)", None),
    ("Reagents.xlsx", ["Enzymes"], "효소 (Enzyme)", None),
    ("restriction enzyme.xlsx", ["1"], "제한효소 (Restriction enzyme)", "total diluted enzyme tubes"),
    ("Reagents.xlsx", ["Cytokines", "Kit", "Inbihitors", "Antibiotics", "Image-related", "FACS", "ETC"],
     "Reagent", None),
    ("Plasmids 230825 Updated.xlsx", None, "플라스미드 (Plasmid)", None),
    ("Cell lines 250905.xlsx", None, "Cell line", "Vials"),
    ("Bacteria Compcell.xls", ["Comp cell", "Bacteria"], "Bacteria", "Vials"),
    ("Bacteria Compcell.xls", ["Virus"], "Virus", "volume"),
]
# Lab-made stocks only need a "do we have it" view; bought consumables are stock-managed.
STATUS_CATEGORIES = {"플라스미드 (Plasmid)", "Cell line", "Bacteria", "Virus"}
SUBGROUP_NAMES = {"Inbihitors": "Inhibitors", "1": "", "Empty": "Empty vector"}
# Same column spelled differently across sheets of one file.
COLUMN_ALIASES = {
    "Location": "Location (plasmid)",
    "Aintibiotics": "Antibiotics",
    "Remark": "Remarks",
    "Remarks (target)": "Remarks",
    "Location (Bac Stock)": "Location (bacteria stock)",
}
ALIAS_FILES = {"Plasmids 230825 Updated.xlsx", "Reagents.xlsx"}


def prepare(table: Table, filename: str) -> None:
    if filename.startswith("restriction"):
        # Order history sits below the stock list; column A holds the box label.
        table.stop_markers = ["최근 주문내역"]
        box = ""
        for i, row in enumerate(table.raw):
            if i == table.header_row:
                row[0] = "Box"
                continue
            if i > table.header_row and row and row[0]:
                box = str(row[0])
            if i > table.header_row and row:
                row[0] = box


def main() -> None:
    if OUT.exists():
        OUT.unlink()
    for extra in (OUT.with_suffix(".labproj-wal"), OUT.with_suffix(".labproj-shm")):
        if extra.exists():
            extra.unlink()
    project = Project.create(OUT, "Sample Lab")
    categories: dict[str, int] = {}
    for filename, sheets, cat_name, qty_col in SPEC:
        tables = read_tables(SAMPLES / filename)
        for table in tables:
            if sheets is not None and table.name not in sheets:
                continue
            prepare(table, filename)
            plans = plan_columns(table)
            if filename in ALIAS_FILES:
                for p in plans:
                    p.header = COLUMN_ALIASES.get(p.header, p.header)
            if qty_col:
                for p in plans:
                    if p.role == "quantity":
                        p.role = ""
                    if p.header == qty_col:
                        p.role = "quantity"
            if cat_name not in categories:
                kind = "status" if cat_name in STATUS_CATEGORIES else "managed"
                categories[cat_name] = project.add_category("common", cat_name, kind=kind).id
            sub = SUBGROUP_NAMES.get(table.name, table.name)
            res = import_table(project, categories[cat_name], table, plans, subgroup=sub)
            print(f"{filename} / {table.name} -> {cat_name} [{sub}]: {res.added} items")
    project.close()
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
