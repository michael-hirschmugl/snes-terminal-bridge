# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

snes-terminal-bridge translates keyboard input to SNES controller button sequences and injects them into emulators — the foundation for running terminal applications on a Super Nintendo.

## Assets

- `assets/SNES-ASCII-Map.ods` — Reference spreadsheet mapping ASCII characters to SNES controller button combinations. All 128 ASCII chars are mapped to SNES button combos using A/B/X/Y, L/R, Start, Select, and D-pad directions.

## Planned Architecture

**Language:** Python 3.10+
**Emulator interface:** Linux `uinput` virtual gamepad via `python-evdev`
**UI:** `curses` TUI (stdlib)
**Input capture:** `curses.getch()` with `keypad(True)` (raw mode, no shell leakage)

### Pipeline

```
Thread 1: input_capture (curses raw read) → queue.Queue
Thread 2: dequeue → mapper → tui.update() → gamepad.press_combo()
```

### Module layout (not yet created)

```
snes_terminal_bridge/
├── __main__.py        # python -m snes_terminal_bridge
├── bridge.py          # main loop
├── config.py          # load/validate mappings.yaml
├── mapper.py          # char → list[SNESButton]
├── input_capture.py   # curses keyboard reader
├── gamepad.py         # UInput virtual SNES controller
└── tui.py             # curses display

scripts/
└── convert_ods.py     # one-shot: ODS → config/mappings.yaml (needs odfpy)

config/
└── mappings.yaml      # user-editable YAML mapping config

udev/
└── 99-snes-terminal-bridge.rules
```

### evdev/uinput critical details

- D-pad → `EV_ABS` hat axes (`ABS_HAT0X`/`ABS_HAT0Y`), **not** `EV_KEY` — emulators expect this
- Face buttons: `BTN_SOUTH`(B), `BTN_EAST`(A), `BTN_NORTH`(X), `BTN_WEST`(Y)
- Shoulders: `BTN_TL`(L), `BTN_TR`(R)
- Write all combo buttons, then single `syn()` → atomic press
- Default `hold_ms: 80`, `release_gap_ms: 20` (configurable in mappings.yaml)

### mappings.yaml schema

```yaml
settings:
  hold_ms: 80
  release_gap_ms: 20

mappings:
  "A": [A]
  "a": [A, Select]
  " ": [Up, Left, A]
  "KEY_UP": [Up]       # escape sequences as KEY_* names
```

### One-time udev setup (needed before first run)

```bash
sudo cp udev/99-snes-terminal-bridge.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
sudo usermod -aG input $USER
```

## Dependencies

```toml
dependencies = ["evdev>=1.6", "pyyaml>=6.0"]
# dev/optional: odfpy>=1.4  (only for convert_ods.py)
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Für convert_ods.py zusätzlich:
pip install odfpy
```

## Implementation Progress

See `docs/plan.md` for the full checklist. Status as of last session:

- [x] `CLAUDE.md` created
- [x] `docs/plan.md` created (detailed plan with checkboxes)
- [x] `requirements.txt` created
- [x] `scripts/convert_ods.py` — ODS → YAML converter (inline list style)
- [x] `config/mappings.yaml` — 97 mappings generated
- [ ] Python package modules (`config.py`, `mapper.py`, `gamepad.py`, `input_capture.py`, `tui.py`, `bridge.py`, `__main__.py`)
- [ ] `udev/99-snes-terminal-bridge.rules`
