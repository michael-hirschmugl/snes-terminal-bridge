################################################################################
# snes/Makefile — build the SNES terminal ROM
#
# Usage:
#   make            — generate font + compile ROM → build/terminal.sfc
#   make font       — (re)generate assets/font.inc only
#   make clean      — remove build outputs
#   make run        — build and open in bsnes
#
# Prerequisites:
#   sudo apt install cc65         (ca65 assembler + ld65 linker)
################################################################################

ROM     := build/terminal.sfc
OBJ     := build/main.o
ASM_SRC := src/main.asm
FONT    := assets/font.inc
TILEMAP := assets/tilemap.inc

TOOLS_VENV   := tools/.venv
TOOLS_PYTHON := $(TOOLS_VENV)/bin/python

.PHONY: all font clean run

all: $(ROM)

# ---- Link ----
$(ROM): $(OBJ) snes.cfg | build
	ld65 -C snes.cfg -o $@ $(OBJ)
	@echo "ROM: $@ ($$(wc -c < $@) bytes)"

# ---- Assemble ----
$(OBJ): $(ASM_SRC) $(FONT) $(TILEMAP) | build
	ca65 --cpu 65816 -o $@ $<

# ---- Font + tilemap generation ----
font: $(FONT) $(TILEMAP)

$(FONT) $(TILEMAP): tools/gen_font.py | $(TOOLS_VENV)
	$(TOOLS_PYTHON) tools/gen_font.py

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
