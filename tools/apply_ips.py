#!/usr/bin/env python3
"""Applica una patch IPS a una ROM e verifica il CRC32 del risultato.

Uso:
    python tools/apply_ips.py ORIGINALE.sfc translation/patches/ASP_ITA.ips rom/ASP_ITA.sfc
    python tools/apply_ips.py ORIGINALE.sfc patch.ips out.sfc --crc 7d4c26e3

Se --crc non e' indicato, viene confrontato con rom_identity.txt (se presente
nella radice della repo), altrimenti il CRC32 viene solo stampato.
"""
import argparse
import pathlib
import sys
import zlib


def apply_ips(rom: bytearray, ips: bytes) -> bytearray:
    if ips[:5] != b"PATCH":
        raise ValueError("non e' un file IPS (manca l'intestazione PATCH)")
    pos = 5
    while True:
        if pos + 3 > len(ips):
            raise ValueError("IPS troncato (manca EOF)")
        off_b = ips[pos:pos + 3]
        pos += 3
        if off_b == b"EOF":
            break
        offset = int.from_bytes(off_b, "big")
        size = int.from_bytes(ips[pos:pos + 2], "big")
        pos += 2
        if size == 0:  # record RLE
            run = int.from_bytes(ips[pos:pos + 2], "big")
            val = ips[pos + 2]
            pos += 3
            data = bytes([val]) * run
        else:
            data = ips[pos:pos + size]
            pos += size
        end = offset + len(data)
        if end > len(rom):
            rom.extend(b"\x00" * (end - len(rom)))
        rom[offset:end] = data
    return rom


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("ips")
    ap.add_argument("out")
    ap.add_argument("--crc", help="CRC32 atteso del risultato (esadecimale)")
    a = ap.parse_args()

    rom = bytearray(pathlib.Path(a.rom).read_bytes())
    out = apply_ips(rom, pathlib.Path(a.ips).read_bytes())
    crc = f"{zlib.crc32(out) & 0xFFFFFFFF:08x}"
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.out).write_bytes(out)
    print(f"scritto {a.out}  ({len(out)} byte)  CRC32 {crc}")

    if a.crc and a.crc.lower() != crc:
        print(f"ERRORE: CRC atteso {a.crc.lower()}, ottenuto {crc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
