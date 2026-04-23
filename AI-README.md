# AI-README

Kurzreferenz für AI-Coding-Agents (und neue Contributor), die in diesem Repo arbeiten. Ergänzt `README.md` (End-User-Sicht) und `CLAUDE.md` (Status & Historie). Hier steht, **wie der Code denkt** — nicht, wie man ihn benutzt.

---

## 1. Architektur (high-level)

Das Projekt besteht aus **zwei unabhängigen, über eine YAML-Datei gekoppelten Teilen**:

```
┌─────────────────────────────┐          ┌────────────────────────────┐
│  Host-Seite (Python)        │          │  SNES-Seite (65816 ASM)    │
│  snes_terminal_bridge/      │          │  snes/src/main.asm         │
│                             │          │                            │
│  curses getch               │          │  JOY1L/JOY1H-Snapshot      │
│    → queue.Queue            │          │    → Debounce (≥2 VBlanks) │
│    → mapper.lookup          │          │    → Keymap-Scan           │
│    → KeyboardInjector       │          │    → Tile-Write in VBlank  │
│        (xdotool XTest)      │          │    → BG1 32×14 Scroll-Grid │
│                             │          │                            │
│           │  X11 Keystate   │          │                            │
│           └─► Emulator  ────┼──────────┼─► Joypad-Poll (bSNES+)     │
└─────────────────────────────┘          └────────────────────────────┘
                     ▲                                  ▲
                     │           single source          │
                     └──── config/mappings.yaml ────────┘
                          (ASCII → SNES-Button-Combo)
```

**Zentrale Invariante:** `config/mappings.yaml` ist die **einzige Wahrheit** für die Zuordnung ASCII → Button-Combo. Sie wird zur Laufzeit von der Bridge und zum Build-Zeitpunkt per `snes/tools/gen_keymap.py` in `snes/assets/keymap.inc` (SNES-Lookup-Tabelle) übersetzt. Beide Seiten müssen bitweise dieselbe Abbildung haben.

### Host-Pipeline (Python)

Zwei-Stufen-Mapping, strikt entkoppelt:

```
keystroke
  → mappings.yaml           (ASCII char  → list[SNESButton])
  → keyboard_mappings.yaml  (SNESButton → X11-Tastenname)
  → xdotool keydown … keyup  (XTest-Injection, kein Fokuswechsel)
```

Thread-Modell:

```
Thread 1 (daemon): curses getch → queue.Queue[str]
Thread 2 (main) : dequeue → mapper → TUI.update → KeyboardInjector.press_combo
```

Startup: `KeyboardInjector.__init__` ruft `_release_all()` zweimal auf, um Keys aus einer vorherigen Session zu befreien.

### SNES-Pipeline (ROM)

Ein einziger Main-Loop, alle Entscheidungen im Frame-Raster:

```
@main_loop:
  wait VBlank → pending tile → VRAM write
  wait VBlank-end → wait auto-joypad
  snapshot JOY1L/JOY1H
  debounce (stable_cnt ≥ 2)
  boot-guard (buttons=0 mindestens einmal gesehen)
  dedupe (last_trig_*)
  linear scan: keymap_data → pending_tile_{lo,hi} + pending_flag
```

Keine IRQs, kein NMI — Synchronisation ausschließlich über `HVBJOY`.

---

## 2. Wichtige Patterns

### Host

- **Two-step mapping** (ASCII → Button → X11-Key): nie in einem Schritt. Die Trennung erlaubt, dass User die Emulator-Tastenbelegung ändern können, ohne `mappings.yaml` anzurühren.
- **Dataclass-Config, keine dicts nach `load()`**: `config.load()` / `config.load_keyboard()` validieren beim Laden (`VALID_BUTTONS`) und liefern `Config` / `KeyboardConfig`. Validierungs-Errors werden früh geworfen — stromabwärts wird nicht mehr gecheckt.
- **`curses.wrapper(run, …)`** umschließt den gesamten Main-Loop. Kein direkter `initscr()`-Aufruf. Shutdown sauber über `signal.SIGTERM` / `SIGHUP` → `injector.close()` (releases alle Keys).
- **Context Manager für den Injector**: `with KeyboardInjector(…) as injector:` garantiert `_release_all()` beim Exit. Verlassen ohne das Flag → hängende Keys im X-Server.
- **XTest statt XSendEvent**: `xdotool keydown KEY` (ohne `--window`). bSNES+ ignoriert synthetische Events (`send_event=True`), XTest setzt aber den globalen X11-Keystate, den bSNES+ per `XQueryKeymap` pollt — **deshalb ist kein Fokuswechsel nötig**.
- **Key-Codes getrennt behandelt**: `input_capture.py` mappt `curses.KEY_*` + Einzelbyte-Steuerzeichen auf `KEY_*`-Strings; printables werden per `32 ≤ code ≤ 126` durchgereicht. `mapper.py` hat eine zusätzliche `CURSES_KEY_NAMES`-Tabelle für String-Keys (Redundanz bewusst, zwei Eingabepfade).

### SNES

- **Direct-Page-Variablen `$00–$0F`**: alle Zustandsflags und Cursor-Position liegen in DP. Kommentarblock oben in `main.asm` ist die verbindliche Liste.
- **Pending-Write-Queue (1 Slot)**: `pending_flag` + `pending_tile_{lo,hi}`. Lookup im aktiven Teil des Frames, Write ausschließlich im VBlank. Kein DMA für einzelne Tiles, nur für Boot-Uploads.
- **Special-Action-Sentinels im High-Byte `$FF`**: `$FFFF` = DELETE, `$FFFE` = ENTER. In `@normal_tile` zuerst `pending_tile_hi == $FF` prüfen, dann Low-Byte unterscheiden.
- **32-row circular buffer + `BG1VOFS = top_vram_row * 16`**: das Tilemap ist 64×64, sichtbar sind 32×14. Scrollen heißt: `cursor_y` erhöhen (mod 32), `top_vram_row` nachziehen, neue Zeile in VRAM clearen (4 Sections wegen Screen-Boundary-Wrap bei Spalten 32/Zeilen 32).
- **Debounce + Dedupe doppelt**: `stable_cnt ≥ 2` verhindert Frame-Rauschen, `last_trig_{lo,hi}` verhindert Auto-Repeat beim Gehalten-Halten derselben Kombo. Beide sind nötig.
- **Boot-Guard (`boot_ready`)**: ROM verwirft jegliche Eingabe, bis einmal alle Buttons = 0 war. Schutz gegen „hängenden Key aus Vorsession".
- **A/X Register-Breitenwechsel explizit kommentieren** (`.a8` / `.a16`, `.i8` / `.i16`). Jeder `rep`/`sep` muss paarig sein, inkl. Sprünge aus dem breiten Bereich heraus.

### Build / Code-Gen

- **`snes/Makefile`** ist die einzige legitime Art, das ROM zu bauen. Er erzeugt automatisch ein `tools/.venv` für `gen_font.py` / `gen_keymap.py` (Pillow + PyYAML). Die generierten `.inc`-Dateien in `snes/assets/` sind **nicht eingecheckt** (außer ggf. Platzhalter).
- **Generierte Dateien haben Header** (`; Auto-generated by tools/... — do not edit manually.`). Nie direkt patchen.
- **Post-Link-Checksum-Patch**: nach `ld65` läuft zwingend `python3 tools/fix_checksum.py build/terminal.sfc`. Der Linker kann die Checksumme nicht berechnen, weil sie sich selbst enthält — das Script setzt erst `complement=$FFFF`, `checksum=$0000`, summiert alle 32768 Bytes (mod `$10000`), schreibt `checksum` an `$FFDE/$FFDF` und `complement = checksum XOR $FFFF` an `$FFDC/$FFDD`. Ohne diesen Schritt lehnen Flash-Cartridges (SD2SNES/FXPak Pro, EverDrive, ...) das ROM ab.

### SNES-Header (Pflichtfelder, `main.asm` → Segment `HEADER`)

Header liegt bei LoROM immer bei `$FFC0–$FFDF` (File-Offset `$7FC0–$7FDF` im 32 KiB-Image). Jede Änderung muss geprüft bleiben — Werte sind für echte Hardware kritisch:

| Offset | Feld | Wert | Bedeutung |
|---|---|---|---|
| `$FFC0–$FFD4` | Title | 21 B ASCII, space-padded | „`SNES TERMINAL        `" |
| `$FFD5` | Map mode | `$20` | LoROM + SlowROM |
| `$FFD6` | Cartridge type | `$00` | nur ROM, keine Co-Prozessoren |
| `$FFD7` | ROM size | `$05` | `2^5 KiB = 32 KiB` — muss zur tatsächlichen Image-Größe passen |
| `$FFD8` | RAM size | `$00` | keine Cartridge-RAM (SRAM) |
| `$FFD9` | Destination | `$02` | Europa / PAL |
| `$FFDA` | Old licensee | `$00` | OK für Homebrew |
| `$FFDB` | Version | `$00` | |
| `$FFDC–DD` | Checksum-Complement | patched | `fix_checksum.py` |
| `$FFDE–DF` | Checksum | patched | `fix_checksum.py` |
| `$FFE4–$FFFF` | Interrupt-Vektoren | `Segment VECTORS` | Reset → `reset`, alle anderen → RTI-Stubs |

Wenn sich die ROM-Größe ändert (z. B. zu 64 KiB), müssen `$FFD7` **und** `snes.cfg` **und** die Checksum-Berechnung (bleibt aber bei Power-of-Two automatisch korrekt) angepasst werden.

---

## 3. Naming Conventions

### Python

- Modul- und Dateinamen: `snake_case` (`keyboard_injector.py`).
- Klassen: `PascalCase` (`KeyboardInjector`, `Config`, `KeyboardConfig`, `TUI`).
- Private Helfer: führender Unterstrich (`_inject_keys`, `_release_all`, `_is_wsl2`, `_scr`).
- Dataclasses für Config-Objekte. Keine frei herumreichten `dict[str, Any]`.
- Type-Hints konsequent: `list[str]`, `dict[str, str]`, `list[str] | None` (PEP 604). Python 3.10+ vorausgesetzt.
- Konstante Tabellen in `UPPER_SNAKE_CASE` auf Modul-Ebene (`VALID_BUTTONS`, `CURSES_KEY_MAP`, `BUTTON_BITS`).

### YAML

- `config/mappings.yaml`:
  - Printable-ASCII-Keys werden als **YAML-Strings** quoted, wenn sie Sonderbedeutung haben (`'!'`, `':'`, `'{'`, `' '` etc.).
  - Special Keys verwenden das Präfix **`KEY_*`** in Großbuchstaben (`KEY_ENTER`, `KEY_DELETE`). Identisch auf Host- und SNES-Seite.
  - Button-Namen in PascalCase, mit fixem Set: `A B X Y L R Start Select Up Down Left Right`. Validiert in `config.py:VALID_BUTTONS`.
- `config/keyboard_mappings.yaml`: Werte sind **X11-/xdotool-Keysyms** (`Return`, `space`, `exclam`, `numbersign` …) — nicht Emulator-Tastenbezeichnungen.

### Assembler (`main.asm`)

- Labels: `snake_case` (`calc_addr_top`, `boot_ready`).
- Lokale Branch-Targets: `@name` (z.B. `@main_loop`, `@wait_vblank`, `@do_delete`).
- Hardware-Register als `UPPER_SNAKE` auf File-Scope (`VMADDL`, `HVBJOY`, `JOY1L`). Stimmen mit den offiziellen SNES-Docs überein.
- DP-Variablen in `snake_case` mit kurzem, kommentarlich erklärtem Namen (`cursor_x`, `prev_joy_lo`, `pending_flag`). Jede neue DP-Variable **muss** in den Kommentarblock oben eingetragen werden.
- Segmente großgeschrieben: `CODE`, `RODATA`, `HEADER`, `VECTORS` — definiert in `snes.cfg`.

### Buttons / Bitmasken

- Button-Namen sind überall identisch (YAML ↔ Python ↔ `gen_keymap.py`). Keine Aliase (kein `Sel` statt `Select`, kein `LB`/`RB` statt `L`/`R`).
- Joypad-Wort als **Little-Endian `.word`**: Byte 0 = `JOY1L` = {A,X,L,R}, Byte 1 = `JOY1H` = {B,Y,Select,Start,Up,Down,Left,Right}. Bit-Zuordnung kanonisch in `gen_keymap.py:BUTTON_BITS` und im Header-Kommentar von `main.asm`.

---

## 4. Typische Stolperfallen

### Konfiguration

- **`mappings.yaml` geändert, aber ROM nicht neu gebaut** → Bridge injiziert neue Kombo, ROM kennt sie nicht (kein Tile). Immer `cd snes && make` nach Änderungen an `mappings.yaml`.
- **Button-Tippfehler** (z.B. `Sel`, `select`) → `ValueError` in `config.load()`. Bewusst strikt, nicht silent-ignorieren.
- **YAML-Parsing-Fallen**: `y`, `n`, `no`, `on`, `off` werden ohne Quotes zu Booleans. Keys wie `'y'` / `'n'` müssen quoted sein (sind es in `mappings.yaml`).

### Host / X11

- **Nie `xdotool key --window …` benutzen.** Das nutzt `XSendEvent`, Events bekommen `send_event=True`, bSNES+ filtert sie weg. Nur `xdotool keydown/keyup` (XTest, ohne `--window`).
- **Nie `windowfocus` / `windowactivate` hinzufügen.** Unter WSLg/XWayland fehlgeschlagen (silent bei `XSetInputFocus`, hart bei `_NET_ACTIVE_WINDOW`). Das Design **braucht** keinen Fokuswechsel, weil bSNES+ per `XQueryKeymap` pollt.
- **Nie das uinput-/evdev-Pattern wieder aufmachen.** bSNES+ filtert alles unter `/devices/virtual/` heraus. Details in `CLAUDE.md` → „Dead Ends".
- **WSLg-Boot-Guard**: nach ROM-Start muss einmal echt in das bSNES+-Fenster getippt werden (oder die Startbalance von Keys im XTest-State stimmt zufällig). Synthetische `keyup` löscht unter XWayland keinen physisch hängenden Key. Nicht als Bug behandeln, sondern im User-Guide erwähnen.
- **`release_gap_ms` zu klein** → Rising-Edge zwischen Kombos fehlt, ROM-Dedupe triggert nicht → Zweitschlag geht verloren. Default 20 ms passt zu `stable_cnt ≥ 2` (≈33 ms @60 Hz).
- **`hold_ms < 17`** (< 1 SNES-Frame) → Kombo wird u. U. nie vom Emulator gesampelt. Minimum praktisch 30–40 ms.

### SNES / ASM

- **VRAM-Writes außerhalb VBlank** → Grafikkorruption. Alle Tile-Writes hinter `@wait_vblank` halten.
- **A/X-Registerbreite vergessen umzuschalten** → Stack-Korruption oder zufällige Hochbytes. Jeder `rep #$20` braucht `.a16` + späteres `sep #$20` + `.a8`. Analog `rep #$10` / `sep #$10` für X/Y.
- **Neue Special-Action vergessen in `gen_keymap.py:SPECIAL_ACTIONS` einzutragen** → wird in `@normal_tile` als gültiger Tile-Index behandelt und schreibt Schrott in VRAM. Sentinels leben im Bereich `$FF00–$FFFF` (High-Byte = `$FF`); neue Aktionen dort anhängen + in `main.asm` `@normal_tile`-Switch ergänzen.
- **Keymap-Reihenfolge irrelevant, aber Sentinel `$0000,$0000` muss existieren** — `@scan_loop` stoppt sonst nie. `gen_keymap.py` schreibt ihn automatisch ans Ende.
- **Scroll-Math bei Spalte 32 / Zeile 32**: Screen-Boundary-Bit (`+$0400` für Spalten ≥ 32, `+$0800` für Zeilen ≥ 32) leicht zu vergessen. Siehe `calc_addr_top` und die vier Clear-Sections in `@do_newline`.
- **`cursor_y` ist (mod 32), nicht (mod 14)**. 14 ist nur das Viewport, 32 der zirkulare Puffer. Wer das verwechselt, berechnet `BG1VOFS` falsch.

### ROM-Header / Hardware

- **Checksum-Patch nicht überspringen**: `ld65` schreibt Platzhalter (`$0000` / `$FFFF`). Ohne `fix_checksum.py` läuft das ROM zwar im Emulator und auf dem nackten S-CPU, aber Flash-Carts validieren den Header vor dem Mount und weisen es ab. Der Makefile-Target koppelt den Schritt ans Linken — **nicht** entfernen oder nur einzeln `ld65` aufrufen.
- **ROM-Size-Byte (`$FFD7`) muss zur Image-Größe passen**: Wert `$08` (256 KiB) auf ein 32 KiB-Image ist zwar historisch weit verbreitet, aber strenge Flash-Cart-Firmwares stolpern. Für LoROM 32 KiB ist `$05` korrekt (`2^5 KiB`).
- **Destination-Code (`$FFD9`) ist real wirksam**: `$02` markiert die ROM als PAL; ein PAL-SNES bootet NTSC-ROMs gar nicht ohne Region-Mod. Umgekehrt zeigen PAL-ROMs auf NTSC-Konsolen oft Bildfehler (falsche VBlank-Länge). Wenn jemand NTSC testen will, parallel bauen statt umflashen.
- **Kein SRAM**: Header-Feld `$FFD8 = $00` und `cartridge type $FFD6 = $00`. Die ROM speichert nichts persistent; `.srm`-Dateien der Emulatoren sind leer/unbenutzt und können bedenkenlos gelöscht werden. Wer SRAM ergänzen will, muss **beide** Felder ändern **und** echte SRAM-Lese-/Schreib-Logik in `main.asm` implementieren.

### Environment / Tooling

- **`.venv` zwischen Maschinen kopiert** → paths stimmen nicht, subtile Fehler. Immer `python3 -m venv .venv` neu aufsetzen.
- **`odfpy` in Hauptanforderungen aufnehmen** → unnötige Laufzeit-Dep. Gehört nur zu `scripts/convert_ods.py` (One-Shot-Import von `SNES-ASCII-Map.ods`). Bleibt optional.
- **`cc65` fehlt** → `make` im `snes/`-Ordner scheitert kryptisch. Voraussetzung: `sudo apt install cc65`.
- **Pillow + PyYAML landen im `snes/tools/.venv`**, nicht im Haupt-`.venv`. Absichtlich getrennt, damit der Bridge-Host ohne Pillow läuft.

---

## 5. Wenn du als Agent hier etwas änderst

1. Host-Änderung an der Mapping-Semantik? → zwingend auch `snes/tools/gen_keymap.py` / `snes/src/main.asm` prüfen.
2. Neue Button-Kombo hinzugefügt? → `make` neu laufen lassen, `keymap.inc` neu generiert.
3. Neues Sonder-Command (`KEY_*`)? → drei Stellen: `mappings.yaml`, `gen_keymap.py:SPECIAL_ACTIONS`, `main.asm:@normal_tile`-Switch. Plus ggf. `input_capture.py:CURSES_KEY_MAP` und `mapper.py:CURSES_KEY_NAMES` für den Host-Eingabepfad.
4. Änderungen an `keyboard_mappings.yaml` brauchen **kein** ROM-Rebuild — sie betreffen nur die Host→Emulator-Übersetzung.
5. Vor jedem Commit: `python scripts/test_mapping.py` für eine Host-Sanity-Probe; für das ROM `cd snes && make` (muss sauber linken, `fix_checksum.py` läuft automatisch, Output-Byte-Count == 32768).
6. Änderungen am SNES-Header (`main.asm` → Segment `HEADER`): Tabelle in Abschnitt 2 aktuell halten und mit `xxd -s 0x7FC0 -l 64 snes/build/terminal.sfc` gegen die erzeugte Datei verifizieren.
