#!/usr/bin/env python3
"""
Generate SNES Mode 5 BG1 border tiles and tilemap (Option D — SNES RPG style).

Replaces the full-screen wallpaper with a 16 px retro frame around the 30×26
text area.  Only 4 unique 16×16 super-tiles are needed:

  k=0  top-left corner (reused via H/V flip for all 4 corners)
  k=1  horizontal edge  (top border; V-flipped for bottom border)
  k=2  vertical edge    (left border; H-flipped for right border)
  k=3  blank            (interior — colour 0, transparent)

VRAM layout (dense-pack, BG1 base word $2000):
  k=0 → slots  0, 1,16,17
  k=1 → slots  2, 3,18,19
  k=2 → slots  4, 5,20,21
  k=3 → slots  6, 7,22,23
  Unused slots (8-15, 24-31) filled with zero tiles.
  Total: 32 slots × 32 bytes = 1024 bytes (vs 24576 for the wallpaper).

Output (build/mode5_border_4bpp/):
  palette.bin       32 bytes — 16-colour BGR555 palette for BG1 sub-palette 0
  tiles.4bpp.chr    1024 bytes — 32 tile slots, 4bpp
  tilemap.bin       2048 bytes — 32×32 tilemap entries (2 bytes each)
  preview.png       rendered preview (2× upscale)
"""

from pathlib import Path
try:
    from PIL import Image
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

ROOT  = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
OUT   = BUILD / "mode5_border_4bpp"

# ---------------------------------------------------------------------------
# Palette  (BGR555: value = B5*1024 + G5*32 + R5)
# ---------------------------------------------------------------------------

PALETTE = [
    0x0000,  # 0  black       – background / interior
    0x3082,  # 1  dark navy   – outer shadow edge   (B=12,G=4,R=2)
    0x5146,  # 2  medium blue – border body          (B=20,G=10,R=6)
    0x724E,  # 3  light blue  – inner lighter strip  (B=28,G=18,R=14)
    0x7F58,  # 4  icy blue    – bright inner edge    (B=31,G=26,R=24)
    0x094F,  # 5  dark gold   – diamond outer ring   (B=2,G=10,R=15)
    0x0E38,  # 6  gold        – diamond body         (B=3,G=17,R=24)
    0x131F,  # 7  bright gold – diamond bright ring  (B=4,G=24,R=31)
    0x7FFF,  # 8  white       – diamond centre
]
# Pad to 16 entries
PALETTE += [0x0000] * (16 - len(PALETTE))

# ---------------------------------------------------------------------------
# Pixel generators
# ---------------------------------------------------------------------------

def _border_depth(d: int) -> int:
    """Palette index for a distance d (0=outer edge) from the outer screen edge."""
    if d <= 1:  return 1   # outer shadow
    if d <= 9:  return 2   # main body
    if d <= 12: return 3   # lighter strip
    return 4               # bright inner edge (d=13..15)


def _diamond(x: int, y: int):
    """Palette index for the ◆ diamond centred at (7,7), or None if outside."""
    dist = abs(x - 7) + abs(y - 7)
    if dist > 4:   return None
    if dist == 0:  return 8   # white centre
    if dist <= 2:  return 7   # bright gold
    if dist <= 3:  return 6   # gold
    return 5                  # dark gold outer ring


def make_corner_pixels() -> list:
    """Top-left corner super-tile (16×16).

    Border brightness = min of h-depth(y) and v-depth(x), so outer edges
    (y=0, x=0) are dark and the inner corner (x=15, y=15) is bright.
    The ◆ diamond is overlaid at the centre.
    """
    out = []
    for y in range(16):
        row = []
        for x in range(16):
            d = _diamond(x, y)
            row.append(d if d is not None else min(_border_depth(y), _border_depth(x)))
        out.append(row)
    return out


def make_h_edge_pixels() -> list:
    """Horizontal edge super-tile (16×16): uniform stripes by row."""
    return [[_border_depth(y)] * 16 for y in range(16)]


def make_v_edge_pixels() -> list:
    """Vertical edge super-tile (16×16): uniform stripes by column."""
    return [[_border_depth(x) for x in range(16)] for _ in range(16)]


def make_blank_pixels() -> list:
    """Blank super-tile (16×16): all colour 0 (transparent / interior)."""
    return [[0] * 16 for _ in range(16)]


# ---------------------------------------------------------------------------
# SNES 4bpp encoding
# ---------------------------------------------------------------------------

def tile_to_4bpp(tile8: list) -> bytes:
    """Encode an 8×8 pixel grid (palette indices 0-15) to 32-byte SNES 4bpp."""
    assert len(tile8) == 8 and all(len(r) == 8 for r in tile8)
    out = bytearray()
    # Bitplanes 0+1 (first 16 bytes)
    for row in tile8:
        p0 = p1 = 0
        for x, v in enumerate(row):
            b = 7 - x
            p0 |= ((v >> 0) & 1) << b
            p1 |= ((v >> 1) & 1) << b
        out += bytes([p0, p1])
    # Bitplanes 2+3 (next 16 bytes)
    for row in tile8:
        p2 = p3 = 0
        for x, v in enumerate(row):
            b = 7 - x
            p2 |= ((v >> 2) & 1) << b
            p3 |= ((v >> 3) & 1) << b
        out += bytes([p2, p3])
    assert len(out) == 32
    return bytes(out)


def split_super_tile(pixels16: list):
    """Split a 16×16 pixel grid into four 8×8 sub-tiles: TL, TR, BL, BR."""
    def quad(oy, ox):
        return [row[ox:ox + 8] for row in pixels16[oy:oy + 8]]
    return quad(0, 0), quad(0, 8), quad(8, 0), quad(8, 8)


# ---------------------------------------------------------------------------
# Build VRAM tile data
# ---------------------------------------------------------------------------

BYTES_PER_TILE = 32          # 4bpp 8×8
TILE_SLOTS     = 32          # two VRAM row-pairs (slots 0-15 and 16-31)
ZERO_TILE      = bytes(BYTES_PER_TILE)


def super_tile_vram_base(k: int) -> int:
    """Dense-pack VRAM base index for super-tile k."""
    return (k // 8) * 32 + (k % 8) * 2


def build_chr(super_tiles: list) -> bytes:
    """Lay out VRAM tile bytes for the given super-tiles in dense-pack order.

    `super_tiles` is a list of 16×16 pixel grids (one per super-tile k).
    Unused VRAM slots are filled with ZERO_TILE.
    """
    slots = [ZERO_TILE] * TILE_SLOTS
    for k, pixels16 in enumerate(super_tiles):
        base = super_tile_vram_base(k)
        tl, tr, bl, br = split_super_tile(pixels16)
        slots[base]      = tile_to_4bpp(tl)
        slots[base + 1]  = tile_to_4bpp(tr)
        slots[base + 16] = tile_to_4bpp(bl)
        slots[base + 17] = tile_to_4bpp(br)
    return b"".join(slots)


# ---------------------------------------------------------------------------
# Build tilemap
# ---------------------------------------------------------------------------

VISIBLE_COLS = 32
VISIBLE_ROWS = 28

K_CORNER  = 0
K_H_EDGE  = 1
K_V_EDGE  = 2
K_BLANK   = 3

HFLIP = 0x4000
VFLIP = 0x8000


def _entry(k: int, hflip: bool = False, vflip: bool = False) -> int:
    base = super_tile_vram_base(k)
    word = base
    if hflip: word |= HFLIP
    if vflip: word |= VFLIP
    return word


def build_tilemap() -> bytes:
    """Build the 32×32 BG1 tilemap for the retro border."""
    tm = [_entry(K_BLANK)] * (32 * 32)

    # Corners
    tm[0 * 32 +  0] = _entry(K_CORNER)
    tm[0 * 32 + 31] = _entry(K_CORNER, hflip=True)
    tm[27 * 32 +  0] = _entry(K_CORNER, vflip=True)
    tm[27 * 32 + 31] = _entry(K_CORNER, hflip=True, vflip=True)

    # Top and bottom edges (cols 1-30)
    for tx in range(1, 31):
        tm[0 * 32 + tx]  = _entry(K_H_EDGE)
        tm[27 * 32 + tx] = _entry(K_H_EDGE, vflip=True)

    # Left and right edges (rows 1-26)
    for ty in range(1, 27):
        tm[ty * 32 +  0] = _entry(K_V_EDGE)
        tm[ty * 32 + 31] = _entry(K_V_EDGE, hflip=True)

    # Interior (rows 1-26, cols 1-30) already blank from initialisation.
    # Rows 28-31 also blank.

    out = bytearray()
    for e in tm:
        out.append(e & 0xFF)
        out.append((e >> 8) & 0xFF)
    return bytes(out)


# ---------------------------------------------------------------------------
# Palette encoding
# ---------------------------------------------------------------------------

def build_palette_bin(pal: list) -> bytes:
    out = bytearray()
    for c in pal:
        out.append(c & 0xFF)
        out.append((c >> 8) & 0xFF)
    return bytes(out)


# ---------------------------------------------------------------------------
# Optional preview PNG
# ---------------------------------------------------------------------------

def _bgr555_to_rgb(c: int):
    r = (c & 0x1F) * 255 // 31
    g = ((c >> 5) & 0x1F) * 255 // 31
    b = ((c >> 10) & 0x1F) * 255 // 31
    return (r, g, b)


def build_preview(super_tiles: list, pal: list, upscale: int = 2) -> "Image":
    if not _PIL_OK:
        return None
    w, h = 512, 448
    rgb = [_bgr555_to_rgb(c) for c in pal]
    img = Image.new("RGB", (w, h), rgb[0])
    px = img.load()

    # Reconstruct full 512×448 from the tilemap layout
    tilemap_entries = []
    out = bytearray(build_tilemap())
    for i in range(0, len(out), 2):
        tilemap_entries.append(out[i] | (out[i + 1] << 8))

    def get_super_pixel(k, flip_h, flip_v, sx, sy):
        pixels16 = super_tiles[k]
        x = (15 - sx) if flip_h else sx
        y = (15 - sy) if flip_v else sy
        return pixels16[y][x]

    for ty in range(VISIBLE_ROWS):
        for tx in range(VISIBLE_COLS):
            entry = tilemap_entries[ty * 32 + tx]
            vram_base = entry & 0x03FF
            flip_h = bool(entry & HFLIP)
            flip_v = bool(entry & VFLIP)
            # Find super-tile k from vram_base
            k = next(
                (i for i in range(len(super_tiles))
                 if super_tile_vram_base(i) == vram_base),
                K_BLANK,
            )
            for sy in range(16):
                for sx in range(16):
                    c = get_super_pixel(k, flip_h, flip_v, sx, sy)
                    px[tx * 16 + sx, ty * 16 + sy] = rgb[c]

    if upscale != 1:
        img = img.resize((w * upscale, h * upscale), Image.NEAREST)
    return img


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    OUT.mkdir(parents=True, exist_ok=True)

    super_tiles = [
        make_corner_pixels(),
        make_h_edge_pixels(),
        make_v_edge_pixels(),
        make_blank_pixels(),
    ]

    chr_data  = build_chr(super_tiles)
    tilemap   = build_tilemap()
    pal_data  = build_palette_bin(PALETTE)

    (OUT / "palette.bin").write_bytes(pal_data)
    (OUT / "tiles.4bpp.chr").write_bytes(chr_data)
    (OUT / "tilemap.bin").write_bytes(tilemap)

    print(
        f"gen_border: -> {OUT}\n"
        f"  palette.bin:      {len(pal_data)} bytes\n"
        f"  tiles.4bpp.chr:   {len(chr_data)} bytes "
        f"({TILE_SLOTS} slots × {BYTES_PER_TILE} B = "
        f"{TILE_SLOTS * BYTES_PER_TILE} B)\n"
        f"  tilemap.bin:      {len(tilemap)} bytes"
    )

    if _PIL_OK:
        preview = build_preview(super_tiles, PALETTE)
        if preview:
            (OUT / "preview.png").write_bytes(b"")  # placeholder
            preview.save(OUT / "preview.png")
            print(f"  preview.png:      saved")
    else:
        print("  (Pillow not available — preview.png skipped)")


if __name__ == "__main__":
    main()
