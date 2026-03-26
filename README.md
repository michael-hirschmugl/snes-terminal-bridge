# snes-terminal-bridge

Translate keyboard input to SNES controller sequences and inject them into emulators — the foundation for running terminal applications on a Super Nintendo.

## How it works

1. **Capture** — reads keyboard input in raw mode (keystrokes don't leak to the shell)
2. **Map** — looks up the SNES button combination for each character (configurable via `config/mappings.yaml`)
3. **Display** — shows `key → [BUTTON, BUTTON, …]` in a terminal UI
4. **Inject** — presses the combo on a virtual Linux gamepad (`uinput`), which any emulator sees as a real controller

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### One-time udev setup (required for `/dev/uinput` access without sudo)

```bash
sudo cp udev/99-snes-terminal-bridge.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
sudo usermod -aG input $USER  # log out and back in afterwards
```

## Usage

```bash
source .venv/bin/activate
python -m snes_terminal_bridge
```

## Configuration

`config/mappings.yaml` maps every ASCII character to a list of SNES buttons:

```yaml
settings:
  hold_ms: 80        # how long buttons are held (ms)
  release_gap_ms: 20 # gap between combos (ms)

mappings:
  A: [A]
  a: [A, Select]
  ' ': [Up, Left, A]
```

Valid button names: `A, B, X, Y, L, R, Start, Select, Up, Down, Left, Right`

To regenerate `mappings.yaml` from the source spreadsheet:

```bash
pip install odfpy
python scripts/convert_ods.py
```

## Status

Work in progress.
