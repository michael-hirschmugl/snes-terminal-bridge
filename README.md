# snes-terminal-bridge

Translates keyboard input into SNES controller button sequences and injects them into a running SNES emulator. The goal is to allow typing ASCII text in a terminal, with each character being converted into the corresponding sequence of SNES button presses — the foundation for running terminal applications on a Super Nintendo.

---

## How it works

The bridge applies two mappings in sequence:

```
keystroke
  → mappings.yaml          (ASCII character → SNES button combination)
  → keyboard_mappings.yaml (SNES button → emulator keyboard key)
  → xdotool XTest injection
```

**Example:** typing `H`

1. `mappings.yaml`: `H` → `[A, L]`
2. `keyboard_mappings.yaml`: `A` → `x`, `L` → `d`
3. The bridge presses `x + d` simultaneously via xdotool XTest for 80 ms

`xdotool` XTest injects events at the X server level. bSNES+ polls the X11 keyboard state directly via `XQueryKeymap` rather than relying on X11 focus, so **no focus switching is performed** — the bridge terminal keeps focus throughout and the user can keep typing immediately.

> **Further reading:** architecture deep-dive for contributors in [`docs/AI-README.md`](docs/AI-README.md); Mode 5 graphics/tileset reference in [`docs/AI-MODE-5-README.md`](docs/AI-MODE-5-README.md).

### Pipeline

```
Thread 1: curses raw keyboard read  →  queue.Queue
Thread 2: dequeue  →  mapper  →  TUI update  →  KeyboardInjector.press_combo()
```

The TUI shows three columns per keypress:

```
snes-terminal-bridge  |  Ctrl+C to quit
  'H'          →  [A, L]               →  x+d
  'e'          →  [A, Select, Right]   →  x+u+Right
  'l'          →  [B, Select, Down]    →  z+u+Down
```

---

## Project layout

```
snes_terminal_bridge/
├── __main__.py           # Entry point: python -m snes_terminal_bridge [--target WINDOW]
├── bridge.py             # Main loop — wires all modules together
├── config.py             # Loads and validates mappings.yaml + keyboard_mappings.yaml
├── mapper.py             # ASCII character → list[SNESButton] lookup
├── input_capture.py      # curses raw keyboard reader (background thread)
├── keyboard_injector.py  # xdotool XTest key injection (no focus switching)
└── tui.py                # curses display (header, scrolling log, status bar)

config/
├── mappings.yaml          # ASCII → SNES button combinations (97 characters mapped)
└── keyboard_mappings.yaml # SNES button → emulator keyboard key

assets/
└── SNES-ASCII-Map.ods  # Reference spreadsheet: all 128 ASCII chars mapped to SNES combos

scripts/
├── convert_ods.py   # One-shot: regenerates mappings.yaml from the ODS spreadsheet
└── test_mapping.py  # Interactive mapping test (no emulator needed)

snes/
├── Makefile              # Build: `make` → Mode 1 ROM, `make mode5` → Mode 5 ROM, `make both` → both
├── snes.cfg              # ld65 linker config (LoROM memory map)
├── src/
│   ├── main.asm          # Mode 1 ROM — 4bpp BG1, 8×16 chars, 32×14 scroll grid
│   └── main_mode5.asm    # Mode 5 ROM — 2bpp BG2, 16×16 chars, 32×26 scroll grid, interlaced 512×448
├── assets/               # Generated files (not committed)
│   ├── font.inc          # Mode 1: 4bpp 8×16 tile data (gen_font.py, DejaVu Sans Mono)
│   ├── tilemap.inc       # Mode 1: BG1 64×64 blank tilemap (gen_font.py)
│   ├── keymap.inc        # Mode 1: Button bitmask → tile number (C*2), gen_keymap.py
│   ├── font2.inc         # Mode 5: 2bpp 16×16 dense-packed tile data (gen_font2.py, JetBrains Mono)
│   └── keymap_mode5.inc  # Mode 5: Button bitmask → VRAM base slot N(C), gen_keymap.py
└── tools/
    ├── gen_font.py        # Renders TTF font → SNES 4bpp tiles + blank tilemap (Mode 1)
    ├── gen_font2.py       # Renders TTF font → 2bpp 16×16 dense-packed VRAM block (Mode 5)
    ├── gen_keymap.py      # Reads mappings.yaml → both keymap.inc and keymap_mode5.inc
    └── requirements.txt   # Pillow, PyYAML

docs/
├── AI-README.md          # Agent/contributor onboarding (architecture, patterns, pitfalls)
└── AI-MODE-5-README.md   # Mode 5 reference (dense-pack tile layout, BG2 setup, PPU read pattern)
```

---

## SNES ROM

The `snes/` subdirectory contains the SNES-side application that runs inside the emulator. **Two parallel ROM builds** are available, sharing the same input/keymap logic but using different SNES graphics modes:

| Build | Makefile target | Output | Graphics mode | Grid | Character cell |
|---|---|---|---|---|---|
| **Mode 1** (original, default) | `make` | `build/terminal.sfc` | Mode 1, BG1, 4bpp, 8×8 tiles | 32×14 visible, 32-row scroll buffer | 8×16 px (two 8×8 tiles stacked) |
| **Mode 5** (new, hi-res) | `make mode5` | `build/terminal_mode5.sfc` | Mode 5 + interlace, BG2, 2bpp, 16×16 tiles | 32×26 visible, 32-row scroll buffer | 16×16 px (PPU assembles from four 8×8 tiles) |

Both ROMs are 32 KiB LoROM, both use identical input handling (debounce, boot guard, dedupe, keymap scan). They differ only in display resolution, font rendering, and VRAM tile layout. The Mode 5 path uses higher effective resolution (512×448 interlaced) for crisper anti-aliased text; the Mode 1 path remains the default for compatibility and broader emulator support. See [`docs/AI-MODE-5-README.md`](docs/AI-MODE-5-README.md) for the Mode 5 graphics details.

### Current state: scrolling multi-row terminal ✅

Each ROM receives joypad combos from the bridge, looks up the corresponding ASCII character, and displays it in a scrolling terminal grid. Characters appear left-to-right, row by row, with Enter advancing to a new line and the viewport scrolling automatically once the visible rows are filled (14 in Mode 1, 26 in Mode 5). Backspace erases the last character.

**Screen at startup:** Intentionally blank — characters appear as you type.

**Protocol:**
- Each character is encoded as a unique joypad bitmask (SNES buttons held simultaneously for ~80 ms)
- ROM debounces: bitmask must be stable for ≥ 2 consecutive VBlanks (~33 ms) before triggering
- Same combo is not re-triggered until all buttons are released (no key repeat while held)
- On match, two tiles (top + bottom half of the character) are written to VRAM at the next VBlank
- Cursor advances left-to-right, auto-wraps at column 32

**Special actions** (non-printable):

| Key name | Buttons | Effect |
|---|---|---|
| `KEY_DELETE` | Down + Right + X + Select | Erase last character, move cursor left |
| `KEY_ENTER`  | Up + Left + A + B          | Advance to next row, scroll if needed |

`gen_keymap.py` generates `assets/keymap.inc` — a lookup table mapping each joypad bitmask to a font tile number (regular characters) or to sentinel value `$FFFF`/`$FFFE` (special actions), compiled from `config/mappings.yaml`.

### Boot guard (`boot_ready`)

The ROM ignores all joypad input until it has seen **all buttons released for ≥ 2 consecutive frames**. This prevents a key stuck in the X server from a previous session from appearing on screen immediately after boot.

The `boot_ready` flag is set the first time the joypad reads all-zero (stable for 2 frames). After that, the ROM accepts input normally.

**Important for WSLg (see Startup Procedure below):** On WSLg/XWayland, synthetic `xdotool keyup` events may not reliably clear a physically-stuck key from `XQueryKeymap`. The bridge's `_release_all()` on startup attempts this, but the reliable workaround is to briefly type directly inside bSNES+ once after loading the ROM (see below).

### How the display works — Mode 1 (default ROM)

- **BG mode:** SNES Mode 1, BG1 only, 8×8 tiles, 4bpp
- **Palette:** colour 0 = black ($0000), colour 1 = white ($7FFF)
- **Character cell:** 8px wide × 16px tall (two stacked 8×8 tiles: top half + bottom half)
- **Grid:** 32 columns × 14 visible rows; 32-row circular buffer with BG1VOFS scroll
- **VRAM layout:**
  - `$0000–$1FFF` — BG1 tilemap (64×64 entries × 2 bytes = 8 KB)
  - `$2000–$37BF` — Font tiles (190 tiles × 32 bytes = 6080 bytes)
- **Tile numbering:** for ASCII char `c`: `C = ord(c) - 0x20`, `tile_top = C*2`, `tile_bot = C*2+1`
- **Font generation:** `tools/gen_font.py` uses Pillow to render DejaVu Sans Mono (size 13) into 8×16 cells, splits into top/bottom 8×8 halves, converts to SNES 4bpp format
- **Data transfer:** Two DMA transfers on reset — tilemap (all blank) to VRAM `$0000`, font tiles to VRAM `$2000`
- **Circular scroll:** `cursor_y` (0–31) tracks the write row; `top_vram_row` tracks the topmost visible row; `BG1VOFS = top_vram_row × 16` pixels

### How the display works — Mode 5 (hi-res ROM)

- **BG mode:** SNES Mode 5 + interlace, BG2 only, 16×16 tiles (PPU assembles them from four 8×8 VRAM tiles per map entry), 2bpp
- **Resolution:** 512×448 effective (interlaced), enabled via `SETINI = $01`; BG2 rendered on both main and sub screen (`TM = TS = $02`) as required by hi-res
- **Palette:** 4-colour 2bpp anti-aliased greyscale (black, dark grey, light grey, white)
- **Character cell:** 16×16 px, one tilemap entry per character — the PPU auto-reads four 8×8 sub-tiles at VRAM slots `N, N+1, N+16, N+17`
- **Grid:** 32 columns × 26 visible rows; 32-row circular buffer with BG2VOFS scroll
- **VRAM layout:**
  - `$0000–$17FF` — Font tiles, 6144 bytes, dense-packed (384 × 8×8 slots)
  - `$1000–$17FF` — BG2 tilemap (32×32 entries × 2 bytes = 2 KB), configured via `BG2SC = $10`
- **Tile numbering:** for ASCII char `c`: `C = ord(c) - 0x20`, `tile = (C // 8) * 32 + (C % 8) * 2` (base VRAM slot of the top-left 8×8 sub-tile). The keymap lookup table (`keymap_mode5.inc`) stores this value directly, so the ASM hot path writes a single 16-bit tilemap word per character.
- **Font generation:** `tools/gen_font2.py` renders JetBrains Mono Regular into 16×16 cells with Freetype anti-aliasing, splits each glyph into four 8×8 sub-tiles, encodes them 2bpp, and places them at VRAM addresses following the `N, N+1, N+16, N+17` pattern so the PPU reads them natively for 16×16 BG tiles. See [`docs/AI-MODE-5-README.md`](docs/AI-MODE-5-README.md) for the dense-pack rationale.
- **Data transfer:** DMA transfers on reset — palette (16 bytes) to CGRAM, font tiles (6144 bytes) to VRAM `$0000`, full VRAM/CGRAM clear beforehand. Tilemap starts at `$1000` and is zero after the VRAM clear — tilemap slot 0 points to the space glyph (all zero pixels), so blank cells render correctly without any fill routine.
- **Circular scroll:** `cursor_y` (0–31) tracks the write row; `top_vram_row` tracks the topmost visible row; `BG2VOFS = top_vram_row × 16` pixels. New rows are cleared with a single 32-word zero-fill (no section-boundary wrap since the tilemap is one 32×32 screen page).

### Joypad register layout (empirically confirmed)

```
$4218 (JOY1L) — low byte:   bit7=A   bit6=X  bit5=L  bit4=R  (bits 3-0 unused)
$4219 (JOY1H) — high byte:  bit7=B   bit6=Y  bit5=Sel bit4=Start  bit3=Up  bit2=Down  bit1=Left  bit0=Right
```

In `keymap.inc` entries (`.word bitmask, .word tile`), the bitmask is stored little-endian:
- byte 0 (low) = JOY1L bits, compared with $4218 snapshot
- byte 1 (high) = JOY1H bits, compared with $4219 snapshot

### Building the ROM

Prerequisites: `sudo apt install cc65` (provides `ca65` + `ld65`)

```bash
cd snes
make            # Mode 1 ROM → build/terminal.sfc (default, 32768 bytes)
make mode5      # Mode 5 ROM → build/terminal_mode5.sfc (32768 bytes)
make both       # builds both ROMs, regenerates all assets once
make font       # regenerate only assets/*.inc + preview PNGs
make run        # build Mode 1 ROM and open it in bsnes
make run-mode5  # build Mode 5 ROM and open it in bsnes
make clean      # remove build/ and generated assets
```

Both output ROMs are LoROM SNES images, exactly 32768 bytes each. They can be loaded side-by-side; the bridge does not need to know which ROM you run — the keymap bitmasks are identical, only the tile encoding differs.

### ROM header and hardware notes

Both generated ROMs are standard **PAL LoROM** images suitable for running from a flash cartridge. They share the same header fields (map mode, cartridge type, ROM size, RAM size, destination), with only the title differing.

- **Title (21 bytes):** `SNES TERMINAL` (Mode 1) or `SNES TERMINAL MODE 5` (Mode 5)
- **Map mode:** `0x20` (LoROM, SlowROM)
- **Cartridge type:** `0x00` (ROM only)
- **ROM size field:** `0x05` (2^5 KiB = 32 KiB, matches actual image)
- **RAM size field:** `0x00` (no cartridge RAM — the ROM uses only SNES internal WRAM)
- **Destination code:** `0x02` (Europe / PAL)
- **Checksum / complement:** patched automatically by `snes/tools/fix_checksum.py` after linking, so flash cartridges accept the ROM. Runs for both ROMs independently.

Before flashing to real hardware:
- Rebuild from a clean state: `cd snes && make clean && make both`
- Verify ROM size is exactly 32768 bytes (`build/terminal.sfc`, `build/terminal_mode5.sfc`)
- Use a PAL console profile on your flash cartridge / setup
- Test first in bsnes: ROMs should be detected as `LOROM` and region `PAL`
- **Mode 5 requires hardware or an emulator that implements interlace correctly.** bsnes-plus works; some older emulators downsample or show only 224 lines.

---

## Requirements

### All platforms
- Python 3.10+
- `xdotool`
- A SNES emulator that accepts keyboard input (bSNES+ recommended)

### WSL2
- Windows 11 with WSLg enabled

### Native Linux
- X11/Xorg session (Wayland not yet tested)

---

## Installation

```bash
git clone <repo-url>
cd snes-terminal-bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt install xdotool
```

> **Note:** Do not copy a `.venv` from another machine. Always create a fresh one.

---

## Emulator configuration

The key names in `config/keyboard_mappings.yaml` must match the keyboard bindings configured in your emulator (Settings → Input → Port 1 in bSNES+).

**Example (bSNES+ with customised Select binding):**

| SNES Button | Keyboard key | Entry in keyboard_mappings.yaml |
|---|---|---|
| Up | Up arrow | `Up: Up` |
| Down | Down arrow | `Down: Down` |
| Left | Left arrow | `Left: Left` |
| Right | Right arrow | `Right: Right` |
| A | x | `A: x` |
| B | z | `B: z` |
| X | s | `X: s` |
| Y | a | `Y: a` |
| L | d | `L: d` |
| R | c | `R: c` |
| Start | Return | `Start: Return` |
| Select | u | `Select: u` (customised from bSNES+ default `'`) |

Key names follow xdotool / X11 syntax (`Return`, `space`, `Up`, `numbersign`, etc.).

---

## Usage

```bash
# 1. Start bSNES+ and load the terminal ROM
bsnes snes/build/terminal.sfc           # Mode 1 (default, 8×16 chars)
# or
bsnes snes/build/terminal_mode5.sfc     # Mode 5 (hi-res, 16×16 anti-aliased chars)

# 2. Unlock the ROM's boot guard (WSLg only — see note below)
#    Press and immediately release any key directly inside the bSNES+ window.

# 3. Activate venv and run the bridge
source .venv/bin/activate
python -m snes_terminal_bridge
# Optional: override the emulator window title pattern
python -m snes_terminal_bridge --target bsnes
```

The bridge is ROM-agnostic — it sends the same button combinations to both ROMs. Pick whichever ROM you want in bSNES+ and start typing.

Press **Ctrl+C** to quit.

### Startup procedure note (WSLg / XWayland)

The SNES ROM uses a `boot_ready` guard: it ignores all input until it sees the joypad in a clean all-released state for at least 2 consecutive frames. This prevents garbage from a stuck key (e.g. `z` left held from a previous bridge session) appearing on screen at boot.

**On native Linux X11** the bridge's own startup sequence (`_release_all()` runs `xdotool keyup` for every mapped key twice before accepting any input) is sufficient to clear stuck keys and unlock the ROM.

**On WSLg/XWayland** there is a known limitation: synthetic `XTest KeyRelease` events (from `xdotool keyup`) may not reliably clear physically-stuck keys from `XQueryKeymap`. This means `_release_all()` is not guaranteed to unlock the boot guard.

**Reliable workaround (WSLg):**

> After loading the ROM in bSNES+, click inside the bSNES+ window and press and release **any key** (e.g. the space bar or an arrow key). This generates a real KeyRelease through the Wayland→XWayland path, which reliably clears the stuck state. The ROM will then accept bridge input immediately.

Once unlocked, the ROM stays unlocked for the duration of the session.

---

## Configuration

### config/mappings.yaml

Maps ASCII characters (and special keys) to SNES button combinations.

```yaml
settings:
  hold_ms: 80          # How long buttons are held (ms). Minimum ~17 ms (1 SNES frame).
  release_gap_ms: 20   # Pause between combos for clean rising edges.

mappings:
  "A": [A]
  "a": [A, Select]
  " ": [Up, Left, A]
  "KEY_DELETE": [Down, Right, X, Select]   # Backspace / erase last character
```

Valid button names: `A`, `B`, `X`, `Y`, `L`, `R`, `Start`, `Select`, `Up`, `Down`, `Left`, `Right`

Special key names (`KEY_*`) are handled by the ROM directly and do not produce a visible tile. Currently supported:

| Key name | ROM action |
|---|---|
| `KEY_DELETE` | Move cursor left, erase last character (writes space tile) |

### config/keyboard_mappings.yaml

Maps SNES button names to xdotool key names injected into the emulator.

```yaml
window: "bsnes"   # Substring of the emulator window title (used for logging only)

buttons:
  A: x
  Select: u
  # ...
```

---

## Testing mappings without an emulator

```bash
python scripts/test_mapping.py
```

Opens an interactive prompt to test character → button mappings without a running emulator.

---

## Tested scenarios

### Scenario 1: WSL2 + WSLg + bSNES+ on Windows 11

**Status:** Working ✅

**System:**
- Windows 11 with WSL2 (Ubuntu 24.04), WSLg enabled
- bSNES-plus v05 installed inside WSL
- Python 3.12

**Steps:**

```bash
sudo apt install xdotool bsnes-plus cc65
git clone <repo-url> && cd snes-terminal-bridge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Build the ROM
cd snes && make
cd ..

# Start emulator
bsnes snes/build/terminal.sfc &

# Unlock boot guard (WSLg workaround — press+release any key in bSNES+ window)

# Run the bridge
python -m snes_terminal_bridge
```

**Why no focus switching is needed:**

bSNES+ polls the X11 keyboard state via its ruby input backend on every frame (`XQueryKeymap`), independently of which window has focus. XTest events (injected via `xdotool keydown/keyup`) update the X server's key state immediately and are picked up by bSNES+ on its next poll — even though the bridge terminal keeps focus throughout.

**Why bSNES+ must be given X11 focus at least once before the bridge can work (WSLg):**

In WSLg, `xdotool keyup` (XTest synthetic KeyRelease) does not reliably clear a key that was physically stuck in the X server's `XQueryKeymap`. Physically pressing and releasing a key inside bSNES+ clears the stuck state via the real Wayland→XWayland event path. After this one-time step, bridge injection works for the rest of the session.

**Dead ends investigated:**

| Approach | Result |
|---|---|
| `xdotool key --window <wid>` | Uses `XSendEvent` → events marked synthetic → bSNES+ ignores them |
| `xdotool windowfocus` | Uses `XSetInputFocus` → silently ignored under WSLg/XWayland |
| `xdotool windowactivate` | `_NET_ACTIVE_WINDOW` fails under WSLg (no Linux WM) |
| `WScript.Shell.AppActivate("bsnes")` | Returns `False` — window title starts with game name |
| `SetForegroundWindow` via PowerShell | Focus switches but permission to restore focus to terminal is lost immediately |
| Virtual gamepad via uinput | bSNES+ filters all `/devices/virtual/` devices; confirmed with `lsof /dev/input/js0` |

---

### Scenario 2: Native Linux (X11) + bSNES+

**Status:** Implemented, not yet tested

XTest events should reach bSNES+ the same way as in WSL2. No focus switching needed. The bridge's `_release_all()` startup sequence should be sufficient to clear stuck keys and unlock the boot guard without any manual step.

```bash
echo $XDG_SESSION_TYPE   # must print "x11"
sudo apt install xdotool bsnes-plus
python -m snes_terminal_bridge
```

**Wayland note:** `xdotool` XTest may work under XWayland but has not been tested.
