################################################################################
# snes/Makefile — builds the SNES terminal ROM (Mode 5).
#
# Output: build/terminal.sfc — 64 KiB LoROM, PAL.
# Graphics: Mode 5 + interlace (512×448), BG2 2bpp text + BG1 4bpp border.
#
# Usage:
#   make              — build the ROM (build/terminal.sfc)
#   make all          — same as `make`
#   make font         — regenerate all .inc assets + font preview PNG
#   make clean        — remove build outputs
#   make run          — build and launch ROM in bsnes
#
# Prerequisites:
#   sudo apt install cc65         (ca65 assembler + ld65 linker)
################################################################################

ROM         := build/terminal.sfc
OBJ         := build/main.o
ASM_SRC     := src/main.asm
FONT        := assets/font.inc
KEYMAP      := assets/keymap.inc

BORDER_STAMP := build/mode5_border_4bpp/tilemap.bin
WELCOME      := assets/welcome.inc

TOOLS_VENV   := tools/.venv
TOOLS_PYTHON := $(TOOLS_VENV)/bin/python

.PHONY: all font clean run

all: $(ROM)

# ---- Link ----
$(ROM): $(OBJ) snes.cfg tools/fix_checksum.py | build
	ld65 -C snes.cfg -o $@ $(OBJ)
	python3 tools/fix_checksum.py $@
	@echo "ROM: $@ ($$(wc -c < $@) bytes)"

# ---- Assemble ----
$(OBJ): $(ASM_SRC) $(FONT) $(KEYMAP) $(BORDER_STAMP) $(WELCOME) | build
	ca65 --cpu 65816 -o $@ $<

# ---- Font + keymap generation ----
font: $(FONT) $(KEYMAP)

$(FONT): tools/gen_font.py tools/fonts/JetBrainsMono-Regular.ttf | $(TOOLS_VENV)
	$(TOOLS_PYTHON) tools/gen_font.py

$(KEYMAP): tools/gen_keymap.py | $(TOOLS_VENV)
	$(TOOLS_PYTHON) tools/gen_keymap.py

# ---- Border asset generation ----
$(BORDER_STAMP): tools/gen_border.py | $(TOOLS_VENV) build
	$(TOOLS_PYTHON) tools/gen_border.py

# ---- Welcome message generation ----
$(WELCOME): tools/gen_welcome.py ../config/welcome.ini | $(TOOLS_VENV)
	$(TOOLS_PYTHON) tools/gen_welcome.py

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

# ---- Clean ----
clean:
	rm -rf build
