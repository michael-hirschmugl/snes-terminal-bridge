# AI-MODE-5-README.md

> **Origin note.** This document was ported from the sibling project
> [`snes-tile-test`](https://example.invalid/) — it is the authoritative
> Mode 5 / hi-res / dense-pack reference that `snes-terminal-bridge`
> relies on for its SNES build path (`snes/src/main.asm`,
> `snes/tools/gen_font.py`, `snes/tools/gen_assets.py`, the keymap
> emitted by `snes/tools/gen_keymap.py`). The hardware/PPU facts and
> layout rules described below apply unchanged.
>
> Paths mentioned in the text refer to the original `snes-tile-test`
> layout and are kept for traceability. Mapping to files in this repo:
>
> | Reference in this document | File in this repo |
> |---|---|
> | `tools/gen_assets.py` (image → dense-pack BG1 assets) | `snes/tools/gen_assets.py` (ported directly) |
> | `tools/gen_assets.py` (font → dense-pack BG2 font) | `snes/tools/gen_font.py` |
> | `main_mode5_2bpp.s` (Mode 5 boot + BG2 setup) | `snes/src/main.asm` |
> | `main_mode5_4bpp.s` (BG1 wallpaper) | merged into `snes/src/main.asm` — ROM uses BG1 + BG2 together |
>
> See [`AI-README.md`](AI-README.md) §2 / §4 for how those references
> map onto the terminal ROM's input + keymap pipeline.

A focused Mode 5 cookbook for humans and agents working on this repo.

Companion to [`AI-README.md`](AI-README.md) — that file documents the
whole project; this one drills into **Mode 5 specifically**: how tilesets
and tilemaps must be laid out so BG2 (2bpp) and BG1 (4bpp) render
correctly, why dense packing is the preferred VRAM layout, what screen
area you are actually allowed to draw in, and how to take a full-screen
512x448 PNG and turn it into the binary assets the PPU expects.

---

## 1. Mode 5 at a glance

- Two usable background layers: **BG1 = 4bpp**, **BG2 = 2bpp**.
- Horizontal hi-res: **512 px wide** (main screen supplies the odd
  columns, sub screen supplies the even columns — both TM and TS must
  enable the layer or you only see half the columns).
- Optional interlace via `SETINI` bit 0, doubling the vertical resolution
  from 224 to **448 lines**.
- BG1 and BG2 can each be configured independently for 8x8 or 16x16
  tile size via `BGMODE` bits 4 and 5.
- CGRAM is shared between BG1 and BG2 (see section 6).

This repository uses the **full Mode 5 hi-res + interlace** combination
(512x448): BG1 (4bpp, 16x16) for the wallpaper and BG2 (2bpp, 16x16)
for the text overlay.

---

## 2. Effective resolution and overscan

The "logical" Mode 5 + interlace screen is 512x448, but on real PAL TVs
and in bsnes-plus the outer edge is hidden by overscan. Treat the safe
area as **everything except the outermost 16x16 cell** on each side.

In tile units (16x16 BG cell coordinates, origin at top-left):

```
        x = 0 .. 31       (tilemap has 32 columns)
        y = 0 .. 27       (tilemap has 32 rows, last 4 unused at 224p,
                           visible at 448p interlace; edges hidden)

   safe area:  x in [1..30], y in [1..26]
   hidden:     x = 0 or 31, y = 0 or 27 (plus everything at y > 27)
```

Pixel-wise that corresponds to the inner **480x416 rectangle** inside
the 512x448 surface. The four demo characters in this repo live exactly
on the safe-area corners: `(1,1)`, `(30,1)`, `(1,26)`, `(30,26)`.

When laying out a full-screen picture, either:

1. Author the source PNG so the outer 16 px ring does not carry content
   you care about, or
2. Author a 480x416 PNG and pad it back up to 512x448 with a known fill
   colour (e.g. palette index 0 / black) before slicing.

---

## 3. Tilesets: dense packing is the preferred storage layout

### 3.1 The 16x16 auto-read pattern

When a BG layer is set to 16x16 tile size, the PPU reads **four 8x8
VRAM tiles per tilemap entry**, namely:

```
    N, N+1, N+16, N+17
```

where `N` is the top-left 8x8 tile index stored in the tilemap. The
four sub-tiles are arranged as:

```
    N      N+1
    N+16   N+17
```

`N+16` steps one row down in the 16-tiles-per-row VRAM grid (remember
VRAM tile slots are a 1D index; a tile-viewer displays them 16 per
row for convenience).

### 3.2 Dense packing vs. sparse packing

- **Dense packing** (preferred): consecutive 16x16 characters sit at
  `N = 0, 2, 4, 6, ...` — step of **2**. Each character occupies
  exactly 4 of the 16 VRAM slots in a tile-viewer row; four characters
  fit back-to-back in the top row (slots 0..7) with the bottom halves
  in the row below (slots 16..23). No VRAM is wasted.
- **Sparse packing**: step of 4 (`N = 0, 4, 8, 12`). Half the VRAM
  slots are intentionally blank. Wasteful; only useful if you really
  need the bsnes-plus Tilemap Viewer to render flawlessly (see
  section 9).

Real SNES games that use 16x16 BG tilesets **dense-pack** them because
VRAM is the binding resource (64 KiB total). Prefer dense packing in
this repo too; it is what [`tools/gen_assets.py`](../tools/gen_assets.py)
does for Mode 5.

### 3.3 Blank / transparent slots

Even dense-packed tilesets leave some VRAM unused; reserve at least one
16x16 super-tile slot as a "transparent background" tile. Its four 8x8
partners (`N, N+1, N+16, N+17`) must all be zero. The tilemap is then
filled with this reserved index so empty cells render as black /
transparent. In this repo `BLANK_INDEX = 8` points at such a slot right
after the four dense-packed characters (slots 8, 9, 24, 25 are zero).

---

## 4. Tilemaps for 16x16 BG tiles

Every tilemap entry is a 16-bit word, regardless of BG bit depth:

```
    bit 15    : v-flip
    bit 14    : h-flip
    bit 13    : priority
    bits 12..10 : palette (3 bits -> 0..7)
    bits 9..0 : tile index N (0..1023)
```

A 32x32 tilemap therefore takes `32 * 32 * 2 = 2048 bytes`.

Key rules in 16x16 tile mode:

- One entry covers a 16x16 pixel screen region; you only write the
  **top-left** 8x8 tile index `N`. The PPU synthesises the other three
  sub-tiles.
- A 32x32 tilemap covers `32 * 16 = 512 px` horizontally and
  `32 * 16 = 512 px` vertically — enough for the whole Mode 5 + interlace
  screen (512x448) plus 64 lines of overscan below.
- Fill the entire map with `BLANK_INDEX` and only overwrite the cells
  you want to be non-blank.

---

## 5. Palettes and CGRAM layout

### 5.1 Formats

- **2bpp palette**: 4 colours, 8 bytes (BGR555 little-endian).
- **4bpp palette**: 16 colours, 32 bytes.
- Colour 0 of any palette is treated as transparent for that layer;
  the CGRAM value at `$00` is the screen backdrop.

### 5.2 Palette selection bits in the tilemap entry

The 3-bit palette field selects one of 8 sub-palettes:

- For 4bpp BG1: sub-palette i covers CGRAM `i*16 .. i*16+15`
  (16 colours each, i = 0..7, so CGRAM `$00..$7F`).
- For 2bpp BG2: sub-palette i covers CGRAM `i*4 .. i*4+3`
  (4 colours each, i = 0..7, so CGRAM `$00..$1F`).

### 5.3 Shared CGRAM in Mode 5

BG1 and BG2 draw colours from the **same** CGRAM. Their ranges overlap:
BG1 sub-palette 0 (`$00..$0F`) covers BG2 sub-palettes 0..3
(`$00..$03`, `$04..$07`, `$08..$0B`, `$0C..$0F`). Plan accordingly:

- This repo (BG1 wallpaper + BG2 text): BG1 sub-palette 0 at CGRAM
  `$00..$0F` (32 bytes, 16 colours); BG2 sub-palette 7 at CGRAM
  `$1C..$1F` (8 bytes, 4 colours). No overlap. Colour 0 of BG2
  sub-palette 7 (CGRAM `$1C`) is transparent, so empty cells show the
  wallpaper beneath.
- Alternative layout (BG1 + BG2 disjoint, larger BG1 palette): BG1
  uses `$00..$5F`, BG2 uses `$60..$7F` via sub-palettes 6 and 7.

---

## 6. BG1 + BG2 — what this repo actually does

End-to-end for [`snes/src/main.asm`](../snes/src/main.asm):

1. **Boot init**: standard 65816 native mode, stack, clear WRAM, VRAM,
   CGRAM. Six PPU registers zeroed explicitly (CGADSUB, CGWSEL, TMW,
   TSW, W12SEL, W34SEL) — critical for real hardware (see §11.2).
2. **`BGMODE = $35`**: mode 5 (`$05`) + bit 4 (`$10`) for BG1 16x16 +
   bit 5 (`$20`) for BG2 16x16.
3. **`BG1SC = $50`**: BG1 tilemap base at VRAM word `$5000`, 32x32 size.
4. **`BG2SC = $10`**: BG2 tilemap base at VRAM word `$1000`, 32x32 size.
5. **`BG12NBA = $02`**: BG2 char base nibble `$0` (word `$0000`), BG1
   char base nibble `$2` (word `$2000`).
6. **`SETINI = $01`**: enable interlace (448 lines).
7. **Palette DMA 1** (CGRAM `$00`, 32 bytes): BG1 wallpaper sub-palette 0
   — 16 BGR555 colours from `palette.bin`.
8. **Palette DMA 2** (CGRAM `$1C`, 8 bytes): BG2 text sub-palette 7 —
   4 colours (black, dark grey, light grey, white).
9. **BG2 Font Tile DMA** (6144 bytes, `$1800`) at VRAM word `$0000`:
   95 JetBrains Mono glyphs in dense-packed 16x16 super-tiles; slot 0
   = Space (all-zero, transparent — used by clear/boot invariant).
10. **BG1 Tile DMA** (`$6000` bytes from ROM bank 1) at VRAM word
    `$2000`: wallpaper 4bpp tiles, dense-packed super-tiles.
11. **BG2 Tilemap DMA** (2048 bytes zero-fill) at VRAM word `$1000`:
    cleared to `$0000` (Space tile, transparent).
12. **BG1 Tilemap DMA** (2048 bytes from ROM bank 1) at VRAM word
    `$5000`: wallpaper tilemap with flip flags and palette bits embedded.
13. **`TM = $03` / `TS = $03`**: enable BG1 + BG2 on both main and sub
    screens (hi-res requires both screens for full 512-pixel width).
14. **`INIDISP = $0F`**: end force-blank, max brightness.

Text tilemap entries use `tile_index | $3C00` (priority=1, sub-palette=7)
so text always renders above BG1 (BG2-P1 > BG1-P0). Empty cells (tile
`$0000`) are transparent, letting the wallpaper show through.

---

## 7. BG1 (4bpp) — what this repo actually does (wallpaper demo)

The shape is identical to the 2bpp demo; only the numbers change.
This repo uses BG1 4bpp on the same Mode 5 screen with 16x16 tile size
and dense-packed super-tiles in [`main_mode5_4bpp.s`](../main_mode5_4bpp.s)
(ROM: `build/mode5_wallpaper_pal_demo.sfc`). Its tiles / palette /
tilemap are produced by the `mode5_image` pipeline (section 9.7) from
[`assets/linux_wallpaper_512x448_right_4bpp.png`](../assets/linux_wallpaper_512x448_right_4bpp.png).

1. **`BGMODE`**: set mode to 5 and enable the 16x16 tile-size bits you
   need:
   - bit 4 = BG1 16x16 tile size
   - bit 5 = BG2 16x16 tile size
   - value for "mode 5 + BG1 16x16 only" (what the wallpaper demo uses)
     = `$05 | $10 = $15`.
   - value for "mode 5 + BG1 16x16 + BG2 16x16" (if you ever combine
     both) = `$05 | $10 | $20 = $35`.
2. **`BG1SC`**: tilemap base for BG1. Pick a VRAM word address that
   does not collide with BG1 tiles, BG2 tiles or the BG2 tilemap. The
   wallpaper demo uploads 24 KiB of 4bpp tile data at word
   `$0000..$2FFF` (one 4bpp tile = 32 bytes = 16 words; 768 tile slots
   fill 12288 words) and places the BG1 tilemap at word `$3000`,
   giving `BG1SC = $30` (32x32 size). Generic layout for BG1+BG2
   together:
   - BG1 tiles: word `$0000..$3FFF`   (up to 16 KiB of 4bpp data; one
     4bpp tile = 32 bytes = 16 words, so 1024 unique 8x8 tiles max)
   - BG2 tiles: word `$4000..$4FFF`   (up to 4 KiB of 2bpp data)
   - BG1 tilemap: word `$5000` (`BG1SC = $50 | size`)
   - BG2 tilemap: word `$5800` (`BG2SC = $58 | size`)
3. **`BG12NBA`**: low nibble = BG1 char base (word / 4096), high nibble
   = BG2 char base. For the wallpaper demo (BG1 only, char base word
   `$0000`) the whole register is `$00`. For the combined BG1+BG2
   example above BG1 char base word `$0000` is nibble `0`, BG2 char
   base word `$4000` is nibble `4`, so `BG12NBA = $40`.
4. **Palette DMA for BG1**: 32 bytes at CGRAM `$00..$1F` (BG1 sub-palette
   0). If you use more than 16 colours, upload up to 128 bytes and
   select sub-palettes 0..7 per tilemap entry.
5. **Tile DMA for BG1**: 32 bytes per 8x8 tile. Dense-packed 16x16
   characters still follow the `N, N+1, N+16, N+17` pattern; a single
   character consumes 128 bytes of VRAM (`4 * 32`). For a full-screen
   wallpaper expect `unique_super_tiles * 128` bytes (up to the 64 KiB
   VRAM budget minus tilemap), rounded up to a full 8-super-tile
   row-pair as in `build_mode5_image_vram`.
6. **Tilemap DMA for BG1**: 2048 bytes; same structure as BG2, just at
   the BG1 tilemap base.
7. **`TM` / `TS`**: add BG1 to both. For BG1 only: `TM = TS = $01`
   (wallpaper demo). For BG1+BG2 on: `TM = TS = $03`.
8. Everything else (interlace, force-blank dance, brightness) stays
   the same.

### 4bpp-specific gotchas

- A 4bpp 8x8 tile stores two pairs of bitplanes: bytes `0x00..0x0F`
  hold rows as `(plane0, plane1)` and bytes `0x10..0x1F` hold rows as
  `(plane2, plane3)`. The encoder in
  [`tools/gen_assets.py`](../tools/gen_assets.py) (`tile_to_bitplanes`)
  already handles this — call it with `bpp=4`.
- For BG1 you can use all 3 palette-select bits meaningfully, giving
  you 8 disjoint 16-colour palettes per tilemap entry. For a full-screen
  picture this is how you push well beyond 16 unique colours.

---

## 8. bsnes-plus Tilemap Viewer quirk (reminder)

In Mode 5 hires + 16x16 the bsnes-plus **Tilemap Viewer** renders each
cell 32x16 px by reading 8 VRAM tiles
(`c, c+1, c+1, c+2 / c+16, c+17, c+17, c+18`) instead of the hardware's
4. With dense packing this makes non-last corners look like "character
+ left half of neighbour". Hardware and the emulator output window are
correct. Full per-corner breakdown in
[`AI-README.md`](AI-README.md#bsnes-plus-tilemap-viewer-quirk-in-mode-5-hires--16x16).

Do **not** un-dense-pack just to silence the viewer; that wastes VRAM
and departs from how real games store 16x16 tilesets.

---

## 9. Converting a 512x448 PNG into Mode 5 assets

The whole pipeline, in the order you should implement it when adding a
`mode5_<target>` entry to [`tools/gen_assets.py`](../tools/gen_assets.py):

### 9.1 Input constraints

- Image must be exactly **512x448 px** (Mode 5 + interlace native
  resolution). Anything else: resize or pad before encoding.
- Colour count must fit the target bit depth:
  - **2bpp**: at most 4 colours across the whole image (single
    palette), or up to 4 colours per 8x8 tile with 8 sub-palettes
    selectable per tilemap entry (max 32 distinct colours total).
  - **4bpp**: at most 16 colours per 8x8 tile; up to 8 sub-palettes
    for 128 distinct colours total. Photographic or full-colour source
    PNGs need prior quantisation (PIL's `image.quantize(colors=16,
    method=...)` or `posterize`, plus a per-tile palette search).
- Favour sources authored against a fixed palette (indexed PNG, mode
  `P`) so you can read `image.getpalette()` directly and avoid a
  quantisation step.

### 9.2 Slicing into tiles

```
tiles_x = 512 / 8 = 64     (8x8 tile columns)
tiles_y = 448 / 8 = 56     (8x8 tile rows)
total   = 3584 8x8 tiles (conceptual; before deduplication)
```

For 16x16 BG tile mode you additionally group 8x8 tiles into 2x2
super-tiles:

```
super_x = 32               (16x16 tile columns)
super_y = 28               (16x16 tile rows)
total   = 896 16x16 super-tiles
```

### 9.3 Deduplication is mandatory

Without deduplication, 3584 unique 4bpp tiles = **114 688 bytes** (far
more than the 64 KiB of VRAM). Even at 2bpp (`57 344 bytes`) the budget
is tight once you include a tilemap and the sub-screen's share. Real
pictures have enormous redundancy; dedupe by hashing each 8x8 tile.

Dedupe sketch:

```python
from collections import OrderedDict

def dedupe_tiles(tiles_8x8):
    table = OrderedDict()        # key = bytes(tile_bitplanes), value = index
    tilemap = []                 # one entry per 8x8 tile slot, row-major
    for tile in tiles_8x8:
        key = bytes(tile)
        if key not in table:
            table[key] = len(table)
        tilemap.append(table[key])
    return list(table.keys()), tilemap
```

Consider also detecting **flipped duplicates** (h-flip, v-flip, both):
that costs one extra bit flag per tilemap entry (you get it for free in
the tilemap word format) but can roughly quarter the unique tile count
on symmetrical art.

### 9.4 Dense-pack into VRAM

After deduplication you have `U` unique 8x8 tiles. For 16x16 BG mode
you want them grouped so each on-screen 16x16 region's four sub-tiles
satisfy the `N, N+1, N+16, N+17` adjacency constraint.

- **Cheap strategy** (no reuse across super-tiles): store super-tiles
  back-to-back at `N = 0, 2, 4, 6, ...`, i.e. super-tile `k` occupies
  slots `2k, 2k+1, 16+2k, 16+2k+1`. This wastes some VRAM because
  identical 8x8 tiles that appear in multiple super-tiles are stored
  once per super-tile. Uses `4 * U_super * bytes_per_tile` VRAM where
  `U_super` is the number of unique 16x16 super-tiles.
- **Full strategy** (reuse across super-tiles): pack deduplicated 8x8
  tiles into VRAM freely, then for each on-screen super-tile **search**
  for a VRAM index `N` that already has the right four 8x8 tiles at
  `N, N+1, N+16, N+17`. If no such position exists, either append four
  fresh tiles at the next free aligned slot or — more compact — use 8x8
  tile mode on BG1 instead. Most real games use some variant of this.
- **Compromise** (recommended starting point): deduplicate at the 8x8
  level, then allocate super-tile slots in the order they first appear
  in scan order using the cheap strategy above. This is simple and
  fits most hand-drawn art.

Row 0 of the tile viewer should end up densely filled with character
top halves, row 1 with bottom halves; rows further down hold additional
super-tile pairs (`N` and `N+1` go in one row, `N+16` and `N+17` in
the row below).

### 9.5 Emit the files

For each mode-5 target produce:

- `palette.bin` — 8 bytes (2bpp) or 32 bytes (4bpp), BGR555
  little-endian. If you want multi-palette 4bpp, emit up to 128 bytes.
- `tiles.2bpp.chr` or `tiles.4bpp.chr` — raw tile data, dense-packed as
  described. Size = `unique_tiles * 16` (2bpp) or `* 32` (4bpp).
- `tilemap.bin` — 2048 bytes, 32x32 16-bit entries. For 16x16 BG mode
  the `super_x * super_y = 32 * 28 = 896` entries you care about sit at
  positions `(x, y)` with `y in [0..27]`; set the rest to `BLANK_INDEX`.
  Encode flip flags and palette bits per entry as needed.
- Optional `preview.png` — the rebuilt 512x448 image after dedupe, so
  you can eyeball the result before flashing a ROM.

### 9.6 Tooling hooks already in this repo

[`tools/gen_assets.py`](../tools/gen_assets.py) already provides:

- `tile_to_bitplanes(tile, bpp)` — encodes one 8x8 tile to 2bpp / 4bpp.
- `encode_palette(colors_bgr555, bpp)` — pads / emits the palette file.
- `split_character_tiles(pixels, bpp)` — splits a 16x16 block into the
  four `(N, N+1, N+16, N+17)` 8x8 tiles.
- `build_vram_tiles(characters, blank_tile, tiles_to_upload)` — lays
  out VRAM slots including blank padding.
- `build_tilemap(tile_pixels_size, placements)` — builds the 32x32
  tilemap for either 8x8-tile or 16x16-tile BG mode.

### 9.7 End-to-end image target: `mode5_image`

The full PNG/JPG → assets pipeline described in this section is
implemented as a dynamic target in
[`tools/gen_assets.py`](../tools/gen_assets.py):

```
python3 tools/gen_assets.py mode5_image \
    --source assets/some_photo.jpg \
    --crop-align right \
    --bpp 4 \
    --name mode5_wallpaper_4bpp
```

It composes:

- `load_image_as_indexed(source, bpp, crop_align)` — delegates to
  [`tools/crop_image.py`](../tools/crop_image.py) (`scale_and_crop`,
  `reduce_palette`) for any source that isn't already 512x448 /
  pre-quantised, then returns a 2-D `pixels` grid plus a BGR555
  palette.
- `slice_super_tiles(pixels)` — produces the 32x28 grid of
  `[TL, TR, BL, BR]` 8x8 tiles per 16x16 cell.
- `dedupe_super_tiles(grid)` — flip-aware dedup (identity, H, V, HV)
  yielding `(unique_super_tiles, placements)` where each placement is
  `(index, hflip, vflip)`.
- `super_tile_vram_base(k)` = `(k // 8) * 32 + (k % 8) * 2` — the
  dense-pack VRAM base index from §3.2 / §9.4.
- `build_mode5_image_vram(unique, bpp, blank_index)` — tile data with
  a reserved blank super-tile immediately after the last unique one;
  aborts with a descriptive error if the required tile index exceeds
  the 10-bit budget (`MAX_TILE_INDEX = 1023`).
- `build_mode5_image_tilemap(placements, blank_index)` — 32x32x2 bytes
  with the 4 unused rows (`y >= 28`) pointing at `blank_index`.
- A built-in round-trip assertion reconstructs the quantised image
  from `unique_super_tiles` + `placements` and compares it against
  the input, so flip-dedup bugs surface immediately.

This is the recommended entry point for new Mode 5 background
artwork. The `Makefile` already wires one such invocation up for the
`mode5_wallpaper_4bpp` outputs that feed
[`main_mode5_4bpp.s`](../main_mode5_4bpp.s); adding another image
target is usually just another `mode5_image` invocation with a
different `--source` / `--name` (plus a new `main_*.s` + Makefile
rules if you want an accompanying ROM). Only add a new
`mode5_<name>` *static* target to `TARGETS` if you want hand-authored
pixel art rather than a generated image.

---

## 10. Quick checklist when adding a new Mode 5 screen

1. Is the source **512x448** and does it respect the **overscan safe
   area** (inner 480x416)?
2. Is the palette count within the target bit depth's budget?
3. Are 8x8 tiles **deduplicated** (and optionally flip-dedup'd)?
4. Are 16x16 super-tiles laid out **dense-packed** with the
   `N, N+1, N+16, N+17` adjacency honoured?
5. Is `BLANK_INDEX` pointing at a slot whose four partners are zero?
6. Does `BGMODE` include the right tile-size bit (4 for BG1, 5 for
   BG2) for each 16x16 BG?
7. Are **TM and TS** both enabling the layer (required by hires)?
8. Is `SETINI` bit 0 set if you actually want 448 interlaced lines?
9. Are CGRAM ranges for BG1 and BG2 not trampling each other?
10. Does the whole thing assemble, link and pass `fix_checksum.py`?

If all ten are "yes", the ROM should render correctly on hardware and
in the emulator output window. The bsnes-plus Tilemap Viewer ghosting
(section 8) is expected and benign.

---

## 11. Real-hardware pitfalls (Everdrive / flash cart)

Two issues that cause a black screen on real hardware but are invisible
in bsnes (which initialises all registers to 0 at power-on):

### 11.1 ROM size byte in the SNES header (`$FFD7`)

The Everdrive uses `$FFD7` to choose its internal LoROM mapping:

| Header byte | Everdrive display | Result |
|---|---|---|
| `$08` | "512k" | 32 KiB ROM mirrored correctly across all banks ✓ |
| `$05` | "8m" | ROM placed at wrong address in 8 Mbit space → CPU reads garbage → black screen ✗ |

`$05` is what the SNES spec says for a 32 KiB ROM (`2^5 KiB`), but
Everdrive firmware does not handle it correctly for LoROM. Use `$08`.
The S-CPU itself never reads `$FFD7` — it only matters to the flash
cart.

### 11.2 Uninitialised PPU registers

bsnes resets all PPU registers to `$00`; real hardware leaves them
undefined. Mode 5 with `TM = TS = $02` (BG2 on both screens) is
uniquely vulnerable because any register that references BG2 can kill
the display. Critical registers to zero-initialise **before**
releasing force-blank:

| Register | Address | Why it matters in Mode 5 |
|---|---|---|
| `CGADSUB` | `$2131` | Bit 7=1 (subtract) + bit 1=1 (BG2) → BG2_main − BG2_sub = 0 (same layer on both screens → every pixel cancels to black) |
| `CGWSEL` | `$2130` | Controls when colour math applies; garbage can enable subtraction unconditionally |
| `TMW` | `$212E` | Bit 1=1 → BG2 window-masked on main screen → invisible |
| `TSW` | `$212F` | Bit 1=1 → BG2 window-masked on sub screen |
| `W12SEL` | `$2123` | BG1/BG2 window enable bits; if set, feeds into TMW/TSW masking |
| `W34SEL` | `$2124` | BG3/BG4 window enable bits |

In Mode 1 (single BG1, `TS = $00`) most of these registers are
harmless because BG2 is not on any screen. In Mode 5 they all become
load-bearing. `main.asm` zeroes all six registers explicitly — do not
remove those `stz` instructions.
