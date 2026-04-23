################################################################################
# snes/Makefile — builds two parallel SNES terminal ROMs
#
# Default (Mode 1):  build/terminal.sfc         — BG1 4bpp, 8x16 chars
# Mode 5 (new):      build/terminal_mode5.sfc   — BG2 2bpp hires, 16x16 AA chars
#
# Usage:
#   make              — build the default Mode 1 ROM (build/terminal.sfc)
#   make all          — same as `make`
#   make mode5        — build the Mode 5 ROM (build/terminal_mode5.sfc)
#   make both         — build both ROMs
#   make font         — regenerate all .inc assets
#   make clean        — remove build outputs
#   make run          — default ROM in bsnes
#   make run-mode5    — Mode 5 ROM in bsnes
#
# Prerequisites:
#   sudo apt install cc65         (ca65 assembler + ld65 linker)
################################################################################

# --- Mode 1 (default) ---
ROM         := build/terminal.sfc
OBJ         := build/main.o
ASM_SRC     := src/main.asm
FONT        := assets/font.inc
TILEMAP     := assets/tilemap.inc
KEYMAP      := assets/keymap.inc

# --- Mode 5 (new) ---
ROM_MODE5   := build/terminal_mode5.sfc
OBJ_MODE5   := build/main_mode5.o
ASM_MODE5   := src/main_mode5.asm
FONT2       := assets/font2.inc
KEYMAP_M5   := assets/keymap_mode5.inc

TOOLS_VENV   := tools/.venv
TOOLS_PYTHON := $(TOOLS_VENV)/bin/python

.PHONY: all both mode5 font clean run run-mode5

all: $(ROM)

both: $(ROM) $(ROM_MODE5)

mode5: $(ROM_MODE5)

# ---- Link: Mode 1 ----
$(ROM): $(OBJ) snes.cfg tools/fix_checksum.py | build
	ld65 -C snes.cfg -o $@ $(OBJ)
	python3 tools/fix_checksum.py $@
	@echo "ROM: $@ ($$(wc -c < $@) bytes)"

# ---- Link: Mode 5 ----
$(ROM_MODE5): $(OBJ_MODE5) snes.cfg tools/fix_checksum.py | build
	ld65 -C snes.cfg -o $@ $(OBJ_MODE5)
	python3 tools/fix_checksum.py $@
	@echo "ROM: $@ ($$(wc -c < $@) bytes)"

# ---- Assemble: Mode 1 ----
$(OBJ): $(ASM_SRC) $(FONT) $(TILEMAP) $(KEYMAP) | build
	ca65 --cpu 65816 -o $@ $<

# ---- Assemble: Mode 5 ----
$(OBJ_MODE5): $(ASM_MODE5) $(FONT2) $(KEYMAP_M5) | build
	ca65 --cpu 65816 -o $@ $<

# ---- Font + tilemap + keymap generation ----
# gen_keymap.py emits both keymap.inc (Mode 1, tile=C*2) and
# keymap_mode5.inc (Mode 5, tile=(C//8)*32+(C%8)*2) in a single invocation,
# so we make them share the same recipe.
font: $(FONT) $(FONT2) $(TILEMAP) $(KEYMAP) $(KEYMAP_M5)

$(FONT) $(TILEMAP): tools/gen_font.py | $(TOOLS_VENV)
	$(TOOLS_PYTHON) tools/gen_font.py

$(FONT2): tools/gen_font2.py tools/fonts/JetBrainsMono-Regular.ttf | $(TOOLS_VENV)
	$(TOOLS_PYTHON) tools/gen_font2.py

$(KEYMAP) $(KEYMAP_M5): tools/gen_keymap.py | $(TOOLS_VENV)
	$(TOOLS_PYTHON) tools/gen_keymap.py

$(TOOLS_VENV): tools/requirements.txt
	python3 -m venv $(TOOLS_VENV)
	$(TOOLS_VENV)/bin/pip install -q -r tools/requirements.txt
	@touch $(TOOLS_VENV)  # update mtime so Make won't re-run

# ---- Directories ----
build:
	mkdir -p build

# ---- Run in emulator ----
run: $(ROM)
	bsnes $(ROM)

run-mode5: $(ROM_MODE5)
	bsnes $(ROM_MODE5)

# ---- Clean ----
clean:
	rm -rf build
