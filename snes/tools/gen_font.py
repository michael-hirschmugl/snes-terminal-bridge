#!/usr/bin/env python3
"""
gen_font.py — Generate SNES 2bpp anti-aliased 16×16 font tiles for Mode 5.

Outputs:
  ../assets/font.inc          — tile data (SNES 2bpp, dense-packed for Mode 5)
  ../assets/font_preview.png  — contact sheet of all 95 glyphs with 16×16 pixel grid

Mode 5 dense-pack VRAM layout
-----------------------------
In Mode 5 with BGMODE bit 5 set, BG2 uses 16×16 tiles. The PPU reads four 8×8
VRAM tiles per tilemap entry in the pattern:

    N, N+1, N+16, N+17

where VRAM tile slots are displayed 16 per row in tile viewers. To dense-pack
95 characters without wasting VRAM we use the formula:

    N(C) = (C // 8) * 32 + (C % 8) * 2

so 8 characters fit back-to-back per "row pair" (top halves at slots 0..15,
bottom halves at slots 16..31), and the next row pair starts at slot 32.
Character C thus occupies these four 8×8 VRAM slots:

    slot N(C)      = top-left   (rows  0..7 , cols 0..7)
    slot N(C) + 1  = top-right  (rows  0..7 , cols 8..15)
    slot N(C) + 16 = bottom-left  (rows 8..15, cols 0..7)
    slot N(C) + 17 = bottom-right (rows 8..15, cols 8..15)

For C = 0..94 the highest slot index used is N(94) + 17 = 364 + 17 = 381.
A reserved BLANK super-tile lives at index C = BLANK_INDEX = 95 →
N = 366; its partners (366, 367, 382, 383) stay zero so empty tilemap
cells render as palette index 0 (black).

All glyphs share a common baseline so that the typographic bottoms of the
characters line up. Descenders (g, j, p, q, y) hang below the baseline by a
couple of rows; ascenders and caps reach upward from it.

SNES 2bpp tile format (16 bytes per 8×8 tile)
---------------------------------------------
Two bitplanes are stored row-interleaved:

    byte 0 : row 0 bitplane 0
    byte 1 : row 0 bitplane 1
    byte 2 : row 1 bitplane 0
    byte 3 : row 1 bitplane 1
    ...
    byte 14: row 7 bitplane 0
    byte 15: row 7 bitplane 1

For each pixel the two bitplanes combine into a palette index 0..3:

    index = (bp1 << 1) | bp0

giving four levels of shading used for anti-aliasing.

Bit 7 of each bitplane byte is the LEFT-MOST pixel of the row.

Rendering
---------
Glyphs are rendered with Pillow's FreeType path at FONT_SIZE px from
JetBrainsMono-Regular.ttf using the font's native anti-aliasing, then the
resulting 8-bit grayscale values are quantised to 4 levels (0..3) for 2bpp.

Usage (from snes/ directory):
    make font
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CELL_W          = 16       # character cell width  in pixels
CELL_H          = 16       # character cell height in pixels
TILES_PER_CHAR  = 4        # 2×2 grid of 8×8 tiles
NUM_CHARS       = 95       # ASCII 0x20 (space) through 0x7E (~)

# Mode 5 dense-pack layout: one 8×8 VRAM tile slot holds 16 bytes of 2bpp
# data. BLANK_INDEX is reserved as the "transparent" super-tile used to
# clear the tilemap. We emit one extra super-tile slot (all zero) for it,
# so the total number of emitted 8×8 tiles is N(BLANK_INDEX) + 18.
BLANK_INDEX     = NUM_CHARS   # 95 — first free super-tile slot after glyphs
BYTES_PER_TILE  = 16


def super_tile_vram_base(k: int) -> int:
    """VRAM tile slot for super-tile index k in dense-pack Mode 5 layout.

    Matches `super_tile_vram_base` in snes-tile-test/tools/gen_assets.py:
    8 super-tiles per row-pair (step 2 inside a row-pair, step 32 between
    row-pairs).
    """
    return (k // 8) * 32 + (k % 8) * 2

# JetBrains Mono visual tuning for a 16px cell:
#   FONT_SIZE  — px size fed to truetype loader
#   BASELINE_Y — row index (0=top, 15=bottom) where the baseline sits inside
#                the cell. Everything above is ascender/cap space, everything
#                below is descender space. Raise/lower to taste.
FONT_SIZE  = 16
BASELINE_Y = 13

FONT_PATH = Path(__file__).parent / "fonts" / "JetBrainsMono-Regular.ttf"

# Preview contact sheet
PREVIEW_COLS       = 16
PREVIEW_SCALE      = 2                          # integer upscale factor
PREVIEW_GRID_PX    = 16                         # grid spacing in scaled pixels
PREVIEW_GRID_COLOR = (220, 50, 50)              # red grid lines
PREVIEW_BG         = (0, 0, 0)                  # palette colour 0

# RGB shades for 2bpp palette indices 0..3 in the preview image.
# Four evenly spaced grey levels make the AA visible at a glance.
PREVIEW_SHADES = [
    (  0,   0,   0),   # 0 — background
    ( 85,  85,  85),   # 1
    (170, 170, 170),   # 2
    (255, 255, 255),   # 3 — full ink
]

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_char(char: str, font) -> list[list[int]]:
    """
    Render `char` using anti-aliased FreeType rendering, then quantise to
    4 shading levels (0..3) inside a CELL_W × CELL_H cell with the glyph
    baseline sitting on row BASELINE_Y.

    Returns CELL_H rows of CELL_W ints (each 0..3).
    """
    from PIL import Image, ImageDraw

    # Use a temp canvas big enough to hold any glyph regardless of
    # ascender/descender height, then crop deterministically.
    TEMP = 48
    img  = Image.new("L", (TEMP, TEMP), 0)
    draw = ImageDraw.Draw(img)

    cx, cy = TEMP // 2, TEMP // 2
    # anchor="ms" — middle (horizontal), baseline (vertical).
    # This guarantees the glyph's baseline lands exactly at y=cy, independent
    # of the character. That's what lets us bottom-align every glyph by
    # cropping at a constant offset from cy.
    draw.text((cx, cy), char, fill=255, font=font, anchor="ms")

    crop_left = cx - CELL_W // 2
    crop_top  = cy - BASELINE_Y
    cell = img.crop((crop_left, crop_top, crop_left + CELL_W, crop_top + CELL_H))

    raw = cell.tobytes()
    rows: list[list[int]] = []
    for r in range(CELL_H):
        row = []
        for c in range(CELL_W):
            v   = raw[r * CELL_W + c]
            # Map 0..255 grayscale onto 4 evenly-spaced buckets 0..3.
            lvl = v * 4 // 256
            if lvl > 3:
                lvl = 3
            row.append(lvl)
        rows.append(row)
    return rows


def split_cell(cell: list[list[int]]) -> list[list[list[int]]]:
    """Split a 16×16 cell into four 8×8 subtiles in TL, TR, BL, BR order."""
    tl = [cell[r][0:8 ] for r in range(0, 8)]
    tr = [cell[r][8:16] for r in range(0, 8)]
    bl = [cell[r][0:8 ] for r in range(8, 16)]
    br = [cell[r][8:16] for r in range(8, 16)]
    return [tl, tr, bl, br]


def subtile_to_2bpp(rows_8: list[list[int]]) -> bytes:
    """
    Convert an 8×8 subtile (values 0..3) to 16 bytes of SNES 2bpp.
    Bit 7 = left-most pixel of each row.
    """
    data = bytearray(16)
    for row, scanline in enumerate(rows_8):
        bp0 = 0
        bp1 = 0
        for col in range(8):
            v = scanline[col]
            if v & 1:
                bp0 |= 1 << (7 - col)
            if v & 2:
                bp1 |= 1 << (7 - col)
        data[row * 2]     = bp0
        data[row * 2 + 1] = bp1
    return bytes(data)

# ---------------------------------------------------------------------------
# Preview contact sheet
# ---------------------------------------------------------------------------

SUBTILE_LABELS = ("TL", "TR", "BL", "BR")

def write_preview(rendered_chars: list[list[list[int]]], out_path: Path) -> None:
    """
    Render every glyph (with its 2bpp quantised shades) to a contact sheet,
    scale it up, and overlay a grid that snaps to 8-SNES-pixel (= one tile)
    boundaries. Every character occupies a 2×2 grid block.
    """
    from PIL import Image, ImageDraw

    num    = len(rendered_chars)
    cols   = PREVIEW_COLS
    rows   = (num + cols - 1) // cols
    width  = cols * CELL_W
    height = rows * CELL_H

    img = Image.new("RGB", (width, height), PREVIEW_BG)
    for idx, cell in enumerate(rendered_chars):
        ox = (idx % cols) * CELL_W
        oy = (idx // cols) * CELL_H
        for r, scanline in enumerate(cell):
            for c, lvl in enumerate(scanline):
                img.putpixel((ox + c, oy + r), PREVIEW_SHADES[lvl])

    img = img.resize(
        (width * PREVIEW_SCALE, height * PREVIEW_SCALE),
        Image.NEAREST,
    )

    draw = ImageDraw.Draw(img)
    w_s, h_s = img.size
    for x in range(0, w_s + 1, PREVIEW_GRID_PX):
        draw.line([(x, 0), (x, h_s - 1)], fill=PREVIEW_GRID_COLOR)
    for y in range(0, h_s + 1, PREVIEW_GRID_PX):
        draw.line([(0, y), (w_s - 1, y)], fill=PREVIEW_GRID_COLOR)

    img.save(out_path)
    print(f"font_preview.png: {w_s}×{h_s}px "
          f"({cols}×{rows} glyphs, scale={PREVIEW_SCALE}×, "
          f"grid={PREVIEW_GRID_PX}px = 1 SNES tile)")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        from PIL import ImageFont
    except ImportError:
        sys.exit("Error: Pillow not installed.  Run: pip install Pillow")

    if not FONT_PATH.exists():
        sys.exit(
            f"JetBrains Mono font not found at {FONT_PATH}.\n"
            f"Download JetBrainsMono-Regular.ttf into {FONT_PATH.parent}/"
        )
    print(f"Font: {FONT_PATH.name}  size={FONT_SIZE}px  baseline_y={BASELINE_Y}")

    font = ImageFont.truetype(str(FONT_PATH), FONT_SIZE)

    out_dir         = Path(__file__).parent.parent / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    font_path_out   = out_dir / "font.inc"
    preview_path    = out_dir / "font_preview.png"

    chars          = [chr(c) for c in range(0x20, 0x7F)]
    rendered_chars: list[list[list[int]]] = []

    # Highest 8×8 VRAM slot we need to cover: the bottom-right sub-tile of
    # the reserved BLANK super-tile. Keep all lower slots zero-initialised
    # so empty super-tile entries naturally render as palette index 0.
    max_slot       = super_tile_vram_base(BLANK_INDEX) + 17
    total_slots    = max_slot + 1
    vram_bytes     = bytearray(total_slots * BYTES_PER_TILE)

    # slot_meta[slot] = (char, sub_label) or None (blank). Used only for
    # the per-line comment in the .inc file so the layout is auditable.
    slot_meta: list[tuple[str, str] | None] = [None] * total_slots

    for C, char in enumerate(chars):
        cell = render_char(char, font)
        rendered_chars.append(cell)

        N   = super_tile_vram_base(C)
        tl, tr, bl, br = split_cell(cell)
        placements = (
            (N,      tl, "TL"),
            (N + 1,  tr, "TR"),
            (N + 16, bl, "BL"),
            (N + 17, br, "BR"),
        )
        for slot, subtile, label in placements:
            encoded = subtile_to_2bpp(subtile)
            vram_bytes[slot * BYTES_PER_TILE:(slot + 1) * BYTES_PER_TILE] = encoded
            slot_meta[slot] = (char, label)

    with open(font_path_out, "w", encoding="utf-8") as f:
        f.write("; Auto-generated by tools/gen_font.py — do not edit manually.\n")
        f.write("; SNES 2bpp, 16x16 anti-aliased characters, Mode 5 dense-pack.\n")
        f.write("; Source font: JetBrains Mono Regular @ "
                f"{FONT_SIZE}px, baseline row {BASELINE_Y}.\n")
        f.write(";\n")
        f.write("; Dense-pack VRAM layout (super-tile index k):\n")
        f.write(";   N(k) = (k // 8) * 32 + (k % 8) * 2\n")
        f.write(";   super-tile k occupies 8x8 slots N, N+1, N+16, N+17\n")
        f.write(";   PPU auto-reads (N, N+1, N+16, N+17) per 16x16 BG tile\n")
        f.write(";\n")
        f.write(f"; Character range: 0x20..0x7E -> k = 0..{NUM_CHARS - 1}\n")
        f.write(f"; BLANK_INDEX   (reserved transparent super-tile) = {BLANK_INDEX}\n")
        f.write(f"; Highest 8x8 slot used: {max_slot} "
                f"(super-tile {BLANK_INDEX} partners)\n")
        f.write(f"; Total 8x8 tiles emitted: {total_slots} "
                f"({total_slots * BYTES_PER_TILE} bytes)\n\n")

        for slot in range(total_slots):
            base = slot * BYTES_PER_TILE
            pairs = [
                f"${vram_bytes[base + j * 2]:02X},${vram_bytes[base + j * 2 + 1]:02X}"
                for j in range(8)
            ]
            meta = slot_meta[slot]
            if meta is None:
                comment = f"; slot {slot:4d}  (blank)"
            else:
                ch, sub = meta
                shown = ch if ch.isprintable() and ch != ' ' else {' ': 'SP'}.get(ch, '?')
                comment = f"; slot {slot:4d}  0x{ord(ch):02X} '{shown}' {sub}"
            f.write(f"    .byte {', '.join(pairs)}  {comment}\n")

    print(f"font.inc: {total_slots} 8x8 tiles "
          f"({total_slots * BYTES_PER_TILE} bytes, dense-pack Mode 5)")

    write_preview(rendered_chars, preview_path)


if __name__ == "__main__":
    main()
