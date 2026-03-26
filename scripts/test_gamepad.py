#!/usr/bin/env python3
"""Gamepad test — creates a virtual SNES device and fires button combos.

Usage: python scripts/test_gamepad.py
Type a character + Enter. The virtual device stays open while the script
is running — observe events in a second terminal with evtest.
Ctrl+C to quit.
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from snes_terminal_bridge import config, mapper
from snes_terminal_bridge.gamepad import Gamepad

cfg = config.load()

with Gamepad() as gp:
    print("Virtual SNES device created — start evtest in a second terminal now.")
    print("Type a character + Enter, Ctrl+C to quit.\n")
    try:
        while True:
            raw = input("> ")
            for ch in raw:
                buttons = mapper.lookup(ch, cfg)
                if buttons:
                    print(f"  {ch!r} → {buttons} — pressing...")
                    gp.press_combo(buttons, cfg.settings.hold_ms, cfg.settings.release_gap_ms)
                    print("  done.")
                else:
                    print(f"  {ch!r} → no mapping")
    except (KeyboardInterrupt, EOFError):
        print("\nClosing device.")
