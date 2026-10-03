from pathlib import Path
import sys
import re

# ============================================================
# A.S.P. SNES TRANSLATION
#
# TEXT + POINTER DISCOVERY SCANNER
#
# NON MODIFICA LA ROM
#
# Cerca:
#   1) stringhe ASCII-like
#   2) stringhe terminate da 00
#   3) tabelle di puntatori
#   4) puntatori 16/24 bit
#   5) LDX/LDY -> $07F0
#   6) accessi a $07F0 / $07F2
#   7) JSL verso routine
#   8) JSL $829523
#
# Mappatura LoROM verificata:
#   ROM $0082A1 -> CPU $8182A1
#   ROM $06EC74 -> CPU $8DEC74
# ============================================================


# ------------------------------------------------------------
# PARAMETRI
# ------------------------------------------------------------

MIN_STRING = 5
MAX_STRING = 80

# Quanto vicino devono essere due possibili puntatori
TABLE_MAX_DISTANCE = 32

# Routine testo già conosciuta
KNOWN_TEXT_ROUTINE = 0x829523


# ------------------------------------------------------------
# ROM OFFSET -> CPU ADDRESS
# ------------------------------------------------------------

def rom_to_cpu(offset):

    bank = 0x80 + (offset // 0x8000)
    address = 0x8000 + (offset % 0x8000)

    return (bank << 16) | address


# ------------------------------------------------------------
# CPU ADDRESS -> ROM OFFSET
# ------------------------------------------------------------

def cpu_to_rom(cpu):

    bank = (cpu >> 16) & 0xFF
    address = cpu & 0xFFFF

    if address < 0x8000:
        return None

    bank &= 0x7F

    return bank * 0x8000 + (address - 0x8000)


# ------------------------------------------------------------
# FORMATTAZIONE
# ------------------------------------------------------------

def cpu_str(value):

    return f"${value:06X}"


def rom_str(value):

    return f"${value:06X}"


# ------------------------------------------------------------
# WORD LITTLE ENDIAN
# ------------------------------------------------------------

def read_word(data, pos):

    if pos + 1 >= len(data):
        return None

    return data[pos] | (data[pos + 1] << 8)


# ------------------------------------------------------------
# LONG LITTLE ENDIAN
# ------------------------------------------------------------

def read_long(data, pos):

    if pos + 2 >= len(data):
        return None

    return (
        data[pos]
        | (data[pos + 1] << 8)
        | (data[pos + 2] << 16)
    )


# ------------------------------------------------------------
# STRINGA ASCII-LIKE
# ------------------------------------------------------------

def is_printable_ascii(b):

    return 0x20 <= b <= 0x7E


def scan_strings(data):

    results = []

    i = 0

    while i < len(data):

        if not is_printable_ascii(data[i]):

            i += 1
            continue

        start = i

        while (
            i < len(data)
            and is_printable_ascii(data[i])
            and i - start < MAX_STRING
        ):
            i += 1

        length = i - start

        # Deve avere una lunghezza minima
        if length >= MIN_STRING:

            # Accettiamo solo stringhe terminate da 00
            terminated = (
                i < len(data)
                and data[i] == 0x00
            )

            text = bytes(
                data[start:i]
            ).decode(
                "ascii",
                errors="replace"
            )

            # Punteggio:
            # lettere/spazi/punteggiatura normale
            score = 0

            for b in data[start:i]:

                if (
                    0x41 <= b <= 0x5A
                    or
                    0x61 <= b <= 0x7A
                    or
                    b == 0x20
                ):
                    score += 1

            score /= length

            # Per evitare falsi positivi
            if terminated and score >= 0.50:

                results.append({
                    "offset": start,
                    "cpu": rom_to_cpu(start),
                    "length": length,
                    "text": text,
                    "score": score
                })

        i += 1

    return results


# ------------------------------------------------------------
# CERCA JSL $829523
# ------------------------------------------------------------

def scan_known_routine(data):

    pattern = bytes.fromhex("22 23 95 82")

    results = []

    pos = 0

    while True:

        pos = data.find(pattern, pos)

        if pos == -1:
            break

        results.append(pos)

        pos += 1

    return results


# ------------------------------------------------------------
# CERCA TUTTE LE JSL
#
# 22 LL HH BB
# ------------------------------------------------------------

def scan_all_jsls(data):

    results = []

    for i in range(len(data) - 4):

        if data[i] == 0x22:

            target = (
                data[i + 1]
                | (data[i + 2] << 8)
                | (data[i + 3] << 16)
            )

            results.append({
                "offset": i,
                "cpu": rom_to_cpu(i),
                "target": target
            })

    return results


# ------------------------------------------------------------
# CERCA ACCESSI A $07F0 / $07F2
#
# STX $07F0 = 8E F0 07
# STY $07F0 = 8C F0 07
# STA $07F0 = 8D F0 07
# STA $07F2 = 8D F2 07
# ------------------------------------------------------------

def scan_temp_pointer_accesses(data):

    patterns = {

        bytes.fromhex("8E F0 07"):
            "STX $07F0",

        bytes.fromhex("8C F0 07"):
            "STY $07F0",

        bytes.fromhex("8D F0 07"):
            "STA $07F0",

        bytes.fromhex("8D F2 07"):
            "STA $07F2",
    }

    results = []

    for pattern, name in patterns.items():

        pos = 0

        while True:

            pos = data.find(pattern, pos)

            if pos == -1:
                break

            results.append({
                "offset": pos,
                "cpu": rom_to_cpu(pos),
                "instruction": name
            })

            pos += 1

    results.sort(
        key=lambda x: x["offset"]
    )

    return results


# ------------------------------------------------------------
# CERCA:
#
# LDX #xxxx
# STX $07F0
#
# LDY #xxxx
# STY $07F0
#
# LDA #xxxx
# STA $07F2
# ------------------------------------------------------------

def scan_direct_pointer_builders(data):

    results = []

    for i in range(len(data) - 6):

        # ----------------------------------------------------
        # LDX #xxxx
        # ----------------------------------------------------

        if (
            data[i] == 0xA2
            and
            data[i + 3:i + 6]
            == bytes.fromhex("8E F0 07")
        ):

            offset = read_word(data, i + 1)

            results.append({
                "offset": i,
                "cpu": rom_to_cpu(i),
                "kind": "LDX",
                "value": offset
            })


        # ----------------------------------------------------
        # LDY #xxxx
        # ----------------------------------------------------

        if (
            data[i] == 0xA0
            and
            data[i + 3:i + 6]
            == bytes.fromhex("8C F0 07")
        ):

            offset = read_word(data, i + 1)

            results.append({
                "offset": i,
                "cpu": rom_to_cpu(i),
                "kind": "LDY",
                "value": offset
            })


    return results


# ------------------------------------------------------------
# CERCA TABELLE DI WORD
#
# Cerca sequenze di almeno 3 word che, interpretate come
# offset LoROM nel banco corrente, puntano a zone plausibili.
# ------------------------------------------------------------

def scan_pointer_tables(data):

    results = []

    for i in range(0, len(data) - 12, 2):

        values = []

        valid = True

        for n in range(4):

            value = read_word(
                data,
                i + n * 2
            )

            if value is None:

                valid = False
                break

            values.append(value)

        if not valid:
            continue

        # Gli indirizzi SNES normalmente sono >= $8000
        if not all(
            0x8000 <= x <= 0xFFFF
            for x in values
        ):
            continue

        # Devono essere ragionevolmente vicini
        # (tipico di una tabella di stringhe)
        differences = []

        for n in range(3):

            differences.append(
                abs(
                    values[n + 1]
                    - values[n]
                )
            )

        if max(differences) > 0x4000:

            continue

        results.append({
            "offset": i,
            "cpu": rom_to_cpu(i),
            "values": values
        })

    return results


# ------------------------------------------------------------
# CERCA BYTE 24-bit CHE POTREBBERO ESSERE PUNTATORI
#
# Per ogni tripletta:
#
#   offset low
#   offset high
#   bank
#
# prova a vedere se punta a dati plausibili.
# ------------------------------------------------------------

def scan_long_pointers(data):

    results = []

    for i in range(len(data) - 3):

        pointer = read_long(
            data,
            i
        )

        if pointer is None:
            continue

        bank = (
            pointer >> 16
        ) & 0xFF

        address = pointer & 0xFFFF

        # Indirizzi LoROM plausibili
        if not (
            0x80 <= bank <= 0xFF
            and
            0x8000 <= address <= 0xFFFF
        ):
            continue

        rom = cpu_to_rom(pointer)

        if rom is None:
            continue

        if rom >= len(data):
            continue

        results.append({
            "offset": i,
            "cpu": rom_to_cpu(i),
            "pointer": pointer,
            "target_rom": rom
        })

    return results


# ------------------------------------------------------------
# COLLEGA PUNTATORI A STRINGHE
# ------------------------------------------------------------

def pointer_to_string(data, pointer):

    rom = cpu_to_rom(pointer)

    if rom is None:
        return None

    if rom >= len(data):
        return None

    if not is_printable_ascii(data[rom]):

        return None

    end = rom

    while (
        end < len(data)
        and end - rom < MAX_STRING
        and is_printable_ascii(data[end])
    ):
        end += 1

    length = end - rom

    if length < MIN_STRING:
        return None

    if end >= len(data) or data[end] != 0x00:

        return None

    text = bytes(
        data[rom:end]
    ).decode(
        "ascii",
        errors="replace"
    )

    return {
        "rom": rom,
        "cpu": pointer,
        "length": length,
        "text": text
    }


# ------------------------------------------------------------
# REPORT
# ------------------------------------------------------------

def main():

    if len(sys.argv) < 2:

        print()
        print("A.S.P. SNES TEXT DISCOVERY SCANNER")
        print()
        print("Uso:")
        print("  python asp_text_scanner.py ROM.sfc")
        print()

        return


    filename = Path(
        sys.argv[1]
    )


    if not filename.exists():

        print(
            "ERRORE: ROM non trovata:",
            filename
        )

        return


    data = filename.read_bytes()


    print()
    print("=" * 80)
    print(" A.S.P. SNES TEXT DISCOVERY SCANNER")
    print("=" * 80)
    print()

    print(
        f"ROM       : {filename}"
    )

    print(
        f"Dimensione: {len(data):,} bytes"
    )

    print()


    # ========================================================
    # 1. STRINGHE
    # ========================================================

    print(
        "=" * 80
    )

    print(
        "1) STRINGHE ASCII-LIKE"
    )

    print(
        "=" * 80
    )

    strings = scan_strings(data)

    print(
        f"Trovate: {len(strings)}"
    )

    print()


    for item in strings:

        print(
            f"ROM {rom_str(item['offset'])}  "
            f"CPU {cpu_str(item['cpu'])}  "
            f"LEN {item['length']:02d}  "
            f"SCORE {item['score']:.2f}  "
            f"\"{item['text']}\""
        )


    # ========================================================
    # 2. ROUTINE NOTA
    # ========================================================

    print()
    print(
        "=" * 80
    )

    print(
        "2) JSL $829523"
    )

    print(
        "=" * 80
    )

    known = scan_known_routine(data)

    print(
        f"Trovate: {len(known)}"
    )

    print()

    for pos in known:

        print(
            f"ROM {rom_str(pos)}  "
            f"CPU {cpu_str(rom_to_cpu(pos))}"
        )


    # ========================================================
    # 3. TUTTE LE JSL
    # ========================================================

    print()
    print(
        "=" * 80
    )

    print(
        "3) TUTTE LE JSL"
    )

    print(
        "=" * 80
    )

    jsls = scan_all_jsls(data)

    print(
        f"Trovate: {len(jsls)}"
    )

    print()

    for item in jsls:

        print(
            f"ROM {rom_str(item['offset'])}  "
            f"CPU {cpu_str(item['cpu'])}  "
            f"-> {cpu_str(item['target'])}"
        )


    # ========================================================
    # 4. ACCESSI $07F0 / $07F2
    # ========================================================

    print()
    print(
        "=" * 80
    )

    print(
        "4) ACCESSI A $07F0 / $07F2"
    )

    print(
        "=" * 80
    )

    accesses = scan_temp_pointer_accesses(data)

    print(
        f"Trovati: {len(accesses)}"
    )

    print()

    for item in accesses:

        print(
            f"ROM {rom_str(item['offset'])}  "
            f"CPU {cpu_str(item['cpu'])}  "
            f"{item['instruction']}"
        )


    # ========================================================
    # 5. COSTRUTTORI DI PUNTATORI
    # ========================================================

    print()
    print(
        "=" * 80
    )

    print(
        "5) LDX/LDY -> $07F0"
    )

    print(
        "=" * 80
    )

    builders = scan_direct_pointer_builders(data)

    print(
        f"Trovati: {len(builders)}"
    )

    print()

    for item in builders:

        print(
            f"ROM {rom_str(item['offset'])}  "
            f"CPU {cpu_str(item['cpu'])}  "
            f"{item['kind']} #${item['value']:04X}"
        )


    # ========================================================
    # 6. TABELLE
    # ========================================================

    print()
    print(
        "=" * 80
    )

    print(
        "6) POSSIBILI TABELLE DI PUNTATORI"
    )

    print(
        "=" * 80
    )

    tables = scan_pointer_tables(data)

    print(
        f"Trovate: {len(tables)}"
    )

    print()

    for item in tables:

        values = " ".join(
            f"${x:04X}"
            for x in item["values"]
        )

        print(
            f"ROM {rom_str(item['offset'])}  "
            f"CPU {cpu_str(item['cpu'])}  "
            f"{values}"
        )


    # ========================================================
    # 7. REPORT FILE
    # ========================================================

    report = filename.with_name(
        filename.stem
        + "_TEXT_DISCOVERY.txt"
    )


    with report.open(
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "A.S.P. SNES TEXT DISCOVERY SCANNER\n"
        )

        f.write(
            "=" * 80
            + "\n\n"
        )


        f.write(
            "STRINGHE ASCII-LIKE\n"
        )

        f.write(
            "-" * 80
            + "\n"
        )

        for item in strings:

            f.write(
                f"ROM {rom_str(item['offset'])}  "
                f"CPU {cpu_str(item['cpu'])}  "
                f"LEN {item['length']}  "
                f"SCORE {item['score']:.2f}  "
                f"{item['text']}\n"
            )


        f.write(
            "\n\nJSL $829523\n"
        )

        f.write(
            "-" * 80
            + "\n"
        )

        for pos in known:

            f.write(
                f"ROM {rom_str(pos)}  "
                f"CPU {cpu_str(rom_to_cpu(pos))}\n"
            )


        f.write(
            "\n\nTUTTE LE JSL\n"
        )

        f.write(
            "-" * 80
            + "\n"
        )

        for item in jsls:

            f.write(
                f"ROM {rom_str(item['offset'])}  "
                f"CPU {cpu_str(item['cpu'])}  "
                f"-> {cpu_str(item['target'])}\n"
            )


        f.write(
            "\n\nACCESSI $07F0 / $07F2\n"
        )

        f.write(
            "-" * 80
            + "\n"
        )

        for item in accesses:

            f.write(
                f"ROM {rom_str(item['offset'])}  "
                f"CPU {cpu_str(item['cpu'])}  "
                f"{item['instruction']}\n"
            )


        f.write(
            "\n\nCOSTRUTTORI PUNTATORI\n"
        )

        f.write(
            "-" * 80
            + "\n"
        )

        for item in builders:

            f.write(
                f"ROM {rom_str(item['offset'])}  "
                f"CPU {cpu_str(item['cpu'])}  "
                f"{item['kind']} "
                f"#$%04X\n"
                % item["value"]
            )


        f.write(
            "\n\nTABELLE CANDIDATE\n"
        )

        f.write(
            "-" * 80
            + "\n"
        )

        for item in tables:

            values = " ".join(
                f"${x:04X}"
                for x in item["values"]
            )

            f.write(
                f"ROM {rom_str(item['offset'])}  "
                f"CPU {cpu_str(item['cpu'])}  "
                f"{values}\n"
            )


    print()
    print(
        "=" * 80
    )

    print(
        "ANALISI COMPLETATA"
    )

    print(
        f"Report: {report}"
    )

    print(
        "=" * 80
    )


if __name__ == "__main__":

    main()