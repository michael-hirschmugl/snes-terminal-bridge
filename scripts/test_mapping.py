#!/usr/bin/env python3
"""Quick interactive mapping test — no gamepad, no curses.

Tippe eine Taste, Enter zum Bestätigen. Ctrl+C zum Beenden.
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from snes_terminal_bridge import config, mapper

cfg = config.load()
print(f"Mappings geladen: {len(cfg.mappings)} Einträge")
print("Taste eingeben + Enter (Ctrl+C zum Beenden):\n")

try:
    while True:
        raw = input("> ")
        if not raw:
            continue
        # Test each character individually
        for ch in raw:
            buttons = mapper.lookup(ch, cfg)
            if buttons:
                print(f"  {ch!r:6} → {buttons}")
            else:
                print(f"  {ch!r:6} → (kein Mapping)")
except (KeyboardInterrupt, EOFError):
    print("\nTschüss.")
