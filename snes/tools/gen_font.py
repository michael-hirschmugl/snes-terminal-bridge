#!/usr/bin/env python3
"""
gen_font.py — Generate SNES 4bpp font tiles (8×16 px) for ASCII 0x20–0x7E.

Outputs three files:
  ../assets/font.inc          — tile data (SNES 4bpp, 2 × 8×8 tiles per character)
  ../assets/tilemap.inc       — tilemap words (4096 entries, 64×64 tilemap, all blank)
  ../assets/font_preview.png  — contact sheet of all 95 glyphs with 16×16 pixel grid

(font2.inc is produced by gen_font2.py — a larger 16×16 AA 2bpp variant.)

SNES Mode 5 / 8×16 character cells
-------------------------------------
Each 16-tall character cell uses two stacked 8×8 tiles:
  tile_top = C * 2       → upper 8 rows of the glyph
  tile_bot = C * 2 + 1   → lower 8 rows of the glyph
where C = ord(char) - 0x20  (0-indexed from space)

SNES 4bpp tile format (32 bytes per 8×8 tile):
  Bytes  0–15: bitplane 0 + bitplane 1 interleaved (same layout as 2bpp)
               row 0: [bp0, bp1], row 1: [bp0, bp1], ...
  Bytes 16–31: bitplane 2 + bitplane 3 interleaved (all $00 → colour index 0 or 1)
Bit-7 of each byte = leftmost pixel.
We use only colour 0 (background) and colour 1 (foreground), so bp1=bp2=bp3=0.

Usage (from snes/ directory):
    make font
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FONT_SIZE  = 13     # TTF render size for 8px-wide × 16px-tall cell (DejaVu Sans Mono)
NUM_CHARS  = 95     # ASCII 0x20 (space) through 0x7E (~)
CELL_W     = 8      # character cell width in pixels
CELL_H     = 16     # character cell height in pixels
TILEMAP_W  = 64     # SNES BG tilemap width  (entries) — 64×64 for Mode 5
TILEMAP_H  = 64     # SNES BG tilemap height (entries)

PREVIEW_COLS       = 16                  # characters per row in preview image
PREVIEW_SCALE      = 2                   # integer upscale factor
PREVIEW_GRID_PX    = 16                  # grid spacing in *scaled* pixels
PREVIEW_GRID_COLOR = (220, 50, 50)       # RGB grid line colour (red)
PREVIEW_BG         = (0, 0, 0)           # background = SNES palette colour 0
PREVIEW_FG         = (255, 255, 255)     # foreground = SNES palette colour 1

CANDIDATE_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
]

# ---------------------------------------------------------------------------
# Font discovery
# ---------------------------------------------------------------------------

def find_font() -> str | None:
    for path in CANDIDATE_FONTS:
        if os.path.exists(path):
            return path
    for search_dir in ["/usr/share/fonts", "/usr/local/share/fonts",
                       os.path.expanduser("~/.fonts")]:
        for root, _, files in os.walk(search_dir):
            for f in files:
                name = f.lower()
                if ("mono" in name or "courier" in name) and name.endswith(".ttf"):
                    return os.path.join(root, f)
    return None

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_char(char: str, font) -> list[list[int]]:
    """
    Render `char` into a CELL_W × CELL_H image.
    Returns a list of CELL_H rows; each row is a list of CELL_W ints (0 or 255).
    """
    from PIL import Image, ImageDraw

    img  = Image.new("L", (CELL_W, CELL_H), 0)
    draw = ImageDraw.Draw(img)

    try:
        bbox    = font.getbbox(char)               # (left, top, right, bottom)
        glyph_w = bbox[2] - bbox[0]
        # Horizontal: center within cell
        x = max(0, (CELL_W - glyph_w) // 2) - bbox[0]
        # Vertical: top-align at row 1 so the glyph fills the top tile
        y = 1 - bbox[1]
    except AttributeError:
        w, h = font.getsize(char)
        x = max(0, (CELL_W - w) // 2)
        y = 1

    draw.text((x, y), char, fill=255, font=font)

    pixels = list(img.tobytes())                   # CELL_W × CELL_H bytes
    return [pixels[r * CELL_W:(r + 1) * CELL_W] for r in range(CELL_H)]

# ---------------------------------------------------------------------------
# 8×8 subtile → SNES 4bpp bytes (32 bytes)
# ---------------------------------------------------------------------------

def subtile_to_4bpp(rows_8: list[list[int]]) -> bytes:
    """
    Convert 8 rows of an 8-pixel-wide scanline to a 32-byte SNES 4bpp tile.
    Bitplanes 0 and 1 carry pixel data; bitplanes 2 and 3 are all zero.
    """
    data = bytearray(32)
    for row, scanline in enumerate(rows_8):
        bp0 = 0
        for col in range(8):
            if scanline[col] > 128:
                bp0 |= (1 << (7 - col))
        data[row * 2]     = bp0   # bitplane 0
        data[row * 2 + 1] = 0x00  # bitplane 1 (always 0 → colour indices 0/1 only)
        # bytes 16–31 remain 0x00  (bitplanes 2+3)
    return bytes(data)


# ---------------------------------------------------------------------------
# Preview contact sheet
# ---------------------------------------------------------------------------

def write_preview(rendered_chars: list[list[list[int]]], out_path) -> None:
    """
    Build a contact sheet of every glyph as it will appear on the SNES and
    overlay a 16×16-pixel grid.

    With PREVIEW_SCALE=2 each 8×8 source tile becomes a 16×16 scaled block, so
    every grid cell corresponds exactly to one SNES tile. The top/bottom halves
    of each 8×16 character cell therefore sit in two vertically-stacked grid
    cells.
    """
    from PIL import Image, ImageDraw

    num    = len(rendered_chars)
    cols   = PREVIEW_COLS
    rows   = (num + cols - 1) // cols
    width  = cols * CELL_W
    height = rows * CELL_H

    img = Image.new("RGB", (width, height), PREVIEW_BG)
    for idx, char_rows in enumerate(rendered_chars):
        ox = (idx % cols) * CELL_W
        oy = (idx // cols) * CELL_H
        for r, scanline in enumerate(char_rows):
            for c, v in enumerate(scanline):
                if v > 128:
                    img.putpixel((ox + c, oy + r), PREVIEW_FG)

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
          f"grid={PREVIEW_GRID_PX}px)")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        from PIL import ImageFont
    except ImportError:
        sys.exit("Error: Pillow not installed.  Run: pip install Pillow")

    font_path = find_font()
    if not font_path:
        sys.exit(
            "No monospace TTF font found.\n"
            "Install one, e.g.: sudo apt install fonts-dejavu"
        )
    print(f"Font: {font_path}  size={FONT_SIZE}px")

    font = ImageFont.truetype(font_path, FONT_SIZE)

    out_dir = Path(__file__).parent.parent / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    font_path_out    = out_dir / "font.inc"
    tilemap_path_out = out_dir / "tilemap.inc"
    preview_path_out = out_dir / "font_preview.png"

    # --- Build tile data ---------------------------------------------------
    # 2 tiles per character (top + bottom), tile_top = C*2, tile_bot = C*2+1
    total_tiles  = NUM_CHARS * 2   # 190
    tile_data    = [bytes(32)] * total_tiles  # 4bpp tiles for BG1 (even pixels)

    chars = [chr(c) for c in range(0x20, 0x7F)]   # 95 printable ASCII chars
    rendered_chars: list[list[list[int]]] = []

    for C, char in enumerate(chars):
        rows      = render_char(char, font)
        rendered_chars.append(rows)
        top_rows  = rows[:8]   # upper half → tile_top
        bot_rows  = rows[8:]   # lower half → tile_bot

        tile_data[C * 2]     = subtile_to_4bpp(top_rows)
        tile_data[C * 2 + 1] = subtile_to_4bpp(bot_rows)

    # Write font.inc (4bpp, for BG1 — even screen pixel columns)
    with open(font_path_out, "w", encoding="utf-8") as f:
        f.write("; Auto-generated by tools/gen_font.py — do not edit manually.\n")
        f.write("; SNES 4bpp, 8×16 characters (2 × 8×8 tiles per character).\n")
        f.write("; Used by BG1 in Mode 5 (even screen pixel columns).\n")
        f.write(f"; {total_tiles} tiles total, 32 bytes each.\n\n")
        for idx, tile in enumerate(tile_data):
            # 16 bytes of bp0+bp1, then 16 bytes of bp2+bp3 (all zero)
            pairs = [f"${tile[j*2]:02X},${tile[j*2+1]:02X}" for j in range(8)]
            zeros = ["$00,$00"] * 8
            f.write(f"    .byte {', '.join(pairs)}  ; tile {idx} bp0+bp1\n")
            f.write(f"    .byte {', '.join(zeros)}  ; tile {idx} bp2+bp3\n")
    print(f"font.inc: {total_tiles} tiles ({total_tiles * 32} bytes)")

    # --- Build tilemap (64×64 = 4096 entries) — all blank (tile 0 = space) --
    tilemap = [0] * (TILEMAP_W * TILEMAP_H)

    # Write tilemap.inc
    with open(tilemap_path_out, "w", encoding="utf-8") as f:
        f.write("; Auto-generated by tools/gen_font.py — do not edit manually.\n")
        f.write("; 64×64 BG1 tilemap (4096 × 16-bit entries, all blank).\n\n")
        for i, entry in enumerate(tilemap):
            col_comment = f"; ({i % TILEMAP_W}, {i // TILEMAP_W})"
            f.write(f"    .word ${entry:04X}  {col_comment}\n")
    print(f"tilemap.inc: {len(tilemap)} entries ({len(tilemap) * 2} bytes)")

    # --- Build preview PNG (contact sheet with 16×16-pixel grid) ------------
    write_preview(rendered_chars, preview_path_out)


if __name__ == "__main__":
    main()
