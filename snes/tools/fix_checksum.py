#!/usr/bin/env python3
"""
Patch the SNES internal-header checksum and its complement into a linked .sfc.

Flash cartridges (SD2SNES/FXPak Pro, EverDrive, Super Wildcard, ...) validate
these fields when loading a ROM. The SNES S-CPU boot ROM itself does not check
them, but writing the correct values is good hygiene for real hardware.

Algorithm (LoROM, power-of-two ROM size):
  * Place $00 in the checksum bytes and $FF in the complement bytes first.
  * Sum every byte of the ROM modulo $10000 -> checksum.
  * Complement = checksum XOR $FFFF.

For a 32 KiB LoROM the header is at file offset $7FC0..$7FDF:
  $7FDC-$7FDD : checksum complement (little endian)
  $7FDE-$7FDF : checksum            (little endian)
"""

from __future__ import annotations

import sys
from pathlib import Path


HEADER_OFFSET = 0x7FC0        # LoROM header start in a 32 KiB image
COMPL_OFFSET = 0x7FDC         # checksum complement (2 bytes, little endian)
CHECK_OFFSET = 0x7FDE         # checksum            (2 bytes, little endian)
EXPECTED_SIZE = 32 * 1024     # 32 KiB LoROM


def patch(path: Path) -> tuple[int, int]:
    data = bytearray(path.read_bytes())
    if len(data) != EXPECTED_SIZE:
        raise SystemExit(
            f"{path}: expected {EXPECTED_SIZE} bytes, got {len(data)}"
        )

    data[COMPL_OFFSET:COMPL_OFFSET + 2] = b"\xFF\xFF"
    data[CHECK_OFFSET:CHECK_OFFSET + 2] = b"\x00\x00"

    checksum = sum(data) & 0xFFFF
    complement = checksum ^ 0xFFFF

    data[COMPL_OFFSET]     = complement & 0xFF
    data[COMPL_OFFSET + 1] = (complement >> 8) & 0xFF
    data[CHECK_OFFSET]     = checksum & 0xFF
    data[CHECK_OFFSET + 1] = (checksum >> 8) & 0xFF

    path.write_bytes(bytes(data))
    return checksum, complement


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: fix_checksum.py <rom.sfc>", file=sys.stderr)
        return 2

    rom = Path(argv[1])
    checksum, complement = patch(rom)
    print(f"checksum=${checksum:04X} complement=${complement:04X} -> {rom}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
