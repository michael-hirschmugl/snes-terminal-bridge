#!/usr/bin/env python3
"""
gen_font.py — Generate SNES 2bpp font tiles (16×16 px) for ASCII 0x20–0x7E.

Outputs two files:
  ../assets/font.inc     — tile data (SNES 2bpp, arranged for 16×16 tile mode)
  ../assets/tilemap.inc  — tilemap words (1024 entries, 32×32 tilemap)

SNES 16×16 tile mode
--------------------
Each 16×16 character cell is composed of four 8×8 subtiles.
Given tilemap entry value N, the PPU fetches:
  top-left:     tile N
  top-right:    tile N ^ 1   (= N+1 when N is even)
  bottom-left:  tile N ^ 16  (= N+16 when bit-4 of N is 0)
  bottom-right: tile N ^ 17

Characters are grouped in sets of 8. Character C (0-indexed) occupies
tile slot T = (C // 8) * 32 + (C % 8) * 2, so:
  top-left     → tile T
  top-right    → tile T+1
  bottom-left  → tile T+16
  bottom-right → tile T+17

SNES 2bpp tile format (16 bytes per 8×8 subtile):
  row 0: [bitplane-0, bitplane-1]
  row 1: [bitplane-0, bitplane-1]
  ...
  row 7: [bitplane-0, bitplane-1]
Bit-7 of each byte = leftmost pixel.
For 2-colour text: bitplane-1 is always $00.

Usage (from snes/ directory):
    make font
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FONT_SIZE  = 15     # Adjust if glyphs are too small or clipped
NUM_CHARS  = 95     # ASCII 0x20 (space) through 0x7E (~)
TILE_PX    = 16     # character cell size in pixels
COLS       = 16     # characters per display row
TILEMAP_W  = 32     # SNES BG tilemap width (entries)
TILEMAP_H  = 32     # SNES BG tilemap height (entries)

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
    Render `char` into a TILE_PX × TILE_PX image.
    Returns a list of TILE_PX rows; each row is a list of TILE_PX ints (0 or 255).
    """
    from PIL import Image, ImageDraw

    img  = Image.new("L", (TILE_PX, TILE_PX), 0)
    draw = ImageDraw.Draw(img)

    try:
        bbox    = font.getbbox(char)                   # (left, top, right, bottom)
        glyph_w = bbox[2] - bbox[0]
        glyph_h = bbox[3] - bbox[1]
        x = max(0, (TILE_PX - glyph_w) // 2) - bbox[0]
        y = max(0, (TILE_PX - glyph_h) // 2) - bbox[1]
    except AttributeError:
        w, h = font.getsize(char)
        x = max(0, (TILE_PX - w) // 2)
        y = max(0, (TILE_PX - h) // 2)

    draw.text((x, y), char, fill=255, font=font)

    pixels = list(img.tobytes())                       # TILE_PX × TILE_PX bytes
    return [pixels[r * TILE_PX:(r + 1) * TILE_PX] for r in range(TILE_PX)]

# ---------------------------------------------------------------------------
# 8×8 subtile → SNES 2bpp bytes (16 bytes)
# ---------------------------------------------------------------------------

def subtile_to_2bpp(rows_8: list[list[int]], col_offset: int) -> bytes:
    """
    Convert 8 rows of a 16-pixel-wide scanline to a 16-byte SNES 2bpp subtile.
    `col_offset` is 0 for the left half (TL/BL) or 8 for the right half (TR/BR).
    """
    data = bytearray(16)
    for row, scanline in enumerate(rows_8):
        bp0 = 0
        for col in range(8):
            if scanline[col_offset + col] > 128:
                bp0 |= (1 << (7 - col))
        data[row * 2]     = bp0   # bitplane 0
        data[row * 2 + 1] = 0x00  # bitplane 1 (always 0 → colour indices 0/1 only)
    return bytes(data)

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

    # --- Build tile data ---------------------------------------------------
    # Number of 8-char groups needed for NUM_CHARS characters
    num_groups   = (NUM_CHARS + 7) // 8   # ceil(95/8) = 12
    total_tiles  = num_groups * 32        # 12 × 32 = 384 tiles
    tile_data    = [bytes(16)] * total_tiles  # initialise all tiles to empty

    chars = [chr(c) for c in range(0x20, 0x7F)]   # 95 printable ASCII chars

    for C, char in enumerate(chars):
        group    = C // 8
        pos      = C % 8
        tl_idx   = group * 32 + pos * 2
        tr_idx   = tl_idx + 1
        bl_idx   = tl_idx + 16
        br_idx   = tl_idx + 17

        rows      = render_char(char, font)
        top_rows  = rows[:8]
        bot_rows  = rows[8:]

        tile_data[tl_idx] = subtile_to_2bpp(top_rows, col_offset=0)   # TL
        tile_data[tr_idx] = subtile_to_2bpp(top_rows, col_offset=8)   # TR
        tile_data[bl_idx] = subtile_to_2bpp(bot_rows, col_offset=0)   # BL
        tile_data[br_idx] = subtile_to_2bpp(bot_rows, col_offset=8)   # BR

    # Write font.inc
    with open(font_path_out, "w", encoding="utf-8") as f:
        f.write("; Auto-generated by tools/gen_font.py — do not edit manually.\n")
        f.write(f"; SNES 2bpp, 16×16 tiles (4 × 8×8 subtiles per character).\n")
        f.write(f"; {total_tiles} subtiles total, 16 bytes each.\n\n")
        for idx, tile in enumerate(tile_data):
            pairs = [f"${tile[j*2]:02X},${tile[j*2+1]:02X}" for j in range(8)]
            f.write(f"    .byte {', '.join(pairs)}  ; tile {idx}\n")
    print(f"font.inc: {total_tiles} subtiles ({total_tiles * 16} bytes)")

    # --- Build tilemap (32×32 = 1024 entries) — all blank (tile 0 = space) --
    tilemap = [0] * (TILEMAP_W * TILEMAP_H)

    # Write tilemap.inc
    with open(tilemap_path_out, "w", encoding="utf-8") as f:
        f.write("; Auto-generated by tools/gen_font.py — do not edit manually.\n")
        f.write("; 32×32 BG1 tilemap (1024 × 16-bit entries).\n\n")
        for i, entry in enumerate(tilemap):
            col_comment = f"; ({i % TILEMAP_W}, {i // TILEMAP_W})"
            f.write(f"    .word ${entry:04X}  {col_comment}\n")
    print(f"tilemap.inc: {len(tilemap)} entries ({len(tilemap) * 2} bytes)")


if __name__ == "__main__":
    main()
