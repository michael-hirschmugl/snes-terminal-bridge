# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

For the canonical architecture deep-dive (patterns, invariants, naming, pitfalls) see [`docs/AI-README.md`](docs/AI-README.md). For the Mode 5 graphics/tileset layout specifically see [`docs/AI-MODE-5-README.md`](docs/AI-MODE-5-README.md). This CLAUDE.md keeps the high-level status and history summary.

## Project Purpose

snes-terminal-bridge translates keyboard input to SNES controller button sequences and injects them into a SNES emulator — the foundation for running terminal applications on a Super Nintendo.

## Current Architecture

**Language:** Python 3.10+
**Injection method:** xdotool XTest keyboard injection (not uinput/virtual gamepad)
**UI:** `curses` TUI (stdlib)
**Input capture:** `curses.getch()` with `keypad(True)` (raw mode, no shell leakage)

### Two-step mapping pipeline

```
keystroke
  → config/mappings.yaml          (ASCII char → list of SNES buttons)
  → config/keyboard_mappings.yaml (SNES button → emulator keyboard key)
  → KeyboardInjector.press_combo()
      → focus emulator window
      → xdotool keydown/keyup (XTest — looks like real keyboard input)
      → restore focus to terminal
```

### Thread model

```
Thread 1: input_capture (curses raw read) → queue.Queue
Thread 2 (main): dequeue → mapper → tui.update() → keyboard_injector.press_combo()
```

### Module layout

```
snes_terminal_bridge/
├── __main__.py           # python -m snes_terminal_bridge
├── bridge.py             # main loop, wires all modules
├── config.py             # load/validate mappings.yaml + keyboard_mappings.yaml
├── mapper.py             # char → list[SNESButton]
├── input_capture.py      # curses keyboard reader (background thread)
├── keyboard_injector.py  # xdotool XTest injection (no focus switching; see AI-README)
└── tui.py                # curses display

scripts/
├── convert_ods.py        # one-shot: ODS → config/mappings.yaml (needs odfpy)
└── test_mapping.py       # interactive mapping test, no emulator needed

config/
├── mappings.yaml         # ASCII → SNES button combinations (97 chars mapped)
├── welcome.ini           # boot welcome message (plain text, ≤26 lines, ≤30 chars/line)
├── help.ini              # help command output text (same format, no line limit)
└── keyboard_mappings.yaml # SNES button → xdotool key name + emulator window title

assets/
└── SNES-ASCII-Map.ods    # source spreadsheet for all 128 ASCII mappings

snes/                     # SNES-side ROM (Mode 5)
├── Makefile              # `make` → build/terminal.sfc
├── snes.cfg              # ld65 2-bank LoROM memory map (64 KiB)
├── src/
│   └── main.asm          # SNES Mode 5 ROM: BG1 4bpp retro border frame + BG2 2bpp 16×16 chars, 30×26 grid (16px margin all sides), interlaced 512×448
├── assets/               # source + generated, mostly .gitignored
│   ├── font.inc          # 2bpp dense-pack tiles (gen_font.py, JetBrains Mono)
│   ├── keymap.inc        # Mode 5 keymap (tile = (C//8)*32 + (C%8)*2 | $3C00)
│   └── linux_wallpaper_512x448_right_4bpp.png  # former wallpaper source (gen_assets.py, no longer in main build)
└── tools/
    ├── gen_font.py       # TTF → Mode 5 2bpp dense-packed VRAM block; glyphs auto-stretched to fill 16px cell width (RENDER_W param)
    ├── gen_keymap.py     # mappings.yaml → keymap.inc (adds priority=1/palette=7 bits)
    ├── gen_border.py     # generates BG1 retro border frame: 4 super-tiles (corner/h-edge/v-edge/blank) → palette.bin + tiles.4bpp.chr + tilemap.bin
    ├── gen_welcome.py    # config/welcome.ini → assets/welcome.inc (.word tile entries, $FFFF=newline, $0000=sentinel)
    ├── gen_help.py       # config/help.ini → assets/help.inc (same format as welcome.inc, no line limit)
    ├── gen_assets.py     # PNG → 4bpp palette/tiles/tilemap binaries (mode5_image pipeline; not in main build, available for dev)
    ├── crop_image.py     # image scaling/cropping helper used by gen_assets.py
    └── fix_checksum.py   # post-link SNES-header checksum patch (handles 64 KiB ROM)

docs/
├── AI-README.md          # architecture deep-dive for agents/contributors
└── AI-MODE-5-README.md   # Mode 5 graphics reference (dense-pack, BG2, interlace)
```

### keyboard_mappings.yaml schema

```yaml
window: "bsnes"   # substring of emulator window title (case-insensitive)

buttons:
  Up:     Up
  Down:   Down
  Left:   Left
  Right:  Right
  A:      x        # bSNES+ default for A
  B:      z
  X:      s
  Y:      a
  L:      d
  R:      c
  Start:  Return
  Select: apostrophe
```

Key names follow xdotool / X11 syntax.

### Focus management (platform-aware)

The emulator must have X11 focus to receive XTest key events. `keyboard_injector.py` detects the environment at startup via `/proc/version` and chooses the appropriate strategy:

| Environment | Strategy |
|---|---|
| WSL2 + WSLg | Persistent `powershell.exe` subprocess → `SetForegroundWindow(hwnd)` via Windows user32.dll. Window found via `Get-Process | Where-Object { $_.MainWindowTitle -like '*bsnes*' }`. |
| Native Linux X11 | `xdotool windowactivate --sync <xid>` via `_NET_ACTIVE_WINDOW` EWMH protocol. |

After each injection, focus is returned to the terminal using the same mechanism.

## Dependencies

```
pyyaml>=6.0
# runtime: xdotool (apt)
# dev/optional: odfpy>=1.4  (only for convert_ods.py)
```

No `evdev` dependency — the virtual gamepad approach was abandoned (see below).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt install xdotool
```

## Current Status

Everything is implemented and working. Tested scenarios:

### WSL2 + WSLg + bSNES+ (tested, working)

- Platform: Windows 11, WSL2 Ubuntu 24.04, WSLg
- bSNES-plus v05 running inside WSL, appearing as a native Windows window
- `python -m snes_terminal_bridge` starts, TUI appears, keypresses are translated and injected into bSNES+
- Host-side focus switching was removed; bSNES+ polls X11 keystate via `XQueryKeymap`, so `xdotool keydown/keyup` (XTest) is sufficient with the bridge terminal keeping focus. See `docs/AI-README.md` §2 for details and the WSLg `boot_ready` unlock step.

### Native Linux X11 + bSNES+ (implemented, not yet tested)

- Target: Zorin OS (Ubuntu-based) with Xorg session
- Same XTest-injection path as WSL2; native X11 is expected to clear stuck keys via `_release_all()` without the WSLg one-time unlock step
- Next step: test on the Zorin machine

### SNES ROM — Mode 5 (tested, working on real hardware)

- `cd snes && make` → `build/terminal.sfc` (64 KiB 2-bank LoROM, PAL)
- SNES Mode 5 + interlace (512×448): **BG1 4bpp** retro border frame (SNES RPG style, Option D) + **BG2 2bpp** 16×16 anti-aliased text (JetBrains Mono, horizontally stretched to fill 16px cell), 30×26 visible text grid (16px margin on all sides) over a 32-row circular buffer
- BG1 border frame: blue gradient (dark navy outer → icy blue inner) + gold ◆ diamond corner ornaments; 4 unique 16×16 super-tiles (corner, h-edge, v-edge, blank); all 4 corners reused via H/V flip; 1024 bytes of tile data (vs 24576 for the former wallpaper)
- VRAM layout: BG2 font tiles $0000–$0BFF, BG2 tilemap $1000–$13FF, BG1 border tiles $2000–$20FF (bank 1, 32 slots), BG1 tilemap $5000–$53FF (bank 1)
- CGRAM: BG1 palette at $00–$0F (9 border colours, indices 10–15 unused); text palette at $1C–$1F (sub-palette 7, 4 greyscale colours)
- Text tilemap entries: priority=1, palette=7 ($3C00 flag) so text renders above the BG1 border
- Dense-pack tile layout: `tile = (C//8)*32 + (C%8)*2`, the PPU auto-reads the `N, N+1, N+16, N+17` sub-tiles per 16×16 cell
- Border assets generated by `gen_border.py` → `build/mode5_border_4bpp/`
- ROM size byte `$7FFD7` = `$08`: Everdrive uses this for LoROM address mapping (hardware-tested value, keep as-is)
- Real hardware leaves PPU registers undefined; six registers are explicitly zeroed at init: CGADSUB ($2131), CGWSEL ($2130), TMW ($212E), TSW ($212F), W12SEL ($2123), W34SEL ($2124)
- Graphics details and rationale: `docs/AI-MODE-5-README.md`; real-hardware pitfalls: `docs/AI-MODE-5-README.md` §11
- **Note:** 64 KiB ROM not yet tested on real hardware (Everdrive); only the earlier 32 KiB version was confirmed working

## Open Items

- [ ] Test Scenario 2: native Linux X11 on Zorin OS
- [ ] End-to-end test: type `Hello World` → correct characters appear in SNES program
- [ ] Start/Select as standalone keys in `mappings.yaml` (currently only used in combos)
- [x] Flash-cart / hardware test of `terminal.sfc` (interlaced Mode 5 on real PAL SNES) — tested 2026-04-23, working

### SNES ROM — planned features

- ~~**8×16 character cells:**~~ Not achievable in Mode 5 — see Dead Ends below.
- [x] **16px margin on all sides (fixes top-of-screen clipping):** `BG2VOFS` offset by −16 px at init and on every scroll step (`BG2VOFS = top_vram_row × 16 − 16`); tilemap row 31 (never written) acts as blank top margin. `cursor_x` restricted to columns 1–30 (left/right margin). Old top_vram_row row is cleared on each scroll to keep the margin blank — implemented 2026-04-26.
- [x] **BG1 decorative layer:** Full-screen 4bpp wallpaper implemented 2026-04-25; replaced 2026-04-27 with SNES RPG-style retro border frame (Option D — blue gradient + gold ◆ corner ornaments). 4 unique 16×16 super-tiles, H/V flip covers all 4 corners, 1024 bytes vs 24576 for wallpaper. Generated by `gen_border.py`.
- [x] **Cursor:** Blinking `_` (underscore) at the current input position — implemented 2026-05-01. Uses `blink_ctr` ($10, new DP variable); erase-before-pending-tile + draw-after pattern; ~1 Hz blink (bit 5 of frame counter, 32 frames per phase at PAL 50 fps).
- [x] **Welcome message:** Displayed at boot from `config/welcome.ini` (plain text, `;`/`#` comment lines stripped). `snes/tools/gen_welcome.py` converts it to `snes/assets/welcome.inc` (`.word` tile entries) at build time; `print_welcome_msg` writes directly to VRAM during init before screen enable. Limits: max 26 lines (VISIBLE_ROWS), max 30 chars/line (USABLE_COLS), ASCII 0x20–0x7E only — enforced at build time with `sys.exit`. Cursor lands on the next blank line after the message — implemented 2026-05-01.
- [x] **Terminal prompt:** `>` character written at `LEFT_COL` of each new input line; cursor starts at `PROMPT_COL = LEFT_COL + 1 = 2`. Uses `print_prompt` subroutine (direct VRAM write, same pattern as welcome message). New DP variable `auto_wrap` ($11) distinguishes Enter-triggered newlines (prompt shown) from auto-wrap at RIGHT_COL (no prompt; cursor resets to LEFT_COL and text continues). Called at boot after `print_welcome_msg` and from `@do_newline` on Enter — implemented 2026-05-02.
- [x] **Line input buffer:** DP variable `buf_len` ($12) tracks chars in the current line (0–`INPUT_BUF_MAX`=29). When full, new chars are silently dropped; `cursor_x` parks at 31 (off-screen), blink/erase skipped. Delete guard corrected to `PROMPT_COL` (was `LEFT_COL` — prevented erasing `>`). `buf_len` decrements on Delete, resets on Enter — implemented 2026-05-05.
- [x] **WRAM ASCII input buffer:** `input_buf` at `$7E:0020` (29 bytes) mirrors typed chars as raw ASCII. New `tile_to_ascii` subroutine decodes `pending_tile_lo/hi` back to ASCII (inverse of dense-pack formula, no keymap format change). `line_ready` at `$7E:003D` is set to `$01` on Enter for the host/debugger to poll; host resets it to `$00`. Both zeroed by boot WRAM DMA clear — implemented 2026-05-06.
- [x] **Help command:** `dispatch_command` checks NUL-terminated `input_buf` for known commands; calls `print_help_response` for `help`. `print_help_response` streams `help_data` words ($FFFF=newline via `newline_advance`, $0000=sentinel). `newline_advance` extracted from `@do_newline` as shared subroutine (caller must enter with A=8-bit, X=8-bit). Force-blank pattern in `@do_newline`: `INIDISP=$8F` wraps `dispatch_command + print_prompt` together. Pipeline: `config/help.ini` → `gen_help.py` → `assets/help.inc` — implemented 2026-05-06.

## Dead Ends — Do Not Retry

### Virtual gamepad via uinput (python-evdev)

Abandoned after thorough investigation. bSNES+'s ruby input library filters out all devices under `/devices/virtual/` — confirmed with `lsof /dev/input/js0` showing bSNES+ never opens the device. uinput devices always appear under this path and cannot circumvent it. Virtual gamepad works fine with Retroarch but not with bSNES+ standalone.

### xdotool with `--window` flag (XSendEvent)

Uses `XSendEvent` which marks events as synthetic (`send_event = True`). bSNES+'s ruby input library ignores synthetic events.

### xdotool windowfocus / windowactivate under WSL2/WSLg

Both fail under WSLg/XWayland:
- `windowfocus` uses `XSetInputFocus` — silently ignored
- `windowactivate` uses `_NET_ACTIVE_WINDOW` — fails: `XGetWindowProperty[_NET_ACTIVE_WINDOW] failed (code=1)` (no Linux WM under WSLg)

### WScript.Shell.AppActivate under WSL2

Returns `False` — bSNES+'s window title starts with the game name ("Super Mario World (USA) - bsnes-plus v05 (Ubuntu-24.04)"), not "bsnes". `AppActivate` requires a prefix or exact match. Fixed by using `Get-Process` with wildcard title search + direct `SetForegroundWindow(hwnd)`.

### 8×16 character cells in Mode 5

Attempted in branch `experiment/left-align-font` (commit `24cfa25`). In Mode 5, BG2 8×8 tiles are **lo-res** — each tile renders 16 px wide on screen regardless of tile size. Switching `BGMODE` from `$35` (BG2 16×16) to `$15` (BG2 8×8) still gives only 30 visible columns, identical to the current 16×16 approach but without the hi-res anti-aliasing sharpness. The hoped-for 64-column grid is physically not achievable with BG2 in Mode 5.

### Mode 1 parallel build path (removed)

An earlier iteration maintained a second 4bpp/Mode 1 ROM (`main_mode5.asm` ↔ `main.asm` pair) for broader emulator compatibility. Dropped in favour of a single Mode 5 codebase once bsnes-plus was confirmed as the target. Removing the fork simplified the build, keymap generator, and VRAM-handling logic — all Mode-1-specific DMA sections and the `tile = C*2` lookup are gone.
