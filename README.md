# snes-terminal-bridge

Translates keyboard input into SNES controller button sequences and injects them into a running SNES emulator. The goal is to allow typing ASCII text in a terminal, with each character being converted into the corresponding sequence of SNES button presses — the foundation for running terminal applications on a Super Nintendo.

---

## How it works

The bridge applies two mappings in sequence:

```
keystroke
  → mappings.yaml          (ASCII character → SNES button combination)
  → keyboard_mappings.yaml (SNES button → emulator keyboard key)
  → key injection into emulator window
```

**Example:** typing `H`

1. `mappings.yaml`: `H` → `[A, L]`
2. `keyboard_mappings.yaml`: `A` → `x`, `L` → `d`
3. The bridge presses `x + d` simultaneously in the emulator window for 80 ms

The emulator has its own keyboard → SNES controller mapping configured in its settings. The bridge uses `xdotool` (XTest) to inject key events after briefly switching focus to the emulator window. Focus is then immediately returned to the terminal.

### Pipeline

```
Thread 1: curses raw keyboard read  →  queue.Queue
Thread 2: dequeue  →  mapper  →  TUI update  →  KeyboardInjector.press_combo()
```

### Focus management

X11 focus APIs behave differently depending on the environment:

| Environment | Focus method |
|---|---|
| WSL2 + WSLg | `SetForegroundWindow` via persistent PowerShell subprocess |
| Native Linux (X11/Xorg) | `xdotool windowactivate` via `_NET_ACTIVE_WINDOW` |

The correct method is detected automatically at startup by checking `/proc/version` for the WSL2 signature.

---

## Project layout

```
snes_terminal_bridge/
├── __main__.py           # Entry point: python -m snes_terminal_bridge
├── bridge.py             # Main loop — wires all modules together
├── config.py             # Loads and validates mappings.yaml + keyboard_mappings.yaml
├── mapper.py             # ASCII character → list[SNESButton] lookup
├── input_capture.py      # curses raw keyboard reader (background thread)
├── keyboard_injector.py  # Focus switching + xdotool XTest key injection
└── tui.py                # curses display (header, scrolling log, status bar)

config/
├── mappings.yaml          # ASCII → SNES button combinations (97 characters mapped)
└── keyboard_mappings.yaml # SNES button → emulator keyboard key

assets/
└── SNES-ASCII-Map.ods  # Reference spreadsheet: all 128 ASCII chars mapped to SNES combos

scripts/
├── convert_ods.py   # One-shot: regenerates mappings.yaml from the ODS spreadsheet
└── test_mapping.py  # Interactive mapping test (no emulator needed)
```

---

## Requirements

### All platforms
- Python 3.10+
- `xdotool`
- A SNES emulator that accepts keyboard input (bSNES+ recommended)

### WSL2
- Windows 11 with WSLg enabled (Linux GUI apps must appear as native Windows windows)
- `powershell.exe` accessible from WSL (standard in all WSL2 installations)

### Native Linux
- X11/Xorg session (Wayland is not yet supported — see Scenario 2)
- A window manager with `_NET_ACTIVE_WINDOW` support (GNOME on Xorg, KDE Plasma on Xorg, i3, Openbox, XFCE, etc.)

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

> **Note:** Do not copy a `.venv` from another machine. It contains absolute paths and
> architecture-specific binaries. Always create a fresh one with the steps above.

---

## Emulator configuration

The bridge injects keystrokes that the emulator maps to SNES controller buttons. The key names in `config/keyboard_mappings.yaml` must match what the emulator expects.

**bSNES+ defaults (Settings → Input → Port 1):**

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
| Select | ' (apostrophe) | `Select: apostrophe` |

The defaults in `config/keyboard_mappings.yaml` already match the bSNES+ defaults. If you use a different emulator or have changed its keyboard settings, edit that file accordingly.

Key names follow xdotool / X11 syntax. Run `xdotool key --help` or check the xdotool man page for special key names (`Return`, `space`, `BackSpace`, `Tab`, `Escape`, `exclam`, etc.).

---

## Usage

1. Start bSNES+ and load a game (the emulator window must be visible and not minimized)
2. Activate your venv: `source .venv/bin/activate`
3. Run the bridge: `python -m snes_terminal_bridge`

The TUI shows a scrolling log of each keypress and its translation:

```
snes-terminal-bridge  |  Ctrl+C to quit
  'H'          →  [A, L]
  'e'          →  [A, Select, Right]
  'l'          →  [B, Select, Down]
  ...
```

Press **Ctrl+C** to quit.

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
  "KEY_UP": [Up]       # Special keys use KEY_* names
```

Valid button names: `A`, `B`, `X`, `Y`, `L`, `R`, `Start`, `Select`, `Up`, `Down`, `Left`, `Right`

Special key names: `KEY_UP`, `KEY_DOWN`, `KEY_LEFT`, `KEY_RIGHT`, `KEY_ENTER`, `KEY_BACKSPACE`, `KEY_DELETE`, `KEY_ESCAPE`, `KEY_TAB`, `KEY_F1`–`KEY_F4`

### config/keyboard_mappings.yaml

Maps SNES button names to xdotool key names that get injected into the emulator.

```yaml
window: "bsnes"   # Substring of the emulator window title (case-insensitive)

buttons:
  A: x
  B: z
  # ...
```

The `window` field is used to find the emulator window:
- **WSL2:** matched against `MainWindowTitle` via PowerShell `Get-Process` (wildcard: `*bsnes*`)
- **Native Linux:** matched against the X11 window title via `xdotool search --name`

### Regenerating mappings.yaml from the ODS spreadsheet

`assets/SNES-ASCII-Map.ods` is the authoritative source for all 128 ASCII → SNES button mappings. To regenerate `config/mappings.yaml` from it:

```bash
pip install odfpy   # only needed for this script
python scripts/convert_ods.py
```

---

## Testing mappings without an emulator

```bash
python scripts/test_mapping.py
```

Opens an interactive prompt where you can type characters and see which SNES button combination they resolve to, without needing a running emulator.

---

## Tested scenarios

### Scenario 1: WSL2 + WSLg + bSNES+ on Windows 11

**Status:** Working

**System:**
- Windows 11 with WSL2 (Ubuntu 24.04)
- WSLg enabled — Linux GUI apps appear as individual native Windows windows in the taskbar
- bSNES-plus v05 installed inside WSL
- Python 3.12 inside WSL

**Reproduction steps:**

```bash
# 1. Install dependencies inside WSL
sudo apt install xdotool bsnes-plus

# 2. Clone and set up the project
git clone <repo-url> && cd snes-terminal-bridge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Start bSNES+, load a ROM, leave the window visible (not minimized)
#    Verify Settings → Input → Port 1 matches config/keyboard_mappings.yaml

# 4. Run the bridge (in a separate terminal)
python -m snes_terminal_bridge
```

**Verify the setup works:**

Before running the bridge, confirm PowerShell can see the bSNES+ window:

```bash
powershell.exe -NoProfile -Command \
  "Get-Process | Where-Object { \$_.MainWindowTitle -like '*bsnes*' } | Select-Object MainWindowTitle, MainWindowHandle"
```

This should print the window title and a non-zero handle. If it returns nothing, WSLg is not running properly or the window is minimized.

**How focus switching works:**

The bridge spawns a persistent `powershell.exe` subprocess at the first keypress and loads the Windows `user32.dll` API into it once. For each button combo:

1. `Get-Process` finds bSNES+'s `MainWindowHandle` (searched by `*bsnes*` title wildcard)
2. `SetForegroundWindow(hwnd)` gives bSNES+ Windows-level focus
3. WSLg propagates the focus change to an X11 `FocusIn` event within ~30 ms
4. `xdotool keydown/keyup` (XTest, no `--window`) injects the keys to the now-focused bSNES+ window
5. `SetForegroundWindow` restores focus to the terminal

The persistent PowerShell process avoids the ~300 ms startup cost on every keystroke.

**Why standard X11 focus APIs do not work here:**

WSLg runs Linux GUI apps through XWayland. Under XWayland there is no Linux window manager:

| Approach | Result |
|---|---|
| `xdotool key --window <wid>` | Uses `XSendEvent` → events marked as synthetic → bSNES+ ruby input library ignores them |
| `xdotool windowfocus` | Uses `XSetInputFocus` → silently ignored by XWayland |
| `xdotool windowactivate` | Uses `_NET_ACTIVE_WINDOW` → fails: `XGetWindowProperty[_NET_ACTIVE_WINDOW] failed (code=1)` |
| `WScript.Shell.AppActivate("bsnes")` | Returns `False` — bSNES+'s window title starts with the game name, not "bsnes" |

Only `SetForegroundWindow` via the Windows API reliably changes focus in this environment.

**Other dead ends investigated:**

- **Virtual gamepad via uinput** (`python-evdev`): bSNES+'s ruby input library filters out all devices under `/devices/virtual/` (the path all uinput devices appear under). Confirmed with `lsof /dev/input/js0` while bSNES+ was running — bSNES+ never opened the device. The virtual gamepad approach works with Retroarch but not with bSNES+ standalone.

---

### Scenario 2: Native Linux (X11) + bSNES+

**Status:** Implemented, not yet tested

**Expected system:**
- Linux with an Xorg/X11 session
- A window manager with `_NET_ACTIVE_WINDOW` support (GNOME on Xorg, KDE Plasma on Xorg, i3, Openbox, XFCE, ...)
- bSNES+ installed natively
- Python 3.10+

**Reproduction steps:**

```bash
# 1. Verify you are on an X11 session (not Wayland)
echo $XDG_SESSION_TYPE   # must print "x11"

# 2. Install dependencies
sudo apt install xdotool bsnes-plus   # Zorin, Ubuntu, Debian

# 3. Clone and set up the project
git clone <repo-url> && cd snes-terminal-bridge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4. Start bSNES+, load a ROM
#    Verify Settings → Input → Port 1 matches config/keyboard_mappings.yaml

# 5. Verify xdotool can find the bSNES+ window
xdotool search --name bsnes   # should return one or more window IDs

# 6. Run the bridge
python -m snes_terminal_bridge
```

**How focus switching works in this scenario:**

On a native Linux X11 session there is a real window manager running that maintains `_NET_ACTIVE_WINDOW`. The bridge calls `xdotool windowactivate --sync <xid>`, which sends a `_NET_ACTIVE_WINDOW` client message to the root window. The window manager honors this and generates an X11 `FocusIn` event for bSNES+. Keys are then injected via XTest and focus is returned to the terminal using the same mechanism.

**Wayland note:**

If `$XDG_SESSION_TYPE` is `wayland`, Wayland restricts focus stealing at the compositor level and `xdotool windowactivate` will not work. Switch to an Xorg session (at the login screen, choose "GNOME on Xorg" or equivalent) until Wayland support is added.
