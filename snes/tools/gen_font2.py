#!/usr/bin/env python3
"""
gen_font2.py — Generate SNES 2bpp anti-aliased 16×16 font tiles.

Outputs:
  ../assets/font2.inc          — tile data (SNES 2bpp, 4 × 8×8 tiles per character)
  ../assets/font2_preview.png  — contact sheet of all 95 glyphs with 16×16 pixel grid

Glyph layout
------------
Each printable ASCII character (0x20..0x7E) occupies a 16×16 pixel raster that
is stored as a 2×2 block of 8×8 SNES tiles in the following order:

    tile +0 = top-left   (rows  0..7 , cols 0..7)
    tile +1 = top-right  (rows  0..7 , cols 8..15)
    tile +2 = bottom-left  (rows  8..15, cols 0..7)
    tile +3 = bottom-right (rows  8..15, cols 8..15)

So character C (C = ord(ch) - 0x20) occupies tile indices C*4 .. C*4+3.

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
    print(f"font2_preview.png: {w_s}×{h_s}px "
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
    font2_path_out  = out_dir / "font2.inc"
    preview_path    = out_dir / "font2_preview.png"

    chars          = [chr(c) for c in range(0x20, 0x7F)]
    rendered_chars: list[list[list[int]]] = []
    tile_data:      list[bytes]           = []

    for char in chars:
        cell = render_char(char, font)
        rendered_chars.append(cell)
        for subtile in split_cell(cell):
            tile_data.append(subtile_to_2bpp(subtile))

    assert len(tile_data) == NUM_CHARS * TILES_PER_CHAR

    with open(font2_path_out, "w", encoding="utf-8") as f:
        f.write("; Auto-generated by tools/gen_font2.py — do not edit manually.\n")
        f.write("; SNES 2bpp, 16x16 anti-aliased characters.\n")
        f.write("; Source font: JetBrains Mono Regular @ "
                f"{FONT_SIZE}px, baseline row {BASELINE_Y}.\n")
        f.write(";\n")
        f.write("; Per-character layout (C = ord(ch) - 0x20):\n")
        f.write(";   tile C*4+0 = top-left,    tile C*4+1 = top-right\n")
        f.write(";   tile C*4+2 = bottom-left, tile C*4+3 = bottom-right\n")
        f.write(f"; {len(tile_data)} tiles total, 16 bytes each "
                f"({len(tile_data) * 16} bytes).\n\n")
        for idx, tile in enumerate(tile_data):
            char_idx = idx // TILES_PER_CHAR
            sub      = SUBTILE_LABELS[idx % TILES_PER_CHAR]
            ch       = chars[char_idx]
            shown    = ch if ch.isprintable() and ch != ' ' else {' ': 'SP'}.get(ch, '?')
            pairs    = [f"${tile[j*2]:02X},${tile[j*2+1]:02X}" for j in range(8)]
            f.write(f"    .byte {', '.join(pairs)}"
                    f"  ; tile {idx:3d}  0x{0x20+char_idx:02X} '{shown}' {sub}\n")
    print(f"font2.inc: {len(tile_data)} tiles ({len(tile_data) * 16} bytes)")

    write_preview(rendered_chars, preview_path)


if __name__ == "__main__":
    main()
