#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
A.S.P. - Air Strike Patrol (SNES)
SCANNER DEFINITIVO DEI TESTI / PUNTATORI

Uso:
    python asp_text_scanner_definitivo.py "A.S.P. - Air Strike Patrol (USA)(1).sfc"
    python asp_text_scanner_definitivo.py "A.S.P. - Air Strike Patrol (USA)(1).sfc" gioco_TEXT_DISCOVERY.txt

Il programma:
  1) trova tutti i JSL $829523;
  2) ricostruisce i valori caricati in $07F0/$07F2 nelle istruzioni precedenti;
  3) converte i puntatori CPU <-> ROM secondo la mappatura verificata per questo ROM;
  4) cerca le stringhe ASCII-like presenti nella scansione precedente, se fornita;
  5) cerca nel ROM i riferimenti a quei puntatori a 3 byte;
  6) produce un report separando HIGH/MEDIUM/LOW confidence;
  7) non considera automaticamente grafica/tabelle come testo.

Nota:
  La routine $829523 usa LDA [$00],Y e quindi il puntatore effettivo è
  $07F0-$07F2 (offset 16 bit + bank).
  Il mapping ROM/CPU usato qui è quello verificato sul gioco:
      ROM $0082A1 -> CPU $8182A1
      ROM $06EC74 -> CPU $8DEC74

  Per la regione PRG:
      bank = $80 + ROM_offset // $8000
      addr = $8000 + ROM_offset % $8000
"""

import re
import sys
from pathlib import Path
from collections import defaultdict

JSL = bytes((0x22, 0x23, 0x95, 0x82))
PTR_LO = 0x07F0
PTR_BANK = 0x07F2

# ----------------------------------------------------------------------
# MAPPATURA ROM <-> CPU verificata per questo gioco
# ----------------------------------------------------------------------

def rom_to_cpu(off):
    if off < 0 or off >= 0x800000:
        return None
    bank = 0x80 + (off // 0x8000)
    addr = 0x8000 + (off % 0x8000)
    return (bank << 16) | addr

def cpu_to_rom(cpu):
    bank = (cpu >> 16) & 0xFF
    addr = cpu & 0xFFFF
    if not (0x80 <= bank <= 0xFF and 0x8000 <= addr <= 0xFFFF):
        return None
    return (bank - 0x80) * 0x8000 + (addr - 0x8000)

def fmt_cpu(cpu):
    return f"{(cpu>>16)&0xFF:02X}:{cpu&0xFFFF:04X}"

# ----------------------------------------------------------------------
# DISASSEMBLY MINIMALE 65816
# Serve soltanto a capire le istruzioni che precedono il JSL.
# Non pretende di essere un disassembler completo.
# ----------------------------------------------------------------------

# opcode -> (nome, lunghezza). Le istruzioni non presenti vengono trattate
# come 1 byte, ma la ricerca all'indietro usa pattern robusti.
OPLEN = {
    0xA9: 3, # LDA #imm16 (M=0 nei casi che ci interessano)
    0xA2: 3, # LDX #imm16
    0xA0: 3, # LDY #imm16
    0x8D: 3, # STA abs
    0x9D: 3, # STA abs,X
    0x8E: 3, # STX abs
    0x8C: 3, # STY abs
    0xAF: 4, # LDA long
    0xBF: 4, # LDA long,X
    0xAD: 3, # LDA abs
    0xBD: 3, # LDA abs,X
    0xB9: 3, # LDA abs,Y
    0x8F: 4, # STA long
    0x9F: 4, # STA long,X
    0x20: 3, # JSR abs
    0x22: 4, # JSL long
    0x5B: 1, # TCD
    0x48: 1, # PHA
    0x68: 1, # PLA
    0x8A: 1, # TXA
    0x9A: 1, # TXS
    0xAA: 1, # TAX
    0xE2: 2, # SEP
    0xC2: 2, # REP
    0x18: 1, # CLC
    0x38: 1, # SEC
    0x69: 3, # ADC #imm16
    0xE9: 3, # SBC #imm16
    0x29: 3, # AND #imm16
    0x09: 3, # ORA #imm16
    0x49: 3, # EOR #imm16
    0x0A: 1, # ASL A
    0x2A: 1, # ROL A
    0x4A: 1, # LSR A
    0x6A: 1, # ROR A
    0xC8: 1, # INY
    0xCA: 1, # DEX
    0xE8: 1, # INX
    0x60: 1, # RTS
    0x6B: 1, # RTL
    0x80: 2, # BRA
    0x90: 2, # BCC
    0xB0: 2, # BCS
    0xD0: 2, # BNE
    0xF0: 2, # BEQ
}

def disasm_lengths(buf):
    """Restituisce offset/len di una sequenza usando una tabella minima."""
    i = 0
    out = []
    while i < len(buf):
        op = buf[i]
        ln = OPLEN.get(op, 1)
        if i + ln > len(buf):
            ln = 1
        out.append((i, op, ln))
        i += ln
    return out

# ----------------------------------------------------------------------
# RICERCA JSL + RICOSTRUZIONE PUNTATORE
# ----------------------------------------------------------------------

def find_jsls(data):
    return [m.start() for m in re.finditer(re.escape(JSL), data)]

def decode_abs16(data, p):
    if p + 2 >= len(data):
        return None
    return data[p+1] | (data[p+2] << 8)

def decode_imm16(data, p):
    if p + 2 >= len(data):
        return None
    return data[p+1] | (data[p+2] << 8)

def trace_pointer_before_call(data, call_off, window=96):
    """
    Cerca pattern reali usati nel codice:
      A2 lo hi 8E F0 07          => LDX #xxxx / STX $07F0
      A9 lo hi 8D F0 07          => LDA #xxxx / STA $07F0
      A9 bb 00 8D F2 07          => bank bb / STA $07F2
      8E F0 07                   => STX $07F0
      8D F0 07                   => STA $07F0

    Mantiene anche l'ultimo valore noto di A/X/bank.
    """
    start = max(0, call_off - window)
    end = call_off
    buf = data[start:end]

    # Scansione byte-oriented dei pattern: più affidabile del disassembly
    # parziale quando ci sono modalità M/X diverse.
    candidates = []

    last_a = None
    last_x = None
    last_bank = None

    i = 0
    while i < len(buf):
        p = start + i
        op = data[p]

        if op == 0xA9 and p + 2 < end:          # LDA #imm
            last_a = data[p+1] | (data[p+2] << 8)
            i += 3
            continue

        if op == 0xA2 and p + 2 < end:          # LDX #imm
            last_x = data[p+1] | (data[p+2] << 8)
            i += 3
            continue

        if op == 0x8D and p + 2 < end:          # STA abs
            a = data[p+1] | (data[p+2] << 8)
            if a == PTR_LO and last_a is not None:
                candidates.append(("A->07F0", last_a, p))
            elif a == PTR_BANK and last_a is not None:
                candidates.append(("A->07F2", last_a & 0xFF, p))
            i += 3
            continue

        if op == 0x8E and p + 2 < end:          # STX abs
            a = data[p+1] | (data[p+2] << 8)
            if a == PTR_LO and last_x is not None:
                candidates.append(("X->07F0", last_x, p))
            i += 3
            continue

        i += 1

    # Prende gli ultimi valori noti prima della chiamata.
    low = None
    bank = None
    source_low = None
    source_bank = None

    for typ, val, pos in candidates:
        if typ in ("A->07F0", "X->07F0"):
            low = val
            source_low = (typ, pos)
        elif typ == "A->07F2":
            bank = val
            source_bank = (typ, pos)

    # Se manca il bank ma il low è noto, non inventiamo il bank.
    if low is not None and bank is not None:
        cpu = (bank << 16) | low
        roff = cpu_to_rom(cpu)
        confidence = "HIGH" if roff is not None else "MEDIUM"
    elif low is not None:
        cpu = None
        roff = None
        confidence = "MEDIUM"
    else:
        cpu = None
        roff = None
        confidence = "LOW"

    return {
        "call": call_off,
        "low": low,
        "bank": bank,
        "cpu": cpu,
        "rom": roff,
        "confidence": confidence,
        "source_low": source_low,
        "source_bank": source_bank,
        "candidates": candidates,
    }

# ----------------------------------------------------------------------
# ASCII SCANNER
# ----------------------------------------------------------------------

def printable_ratio(bs):
    if not bs:
        return 0
    ok = sum(32 <= b <= 126 or b in (9, 10, 13) for b in bs)
    return ok / len(bs)

def ascii_strings(data, min_len=5, max_len=200):
    """
    Trova sequenze ASCII stampabili.
    Filtra alcuni falsi positivi evidenti.
    """
    result = []
    i = 0
    n = len(data)
    while i < n:
        if 32 <= data[i] <= 126:
            j = i
            while j < n and 32 <= data[j] <= 126:
                j += 1
            ln = j - i
            if min_len <= ln <= max_len:
                s = data[i:j].decode("ascii", "replace")
                score = printable_ratio(data[i:j])
                if is_likely_text(s, data[i:j]):
                    result.append((i, ln, score, s))
            i = j
        else:
            i += 1
    return result

def is_likely_text(s, raw):
    u = s.upper()

    # Grafica/tabelle tipiche emerse dalla scansione originale.
    graphic_tokens = (
        "BCBCBC", "FHHHHH", "BBBBBB", "FFFFFFFF",
    )
    if len(s) >= 5 and any(t in u for t in graphic_tokens):
        # Non scartiamo stringhe che contengono parole inglesi reali.
        words = sum(c.isalpha() for c in s)
        if words < len(s) * 0.75:
            return False

    letters = sum(c.isalpha() for c in s)
    spaces = s.count(" ")
    punctuation = sum(c in ".,:;!?'-/%[]()" for c in s)

    # Formati C e messaggi di debug possono essere veri testi.
    if re.search(r"%[-+0-9.*]*[diuxXlf]", s):
        return True

    # Una sequenza con molte lettere è generalmente testo.
    if letters >= 4 and letters / max(1, len(s)) >= 0.35:
        return True

    return False

# ----------------------------------------------------------------------
# PARSING DEL FILE DELLA SCANSIONE PRECEDENTE
# ----------------------------------------------------------------------

SCAN_RE = re.compile(
    r"ROM \$([0-9A-Fa-f]{6})\s+CPU \$([0-9A-Fa-f]{6})\s+LEN\s+(\d+)"
    r"(?:\s+SCORE\s+([0-9.]+))?\s*(.*)"
)

def load_scan(path):
    out = []
    if not path or not Path(path).exists():
        return out

    for line in Path(path).read_text(errors="replace").splitlines():
        m = SCAN_RE.search(line)
        if not m:
            continue
        roff = int(m.group(1), 16)
        cpu = int(m.group(2), 16)
        ln = int(m.group(3))
        score = float(m.group(4)) if m.group(4) else 0.0
        text = m.group(5).rstrip()
        out.append({
            "rom": roff,
            "cpu": cpu,
            "len": ln,
            "score": score,
            "text": text,
        })
    return out

# ----------------------------------------------------------------------
# RIFERIMENTI AI PUNTATORI
# ----------------------------------------------------------------------

def find_pointer_refs(data, roff):
    """
    Un puntatore 24-bit little-endian è:
       low, high, bank

    Cerca tutte le occorrenze esatte nel ROM.
    """
    cpu = rom_to_cpu(roff)
    if cpu is None:
        return []
    addr = cpu & 0xFFFF
    bank = (cpu >> 16) & 0xFF
    pat = bytes((addr & 0xFF, (addr >> 8) & 0xFF, bank))
    return [m.start() for m in re.finditer(re.escape(pat), data)]

def preview(data, roff, length=80):
    if roff is None or not (0 <= roff < len(data)):
        return ""
    b = data[roff:roff+length]
    s = "".join(chr(x) if 32 <= x <= 126 else "." for x in b)
    return s

# ----------------------------------------------------------------------
# COLLEGAMENTO: JSL -> STRINGA
# ----------------------------------------------------------------------

def best_scan_match(scan_entries, roff):
    if roff is None:
        return None

    # Match esatto.
    exact = [x for x in scan_entries if x["rom"] == roff]
    if exact:
        return max(exact, key=lambda x: x["score"])

    # Il puntatore può puntare a un byte di controllo/spazio appena prima
    # della parte ASCII rilevata dallo scanner.
    near = [
        x for x in scan_entries
        if abs(x["rom"] - roff) <= 2
    ]
    if near:
        return min(near, key=lambda x: abs(x["rom"] - roff))
    return None

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Uso: python asp_text_scanner_definitivo.py ROM.sfc [scansione.txt]")
        sys.exit(1)

    rom_path = Path(sys.argv[1])
    scan_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else None

    if not rom_path.exists():
        print(f"ERRORE: ROM non trovato: {rom_path}")
        sys.exit(2)

    data = rom_path.read_bytes()
    scan_entries = load_scan(scan_path)

    print("=" * 92)
    print("A.S.P. SNES - TEXT SCANNER DEFINITIVO")
    print("=" * 92)
    print(f"ROM : {rom_path}")
    print(f"SIZE: {len(data):X} hex / {len(data)} bytes")
    print()

    # --------------------------------------------------------------
    # 1. JSL
    # --------------------------------------------------------------
    jsls = find_jsls(data)

    print(f"JSL $829523 trovati: {len(jsls)}")
    print("-" * 92)

    call_reports = []
    for call in jsls:
        r = trace_pointer_before_call(data, call)
        call_reports.append(r)

        call_cpu = rom_to_cpu(call)
        ptr = fmt_cpu(r["cpu"]) if r["cpu"] is not None else "DYNAMIC/UNKNOWN"
        romtxt = f"${r['rom']:06X}" if r["rom"] is not None else "?"
        match = best_scan_match(scan_entries, r["rom"])
        text = match["text"] if match else ""

        print(
            f"CALL CPU {fmt_cpu(call_cpu):>9}  "
            f"PTR {ptr:>12}  ROM {romtxt:>9}  "
            f"{r['confidence']:<6}  {text[:55]}"
        )

    print()

    # --------------------------------------------------------------
    # 2. Riferimenti ai puntatori delle chiamate
    # --------------------------------------------------------------
    print("RIFERIMENTI AI PUNTATORI RICOSTRUITI")
    print("-" * 92)

    seen = set()
    for r in call_reports:
        if r["rom"] is None:
            continue
        if r["rom"] in seen:
            continue
        seen.add(r["rom"])

        refs = find_pointer_refs(data, r["rom"])
        cpu = rom_to_cpu(r["call"])
        ptrcpu = rom_to_cpu(r["rom"])
        match = best_scan_match(scan_entries, r["rom"])

        print(
            f"PTR {fmt_cpu(ptrcpu):>9} / ROM ${r['rom']:06X}  "
            f"refs={len(refs):3d}  "
            f"CALL={fmt_cpu(cpu):>9}"
        )
        if match:
            print(f"    TEXT: {match['text']}")
        if refs:
            shown = ", ".join(f"${x:06X}" for x in refs[:20])
            if len(refs) > 20:
                shown += ", ..."
            print(f"    REF : {shown}")

    print()

    # --------------------------------------------------------------
    # 3. Stringhe note dalla scansione precedente
    # --------------------------------------------------------------
    if scan_entries:
        print("STRINGHE DELLA SCANSIONE PRECEDENTE + RIFERIMENTI")
        print("-" * 92)

        # Limita alle stringhe realmente plausibili, eliminando la massa
        # di grafica BC/FF che il vecchio scanner segnalava.
        plausible = []
        for x in scan_entries:
            raw = data[x["rom"]:x["rom"] + min(x["len"], 120)]
            if is_likely_text(x["text"], raw):
                plausible.append(x)

        # Raggruppa stringhe troppo ravvicinate.
        for x in plausible:
            refs = find_pointer_refs(data, x["rom"])
            reftext = (
                ", ".join(f"${r:06X}" for r in refs[:8])
                if refs else "-"
            )
            print(
                f"ROM ${x['rom']:06X}  CPU ${x['cpu']:06X}  "
                f"score={x['score']:.2f}  refs={len(refs):3d}  "
                f"{x['text'][:90]}"
            )
            if refs:
                print(f"    PTR-BYTES: {reftext}")

    # --------------------------------------------------------------
    # 4. ASCII trovate direttamente nel ROM
    # --------------------------------------------------------------
    print()
    print("NUOVE STRINGHE ASCII-LIKE TROVATE DIRETTAMENTE NEL ROM")
    print("-" * 92)

    strings = ascii_strings(data)

    # Mostra solo stringhe ragionevoli e abbastanza lunghe.
    for roff, ln, score, s in strings:
        if ln < 8:
            continue
        cpu = rom_to_cpu(roff)
        if cpu is None:
            continue
        print(
            f"ROM ${roff:06X}  CPU ${cpu:06X}  LEN {ln:3d}  "
            f"SCORE {score:.2f}  {s[:110]}"
        )

    # --------------------------------------------------------------
    # 5. RIASSUNTO OPERATIVO
    # --------------------------------------------------------------
    print()
    print("=" * 92)
    print("RIASSUNTO")
    print("=" * 92)

    high = sum(r["confidence"] == "HIGH" for r in call_reports)
    med = sum(r["confidence"] == "MEDIUM" for r in call_reports)
    low = sum(r["confidence"] == "LOW" for r in call_reports)

    print(f"JSL $829523       : {len(jsls)}")
    print(f"Pointer HIGH      : {high}")
    print(f"Pointer MEDIUM    : {med}")
    print(f"Pointer LOW       : {low}")
    print(f"Stringhe scanner  : {len(scan_entries)}")
    print(f"Stringhe plausibili: {sum(is_likely_text(x['text'], data[x['rom']:x['rom']+x['len']]) for x in scan_entries)}")
    print()
    print("IMPORTANTE:")
    print("  Le stringhe ASCII non referenziate da $829523 NON sono automaticamente")
    print("  inutilizzate. Possono essere gestite da altre routine di stampa.")
    print("  Il report dei riferimenti ai 3 byte serve proprio a individuare anche")
    print("  queste tabelle e routine alternative.")
    print()
    print("Per la traduzione:")
    print("  - non modificare ancora i caratteri speciali;");
    print("  - usare gli offset ROM del report come coordinate di lavoro;");
    print("  - verificare in Mesen2 ogni candidato prima di patcharlo.")

if __name__ == "__main__":
    main()
