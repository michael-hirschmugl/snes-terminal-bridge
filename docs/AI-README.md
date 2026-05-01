# AI-README

Kurzreferenz für AI-Coding-Agents (und neue Contributor), die in diesem Repo arbeiten. Ergänzt `../README.md` (End-User-Sicht) und `../CLAUDE.md` (Status & Historie). Hier steht, **wie der Code denkt** — nicht, wie man ihn benutzt.

Für die Mode-5-Grafik-Details (Dense-Pack-Tileset-Layout, `N, N+1, N+16, N+17`-Auto-Read, Overscan, BG2-Tilemap) siehe [`AI-MODE-5-README.md`](AI-MODE-5-README.md).

---

## 1. Architektur (high-level)

Das Projekt besteht aus **zwei unabhängigen, über eine YAML-Datei gekoppelten Teilen**. Die SNES-Seite hat genau **einen** Rendering-Pfad: SNES Mode 5 + Interlace, BG2 2bpp, 16×16 Dense-Pack.

```
┌─────────────────────────────┐          ┌──────────────────────────────────┐
│  Host-Seite (Python)        │          │  SNES-Seite (65816 ASM)          │
│  snes_terminal_bridge/      │          │                                  │
│                             │          │  ROM: src/main.asm               │
│  curses getch               │          │    Mode 5 + Interlace 512×448    │
│    → queue.Queue            │          │    BG2 2bpp, 16×16 Dense-Pack    │
│    → mapper.lookup          │          │    30×26 Grid (16px Rand), 1T/Ch │
│    → KeyboardInjector       │          │                                  │
│        (xdotool XTest)      │          │                                  │
│           │  X11 Keystate   │          │                                  │
│           └─► Emulator  ────┼──────────┼─► Joypad-Poll (bSNES+)           │
└─────────────────────────────┘          └──────────────────────────────────┘
                     ▲                                  ▲
                     │           single source          │
                     └──── config/mappings.yaml ────────┘
                          (ASCII → SNES-Button-Combo)
```

**Zentrale Invariante:** `config/mappings.yaml` ist die **einzige Wahrheit** für die Zuordnung ASCII → Button-Combo. Sie wird zur Laufzeit von der Bridge und zum Build-Zeitpunkt per `snes/tools/gen_keymap.py` in `snes/assets/keymap.inc` übersetzt (`tile = (C//8)*32 + (C%8)*2`, Dense-Pack-VRAM-Base-Slot; die PPU liest `N, N+1, N+16, N+17` automatisch).

Host-Seite und ROM müssen bitweise dieselbe Bitmask-Zuordnung haben.

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
  wait VBlank
  cursor erase  (write 0,0 to current cursor cell — before pending tile moves cursor_x/y)
  pending tile  → VRAM write  ┐  1 Tilemap-Word (16 bit) schreiben
  cursor draw   (blink_ctr bit 5 = 0: write CURSOR_TILE to new cursor_x/y)
  wait VBlank-end → wait auto-joypad        │  — die PPU liest N, N+1, N+16, N+17
  snapshot JOY1L/JOY1H                      │  automatisch.
  debounce (stable_cnt ≥ 2)                 ┘
  boot-guard (buttons=0 mindestens einmal gesehen)
  dedupe (last_trig_*)
  linear scan: keymap_data → pending_tile_{lo,hi} + pending_flag
```

Weil BG2 16×16-Tiles vier 8×8-Subtiles (`N, N+1, N+16, N+17`) pro Tilemap-Eintrag automatisch liest, reicht ein einziger 16-Bit-Tilemap-Write pro Zeichen. Die Clear-Routine beim Newline schreibt genau eine 32-Word-Section (kein Boundary-Wrap, da Tilemap 32×32).

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

- **Direct-Page-Variablen `$00–$10`**: alle Zustandsflags, Cursor-Position und Blink-Counter liegen in DP. Der Kommentarblock oben in `main.asm` ist die verbindliche Liste. `blink_ctr` ($10) ist die zuletzt hinzugefügte Variable; die Init-Zero-Schleife deckt jetzt `$00–$10` ab.
- **Pending-Write-Queue (1 Slot)**: `pending_flag` + `pending_tile_{lo,hi}`. Lookup im aktiven Teil des Frames, Write ausschließlich im VBlank. Kein DMA für einzelne Tiles, nur für Boot-Uploads.
- **Special-Action-Sentinels im High-Byte `$FF`**: `$FFFF` = DELETE, `$FFFE` = ENTER. In `@normal_tile` zuerst `pending_tile_hi == $FF` prüfen, dann Low-Byte unterscheiden.
- **32-row circular buffer + `BG2VOFS = (top_vram_row * 16 - 16) & $1FF`**: Tilemap ist 32×32, sichtbar 30×26 (Spalten 1–30, `LEFT_COL=1`/`RIGHT_COL=30`; Spalten 0 und 31 immer leer = linker/rechter 16px-Rand). Das `−16` im BG2VOFS verschiebt die Anzeige um 1 Tile nach unten: Tilemap-Zeile 31 (nie beschrieben) erscheint bei Screen-Y=0–15 als oberer Rand. Beim Scrollen: `top_vram_row` nachziehen, **alte** `top_vram_row`-Zeile clearen (wird neue Rand-Zeile), dann neue `cursor_y`-Zeile clearen (**je 1 Section**, 32 Word-Writes — kein Boundary-Wrap). Unterer Rand: Zeile `top_vram_row + 26` wird nie beschrieben → Screen-Y=432–447 bleibt leer.
- **Tilemap-Einträge sind VRAM-Slot-Indizes, nicht Char-Indizes**: Das Low-Byte des Tilemap-Worts ist `N(C) = (C//8)*32 + (C%8)*2`, nicht `C`. Das High-Byte trägt Flip/Palette/Priority; für Textzeichen $3C oder $3D (Priority=1, Sub-Palette=7) — vorcodiert in `gen_keymap.py`, **kein** CPU-seitiges OR im Hot-Path. `gen_keymap.py` liefert das fertig kodierte 16-Bit-Wort in `keymap.inc`, der ASM-Code stellt nur `pending_tile_{lo,hi}` aus der Lookup-Tabelle in VRAM — **keine** CPU-seitige Umrechnung.
- **Space rendert aus Tile-Slot 0**: Die Zero-Clear-DMA beim Reset setzt die gesamte Tilemap (Bereich `$1000..$17FF`) auf `$0000`. Tilemap-Index 0 zeigt auf die vier Sub-Tiles der Space-Glyphe (alle Bytes 0, weil `gen_font.py` Space so kodiert). Deshalb ist **kein** dedizierter Blank-Index-Fill nötig; das ROM verlässt sich auf diese Invariante. Wer in `gen_font.py` Space nicht-leer macht, zerstört den Boot-Bildschirm.
- **Debounce + Dedupe doppelt**: `stable_cnt ≥ 2` verhindert Frame-Rauschen, `last_trig_{lo,hi}` verhindert Auto-Repeat beim Gehalten-Halten derselben Kombo. Beide sind nötig.
- **Boot-Guard (`boot_ready`)**: ROM verwirft jegliche Eingabe, bis einmal alle Buttons = 0 war. Schutz gegen „hängenden Key aus Vorsession".
- **`TM = TS = $03`**: Beide BG-Layer (BG1 Retro-Rahmen + BG2 Text) müssen auf **Main- und Sub-Screen** gleichzeitig aktiv sein. Hi-Res teilt gerade/ungerade Pixelspalten zwischen beiden Screens auf; fehlt einer, sieht man nur jede zweite Spalte. Dasselbe gilt für das Interlace-Bit (`SETINI = $01`) — ohne bleibt die Auflösung bei 224 Zeilen, Glyphen wirken doppelt so hoch gestaucht.
- **A/X Register-Breitenwechsel explizit kommentieren** (`.a8` / `.a16`, `.i8` / `.i16`). Jeder `rep`/`sep` muss paarig sein, inkl. Sprünge aus dem breiten Bereich heraus.

### Build / Code-Gen

- **`snes/Makefile`** ist die einzige legitime Art, das ROM zu bauen. Er erzeugt automatisch ein `tools/.venv` für `gen_font.py` / `gen_keymap.py` (Pillow + PyYAML). Die generierten Dateien in `snes/assets/` sind **nicht eingecheckt** — alle per `.gitignore` ausgeschlossen.

  Make-Targets:
  | Target | Output | Zweck |
  |---|---|---|
  | `make` / `make all` | `build/terminal.sfc` | Das Mode-5-ROM (64 KiB 2-Bank-LoROM, PAL) |
  | `make font` | alle `*.inc` + `font_preview.png` | Nur Asset-Regeneration |
  | `make run` | startet ROM in bsnes | |
  | `make clean` | löscht `build/` + `assets/*.inc` | |

- **Generierte Dateien haben Header** (`; Auto-generated by tools/... — do not edit manually.`). Nie direkt patchen.
- **`gen_font.py` erzeugt zwei Artefakte** (JetBrains Mono Regular, `tools/fonts/JetBrainsMono-Regular.ttf`, 16×16-Zellen, 2bpp anti-aliased):
  - `font.inc` — 2bpp Font-Tiles in **Dense-Pack-VRAM-Order**. Pro Zeichen vier 8×8-Sub-Tiles (TL/TR/BL/BR) an VRAM-Slots `N, N+1, N+16, N+17` mit `N(C) = (C//8)*32 + (C%8)*2`. Das Script schreibt einen flachen 6144-Byte-`.byte`-Dump der gesamten VRAM-Region (384 Slots × 16 Bytes), kein Char-basiertes Layout. **Diese Reihenfolge ist Pflicht** — die PPU liest 16×16-BG-Tiles immer nach dem `N, N+1, N+16, N+17`-Muster, und das Keymap liefert genau diesen `N`-Wert. Details siehe [`AI-MODE-5-README.md`](AI-MODE-5-README.md).
  - **Horizontale Streckung:** `RENDER_W` (default `0` = auto-detect via `font.getlength("M")`, ergibt ~10 px bei Größe 16) bestimmt die Crop-Breite vor dem Skalieren auf `CELL_W=16`. Jede Glyphe wird so von ihrer natürlichen Advance-Width auf die volle Zellbreite gestreckt (LANCZOS). Größerer `RENDER_W` = weniger Stretch (mehr Rand bleibt); kleinerer = stärkere Streckung.
  - `font_preview.png` — Kontaktbogen aller 95 Glyphen mit 8-SNES-Pixel-Raster. Reines Debug-Artefakt, nicht Teil des Builds.
- **`gen_border.py`** erzeugt die BG1-Rahmen-Daten programmatisch (kein Quell-PNG):
  - `build/mode5_border_4bpp/palette.bin` — 32 Bytes (16 BGR555-Farben, BG1 Sub-Palette 0; 9 genutzt: Schwarz, 4 Blautöne, 4 Goldtöne/Weiß)
  - `build/mode5_border_4bpp/tiles.4bpp.chr` — 1024 Bytes (32 Slots × 32 Bytes): 4 unique 16×16 Super-Tiles (corner, h-edge, v-edge, blank) in Dense-Pack-Reihenfolge; Slots 8–15 und 24–31 leer (Null-Tiles)
  - `build/mode5_border_4bpp/tilemap.bin` — 2048 Bytes (32×32 Einträge): Ecken mit H/V-Flip-Flags wiederverwendet, Innenbereich blank
  - `build/mode5_border_4bpp/preview.png` — 2× gerendertes Preview (Pillow, optional)
  - Super-Tile-Layout: k=0 (corner, VRAM-Base 0), k=1 (h-edge, Base 2), k=2 (v-edge, Base 4), k=3 (blank, Base 6). H-Flip für rechte Kante/Ecken, V-Flip für untere Kante/Ecken.
- **`gen_assets.py`** (aus snes-tile-test übernommen): PNG → 4bpp BG1-Daten via `mode5_image`-Pipeline — **nicht mehr Teil des Haupt-Builds**, verfügbar für Dev-Zwecke (z. B. Wallpaper-Experimente):
  - Ausgabe nach `build/mode5_wallpaper_4bpp/` (oder `--name`)
  - `crop_image.py` ist ein Hilfsmodul für Skalierung/Crop, das von `gen_assets.py` importiert wird.
- **`gen_keymap.py` erzeugt ein Artefakt** aus `mappings.yaml`:
  - `keymap.inc` — Tilemap-Wort `N(C) = (C//8)*32 + (C%8)*2 | $3C00` (Dense-Pack-VRAM-Slot + Priority=1 + Sub-Palette=7). Enthält pro Mapping-Eintrag `.word bitmask, .word tile_word`, mit `SPECIAL_ACTIONS`-Sentinels (`$FFFF`=DELETE, `$FFFE`=ENTER) und Terminator-Word `$0000,$0000`.
- **`gen_welcome.py` erzeugt ein Artefakt** aus `config/welcome.ini` (Plain-Text, `;`/`#`-Kommentarzeilen werden übersprungen, Leerzeilen → leere Bildschirmzeile):
  - `assets/welcome.inc` — Sequenz von `.word`-Einträgen: pro Zeichen `0x3C00 | N(C)` (dasselbe Tilemap-Wort wie `keymap.inc`), `$FFFF` als Zeilenvorschub-Marker, `$0000` als End-Sentinel. Werden zur Init-Zeit von `print_welcome_msg` direkt (ohne VBlank-Queue) in den BG2-Tilemap-VRAM geschrieben, bevor `INIDISP = $0F` die Anzeige aktiviert.
  - Grenzen (hartes `sys.exit` bei Überschreitung): max. 26 Zeilen (`VISIBLE_ROWS`), max. 30 Zeichen pro Zeile (`USABLE_COLS`), nur ASCII `0x20–0x7E`.
  - Make-Abhängigkeit: `$(WELCOME)` hängt von `../config/welcome.ini` ab, sodass `make` bei einer Änderung der Nachricht automatisch neu baut.
- **Post-Link-Checksum-Patch**: nach `ld65` läuft zwingend `python3 tools/fix_checksum.py <rom>`. Der Linker kann die Checksumme nicht berechnen, weil sie sich selbst enthält — das Script setzt erst `complement=$FFFF`, `checksum=$0000`, summiert alle Bytes (mod `$10000`), schreibt `checksum` an `$FFDE/$FFDF` und `complement = checksum XOR $FFFF` an `$FFDC/$FFDD`. Ohne den Patch lehnen Flash-Cartridges das ROM ab. Der Makefile koppelt den Schritt ans Linken — **nicht** entfernen oder nur einzeln `ld65` aufrufen.

### SNES-Header (Pflichtfelder, `main.asm` → Segment `HEADER`)

Header liegt bei LoROM immer bei `$FFC0–$FFDF` (File-Offset `$7FC0–$7FDF` im 32 KiB-Image). Jede Änderung muss geprüft bleiben — Werte sind für echte Hardware kritisch:

| Offset | Feld | Wert | Bedeutung |
|---|---|---|---|
| `$FFC0–$FFD4` | Title | 21 B ASCII, space-padded | „`SNES TERMINAL+BORDER `" |
| `$FFD5` | Map mode | `$20` | LoROM + SlowROM |
| `$FFD6` | Cartridge type | `$00` | nur ROM, keine Co-Prozessoren |
| `$FFD7` | ROM size | `$08` | Everdrive-Mapping: `$08` → „512k" (korrekt); `$05` → „8m" → ROM landet an falscher Adresse (schwarzer Bildschirm). S-CPU ignoriert dieses Byte. |
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

- Labels: `snake_case` (`calc_tilemap_addr`, `boot_ready`, `font_tiles`).
- Lokale Branch-Targets: `@name` (z.B. `@main_loop`, `@wait_vblank`, `@do_delete`).
- Hardware-Register als `UPPER_SNAKE` auf File-Scope (`VMADDL`, `HVBJOY`, `JOY1L`). Stimmen mit den offiziellen SNES-Docs überein.
- DP-Variablen in `snake_case` mit kurzem, kommentarlich erklärtem Namen (`cursor_x`, `prev_joy_lo`, `pending_flag`). Jede neue DP-Variable **muss** in den Kommentarblock oben von `main.asm` eingetragen werden.
- Segmente großgeschrieben: `CODE`, `RODATA`, `HEADER`, `VECTORS` — definiert in `snes.cfg`.
- Konstanten für den Mode-5-Layout-Block (`NUM_GLYPHS`, `FONT_BYTES`, `VISIBLE_ROWS`, `TILEMAP_WORD`) stehen oben in `main.asm` als `=`-Aliasse. Wer das Layout (Tilemap-Adresse, Font-Größe, sichtbare Zeilen) ändert, editiert **nur dort** — alle abhängigen DMA-Längen und Adressrechnungen referenzieren die Konstanten.

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
- **Neue Special-Action vergessen in `gen_keymap.py:SPECIAL_ACTIONS` einzutragen** → wird in `@normal_tile` als gültiger Tile-Index behandelt und schreibt Schrott in VRAM. Sentinels leben im Bereich `$FF00–$FFFF` (High-Byte = `$FF`); neue Aktionen dort anhängen + in `main.asm` den `@normal_tile`-Switch ergänzen.
- **Keymap-Reihenfolge irrelevant, aber Sentinel `$0000,$0000` muss existieren** — `@scan_loop` stoppt sonst nie. `gen_keymap.py` schreibt ihn automatisch ans Ende.
- **`cursor_y` ist (mod 32)**. Viewport sind 26 Zeilen (`VISIBLE_ROWS` im Header), 32 ist der zirkuläre Puffer. Wer das mit der Viewport-Höhe verwechselt, berechnet `BG2VOFS` falsch.
- **`cursor_x`-Init muss nach dem WRAM-DMA-Clear stehen**, nicht davor. Der DMA-Clear überschreibt alle WRAM-Adressen (inklusive Direct-Page $0000 = `cursor_x`) mit Null. Wer `cursor_x = LEFT_COL` vor dem DMA-Clear setzt, bekommt `cursor_x = 0` in der ersten Zeile (alle weiteren Zeilen sind korrekt, weil der Newline-Handler `cursor_x` explizit setzt).
- **Tile-Bytes in der Pending-Queue sind schon das komplette 16-Bit-Wort**: `pending_tile_lo` = low byte von `N(C) | $3C00`, `pending_tile_hi` = high byte ($3C/$3D für Textzeichen, $FF für Sentinels). Kein `* 2`, kein `+ 1`, kein `OR` mit Palette-Bits im ASM. Alles was im Tilemap landen soll, muss vorher in `gen_keymap.py` kodiert werden. Dadurch bleibt der Hot-Path VRAM-Write ein einziger 16-Bit-Store.
- **BG-Register falsch gesetzt** → black screen oder gescrambelte Tiles. Kanonische Werte: `BGMODE=$35` (Mode 5 + BG1 16×16 + BG2 16×16), `BG1SC=$50` (BG1 Tilemap @ Word `$5000`, 32×32), `BG2SC=$10` (BG2 Tilemap @ Word `$1000`, 32×32), `BG12NBA=$02` (BG2 Char-Base @ Word `$0000`, BG1 Char-Base @ Word `$2000`). BG1 enthält jetzt den Retro-Rahmen (gen_border.py, 1024 Bytes statt 24576 Bytes Wallpaper); das BG12NBA-Feld und die Tilemap-Base bleiben unverändert. Wenn das Char-Base von BG2 verschoben wird, muss `FONT_BYTES` und die Tile-Upload-Adresse in `main.asm` mit.
- **`TM`/`TS` dürfen BG-Layer nicht nur auf einem Screen haben**: Hi-Res verlangt jeden aktiven Layer auf Main **und** Sub. `TM=TS=$03` (BG1+BG2). Wer nur `TM=$03` setzt, sieht jede zweite Pixelspalte schwarz.
- **Space ist kein Leerzeichen-Code-Pfad, sondern Tile-Slot 0**: Wer Space „spart" und aus `font.inc` entfernt, zerstört die Boot-Clear-Invariante (siehe Abschnitt 2, „Space rendert aus Tile-Slot 0"). Slot 0 muss vier Null-Sub-Tiles enthalten.

### ROM-Header / Hardware

- **Checksum-Patch nicht überspringen**: `ld65` schreibt Platzhalter (`$0000` / `$FFFF`). Ohne `fix_checksum.py` läuft das ROM zwar im Emulator und auf dem nackten S-CPU, aber Flash-Carts validieren den Header vor dem Mount und weisen es ab. Der Makefile-Target koppelt den Schritt ans Linken — **nicht** entfernen oder nur einzeln `ld65` aufrufen.
- **ROM-Size-Byte (`$FFD7`) = `$08`, nicht `$05`**: Das Everdrive verwendet dieses Byte, um die LoROM-Adressierung einzurichten. Mit `$05` (= 32 KiB nach SNES-Spec) klassifiziert das Everdrive das ROM als „8 Mbit" und legt es an die falsche Stelle im Adressraum — schwarzer Bildschirm. Mit `$08` wählt das Everdrive das „512k"-Mapping, das 32-KiB-ROMs korrekt spiegelt. Der S-CPU selbst ignoriert dieses Byte. Faustregel für LoROM-Homebrew auf Everdrive: `$08` verwenden, auch wenn `$05` laut Spec korrekt wäre.
- **Destination-Code (`$FFD9`) ist real wirksam**: `$02` markiert die ROM als PAL; ein PAL-SNES bootet NTSC-ROMs gar nicht ohne Region-Mod. Umgekehrt zeigen PAL-ROMs auf NTSC-Konsolen oft Bildfehler (falsche VBlank-Länge).
- **Kein SRAM**: Header-Feld `$FFD8 = $00` und `cartridge type $FFD6 = $00`. Die ROM speichert nichts persistent; `.srm`-Dateien der Emulatoren sind leer/unbenutzt und können bedenkenlos gelöscht werden. Wer SRAM ergänzen will, muss **beide** Felder ändern **und** echte SRAM-Lese-/Schreib-Logik in `main.asm` implementieren.
- **PPU-Register auf echter Hardware undefiniert**: Emulatoren (bsnes) initialisieren alle PPU-Register auf 0; echte Hardware lässt sie undefiniert. Fehlende `stz`-Initialisierungen können auf echter Hardware einen schwarzen Bildschirm verursachen, der im Emulator nie auftritt. Kritisch für Mode 5 mit `TM=TS=$02` sind insbesondere:
  - `CGADSUB ($2131)`: Bit 7=1 (Subtraktion) + Bit 1=1 (BG2) → BG2_main − BG2_sub = 0 = schwarz (beide Screens zeigen dasselbe BG2).
  - `TMW ($212E)`: Bit 1=1 → BG2 auf Main-Screen durch Window maskiert.
  - `TSW ($212F)`, `CGWSEL ($2130)`, `W12SEL ($2123)`, `W34SEL ($2124)`: ebenfalls auf 0 setzen.
  
  Das ROM initialisiert alle diese Register explizit — nie entfernen.

### Environment / Tooling

- **`.venv` zwischen Maschinen kopiert** → paths stimmen nicht, subtile Fehler. Immer `python3 -m venv .venv` neu aufsetzen.
- **`odfpy` in Hauptanforderungen aufnehmen** → unnötige Laufzeit-Dep. Gehört nur zu `scripts/convert_ods.py` (One-Shot-Import von `SNES-ASCII-Map.ods`). Bleibt optional.
- **`cc65` fehlt** → `make` im `snes/`-Ordner scheitert kryptisch. Voraussetzung: `sudo apt install cc65`.
- **Pillow + PyYAML landen im `snes/tools/.venv`**, nicht im Haupt-`.venv`. Absichtlich getrennt, damit der Bridge-Host ohne Pillow läuft.

---

## 5. Geplante Erweiterungen (SNES ROM)

Diese Features sind noch nicht implementiert. Vor der Umsetzung die Auswirkungen auf die gesamte Pipeline (gen_font.py → gen_keymap.py → main.asm) durchdenken.

### ~~8×16-Zeichenzellen~~ — Dead End (nicht umsetzbar in Mode 5)

Versucht in Branch `experiment/left-align-font` (Commit `24cfa25`). Ergebnis: In Mode 5 sind BG2-Tiles immer **lo-res** — jedes 8×8-Tile rendert 16 px breit auf dem Bildschirm, unabhängig von der Tile-Größe. `BGMODE $15` (BG2 8×8) statt `$35` (BG2 16×16) liefert weiterhin nur 30 sichtbare Spalten, genauso viele wie der aktuelle 16×16-Ansatz, aber ohne die Hi-Res-Anti-Aliasing-Schärfe. Ein 64-Spalten-Grid ist mit BG2 in Mode 5 physisch nicht erreichbar. **Nicht nochmal versuchen.**

### ~~Overscan-Beschnitt oben beheben~~ ✅ Implementiert (2026-04-26)

`BG2VOFS` wird dauerhaft um −16 versetzt (`BG2VOFS = top_vram_row * 16 − 16`, 9-Bit-Maske). Tilemap-Zeile 31 (nie beschrieben) erscheint bei Screen-Y=0–15. Spalten 0 und 31 werden nie beschrieben → linker/rechter Rand. Beim Scrollen wird die alte `top_vram_row` als neue Rand-Zeile geleert. `cursor_x` startet nach WRAM-DMA-Clear bei `LEFT_COL=1` (nicht nach dem DP-Nullsetzen — das würde der DMA-Clear überschreiben).

### ~~Cursor~~ ✅ Implementiert (2026-05-01)

Blinkendes `_`-Zeichen (Unterstrich, ASCII 95, Tile $EE/$3C) an der aktuellen Eingabeposition.

**Implementierung:**
- Neues DP-Variable `blink_ctr` ($10), von der Init-Nullschleife abgedeckt.
- **Erase-before-draw-Muster** (verhindert Geisterzeichen bei Delete/Newline): Am Anfang jedes VBlanks — *bevor* das Pending Tile `cursor_x/cursor_y` verändert — wird die aktuelle Cursor-Zelle mit `0,0` überschrieben. Danach läuft der normale Pending-Tile-Pfad. Am `@no_pending` wird `blink_ctr` inkrementiert; wenn Bit 5 = 0 (32-Frame-Phase → ~1 Hz bei PAL 50 fps), wird `CURSOR_TILE_LO/HI` ($EE/$3C) in die Tilemap-Zelle an `(cursor_x, cursor_y)` geschrieben.
- Kein Extra-Puffer, keine NMI nötig — alles im bestehenden VBlank-Poll-Raster.

### ~~Willkommensnachricht~~ ✅ Implementiert (2026-05-01)

Beim Boot zeigt das ROM den Inhalt von `config/welcome.ini` an (bis zu 26 Zeilen, 30 Zeichen je).

**Implementierung:**
- `snes/tools/gen_welcome.py` liest die `.ini`, validiert, und erzeugt `snes/assets/welcome.inc` mit `.word`-Einträgen: je ein Tilemap-Wort pro Zeichen (`0x3C00 | N(C)`), `$FFFF` als Zeilenvorschub-Marker, `$0000` als End-Sentinel.
- `print_welcome_msg` (65816-Subroutine in `main.asm`) läuft in der Init-Sequenz nach dem DMA-Upload, aber *vor* `INIDISP = $0F` (Bildschirm noch geblankt). Sie liest `welcome_data` mit 16-Bit-X-Index, schreibt jedes Tilemap-Wort direkt per `VMADDL/VMDATAL/VMDATAH` an die mit `calc_tilemap_addr` berechnete VRAM-Adresse — keine VBlank-Queue, weil der Screen geblankt ist.
- Prozessor-Zustand: Eintritt A=8-Bit/X=8-Bit; Routine schaltet intern auf X=16-Bit (wie `@do_lookup`); Rückkehr A=8-Bit/X=8-Bit.
- `cursor_x/cursor_y` zeigen nach der Routine auf die erste freie Zeile; der `@main_loop` beginnt dort direkt.

### Zeileneingabe-Puffer

**Ziel:** Eingetippte Zeichen lokal im ROM-Puffer akkumulieren und erst beim Enter die Zeile in die sichtbare Tilemap übertragen. Das ermöglicht In-Line-Editieren (Backspace, Cursor-Bewegung) vor dem Submit.

**Ansatz:** Separater WRAM-Puffer (z. B. 64 Bytes bei `$7E0100`) für die aktuelle Eingabezeile. Render-Pfad schreibt Zeichen in den Puffer und gleichzeitig temporär in die Tilemap (Live-Vorschau). Bei Backspace: Puffer und Tilemap-Eintrag gemeinsam zurücksetzen. Bei Enter: Puffer in die „committed"-Tilemap-Zeile übernehmen, neue Zeile beginnen, Puffer leeren.

### Terminal-Prompt

**Ziel:** Prompt-String (z. B. `> `) am Anfang jeder neuen Eingabezeile, bevor der Cursor erscheint.

**Ansatz:** Nach Enter / Zeilenvorschub die Prompt-Zeichen automatisch in Tilemap schreiben (gleicher Pfad wie Willkommensnachricht) und `cursor_x` hinter den Prompt-End setzen. Prompt-Länge als Konstante in `main.asm` führen.

---

## 6. Wenn du als Agent hier etwas änderst

1. Host-Änderung an der Mapping-Semantik? → zwingend auch `snes/tools/gen_keymap.py` und `snes/src/main.asm` prüfen.
2. Neue Button-Kombo hinzugefügt? → `cd snes && make` neu laufen lassen, `keymap.inc` wird regeneriert.
3. Neues Sonder-Command (`KEY_*`)? → **drei** Stellen: `mappings.yaml`, `gen_keymap.py:SPECIAL_ACTIONS` und `main.asm:@normal_tile`-Switch. Plus ggf. `input_capture.py:CURSES_KEY_MAP` und `mapper.py:CURSES_KEY_NAMES` für den Host-Eingabepfad.
4. Änderungen an `keyboard_mappings.yaml` brauchen **kein** ROM-Rebuild — sie betreffen nur die Host→Emulator-Übersetzung.
5. Vor jedem Commit: `python scripts/test_mapping.py` für eine Host-Sanity-Probe; für das ROM `cd snes && make` (muss sauber linken, `fix_checksum.py` läuft automatisch, Output == 65536 Bytes).
6. Änderungen am SNES-Header (`main.asm` → Segment `HEADER`): Tabelle in Abschnitt 2 aktuell halten und mit `xxd -s 0x7FC0 -l 64 snes/build/terminal.sfc` gegen die erzeugte Datei verifizieren. Das ROM ist 64 KiB (2 Bänke): `CODE`/`RODATA` → ROM0 (Bank 0, `$8000–$FFFF`), `RODATA1` → ROM1 (Bank 1, `$18000–$1FFFF`). Große statische Daten (BG1-Tiles, BG1-Tilemap) gehören in `RODATA1`; DMA aus Bank 1 setzt Source-Bank-Byte auf `^label` (= `$01`).
7. Mode-5-Layout-Änderung (VRAM-Adressen, Dense-Pack-Formel, Interlace-Flag, 16×16-Read-Pattern)? → **immer** zuerst [`AI-MODE-5-README.md`](AI-MODE-5-README.md) lesen. Diese Datei dokumentiert das PPU-Verhalten, auf dem `gen_font.py` + `gen_keymap.py` + `main.asm` aufsetzen. Änderungen müssen zu den dort beschriebenen Invarianten passen.
8. Eines der geplanten Features aus Abschnitt 5 umsetzen? → Vor der Umsetzung Abschnitt 5 lesen; das 8×16-Feature ist ein dokumentierter Dead End und darf nicht nochmal versucht werden.
9. **Welcome-Message geändert** (`config/welcome.ini` editiert)? → `cd snes && make` reicht — die Makefile-Abhängigkeit auf `../config/welcome.ini` triggert `gen_welcome.py` automatisch. Kein manuelles `make font` nötig. Grenzen (`≤26 Zeilen`, `≤30 Zeichen/Zeile`, ASCII `0x20–0x7E`) werden bei Build-Zeit geprüft.
