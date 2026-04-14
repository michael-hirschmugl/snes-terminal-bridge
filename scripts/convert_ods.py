#!/usr/bin/env python3
"""
Convert assets/SNES-ASCII-Map.ods → config/mappings.yaml

Usage:
    python scripts/convert_ods.py
    python scripts/convert_ods.py --ods path/to/file.ods --out path/to/mappings.yaml

Requires: odfpy  (pip install odfpy)
"""

import argparse
import sys
from pathlib import Path

try:
    from odf.opendocument import load
    from odf.table import Table, TableRow, TableCell
    from odf.text import P
except ImportError:
    sys.exit("odfpy not installed. Run: pip install odfpy")

try:
    import yaml
except ImportError:
    sys.exit("pyyaml not installed. Run: pip install pyyaml")

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_ODS = REPO_ROOT / "assets" / "SNES-ASCII-Map.ods"
DEFAULT_OUT = REPO_ROOT / "config" / "mappings.yaml"

VALID_BUTTONS = {"A", "B", "X", "Y", "L", "R", "Start", "Select",
                 "Up", "Down", "Left", "Right"}

CONTROL_CHAR_KEYS = {
    9:  "KEY_TAB",
    10: "KEY_ENTER",
    13: "KEY_ENTER",
    27: "KEY_ESCAPE",
    127: "KEY_DELETE",
}


def cell_text(cell) -> str:
    ps = cell.getElementsByType(P)
    if not ps:
        return ""
    node = ps[0].firstChild
    return node.data if node else ""


def get_buttons(cells: list) -> list[str]:
    """Extract non-empty button names from columns 8-11."""
    buttons = []
    for cell in cells[8:12]:
        text = cell_text(cell).strip()
        if text and text != "\xa0":
            buttons.append(text)
    return buttons


def char_key(code: int) -> str:
    """Return the YAML mapping key for an ASCII code."""
    if code in CONTROL_CHAR_KEYS:
        return CONTROL_CHAR_KEYS[code]
    if 32 <= code <= 126:
        return chr(code)
    return None


def convert(ods_path: Path, out_path: Path) -> None:
    doc = load(str(ods_path))
    sheet = doc.spreadsheet.getElementsByType(Table)[0]
    rows = sheet.getElementsByType(TableRow)

    mappings = {}
    skipped = []

    for row in rows[1:]:  # skip header
        cells = row.getElementsByType(TableCell)
        if not cells:
            continue

        dez = cell_text(cells[0]).strip()
        if not dez.isdigit():
            continue
        code = int(dez)
        if code > 127:
            continue

        buttons = get_buttons(cells)
        if not buttons:
            continue

        unknown = [b for b in buttons if b not in VALID_BUTTONS]
        if unknown:
            skipped.append((code, chr(code) if 32 <= code <= 126 else f"\\x{code:02x}", unknown))
            continue

        key = char_key(code)
        if key is None:
            continue

        mappings[key] = buttons

    data = {
        "settings": {
            "hold_ms": 80,
            "release_gap_ms": 20,
        },
        "mappings": mappings,
    }

    # Custom representer: dicts block-style, lists inline
    class InlineListDumper(yaml.Dumper):
        pass

    InlineListDumper.add_representer(
        list,
        lambda dumper, data: dumper.represent_sequence(
            "tag:yaml.org,2002:seq", data, flow_style=True
        ),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(
            data,
            f,
            Dumper=InlineListDumper,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    print(f"Written {len(mappings)} mappings → {out_path}")
    if skipped:
        print("Skipped (unknown button names):")
        for code, sym, unknown in skipped:
            print(f"  ASCII {code} ({sym!r}): {unknown}")


def main():
    parser = argparse.ArgumentParser(description="Convert SNES-ASCII-Map.ods to mappings.yaml")
    parser.add_argument("--ods", type=Path, default=DEFAULT_ODS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.ods.exists():
        sys.exit(f"ODS file not found: {args.ods}")

    convert(args.ods, args.out)


if __name__ == "__main__":
    main()
