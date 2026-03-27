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

The tool creates a virtual gamepad via `/dev/uinput`, a Linux kernel interface for virtual input devices. By default only `root` can access it. The udev rule in this repo assigns `/dev/uinput` to the `input` group and grants group read/write access (`MODE="0660"`), so any user in that group can use it without sudo.

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

## Testing

### Mapping test (no emulator needed)

```bash
source .venv/bin/activate
python scripts/test_mapping.py
```

Shows the corresponding SNES buttons for each character you type.

### Virtual gamepad test

Terminal 1:
```bash
source .venv/bin/activate
python scripts/test_gamepad.py
```

Terminal 2 (in parallel):
```bash
sudo evtest
# → select "SNES Terminal Bridge" from the list
```

Type characters in terminal 1 — the corresponding input events appear in terminal 2 (e.g. `BTN_EAST` for `A`, `ABS_HAT0Y` for D-pad Up).

## Emulator compatibility

| Emulator | Status |
|---|---|
| Retroarch + bsnes-mercury core | Works — virtual controller is detected |
| bSNES+ standalone | Not working — ruby input library filters out virtual devices |

### Retroarch setup

```bash
sudo apt install libretro-bsnes-mercury-balanced
```

In Retroarch: **Load Core → bsnes-mercury Balanced**, then configure Port 1 controls to use "SNES Terminal Bridge".

### Why bSNES+ doesn't work

bSNES+ uses the ruby input library which enumerates joystick devices via libudev. It filters out devices under `/devices/virtual/` in sysfs — which is where all uinput devices appear, regardless of their reported name or IDs. `lsof` confirms bSNES+ never opens the virtual device at all. The fix requires a source-level change to bSNES+.

## Status

The core pipeline is complete: keyboard input is captured, mapped to SNES buttons, displayed in the TUI, and injected into the virtual gamepad. Retroarch integration is working. Next: patch bSNES+ to accept virtual input devices.
