# snes-terminal-bridge — Implementierungsplan

## Ziel

Ein Tool, das Tastatureingaben in SNES-Controller-Tastenkombinationen übersetzt und diese an einen SNES-Emulator (bSNES / Retroarch) unter Linux weiterleitet. Die Zuordnung aller 128 ASCII-Zeichen liegt in `assets/SNES-ASCII-Map.ods` vor und wird in eine editierbare YAML-Datei exportiert.

---

## Architektur

**Sprache:** Python 3.10+

**Emulator-Schnittstelle:** Linux `uinput` virtueller Gamepad via `python-evdev`
- Kompatibelste Lösung — funktioniert mit jedem Emulator (Retroarch, bSNES/ares, snes9x, ...)
- Virtuelles Gerät erscheint in `/dev/input/eventN` wie ein echter Controller
- Einmalige udev-Einrichtung nötig (siehe unten)

**UI:** `curses` TUI (stdlib)
- Linke Spalte: scrollendes Log `Taste → [BUTTON, BUTTON]`
- Rechte Spalte: vollständige Mapping-Legende

**Keyboard-Capture:** `curses.getch()` mit `keypad(True)`
- Verarbeitet Escape-Sequenzen (Pfeiltasten, F-Tasten) automatisch
- Terminal im Raw-Modus — Tastendrücke gelangen nicht an die Shell

### Dateistruktur

```
snes_terminal_bridge/
├── __main__.py        # Einstiegspunkt: python -m snes_terminal_bridge
├── bridge.py          # Haupt-Loop, verbindet alle Module
├── config.py          # Lädt/validiert mappings.yaml
├── mapper.py          # char → list[SNESButton] (zustandslos)
├── input_capture.py   # curses Raw-Keyboard-Reader
├── gamepad.py         # UInput virtueller SNES-Controller
└── tui.py             # curses-Anzeige

scripts/
└── convert_ods.py     # Einmalig: ODS → mappings.yaml (braucht odfpy)

config/
└── mappings.yaml      # Generiert von convert_ods.py, vom User editierbar

udev/
└── 99-snes-terminal-bridge.rules   # /dev/uinput-Zugriff für input-Gruppe

pyproject.toml
```

---

## YAML-Konfiguration (`config/mappings.yaml`)

```yaml
settings:
  hold_ms: 80          # Button-Haltezeit in ms (mind. 1 SNES-Frame = ~17ms)
  release_gap_ms: 20   # Pause zwischen Combos (für saubere Rising-Edge)

mappings:
  "A": [A]
  "a": [A, Select]
  " ": [Up, Left, A]
  "\r": [L, R, A]
  "KEY_UP": [Up]       # Escape-Sequenzen als KEY_*-Name
  # ... 128 Einträge gesamt
```

Gültige Button-Namen: `A, B, X, Y, L, R, Start, Select, Up, Down, Left, Right`

---

## Technische Details (evdev/uinput)

- **D-Pad** → `EV_ABS` Hat-Achsen (`ABS_HAT0X`, `ABS_HAT0Y`), **keine** `EV_KEY`-Buttons — das erwarten Emulatoren
- **Face-Buttons:** `BTN_SOUTH`(B), `BTN_EAST`(A), `BTN_NORTH`(X), `BTN_WEST`(Y)
- **Schultertasten:** `BTN_TL`(L), `BTN_TR`(R)
- Alle Combo-Buttons auf einmal schreiben, dann einmal `syn()` → atomarer Druck

**Main-Loop (threaded):**
```
Thread 1: input_capture → queue.Queue
Thread 2 (main + curses): dequeue → mapper → tui.update() → gamepad.press_combo()
```

---

## Einmalige udev-Einrichtung

```bash
sudo cp udev/99-snes-terminal-bridge.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
sudo usermod -aG input $USER   # danach neu einloggen
```

---

## Implementierungsschritte

- [x] `docs/plan.md` erstellen
- [ ] `pyproject.toml` erstellen (deps: `evdev>=1.6`, `pyyaml>=6.0`; dev: `odfpy>=1.4`)
- [ ] `scripts/convert_ods.py` erstellen — ODS parsen, `config/mappings.yaml` ausgeben
- [ ] `convert_ods.py` ausführen → `config/mappings.yaml` mit 128 Einträgen generieren
- [ ] `snes_terminal_bridge/__init__.py` erstellen
- [ ] `snes_terminal_bridge/config.py` — mappings.yaml laden/validieren
- [ ] `snes_terminal_bridge/mapper.py` — char → list[SNESButton] Lookup
- [ ] `snes_terminal_bridge/gamepad.py` — UInput virtueller SNES-Controller (Hat-Achsen!)
- [ ] `snes_terminal_bridge/input_capture.py` — curses Raw-Keyboard-Reader
- [ ] `snes_terminal_bridge/tui.py` — curses-Anzeige
- [ ] `snes_terminal_bridge/bridge.py` — Threaded Main-Loop
- [ ] `snes_terminal_bridge/__main__.py` — Einstiegspunkt
- [ ] `udev/99-snes-terminal-bridge.rules` erstellen
- [ ] `CLAUDE.md` und `README.md` mit Build/Run-Anleitung aktualisieren

---

## Verifikation

1. `python scripts/convert_ods.py` → `config/mappings.yaml` hat 128 Einträge
2. `python -m snes_terminal_bridge` → TUI erscheint, Tippen zeigt z.B. `A → [A]`
3. In zweitem Terminal: `cat /proc/bus/input/devices` → virtuelles Gerät sichtbar
4. Retroarch/bSNES öffnen, virtuellen Controller konfigurieren → Tastendrücke landen im Emulator
5. `Hello World` tippen → korrekte SNES-Combo-Sequenz für jeden Buchstaben
