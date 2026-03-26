#!/usr/bin/env python3
"""Gamepad test — erstellt ein virtuelles SNES-Gerät und feuert Combos.

Usage: python scripts/test_gamepad.py
Tippe einen Buchstaben + Enter. Das virtuelle Gerät bleibt offen
solange das Script läuft — in einem zweiten Terminal mit evtest beobachten.
Ctrl+C zum Beenden.
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from snes_terminal_bridge import config, mapper
from snes_terminal_bridge.gamepad import Gamepad

cfg = config.load()

with Gamepad() as gp:
    print("Virtuelles SNES-Gerät erstellt — jetzt in zweitem Terminal evtest starten.")
    print("Taste + Enter eingeben, Ctrl+C zum Beenden.\n")
    try:
        while True:
            raw = input("> ")
            for ch in raw:
                buttons = mapper.lookup(ch, cfg)
                if buttons:
                    print(f"  {ch!r} → {buttons} — drücke...")
                    gp.press_combo(buttons, cfg.settings.hold_ms, cfg.settings.release_gap_ms)
                    print("  fertig.")
                else:
                    print(f"  {ch!r} → kein Mapping")
    except (KeyboardInterrupt, EOFError):
        print("\nGerät wird geschlossen.")
