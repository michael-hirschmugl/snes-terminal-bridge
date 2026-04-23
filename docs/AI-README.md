# AI-README

Kurzreferenz für AI-Coding-Agents (und neue Contributor), die in diesem Repo arbeiten. Ergänzt `../README.md` (End-User-Sicht) und `../CLAUDE.md` (Status & Historie). Hier steht, **wie der Code denkt** — nicht, wie man ihn benutzt.

Für die Mode-5-Grafik-Details (Dense-Pack-Tileset-Layout, `N, N+1, N+16, N+17`-Auto-Read, Overscan, BG2-Tilemap) siehe die direkt aus Projekt `snes-tile-test` übernommene Referenz [`AI-MODE-5-README.md`](AI-MODE-5-README.md).

---

## 1. Architektur (high-level)

Das Projekt besteht aus **zwei unabhängigen, über eine YAML-Datei gekoppelten Teilen**. Auf der SNES-Seite existieren **zwei parallele Build-Pfade**, die sich denselben Input-/Debounce-/Keymap-Scan-Code teilen, aber unterschiedliche Grafik-Modi verwenden:

```
┌─────────────────────────────┐          ┌──────────────────────────────────┐
│  Host-Seite (Python)        │          │  SNES-Seite (65816 ASM)          │
│  snes_terminal_bridge/      │          │                                  │
│                             │          │  ROM A: src/main.asm             │
│  curses getch               │          │    Mode 1, BG1 4bpp, 8×16-Chars  │
│    → queue.Queue            │          │    32×14 Scroll-Grid, 2 Tiles/ch │
│    → mapper.lookup          │          │                                  │
│    → KeyboardInjector       │          │  ROM B: src/main_mode5.asm       │
│        (xdotool XTest)      │          │    Mode 5 + Interlace 512×448    │
│                             │          │    BG2 2bpp, 16×16 Dense-Pack    │
│           │  X11 Keystate   │          │    32×26 Scroll-Grid, 1 Tile/ch  │
│           └─► Emulator  ────┼──────────┼─► Joypad-Poll (bSNES+)           │
└─────────────────────────────┘          └──────────────────────────────────┘
                     ▲                                  ▲
                     │           single source          │
                     └──── config/mappings.yaml ────────┘
                          (ASCII → SNES-Button-Combo)
```

**Zentrale Invariante:** `config/mappings.yaml` ist die **einzige Wahrheit** für die Zuordnung ASCII → Button-Combo. Sie wird zur Laufzeit von der Bridge und zum Build-Zeitpunkt per `snes/tools/gen_keymap.py` in **zwei** SNES-Lookup-Tabellen übersetzt:

- `snes/assets/keymap.inc` für den Mode-1-Build (`tile = C*2`, Top-Tile; Bottom-Tile schreibt das ROM automatisch dazu).
- `snes/assets/keymap_mode5.inc` für den Mode-5-Build (`tile = (C//8)*32 + (C%8)*2`, Dense-Pack-VRAM-Base-Slot; die PPU liest `N, N+1, N+16, N+17` automatisch).

Beide Dateien werden in einem einzigen `gen_keymap.py`-Lauf erzeugt, damit die Tabellen nie divergieren. Host-Seite und beide ROMs müssen bitweise dieselbe Bitmask-Zuordnung haben; nur die Tile-Spalte unterscheidet sich.

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

Ein einziger Main-Loop, alle Entscheidungen im Frame-Raster. **Diese Schleife ist in `main.asm` und `main_mode5.asm` 1:1 identisch** — beide Pfade teilen sich Debounce, Boot-Guard, Dedupe und Keymap-Scan. Nur der „VRAM-Write"-Schritt unterscheidet sich:

```
@main_loop:
  wait VBlank → pending tile → VRAM write  ┐
  wait VBlank-end → wait auto-joypad        │  Mode 1: 2 Tiles (top+bottom) schreiben
  snapshot JOY1L/JOY1H                      │  Mode 5: 1 Tilemap-Word schreiben
  debounce (stable_cnt ≥ 2)                 ┘
  boot-guard (buttons=0 mindestens einmal gesehen)
  dedupe (last_trig_*)
  linear scan: keymap_data → pending_tile_{lo,hi} + pending_flag
```

Der Grund: in Mode 5 mit BG2 16×16-Tiles ließt die PPU vier 8×8-Subtiles
(`N, N+1, N+16, N+17`) pro Tilemap-Eintrag automatisch — ein einziger
16-Bit-Tilemap-Write pro Zeichen reicht. Damit entfällt die zweite
VRAM-Adressberechnung, und die Clear-Routine beim Newline hat nur noch
eine Section statt vier (Section-Boundary-Wrap ist in 32×32 nicht nötig).

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

- **Direct-Page-Variablen `$00–$0F`**: alle Zustandsflags und Cursor-Position liegen in DP. Layout ist zwischen Mode 1 und Mode 5 identisch — Kommentarblock oben in `main.asm` / `main_mode5.asm` ist die verbindliche Liste.
- **Pending-Write-Queue (1 Slot)**: `pending_flag` + `pending_tile_{lo,hi}`. Lookup im aktiven Teil des Frames, Write ausschließlich im VBlank. Kein DMA für einzelne Tiles, nur für Boot-Uploads.
- **Special-Action-Sentinels im High-Byte `$FF`**: `$FFFF` = DELETE, `$FFFE` = ENTER. In `@normal_tile` zuerst `pending_tile_hi == $FF` prüfen, dann Low-Byte unterscheiden. Gilt für beide ROMs identisch.
- **Mode 1: 32-row circular buffer + `BG1VOFS = top_vram_row * 16`**: das Tilemap ist 64×64, sichtbar sind 32×14. Scrollen heißt: `cursor_y` erhöhen (mod 32), `top_vram_row` nachziehen, neue Zeile in VRAM clearen (**4 Sections** wegen Screen-Boundary-Wrap bei Spalten 32/Zeilen 32).
- **Mode 5: 32-row circular buffer + `BG2VOFS = top_vram_row * 16`**: Tilemap ist 32×32 (eine Screen-Page reicht, weil 16×16-Tiles 32×26 = fast volle Höhe abdecken), sichtbar sind 32×26. Scrollen ist dieselbe Logik mit `VISIBLE_ROWS=26`. Neue Zeile clearen = **1 Section**, 32 Word-Writes — kein Boundary-Wrap.
- **Mode 5: Tilemap-Einträge sind VRAM-Slot-Indizes, nicht Char-Indizes**: Das Low-Byte des Tilemap-Worts ist `N(C) = (C//8)*32 + (C%8)*2`, nicht `C`. Das High-Byte trägt Flip/Palette/Priority (aktuell 0). `gen_keymap.py` liefert das fertig kodierte 16-Bit-Wort in `keymap_mode5.inc`, der ASM-Code stellt nur `pending_tile_{lo,hi}` aus der Lookup-Tabelle in VRAM — **keine** CPU-seitige Umrechnung.
- **Mode 5: Space rendert aus Tile-Slot 0**: Die Zero-Clear-DMA beim Reset setzt die gesamte Tilemap (Bereich `$1000..$17FF`) auf `$0000`. Tilemap-Index 0 zeigt auf die vier Sub-Tiles der Space-Glyphe (alle Bytes 0, weil `gen_font2.py` Space so kodiert). Deshalb ist **kein** dedizierter Blank-Index-Fill nötig; das ROM verlässt sich auf diese Invariante. Wer in `gen_font2.py` Space nicht-leer macht, zerstört den Boot-Bildschirm.
- **Debounce + Dedupe doppelt**: `stable_cnt ≥ 2` verhindert Frame-Rauschen, `last_trig_{lo,hi}` verhindert Auto-Repeat beim Gehalten-Halten derselben Kombo. Beide sind nötig.
- **Boot-Guard (`boot_ready`)**: ROM verwirft jegliche Eingabe, bis einmal alle Buttons = 0 war. Schutz gegen „hängenden Key aus Vorsession".
- **Mode 5: `TM = TS = $02`**: BG2 muss auf **Main- und Sub-Screen** gleichzeitig aktiv sein. Hi-Res teilt gerade/ungerade Pixelspalten zwischen beiden Screens auf; fehlt einer, sieht man nur jede zweite Spalte. Dasselbe gilt für das Interlace-Bit (`SETINI = $01`) — ohne bleibt die Auflösung bei 224 Zeilen, Glyphen wirken doppelt so hoch gestaucht.
- **A/X Register-Breitenwechsel explizit kommentieren** (`.a8` / `.a16`, `.i8` / `.i16`). Jeder `rep`/`sep` muss paarig sein, inkl. Sprünge aus dem breiten Bereich heraus.

### Build / Code-Gen

- **`snes/Makefile`** ist die einzige legitime Art, die ROMs zu bauen. Er erzeugt automatisch ein `tools/.venv` für `gen_font.py` / `gen_font2.py` / `gen_keymap.py` (Pillow + PyYAML). Die generierten Dateien in `snes/assets/` sind **nicht eingecheckt** — alle per `.gitignore` ausgeschlossen.

  Make-Targets:
  | Target | Output | Zweck |
  |---|---|---|
  | `make` / `make all` | `build/terminal.sfc` | **Mode 1 ROM** (Default, alter Pfad, unverändert) |
  | `make mode5` | `build/terminal_mode5.sfc` | **Mode 5 ROM** (neuer Hi-Res-Pfad, BG2 16×16) |
  | `make both` | beide | Parallel-Build, gleiche Asset-Regeneration |
  | `make font` | alle `*.inc` + `font*_preview.png` | Nur Asset-Regeneration |
  | `make run` / `make run-mode5` | startet im Emulator | |
  | `make clean` | löscht `build/` + `assets/*.inc` | |

  Die Asset-Generation ist **geteilt**: ein einziger `gen_keymap.py`-Lauf produziert `keymap.inc` + `keymap_mode5.inc`, beide ROMs linken ihre passende Variante. `gen_font.py` und `gen_font2.py` sind disjunkt (verschiedene Fonts, verschiedene Layouts) und liefern jeweils ihr eigenes `*.inc` + `*_preview.png`.

- **Generierte Dateien haben Header** (`; Auto-generated by tools/... — do not edit manually.`). Nie direkt patchen.
- **`gen_font.py` erzeugt drei Artefakte** (DejaVu Sans Mono, 8×16-Zellen, 4bpp — nur Mode 1):
  - `font.inc` — 4bpp-Tiles, **aktiv verwendet** (BG1 in Mode 1, per `.include` in `main.asm`).
  - `tilemap.inc` — 64×64 leere Tilemap-Einträge (alle `$0000`), per DMA beim Reset nach VRAM `$0000` geschrieben. Der initiale Bildschirm; ab dann füllt `main.asm` ihn zur Laufzeit per Tile-Writes direkt im VRAM.
  - `font_preview.png` — Kontaktbogen aller 95 Glyphen mit 16×16‑px‑Raster zur visuellen Inspektion. Reines Debug-Artefakt, kein Build-Input; wird nicht vom Makefile referenziert und darf jederzeit gelöscht werden. Parameter (`PREVIEW_SCALE`, `PREVIEW_GRID_PX`, Farbe) oben in `gen_font.py` als Konstanten.
- **`gen_font2.py` erzeugt zwei Artefakte** (JetBrains Mono Regular, `tools/fonts/JetBrainsMono-Regular.ttf`, 16×16-Zellen, 2bpp anti-aliased — **Mode 5, aktiv verwendet**):
  - `font2.inc` — 2bpp Font-Tiles in **Dense-Pack-VRAM-Order**. Pro Zeichen vier 8×8-Sub-Tiles (TL/TR/BL/BR) an VRAM-Slots `N, N+1, N+16, N+17` mit `N(C) = (C//8)*32 + (C%8)*2`. Das Script schreibt einen flachen 6144-Byte-`.byte`-Dump der gesamten VRAM-Region (384 Slots × 16 Bytes), kein Char-basiertes Layout. **Diese Reihenfolge ist Pflicht** — die PPU liest 16×16-BG-Tiles immer nach dem `N, N+1, N+16, N+17`-Muster, und das Keymap liefert genau diesen `N`-Wert. Details siehe [`AI-MODE-5-README.md`](AI-MODE-5-README.md).
  - `font2_preview.png` — Kontaktbogen aller 95 Glyphen mit 8-SNES-Pixel-Raster. Reines Debug-Artefakt, nicht Teil des Builds.
- **`gen_keymap.py` erzeugt zwei Artefakte** aus derselben `mappings.yaml`:
  - `keymap.inc` — Tile-Feld `C*2` (Mode-1-Format, Top-Tile-Index; Bottom wird in `main.asm` automatisch zu `C*2+1`).
  - `keymap_mode5.inc` — Tile-Feld `N(C) = (C//8)*32 + (C%8)*2` (Mode-5-Format, Dense-Pack-VRAM-Base-Slot).
  - Beide Dateien haben dieselben Bitmasks, dieselben `SPECIAL_ACTIONS`-Sentinels (`$FFFF`=DELETE, `$FFFE`=ENTER) und denselben Terminator-Word `$0000,$0000`. Nur das Tile-Byte unterscheidet sich.
- **Post-Link-Checksum-Patch**: nach `ld65` läuft zwingend `python3 tools/fix_checksum.py <rom>`. Der Linker kann die Checksumme nicht berechnen, weil sie sich selbst enthält — das Script setzt erst `complement=$FFFF`, `checksum=$0000`, summiert alle Bytes (mod `$10000`), schreibt `checksum` an `$FFDE/$FFDF` und `complement = checksum XOR $FFFF` an `$FFDC/$FFDD`. **Für beide ROMs** nötig (Mode 1 _und_ Mode 5), ohne lehnen Flash-Cartridges sie ab. Der Makefile ruft `fix_checksum.py` in beiden Link-Regeln explizit auf.

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

### Assembler (`main.asm` / `main_mode5.asm`)

- Labels: `snake_case` (`calc_addr_top`, `calc_tilemap_addr`, `boot_ready`).
- Lokale Branch-Targets: `@name` (z.B. `@main_loop`, `@wait_vblank`, `@do_delete`).
- Hardware-Register als `UPPER_SNAKE` auf File-Scope (`VMADDL`, `HVBJOY`, `JOY1L`). Stimmen mit den offiziellen SNES-Docs überein.
- DP-Variablen in `snake_case` mit kurzem, kommentarlich erklärtem Namen (`cursor_x`, `prev_joy_lo`, `pending_flag`). Jede neue DP-Variable **muss** in den Kommentarblock oben beider `main*.asm`-Dateien eingetragen werden — Layout ist synchron zu halten.
- Segmente großgeschrieben: `CODE`, `RODATA`, `HEADER`, `VECTORS` — definiert in `snes.cfg`. Beide ROMs nutzen denselben Linker-Config, denselben LoROM-Memory-Map und denselben `HEADER`/`VECTORS`-Aufbau (nur Titel und `.include`-Assets unterscheiden sich).
- Konstanten für den Mode-5-Layout-Block (`NUM_GLYPHS`, `FONT_BYTES`, `VISIBLE_ROWS`, `TILEMAP_WORD`) stehen oben in `main_mode5.asm` als `=`-Aliasse. Wer das Layout (Tilemap-Adresse, Font-Größe, sichtbare Zeilen) ändert, editiert **nur dort** — alle abhängigen DMA-Längen und Adressrechnungen referenzieren die Konstanten.

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

- **VRAM-Writes außerhalb VBlank** → Grafikkorruption. Alle Tile-Writes hinter `@wait_vblank` halten. Gilt für beide ROMs.
- **A/X-Registerbreite vergessen umzuschalten** → Stack-Korruption oder zufällige Hochbytes. Jeder `rep #$20` braucht `.a16` + späteres `sep #$20` + `.a8`. Analog `rep #$10` / `sep #$10` für X/Y.
- **Neue Special-Action vergessen in `gen_keymap.py:SPECIAL_ACTIONS` einzutragen** → wird in `@normal_tile` als gültiger Tile-Index behandelt und schreibt Schrott in VRAM. Sentinels leben im Bereich `$FF00–$FFFF` (High-Byte = `$FF`); neue Aktionen dort anhängen + in **beiden** `main*.asm`-Dateien den `@normal_tile`-Switch ergänzen. `gen_keymap.py` schreibt den Sentinel bereits in beide `.inc`-Dateien gleichermaßen; die ASM-Seite muss nachgezogen werden.
- **Keymap-Reihenfolge irrelevant, aber Sentinel `$0000,$0000` muss existieren** — `@scan_loop` stoppt sonst nie. `gen_keymap.py` schreibt ihn automatisch ans Ende beider Files.
- **Mode 1 — Scroll-Math bei Spalte 32 / Zeile 32**: Screen-Boundary-Bit (`+$0400` für Spalten ≥ 32, `+$0800` für Zeilen ≥ 32) leicht zu vergessen. Siehe `calc_addr_top` und die vier Clear-Sections in `@do_newline` von `main.asm`. **Nur Mode 1**.
- **Mode 5 — keine Boundary-Wrap**: Tilemap ist 32×32 = eine Screen-Page, `calc_tilemap_addr` macht `base + cursor_y*32 + cursor_x` ohne Extra-Bit. Die Clear-Zeile in `@do_newline` schreibt eine zusammenhängende 32-Word-Sequenz, kein 4-Section-Split. Wer Mode 1-Scroll-Code 1:1 portieren will, produziert Off-by-`$0400`-Fehler.
- **`cursor_y` ist (mod 32)** in beiden ROMs. In Mode 1 ist 14 das Viewport, in Mode 5 sind es 26 (`VISIBLE_ROWS` im Header). 32 ist in beiden Fällen der zirkuläre Puffer. Wer das mit der Viewport-Höhe verwechselt, berechnet `BG1VOFS`/`BG2VOFS` falsch.
- **Mode 5 — Tile-Bytes in Pending-Queue sind schon das komplette 16-Bit-Wort**: `pending_tile_lo` = `N(C)` (low byte), `pending_tile_hi` = attribute byte (aktuell 0). Kein `* 2`, kein `+ 1`, kein `OR` mit Palette-Bits im ASM. Alles was im Tilemap landen soll, muss vorher in `gen_keymap.py` kodiert werden. Dadurch bleibt der Hot-Path VRAM-Write ein einziger 16-Bit-Store.
- **Mode 5 — BG2SC/BG12NBA falsch** → black screen oder gescrambelte Tiles. Kanonische Werte: `BGMODE=$25` (Mode 5 + BG2 16×16), `BG2SC=$10` (Tilemap @ Word `$1000`, 32×32), `BG12NBA=$00` (beide Char-Bases bei 0). Wenn das Char-Base von BG2 verschoben wird, muss `FONT_BYTES` und die Tile-Upload-Adresse in `main_mode5.asm` mit.
- **Mode 5 — `TM`/`TS` dürfen BG2 nicht nur auf einem Screen haben**: Hi-Res verlangt BG2 auf Main **und** Sub. `TM=TS=$02`. Wer nur `TM=$02` setzt, sieht jede zweite Pixelspalte schwarz.
- **Mode 5 — Space ist kein Leerzeichen-Code-Pfad, sondern Tile-Slot 0**: Wer Space „spart" und aus `font2.inc` entfernt, zerstört die Boot-Clear-Invariante (siehe Abschnitt 2, „Mode 5: Space rendert aus Tile-Slot 0"). Slot 0 muss vier Null-Sub-Tiles enthalten.

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

1. Host-Änderung an der Mapping-Semantik? → zwingend auch `snes/tools/gen_keymap.py` / `snes/src/main.asm` **und** `snes/src/main_mode5.asm` prüfen.
2. Neue Button-Kombo hinzugefügt? → `cd snes && make both` neu laufen lassen, beide Keymaps (`keymap.inc` + `keymap_mode5.inc`) werden in einem Lauf regeneriert.
3. Neues Sonder-Command (`KEY_*`)? → **vier** Stellen: `mappings.yaml`, `gen_keymap.py:SPECIAL_ACTIONS`, `main.asm:@normal_tile`-Switch **und** `main_mode5.asm:@normal_tile`-Switch. Plus ggf. `input_capture.py:CURSES_KEY_MAP` und `mapper.py:CURSES_KEY_NAMES` für den Host-Eingabepfad. Der `SPECIAL_ACTIONS`-Eintrag ist geteilt zwischen beiden `.inc`-Files, ein Eintrag genügt.
4. Änderungen an `keyboard_mappings.yaml` brauchen **kein** ROM-Rebuild — sie betreffen nur die Host→Emulator-Übersetzung.
5. Vor jedem Commit: `python scripts/test_mapping.py` für eine Host-Sanity-Probe; für die ROMs `cd snes && make both` (beide müssen sauber linken, `fix_checksum.py` läuft für beide automatisch, Mode-1-Output == 32768 Bytes, Mode-5-Output == 32768 Bytes).
6. Änderungen am SNES-Header (`main.asm` oder `main_mode5.asm` → Segment `HEADER`): Tabelle in Abschnitt 2 aktuell halten und mit `xxd -s 0x7FC0 -l 64 snes/build/<rom>.sfc` gegen die erzeugte Datei verifizieren. Beide ROMs haben eigenständige Header (unterschiedlicher Titel: „SNES TERMINAL" vs. „SNES TERMINAL MODE 5").
7. Mode-5-Layout-Änderung (VRAM-Adressen, Dense-Pack-Formel, Interlace-Flag, 16×16-Read-Pattern)? → **immer** zuerst [`AI-MODE-5-README.md`](AI-MODE-5-README.md) lesen. Diese Datei dokumentiert das PPU-Verhalten, auf dem `gen_font2.py` + `gen_keymap.py` + `main_mode5.asm` aufsetzen. Änderungen müssen zu den dort beschriebenen Invarianten passen.
