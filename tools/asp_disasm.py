#!/usr/bin/env python3
"""Disassemblatore lineare 65816 per A.S.P., basato sul decoder di snesrecomp.

Segue REP/SEP per le larghezze M/X e si ferma a RTS/RTL/RTI/JMP/BRA/JML
(salvo --count). Solo per lettura e analisi: non modifica nulla.

Uso:
    python tools/asp_disasm.py rom/ASP_ITA.sfc 00:8100            # reset
    python tools/asp_disasm.py rom/ASP_ITA.sfc 82D5 --count 80    # NMI
    python tools/asp_disasm.py rom/ASP_ITA.sfc 00:8100 --m 0 --x 0
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "snesrecomp" / "recompiler"))
import snes65816 as d  # noqa: E402

STOP = {"RTS", "RTL", "RTI", "JMP", "JML", "BRA", "BRL", "STP"}

FMT = {
    d.IMM: "#${:0{w}X}", d.ABS: "${:04X}", d.ABS_X: "${:04X},X", d.ABS_Y: "${:04X},Y",
    d.LONG: "${:06X}", d.LONG_X: "${:06X},X", d.DP: "${:02X}", d.DP_X: "${:02X},X",
    d.DP_Y: "${:02X},Y", d.INDIR: "(${:04X})", d.INDIR_X: "(${:04X},X)",
    d.INDIR_Y: "(${:02X}),Y", d.INDIR_LY: "[${:02X}],Y", d.INDIR_L: "[${:02X}]",
    d.INDIR_DPX: "(${:02X},X)", d.DP_INDIR: "(${:02X})", d.STK: "${:02X},S",
    d.STK_IY: "(${:02X},S),Y", d.REL: "${:04X}", d.REL16: "${:04X}",
}

# Registri hardware piu' usati: annotati accanto all'istruzione
HW = {0x2100: "INIDISP", 0x2140: "APUIO0", 0x2141: "APUIO1", 0x2142: "APUIO2",
      0x2143: "APUIO3", 0x4200: "NMITIMEN", 0x420B: "MDMAEN", 0x420C: "HDMAEN",
      0x4210: "RDNMI", 0x4211: "TIMEUP", 0x4212: "HVBJOY", 0x4218: "JOY1L",
      0x4219: "JOY1H", 0x2118: "VMDATAL", 0x2116: "VMADDL", 0x2122: "CGDATA"}


def parse_addr(s: str) -> tuple[int, int]:
    if ":" in s:
        b, a = s.split(":")
        return int(b, 16), int(a, 16)
    v = int(s, 16)
    return (v >> 16) & 0xFF, v & 0xFFFF


def disasm(rom: bytes, bank: int, pc: int, m: int, x: int, count: int, follow: bool):
    out = []
    for _ in range(count):
        off = d.rom_offset(bank, pc)
        if off is None or off < 0 or off >= len(rom):
            out.append(f"{bank:02X}:{pc:04X}  <fuori ROM>")
            break
        ins = d.decode_insn(rom, off, pc, bank, m, x)
        if ins is None:
            out.append(f"{bank:02X}:{pc:04X}  .db ${rom[off]:02X}")
            break
        raw = " ".join(f"{b:02X}" for b in rom[off:off + ins.length])
        fmt = FMT.get(ins.mode)
        opnd = fmt.format(ins.operand, w=2 * (ins.length - 1)) if fmt else ""
        note = ""
        if ins.mode in (d.ABS, d.ABS_X, d.ABS_Y, d.LONG) and (ins.operand & 0xFFFF) in HW:
            note = "  ; " + HW[ins.operand & 0xFFFF]
        out.append(f"{bank:02X}:{pc:04X}  {raw:<12} {ins.mnem} {opnd}{note}"
                   f"{'' if note else ''}   [m{m}x{x}]")
        if ins.mnem == "REP":
            m &= 0 if ins.operand & 0x20 else 1
            x &= 0 if ins.operand & 0x10 else 1
        elif ins.mnem == "SEP":
            m |= 1 if ins.operand & 0x20 else 0
            x |= 1 if ins.operand & 0x10 else 0
        if ins.mnem in STOP and not follow:
            break
        pc = (pc + ins.length) & 0xFFFF
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("addr", help="indirizzo esadecimale, es. 00:8100 o 8100")
    ap.add_argument("--m", type=int, default=1)
    ap.add_argument("--x", type=int, default=1)
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--follow", action="store_true", help="non fermarsi a RTS/JMP")
    a = ap.parse_args()
    rom = pathlib.Path(a.rom).read_bytes()
    if len(rom) % 0x8000 == 512:
        rom = rom[512:]
    d.set_rom_image_size(len(rom))
    d.set_rom_mapping(d.detect_rom_mapping(rom))
    bank, pc = parse_addr(a.addr)
    print("\n".join(disasm(rom, bank, pc, a.m, a.x, a.count, a.follow)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
