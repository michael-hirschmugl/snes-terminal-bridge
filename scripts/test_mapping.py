#!/usr/bin/env python3
"""Quick interactive mapping test — no gamepad, no curses.

Type a character and press Enter to see the corresponding SNES buttons.
Ctrl+C to quit.
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from snes_terminal_bridge import config, mapper

cfg = config.load()
print(f"Mappings loaded: {len(cfg.mappings)} entries")
print("Type a character + Enter (Ctrl+C to quit):\n")

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
                print(f"  {ch!r:6} → (no mapping)")
except (KeyboardInterrupt, EOFError):
    print("\nBye.")
