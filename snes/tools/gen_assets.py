#!/usr/bin/env python3
"""
SNES asset generator (supports 2bpp and 4bpp BG tile formats).

Three independent targets:

  mode0_2bpp/
    palette.bin       4-color 2bpp palette, 8 bytes (BGR555)
    tiles.2bpp.chr    2bpp tile data, 16 bytes per 8x8 tile
    tilemap.bin       32x32 BG1 tilemap (2-byte entries, format-agnostic)
    preview.png       expected screen preview (256x224, 3x upscaled)
    -> character uses palette indices 0..3 only (fits a 2bpp palette).

  mode1_4bpp/
    palette.bin       16-color 4bpp palette, 32 bytes (BGR555)
    tiles.4bpp.chr    4bpp tile data, 32 bytes per 8x8 tile
    tilemap.bin       32x32 BG1 tilemap (2-byte entries, format-agnostic)
    preview.png       expected screen preview (256x224, 3x upscaled)
    -> character uses ALL 16 palette indices (4x4 grid of 4x4 colored
       blocks) so the 4bpp path actually exercises 16 colors.

  mode5_2bpp/
    palette.bin       4-color 2bpp palette, 8 bytes (same art as mode0)
    tiles.2bpp.chr    2bpp tile data, 24 tiles (four 16x16 characters
                      dense-packed at N=0,2,4,6 + blank slot at 8)
    tilemap.bin       32x32 BG2 tilemap; FOUR non-blank entries (one per
                      screen corner) hold the top-left index of each
                      character because Mode 5 BG2 is configured with
                      16x16 tile size (BGMODE bit 5) so the PPU auto-reads
                      N, N+1, N+16, N+17 from a single entry.
    preview.png       expected screen preview (512x448, 2x upscaled)
    -> Mode 5 is horizontal hi-res (512 px) and this demo runs with
       interlace on, so the effective resolution is 512x448. The 16x16
       character therefore appears at half the physical size of the
       mode0 / mode1 previews; that is intentional.

  mode5_image/ (or custom --name)
    palette.bin       2bpp/4bpp palette (BGR555) derived from --source
    tiles.2bpp.chr /  dense-packed, dedup'd (incl. H/V flips) 8x8 tiles
      tiles.4bpp.chr    in the N, N+1, N+16, N+17 super-tile layout
    tilemap.bin       32x32 BG1 tilemap covering the full 512x448 screen
    preview.png       rebuilt 512x448 image (2x upscaled)
    -> Expects a 512x448 source image. If --source is a JPG, a wrong
       size, or not yet palette-reduced, the tool internally reuses the
       crop_image pipeline (scale + center/left/right crop + palette
       quantise with dithering) before slicing into tiles.

Usage:
    python3 tools/gen_assets.py mode0_2bpp
    python3 tools/gen_assets.py mode1_4bpp
    python3 tools/gen_assets.py mode5_2bpp
    python3 tools/gen_assets.py all
    python3 tools/gen_assets.py mode5_image --source PATH [--crop-align {left,center,right}]
                                            [--bpp {2,4}] [--name NAME]
"""
import argparse
import sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crop_image import scale_and_crop, reduce_palette  # noqa: E402

BYTES_PER_TILE = {2: 16, 4: 32}
PALETTE_COLORS = {2: 4, 4: 16}

CHAR_W, CHAR_H = 16, 16

# 4-color palette used by the 2bpp / Mode 0 target.
PALETTE_2BPP_BGR555 = [
    0x0000,  # 0 background (black)
    0x7FFF,  # 1 border     (white)
    0x03E0,  # 2 cross      (green)
    0x7C00,  # 3 center     (blue)
]

# 16-color palette used by the 4bpp / Mode 1 target. Each entry is a
# distinct BGR555 color so all 16 slots are visibly different on screen.
PALETTE_4BPP_BGR555 = [
    0x0000,  # 0  black (also tilemap background)
    0x7FFF,  # 1  white
    0x001F,  # 2  red
    0x01FF,  # 3  orange       (R=31, G=15)
    0x03FF,  # 4  yellow
    0x03E0,  # 5  green
    0x7FE0,  # 6  cyan
    0x7C00,  # 7  blue
    0x7C1F,  # 8  magenta
    0x7C0F,  # 9  purple       (R=15, B=31)
    0x000F,  # 10 dark red     (R=15)
    0x01E0,  # 11 dark green   (G=15)
    0x3C00,  # 12 dark blue    (B=15)
    0x4210,  # 13 dark grey    (R=G=B=16)
    0x6318,  # 14 light grey   (R=G=B=24)
    0x014F,  # 15 brown        (R=15, G=10)
]

# Default 2x2 tile character placement on the BG tilemap, expressed in
# tile-units. For 8x8-tile targets this is 8x8 pixel units, for 16x16-tile
# targets (Mode 5 BG2 with BGMODE bit 5 set) this is 16x16 pixel units.
CHAR_TILE_POS_8 = (14, 11)            # mode0/mode1: near screen center
# Mode 5 screen is 512x448 = 32x28 cells of 16x16 BG tiles. The four
# corner positions are nudged one cell inside the edge so they stay in
# the safe display area (bsnes-plus and real PAL TVs hide a few pixels
# of overscan at every border).
CHAR1_TILE_POS_16 = (1, 1)            # mode5 top-left
CHAR2_TILE_POS_16 = (30, 1)           # mode5 top-right
CHAR3_TILE_POS_16 = (1, 26)           # mode5 bottom-left
CHAR4_TILE_POS_16 = (30, 26)          # mode5 bottom-right

# VRAM tile indices for the 2x2 layout. In 16x16-tile modes the PPU auto-
# assembles (N, N+1, N+16, N+17) from a single tilemap entry, so each
# character occupies four VRAM tile slots in that exact pattern. The
# indices are chosen so that 8x8-tile modes can reference all four slots
# explicitly and 16x16-tile modes can reference only the top-left slot.
#
# Base indices step by 2: N, N+1 sit next to each other in the top VRAM
# row and N+16, N+17 sit next to each other in the row below, so four
# consecutive characters fit back-to-back without wasting slots. This is
# the same dense packing real SNES games use for 16x16 BG tilesets.
#
# Known bsnes-plus Tilemap Viewer quirk: in Mode 5 hires + 16x16 it
# reads eight tiles per cell instead of four, pulling in the next
# character's left column as a ghost on three of the four corners. This
# is a debugger bug, not a ROM bug; hardware and the emulator output
# window are correct. See docs/AI-README.md section "bsnes-plus Tilemap
# Viewer quirk in Mode 5 hires + 16x16" for the full breakdown.
CHAR1_INDICES = (0, 1, 16, 17)        # first 2x2 block
CHAR2_INDICES = (2, 3, 18, 19)        # mode5 only: top-right tile
CHAR3_INDICES = (4, 5, 20, 21)        # mode5 only: bottom-left tile
CHAR4_INDICES = (6, 7, 22, 23)        # mode5 only: bottom-right tile

# BLANK_INDEX points to a reserved 16x16 "transparent" super-tile sitting
# in the free VRAM column right after the four characters. For Mode 5
# hardware auto-read pulls in tiles 8, 9, 24, 25 (all zero: 8..15 are
# padding inside the upload, 24, 25 stay zero from the boot-time VRAM
# clear). bsnes-plus' Mode 5 hires Tilemap Viewer additionally pulls in
# tiles 10 and 26; those are also zero for the same reasons. So empty
# tilemap entries render as true black in every viewer and on hardware.
# For the 8x8-tile mode0/mode1 targets this same slot is equally fine —
# tile 8 is inside DEFAULT_TILES_TO_UPLOAD but not touched by any
# character, so build_vram_tiles leaves it as the all-zero blank_tile.
BLANK_INDEX = 8

# How many tiles each target uploads to VRAM. Mode0/Mode1 only need the
# default character (indices 0,1,16,17); uploading 18 tiles (0..17)
# comfortably covers both the character and the blank tile at index 8.
# Mode5 uses four 16x16 characters whose highest VRAM index is 23
# (CHAR4_INDICES[3]), so it must upload 24 tiles (indices 0..23).
DEFAULT_TILES_TO_UPLOAD = 18
MODE5_TILES_TO_UPLOAD = 24


def render_2bpp_character_pixels():
    """16x16 pixel art using only palette indices 0..3 (fits a 2bpp palette).

    - border of color 1
    - cross through the center in color 2
    - 4x4 center block in color 3
    """
    pixels = [[0 for _ in range(CHAR_W)] for _ in range(CHAR_H)]
    for y in range(CHAR_H):
        for x in range(CHAR_W):
            if x in (0, CHAR_W - 1) or y in (0, CHAR_H - 1):
                pixels[y][x] = 1
            if x == CHAR_W // 2 or y == CHAR_H // 2:
                pixels[y][x] = 2
    for y in range(6, 10):
        for x in range(6, 10):
            pixels[y][x] = 3
    return pixels


def render_2bpp_character2_pixels():
    """Second 16x16 pixel art, also constrained to palette indices 0..3.

    Visually distinct from `render_2bpp_character_pixels` so the Mode 5
    demo clearly shows two different tiles side by side:

    - border of color 1 (white)
    - diagonal X through the tile in color 2 (green)
    - 2x2 center block in color 3 (blue)
    """
    pixels = [[0 for _ in range(CHAR_W)] for _ in range(CHAR_H)]
    for y in range(CHAR_H):
        for x in range(CHAR_W):
            if x in (0, CHAR_W - 1) or y in (0, CHAR_H - 1):
                pixels[y][x] = 1
            if x == y or x == CHAR_W - 1 - y:
                pixels[y][x] = 2
    for y in range(7, 9):
        for x in range(7, 9):
            pixels[y][x] = 3
    return pixels


def render_2bpp_character3_pixels():
    """Third 16x16 pixel art, palette indices 0..3 only.

    Visually distinct from the other three Mode 5 tiles:

    - border of color 1 (white)
    - solid 12x12 filled interior in color 3 (blue)
    """
    pixels = [[0 for _ in range(CHAR_W)] for _ in range(CHAR_H)]
    for y in range(CHAR_H):
        for x in range(CHAR_W):
            if x in (0, CHAR_W - 1) or y in (0, CHAR_H - 1):
                pixels[y][x] = 1
            elif 2 <= x <= CHAR_W - 3 and 2 <= y <= CHAR_H - 3:
                pixels[y][x] = 3
    return pixels


def render_2bpp_character4_pixels():
    """Fourth 16x16 pixel art, palette indices 0..3 only.

    Visually distinct from the other three Mode 5 tiles:

    - border of color 1 (white)
    - 2x2-block checkerboard of colors 2 (green) and 3 (blue) filling
      the 14x14 interior
    """
    pixels = [[0 for _ in range(CHAR_W)] for _ in range(CHAR_H)]
    for y in range(CHAR_H):
        for x in range(CHAR_W):
            if x in (0, CHAR_W - 1) or y in (0, CHAR_H - 1):
                pixels[y][x] = 1
            else:
                block_x = (x - 1) // 2
                block_y = (y - 1) // 2
                pixels[y][x] = 2 if (block_x + block_y) % 2 == 0 else 3
    return pixels


def render_4bpp_character_pixels():
    """16x16 pixel art that uses ALL 16 palette indices (0..15).

    The tile is divided into a 4x4 grid of 4x4 colored blocks. Block
    (bx, by) paints palette index `by * 4 + bx`, so every 4bpp palette
    slot shows up as a visible 4x4 patch on screen.
    """
    pixels = [[0 for _ in range(CHAR_W)] for _ in range(CHAR_H)]
    for y in range(CHAR_H):
        for x in range(CHAR_W):
            block_y = y // 4
            block_x = x // 4
            pixels[y][x] = block_y * 4 + block_x
    return pixels


def tile_to_bitplanes(tile_pixels, bpp):
    """Encode an 8x8 tile to the SNES 2bpp or 4bpp tile format.

    2bpp (16 bytes/tile):
      off 0x00..0x0F : 8 rows of (plane0, plane1)

    4bpp (32 bytes/tile):
      off 0x00..0x0F : 8 rows of (plane0, plane1)
      off 0x10..0x1F : 8 rows of (plane2, plane3)
    """
    assert bpp in BYTES_PER_TILE
    max_val = (1 << bpp) - 1
    assert len(tile_pixels) == 8 and all(len(r) == 8 for r in tile_pixels)
    for row in tile_pixels:
        assert all(0 <= v <= max_val for v in row), \
            f"pixel value out of range for {bpp}bpp"

    out = bytearray()
    for row in tile_pixels:
        p0 = 0
        p1 = 0
        for x, val in enumerate(row):
            bit = 7 - x
            p0 |= ((val >> 0) & 1) << bit
            p1 |= ((val >> 1) & 1) << bit
        out.append(p0)
        out.append(p1)
    if bpp == 4:
        for row in tile_pixels:
            p2 = 0
            p3 = 0
            for x, val in enumerate(row):
                bit = 7 - x
                p2 |= ((val >> 2) & 1) << bit
                p3 |= ((val >> 3) & 1) << bit
            out.append(p2)
            out.append(p3)
    assert len(out) == BYTES_PER_TILE[bpp]
    return out


def encode_palette(colors_bgr555, bpp):
    expected = PALETTE_COLORS[bpp]
    colors = list(colors_bgr555)
    if len(colors) > expected:
        raise ValueError(
            f"palette has {len(colors)} colors, too many for {bpp}bpp "
            f"(max {expected})"
        )
    colors += [0x0000] * (expected - len(colors))
    out = bytearray()
    for c in colors:
        out.append(c & 0xFF)
        out.append((c >> 8) & 0xFF)
    assert len(out) == expected * 2
    return out


def split_character_tiles(pixels, bpp):
    quads = []
    for ty in range(2):
        for tx in range(2):
            tile = [
                [pixels[ty * 8 + y][tx * 8 + x] for x in range(8)]
                for y in range(8)
            ]
            quads.append(tile_to_bitplanes(tile, bpp))
    return quads


def build_vram_tiles(character_tile_sets, blank_tile, tiles_to_upload):
    """Lay out VRAM tile data for upload.

    character_tile_sets is a list of (quads, indices) pairs where:
      - quads is the 4-tile character produced by split_character_tiles
      - indices is a (tl, tr, bl, br) tuple giving VRAM tile slots

    Slots not populated by any character are filled with `blank_tile`.
    """
    tiles = [blank_tile] * tiles_to_upload
    for quads, (tl, tr, bl, br) in character_tile_sets:
        tiles[tl] = quads[0]
        tiles[tr] = quads[1]
        tiles[bl] = quads[2]
        tiles[br] = quads[3]
    return bytearray().join(tiles)


def build_tilemap(tile_pixels_size, placements):
    """Build a 32x32 BG tilemap that places one or more characters.

    `placements` is a list of (tile_pos, vram_indices) pairs, where:
      - tile_pos = (x, y) in tile units (8x8 or 16x16 pixels depending
        on the BG's configured tile size).
      - vram_indices = (tl, tr, bl, br) — the VRAM tile indices making
        up the character's 2x2 layout.

    tile_pixels_size = 8  -> each character writes four entries
                             (tl, tr, bl, br) into the tilemap.
    tile_pixels_size = 16 -> each character writes a single entry (the
                             top-left index); the PPU auto-reads
                             N, N+1, N+16, N+17 per tilemap entry, so
                             a single entry covers the whole 2x2 VRAM
                             tile block.
    """
    tm = [BLANK_INDEX] * (32 * 32)
    for (tile_x, tile_y), (tl, tr, bl, br) in placements:
        if tile_pixels_size == 8:
            base = tile_y * 32 + tile_x
            tm[base] = tl
            tm[base + 1] = tr
            tm[base + 32] = bl
            tm[base + 33] = br
        elif tile_pixels_size == 16:
            tm[tile_y * 32 + tile_x] = tl
        else:
            raise ValueError(
                f"unsupported tile_pixels_size {tile_pixels_size}"
            )
    out = bytearray()
    for entry in tm:
        out.append(entry & 0xFF)
        out.append((entry >> 8) & 0xFF)
    return out


def bgr555_to_rgb(c):
    r = (c & 0x1F) * 255 // 31
    g = ((c >> 5) & 0x1F) * 255 // 31
    b = ((c >> 10) & 0x1F) * 255 // 31
    return (r, g, b)


def build_preview(
    rendered_characters, palette_bgr555, bpp, tile_pixels_size, screen_size
):
    """Render a 1:1 preview of what the ROM should show, then upscale.

    rendered_characters is a list of (pixels, tile_pos) pairs giving
    each character's 16x16 pixel bitmap and its tilemap position.

    screen_size = (256, 224) for standard modes, (512, 448) for Mode 5
                  + interlace hi-res. The tile-unit-to-pixel origin is
                  derived from the same tile_pixels_size as
                  build_tilemap().
    """
    slots = PALETTE_COLORS[bpp]
    rgb = [bgr555_to_rgb(c) for c in palette_bgr555]
    while len(rgb) < slots:
        rgb.append((0, 0, 0))
    screen_w, screen_h = screen_size
    img = Image.new("RGB", (screen_w, screen_h), rgb[0])
    for pixels, (tile_x, tile_y) in rendered_characters:
        origin_x = tile_x * tile_pixels_size
        origin_y = tile_y * tile_pixels_size
        for y in range(CHAR_H):
            for x in range(CHAR_W):
                img.putpixel(
                    (origin_x + x, origin_y + y), rgb[pixels[y][x]]
                )
    # Smaller screens get 3x upscale; the 512x448 hi-res preview stays at
    # 2x so the PNG doesn't explode in size while still being inspectable.
    upscale = 3 if screen_w <= 256 else 2
    return img.resize((screen_w * upscale, screen_h * upscale), Image.NEAREST)


# ---------------------------------------------------------------------------
# Target registry
# ---------------------------------------------------------------------------

TARGETS = {
    "mode0_2bpp": {
        "bpp": 2,
        "chr_name": "tiles.2bpp.chr",
        "palette": PALETTE_2BPP_BGR555,
        "tile_pixels_size": 8,
        "screen_size": (256, 224),
        "tiles_to_upload": DEFAULT_TILES_TO_UPLOAD,
        "characters": [
            {
                "render_pixels": render_2bpp_character_pixels,
                "tile_pos": CHAR_TILE_POS_8,
                "vram_indices": CHAR1_INDICES,
            },
        ],
    },
    "mode1_4bpp": {
        "bpp": 4,
        "chr_name": "tiles.4bpp.chr",
        "palette": PALETTE_4BPP_BGR555,
        "tile_pixels_size": 8,
        "screen_size": (256, 224),
        "tiles_to_upload": DEFAULT_TILES_TO_UPLOAD,
        "characters": [
            {
                "render_pixels": render_4bpp_character_pixels,
                "tile_pos": CHAR_TILE_POS_8,
                "vram_indices": CHAR1_INDICES,
            },
        ],
    },
    "mode5_2bpp": {
        "bpp": 2,
        "chr_name": "tiles.2bpp.chr",
        "palette": PALETTE_2BPP_BGR555,
        # Mode 5 BG2 with BGMODE bit 5 set uses 16x16 BG tiles assembled
        # from the same N, N+1, N+16, N+17 VRAM layout as mode0.
        "tile_pixels_size": 16,
        # Mode 5 is horizontal hi-res; with interlace on, the effective
        # display is 512x448, so previews are rendered at that size.
        "screen_size": (512, 448),
        # Mode 5 uploads four characters (cross, X, filled square,
        # checkerboard) arranged at all four screen corners, so Mode 5's
        # VRAM must cover tile indices up to 29 inclusive.
        "tiles_to_upload": MODE5_TILES_TO_UPLOAD,
        "characters": [
            {
                "render_pixels": render_2bpp_character_pixels,
                "tile_pos": CHAR1_TILE_POS_16,
                "vram_indices": CHAR1_INDICES,
            },
            {
                "render_pixels": render_2bpp_character2_pixels,
                "tile_pos": CHAR2_TILE_POS_16,
                "vram_indices": CHAR2_INDICES,
            },
            {
                "render_pixels": render_2bpp_character3_pixels,
                "tile_pos": CHAR3_TILE_POS_16,
                "vram_indices": CHAR3_INDICES,
            },
            {
                "render_pixels": render_2bpp_character4_pixels,
                "tile_pos": CHAR4_TILE_POS_16,
                "vram_indices": CHAR4_INDICES,
            },
        ],
    },
}


def generate_target(name):
    spec = TARGETS[name]
    bpp = spec["bpp"]
    chr_name = spec["chr_name"]
    palette = spec["palette"]
    tile_pixels_size = spec["tile_pixels_size"]
    screen_size = spec["screen_size"]
    tiles_to_upload = spec["tiles_to_upload"]

    target_dir = BUILD / name
    target_dir.mkdir(parents=True, exist_ok=True)

    rendered = [
        (c["render_pixels"](), c["tile_pos"], c["vram_indices"])
        for c in spec["characters"]
    ]
    character_tile_sets = [
        (split_character_tiles(pixels, bpp), vram_indices)
        for pixels, _, vram_indices in rendered
    ]
    blank_tile = tile_to_bitplanes([[0] * 8 for _ in range(8)], bpp)

    placements = [
        (tile_pos, vram_indices) for _, tile_pos, vram_indices in rendered
    ]
    preview_characters = [
        (pixels, tile_pos) for pixels, tile_pos, _ in rendered
    ]

    (target_dir / "palette.bin").write_bytes(encode_palette(palette, bpp))
    (target_dir / chr_name).write_bytes(
        build_vram_tiles(character_tile_sets, blank_tile, tiles_to_upload)
    )
    (target_dir / "tilemap.bin").write_bytes(
        build_tilemap(tile_pixels_size, placements)
    )
    build_preview(
        preview_characters, palette, bpp, tile_pixels_size, screen_size
    ).save(target_dir / "preview.png")


# ---------------------------------------------------------------------------
# Image-based Mode 5 target (mode5_image)
# ---------------------------------------------------------------------------
#
# Pipeline:
#   1. Load source image (JPG/PNG). If size != 512x448 or color count
#      exceeds 2**bpp, reuse crop_image.scale_and_crop + reduce_palette to
#      coerce it into a 512x448 indexed image with <= 2**bpp colors.
#   2. Slice into 32x28 super-tiles (each 16x16 px = 4 8x8 tiles in
#      TL, TR, BL, BR order).
#   3. Dedupe super-tiles across 4 flip variants (identity, H, V, HV);
#      the tilemap word encodes H/V-flip for free, so mirrored regions
#      of the image share VRAM slots.
#   4. Dense-pack unique super-tiles: super-tile k -> VRAM base index
#      (k // 8) * 32 + (k % 8) * 2 (see docs/AI-MODE-5-README.md 3.2 /
#      9.4). After the last unique super-tile comes one reserved blank
#      super-tile; empty tilemap cells (rows y >= 28) point at it.
#   5. Emit palette.bin, tiles.<bpp>bpp.chr, tilemap.bin and preview.png.
#
# Defaults match section 7 of AI-MODE-5-README.md for BG1 4bpp.

MODE5_IMAGE_TARGET = "mode5_image"
MODE5_SCREEN_W, MODE5_SCREEN_H = 512, 448
MODE5_SUPER_SIZE = 16
MODE5_SUPER_COLS = MODE5_SCREEN_W // MODE5_SUPER_SIZE   # 32
MODE5_SUPER_ROWS = MODE5_SCREEN_H // MODE5_SUPER_SIZE   # 28
MAX_TILE_INDEX = 1023   # tilemap entry has a 10-bit tile index


def rgb_to_bgr555(r, g, b):
    r5 = min(31, (r * 31 + 127) // 255)
    g5 = min(31, (g * 31 + 127) // 255)
    b5 = min(31, (b * 31 + 127) // 255)
    return (b5 << 10) | (g5 << 5) | r5


def load_image_as_indexed(source_path, bpp, crop_align="center"):
    """Return (pixels_2d, palette_bgr555) for a 512x448 image with <= 2**bpp
    colors. Delegates to crop_image.scale_and_crop / reduce_palette when
    the source isn't already the right shape and palette."""
    max_colors = 1 << bpp
    with Image.open(source_path) as im:
        im.load()

    # Coerce to 512x448.
    if im.size != (MODE5_SCREEN_W, MODE5_SCREEN_H):
        im = scale_and_crop(
            im.convert("RGB"),
            MODE5_SCREEN_W,
            MODE5_SCREEN_H,
            crop_align,
        )

    # Coerce color count. If already palette and within budget, keep it
    # so the caller's existing palette survives; otherwise quantise.
    need_quantise = True
    if im.mode == "P" and im.getpalette() is not None:
        _, hi = im.getextrema()
        if hi < max_colors:
            need_quantise = False
    if need_quantise:
        im = reduce_palette(im, bpp)

    flat_palette = im.getpalette()[: max_colors * 3]
    while len(flat_palette) < max_colors * 3:
        flat_palette.append(0)
    palette_bgr555 = [
        rgb_to_bgr555(
            flat_palette[i * 3],
            flat_palette[i * 3 + 1],
            flat_palette[i * 3 + 2],
        )
        for i in range(max_colors)
    ]

    w, h = im.size
    px = im.load()
    pixels = [[px[x, y] for x in range(w)] for y in range(h)]
    return pixels, palette_bgr555


def slice_super_tiles(pixels):
    """Return a MODE5_SUPER_ROWS x MODE5_SUPER_COLS grid of super-tiles.

    Each super-tile is [TL, TR, BL, BR], each entry an 8x8 pixel list."""
    grid = []
    for ty in range(MODE5_SUPER_ROWS):
        row = []
        for tx in range(MODE5_SUPER_COLS):
            quads = []
            for qy in range(2):
                for qx in range(2):
                    ox = tx * MODE5_SUPER_SIZE + qx * 8
                    oy = ty * MODE5_SUPER_SIZE + qy * 8
                    tile = [
                        [pixels[oy + y][ox + x] for x in range(8)]
                        for y in range(8)
                    ]
                    quads.append(tile)
            row.append(quads)
        grid.append(row)
    return grid


def _hflip_tile(tile):
    return [list(reversed(r)) for r in tile]


def _vflip_tile(tile):
    return list(reversed(tile))


def flip_super_tile(super_tile, hflip, vflip):
    tl, tr, bl, br = super_tile
    if hflip:
        tl, tr = _hflip_tile(tr), _hflip_tile(tl)
        bl, br = _hflip_tile(br), _hflip_tile(bl)
    if vflip:
        tl, bl = _vflip_tile(bl), _vflip_tile(tl)
        tr, br = _vflip_tile(br), _vflip_tile(tr)
    return [tl, tr, bl, br]


def _super_tile_key(super_tile):
    buf = bytearray()
    for tile in super_tile:
        for row in tile:
            buf.extend(row)
    return bytes(buf)


def dedupe_super_tiles(grid):
    """Return (unique, placements) with flip-aware dedup.

    placements[ty][tx] = (unique_index, hflip, vflip). unique is a list
    of super-tiles stored in their first-seen (unflipped) form."""
    unique = []
    key_to_index = {}
    placements = []
    for row in grid:
        placement_row = []
        for super_tile in row:
            match = None
            for hf in (0, 1):
                for vf in (0, 1):
                    flipped = flip_super_tile(super_tile, hf, vf)
                    key = _super_tile_key(flipped)
                    if key in key_to_index:
                        match = (key_to_index[key], hf, vf)
                        break
                if match:
                    break
            if match is None:
                idx = len(unique)
                unique.append(super_tile)
                key_to_index[_super_tile_key(super_tile)] = idx
                match = (idx, 0, 0)
            placement_row.append(match)
        placements.append(placement_row)
    return unique, placements


def super_tile_vram_base(k):
    """Dense-pack VRAM base index for super-tile k (8 super-tiles per
    row-pair, each occupying slots N, N+1, N+16, N+17)."""
    return (k // 8) * 32 + (k % 8) * 2


def build_mode5_image_vram(unique_super_tiles, bpp, blank_index):
    """Lay out VRAM tile bytes for all unique super-tiles plus one
    reserved blank super-tile (at `blank_index`). Unused slots inside
    the covered row-pairs are filled with zero tiles."""
    total_supertiles = blank_index + 1
    row_pairs = (total_supertiles + 7) // 8
    total_tile_slots = row_pairs * 32
    blank_tile = tile_to_bitplanes([[0] * 8 for _ in range(8)], bpp)
    tiles = [blank_tile] * total_tile_slots

    last_needed = super_tile_vram_base(blank_index) + 17
    if last_needed > MAX_TILE_INDEX:
        raise SystemExit(
            f"mode5_image: {len(unique_super_tiles)} unique super-tiles "
            f"(+1 blank) require tile index {last_needed}, but BG tilemap "
            f"entries are capped at {MAX_TILE_INDEX}. Source has too much "
            f"detail for a single 4bpp/2bpp BG1. Options: reduce colour "
            f"count, use a source with more self-similarity, or split "
            f"across multiple BG layers."
        )

    for k, super_tile in enumerate(unique_super_tiles):
        base = super_tile_vram_base(k)
        tiles[base] = tile_to_bitplanes(super_tile[0], bpp)
        tiles[base + 1] = tile_to_bitplanes(super_tile[1], bpp)
        tiles[base + 16] = tile_to_bitplanes(super_tile[2], bpp)
        tiles[base + 17] = tile_to_bitplanes(super_tile[3], bpp)
    # blank_index slot intentionally left as zero tiles.
    return bytearray().join(tiles)


def build_mode5_image_tilemap(placements, blank_index, palette_idx=0):
    """Build the 32x32 BG1 tilemap covering the visible 32x28 super-tile
    area; the 4 rows below the screen use the blank super-tile."""
    palette_bits = (palette_idx & 0x7) << 10
    blank_entry = super_tile_vram_base(blank_index) | palette_bits
    tm = [blank_entry] * (32 * 32)
    for ty, row in enumerate(placements):
        for tx, (idx, hflip, vflip) in enumerate(row):
            entry = super_tile_vram_base(idx) | palette_bits
            if hflip:
                entry |= 0x4000
            if vflip:
                entry |= 0x8000
            tm[ty * 32 + tx] = entry
    out = bytearray()
    for e in tm:
        out.append(e & 0xFF)
        out.append((e >> 8) & 0xFF)
    return out


def build_image_preview(pixels, palette_bgr555, upscale=2):
    """Render the deduplicated image back to a PNG for visual inspection."""
    h = len(pixels)
    w = len(pixels[0])
    rgb = [bgr555_to_rgb(c) for c in palette_bgr555]
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = rgb[pixels[y][x]]
    if upscale != 1:
        img = img.resize((w * upscale, h * upscale), Image.NEAREST)
    return img


def _reconstruct_pixels(unique_super_tiles, placements):
    """Reassemble the full 512x448 pixel grid from the deduped data; a
    self-check that the dedupe+flip logic reproduces the input exactly."""
    out = [[0] * MODE5_SCREEN_W for _ in range(MODE5_SCREEN_H)]
    for ty, row in enumerate(placements):
        for tx, (idx, hf, vf) in enumerate(row):
            super_tile = flip_super_tile(unique_super_tiles[idx], hf, vf)
            for qi, tile in enumerate(super_tile):
                qx = qi % 2
                qy = qi // 2
                for y in range(8):
                    for x in range(8):
                        out[ty * 16 + qy * 8 + y][tx * 16 + qx * 8 + x] = (
                            tile[y][x]
                        )
    return out


def generate_mode5_image(source, bpp, crop_align, name):
    if bpp not in BYTES_PER_TILE:
        raise SystemExit(f"unsupported bpp {bpp}, must be 2 or 4")
    source = Path(source)
    if not source.is_file():
        raise SystemExit(f"source image not found: {source}")

    target_dir = BUILD / name
    target_dir.mkdir(parents=True, exist_ok=True)

    pixels, palette_bgr555 = load_image_as_indexed(source, bpp, crop_align)
    grid = slice_super_tiles(pixels)
    unique_super_tiles, placements = dedupe_super_tiles(grid)

    # Sanity check: reassembled image must equal the (quantised) input.
    assert _reconstruct_pixels(unique_super_tiles, placements) == pixels, (
        "dedupe round-trip mismatch"
    )

    blank_index = len(unique_super_tiles)
    chr_name = f"tiles.{bpp}bpp.chr"

    (target_dir / "palette.bin").write_bytes(encode_palette(palette_bgr555, bpp))
    (target_dir / chr_name).write_bytes(
        build_mode5_image_vram(unique_super_tiles, bpp, blank_index)
    )
    (target_dir / "tilemap.bin").write_bytes(
        build_mode5_image_tilemap(placements, blank_index)
    )
    build_image_preview(pixels, palette_bgr555, upscale=2).save(
        target_dir / "preview.png"
    )

    unique_count = len(unique_super_tiles)
    tiles_used = super_tile_vram_base(blank_index) + 18  # inclusive of last slot
    print(
        f"mode5_image: {source} -> {target_dir}\n"
        f"  super-tiles: {MODE5_SUPER_COLS * MODE5_SUPER_ROWS} total, "
        f"{unique_count} unique after flip-dedup "
        f"({unique_count * 100 / (MODE5_SUPER_COLS * MODE5_SUPER_ROWS):.1f}%)\n"
        f"  VRAM tiles used: {tiles_used} / {MAX_TILE_INDEX + 1} "
        f"({tiles_used * BYTES_PER_TILE[bpp]} bytes @ {bpp}bpp)\n"
        f"  palette: {1 << bpp} colors ({1 << bpp} * 2 = "
        f"{(1 << bpp) * 2} bytes)"
    )


# ---------------------------------------------------------------------------
# Image-based Mode 1 target (mode1_image)
# ---------------------------------------------------------------------------
#
# Full-screen 256x224 background for Mode 1 BG1 (4bpp, 8x8 tiles). Unlike
# the Mode 5 pipeline this uses PLAIN 8x8 tiles (no 16x16 super-tiles),
# which fits the Mode 1 BG layout directly. A 256x224 screen = 32x28 =
# 896 cells, well inside the 10-bit (1024) tilemap index limit, so fully
# unique images are representable on BG1 alone (modulo flip-dedup to save
# VRAM / ROM bytes).
#
# Pipeline:
#   1. Load source image; coerce to 256x224 via crop_image.scale_and_crop
#      if it isn't already that size.
#   2. Quantise to 2**bpp colors if needed (same helper as mode5_image).
#   3. Slice into 8x8 tiles, dedupe across 4 flip variants (identity,
#      H, V, HV). Tilemap entries encode H/V flip for free.
#   4. Emit palette.bin, tiles.<bpp>bpp.chr (dense, no blank padding),
#      tilemap.bin (32x32 entries, 32x28 visible + blank rows), and
#      preview.png (1x, 256x224).
#
# Notes:
# - bpp defaults to 4 because Mode 1 BG1 is 4bpp. bpp=2 is allowed for
#   Mode 1 BG3-style experiments, but the generated tilemap assumes
#   palette 0 and is layer-agnostic beyond that.

MODE1_IMAGE_TARGET = "mode1_image"
MODE1_SCREEN_W, MODE1_SCREEN_H = 256, 224
MODE1_TILE_SIZE = 8
MODE1_TILE_COLS = MODE1_SCREEN_W // MODE1_TILE_SIZE   # 32
MODE1_TILE_ROWS = MODE1_SCREEN_H // MODE1_TILE_SIZE   # 28


def load_image_as_indexed_generic(source_path, bpp, width, height, crop_align):
    """Like load_image_as_indexed but for arbitrary target size."""
    max_colors = 1 << bpp
    with Image.open(source_path) as im:
        im.load()
    if im.size != (width, height):
        im = scale_and_crop(im.convert("RGB"), width, height, crop_align)
    need_quantise = True
    if im.mode == "P" and im.getpalette() is not None:
        _, hi = im.getextrema()
        if hi < max_colors:
            need_quantise = False
    if need_quantise:
        im = reduce_palette(im, bpp)
    flat_palette = im.getpalette()[: max_colors * 3]
    while len(flat_palette) < max_colors * 3:
        flat_palette.append(0)
    palette_bgr555 = [
        rgb_to_bgr555(
            flat_palette[i * 3],
            flat_palette[i * 3 + 1],
            flat_palette[i * 3 + 2],
        )
        for i in range(max_colors)
    ]
    w, h = im.size
    px = im.load()
    pixels = [[px[x, y] for x in range(w)] for y in range(h)]
    return pixels, palette_bgr555


def _tile_key(tile):
    buf = bytearray()
    for row in tile:
        buf.extend(row)
    return bytes(buf)


def dedupe_tiles_8x8(pixels):
    """Flip-dedup all 8x8 tiles in `pixels`.

    Returns (unique_tiles, placements). placements[ty][tx] = (index, hflip,
    vflip) for the tile at cell (tx, ty). unique_tiles is a list of 8x8
    pixel grids (stored in their first-seen orientation)."""
    rows = len(pixels) // 8
    cols = len(pixels[0]) // 8
    unique = []
    key_to_index = {}
    placements = []
    for ty in range(rows):
        placement_row = []
        for tx in range(cols):
            tile = [
                [pixels[ty * 8 + y][tx * 8 + x] for x in range(8)]
                for y in range(8)
            ]
            match = None
            for hf in (0, 1):
                for vf in (0, 1):
                    flipped = tile
                    if hf:
                        flipped = [list(reversed(r)) for r in flipped]
                    if vf:
                        flipped = list(reversed(flipped))
                    key = _tile_key(flipped)
                    if key in key_to_index:
                        match = (key_to_index[key], hf, vf)
                        break
                if match:
                    break
            if match is None:
                idx = len(unique)
                unique.append(tile)
                key_to_index[_tile_key(tile)] = idx
                match = (idx, 0, 0)
            placement_row.append(match)
        placements.append(placement_row)
    return unique, placements


def build_mode1_image_vram(unique_tiles, bpp):
    """Dense-pack unique 8x8 tiles as one CHR blob (no padding)."""
    out = bytearray()
    for tile in unique_tiles:
        out.extend(tile_to_bitplanes(tile, bpp))
    return out


def build_mode1_image_tilemap(placements, palette_idx=0):
    """Build a 32x32 BG1 tilemap for a 32x28 image placement.

    Rows 28..31 are filled with tile index 0 (which is part of the dense
    tile set; always drawn but sits below the visible 224-line area)."""
    palette_bits = (palette_idx & 0x7) << 10
    tm = [palette_bits] * (32 * 32)  # index 0, no flip
    for ty, row in enumerate(placements):
        for tx, (idx, hflip, vflip) in enumerate(row):
            entry = (idx & 0x3FF) | palette_bits
            if hflip:
                entry |= 0x4000
            if vflip:
                entry |= 0x8000
            tm[ty * 32 + tx] = entry
    out = bytearray()
    for e in tm:
        out.append(e & 0xFF)
        out.append((e >> 8) & 0xFF)
    return out


def _reconstruct_pixels_8x8(unique_tiles, placements, width, height):
    out = [[0] * width for _ in range(height)]
    for ty, row in enumerate(placements):
        for tx, (idx, hf, vf) in enumerate(row):
            tile = unique_tiles[idx]
            if hf:
                tile = [list(reversed(r)) for r in tile]
            if vf:
                tile = list(reversed(tile))
            for y in range(8):
                for x in range(8):
                    out[ty * 8 + y][tx * 8 + x] = tile[y][x]
    return out


def generate_mode1_image(source, bpp, crop_align, name):
    if bpp not in BYTES_PER_TILE:
        raise SystemExit(f"unsupported bpp {bpp}, must be 2 or 4")
    source = Path(source)
    if not source.is_file():
        raise SystemExit(f"source image not found: {source}")

    target_dir = BUILD / name
    target_dir.mkdir(parents=True, exist_ok=True)

    pixels, palette_bgr555 = load_image_as_indexed_generic(
        source, bpp, MODE1_SCREEN_W, MODE1_SCREEN_H, crop_align
    )
    unique_tiles, placements = dedupe_tiles_8x8(pixels)

    assert _reconstruct_pixels_8x8(
        unique_tiles, placements, MODE1_SCREEN_W, MODE1_SCREEN_H
    ) == pixels, "mode1_image dedupe round-trip mismatch"

    if len(unique_tiles) > MAX_TILE_INDEX + 1:
        raise SystemExit(
            f"mode1_image: {len(unique_tiles)} unique 8x8 tiles exceed the "
            f"10-bit tilemap index limit ({MAX_TILE_INDEX + 1}). Reduce "
            f"colour count or detail."
        )

    chr_name = f"tiles.{bpp}bpp.chr"
    (target_dir / "palette.bin").write_bytes(encode_palette(palette_bgr555, bpp))
    (target_dir / chr_name).write_bytes(
        build_mode1_image_vram(unique_tiles, bpp)
    )
    (target_dir / "tilemap.bin").write_bytes(
        build_mode1_image_tilemap(placements)
    )
    # 1:1 preview so the pixels match the on-screen resolution.
    build_image_preview(pixels, palette_bgr555, upscale=1).save(
        target_dir / "preview.png"
    )

    tile_bytes = len(unique_tiles) * BYTES_PER_TILE[bpp]
    print(
        f"mode1_image: {source} -> {target_dir}\n"
        f"  tiles: {MODE1_TILE_COLS * MODE1_TILE_ROWS} total, "
        f"{len(unique_tiles)} unique after flip-dedup "
        f"({len(unique_tiles) * 100 / (MODE1_TILE_COLS * MODE1_TILE_ROWS):.1f}%)\n"
        f"  CHR bytes: {tile_bytes} ({tile_bytes / 1024:.1f} KiB) "
        f"@ {bpp}bpp, {len(unique_tiles)} / {MAX_TILE_INDEX + 1} indices used\n"
        f"  palette: {1 << bpp} colors ({(1 << bpp) * 2} bytes)"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "target",
        choices=list(TARGETS.keys())
        + [MODE5_IMAGE_TARGET, MODE1_IMAGE_TARGET, "all"],
        help="which asset set to generate",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help=f"source image for {MODE5_IMAGE_TARGET} (JPG/PNG)",
    )
    parser.add_argument(
        "--crop-align",
        choices=("left", "center", "right"),
        default="center",
        help=(
            "horizontal crop anchor when --source needs scaling to 512x448 "
            f"(default: center; only used for {MODE5_IMAGE_TARGET})"
        ),
    )
    parser.add_argument(
        "--bpp",
        type=int,
        choices=(2, 4),
        default=4,
        help=f"bit depth for {MODE5_IMAGE_TARGET} (default: 4)",
    )
    parser.add_argument(
        "--name",
        default=MODE5_IMAGE_TARGET,
        help=(
            f"output directory name under build/ for {MODE5_IMAGE_TARGET} "
            f"(default: {MODE5_IMAGE_TARGET})"
        ),
    )
    args = parser.parse_args()

    BUILD.mkdir(exist_ok=True)
    if args.target == MODE5_IMAGE_TARGET:
        if args.source is None:
            parser.error(f"--source is required for {MODE5_IMAGE_TARGET}")
        generate_mode5_image(args.source, args.bpp, args.crop_align, args.name)
    elif args.target == MODE1_IMAGE_TARGET:
        if args.source is None:
            parser.error(f"--source is required for {MODE1_IMAGE_TARGET}")
        name = args.name if args.name != MODE5_IMAGE_TARGET else MODE1_IMAGE_TARGET
        generate_mode1_image(args.source, args.bpp, args.crop_align, name)
    elif args.target == "all":
        for name in TARGETS:
            generate_target(name)
    else:
        generate_target(args.target)


if __name__ == "__main__":
    main()
