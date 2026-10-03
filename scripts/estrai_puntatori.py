import json
import os
import struct

def snes_to_pc(snes_address, is_hirom=False, has_header=False):
    """Converte un indirizzo SNES 24-bit in offset PC."""
    header_offset = 512 if has_header else 0
    
    if is_hirom:
        return (snes_address & 0x3FFFFF) + header_offset
    else:
        bank = (snes_address >> 16) & 0xFF
        offset = snes_address & 0xFFFF
        if offset < 0x8000:
            return None
        return (((bank & 0x7F) * 0x8000) + (offset - 0x8000)) + header_offset

def load_table_file(tbl_path):
    table = {}
    if not os.path.exists(tbl_path):
        return table
    with open(tbl_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '=' in line:
                hex_str, char = line.split('=', 1)
                table[bytes.fromhex(hex_str)] = char
    return table

def decode_text(raw_bytes, table):
    if not table:
        return raw_bytes.hex(' ').upper()
    decoded, i = "", 0
    while i < len(raw_bytes):
        if i < len(raw_bytes) - 1 and raw_bytes[i:i+2] in table:
            decoded += table[raw_bytes[i:i+2]]
            i += 2
        elif bytes([raw_bytes[i]]) in table:
            decoded += table[bytes([raw_bytes[i]])]
            i += 1
        else:
            decoded += f"[{hex(raw_bytes[i])}]"
            i += 1
    return decoded

def extract_game_text(rom_path, table_snes_addr, num_entries, tbl_path, json_out, is_hirom=False, has_header=False):
    table = load_table_file(tbl_path)
    extracted = []
    
    table_pc_base = snes_to_pc(table_snes_addr, is_hirom, has_header)
    if table_pc_base is None:
        print("Errore: Indirizzo tabella non valido.")
        return

    with open(rom_path, 'rb') as rom:
        rom_data = rom.read()

        for i in range(num_entries):
            # Ogni voce è di 8 byte
            entry_pc_offset = table_pc_base + (i * 8)
            entry_bytes = rom_data[entry_pc_offset : entry_pc_offset + 8]

            # I primi 3 byte sono il puntatore a 24-bit al testo (es. 85 9B C3)
            ptr_low, ptr_high, ptr_bank = entry_bytes[0], entry_bytes[1], entry_bytes[2]
            text_snes_addr = (ptr_bank << 16) | (ptr_high << 8) | ptr_low
            
            # Gli altri 5 byte sono metadati del gioco
            metadata_hex = entry_bytes[3:8].hex(' ').upper()

            text_pc_offset = snes_to_pc(text_snes_addr, is_hirom, has_header)
            if text_pc_offset is None or text_pc_offset >= len(rom_data):
                continue

            # Leggi testo fino al byte 0x00 (BNE/JMP)
            raw_text = bytearray()
            curr = text_pc_offset
            while curr < len(rom_data) and rom_data[curr] != 0x00:
                raw_text.append(rom_data[curr])
                curr += 1

            extracted.append({
                "id": i,
                "table_entry_pc": hex(entry_pc_offset),
                "metadata_hex": metadata_hex,
                "text_snes_address": hex(text_snes_addr),
                "text_pc_address": hex(text_pc_offset),
                "max_bytes_length": len(raw_text),
                "raw_hex": raw_text.hex(' ').upper(),
                "decoded_text": decode_text(raw_text, table)
            })

    with open(json_out, 'w', encoding='utf-8') as f:
        json.dump(extracted, f, indent=4, ensure_ascii=False)
    print(f"Estratte {len(extracted)} voci con successo in {json_out}!")

# --- CONFIGURAZIONE CON I TUOI DATI ---
if __name__ == "__main__":
    ROM_FILE = "gioco.smc"
    TBL_FILE = "gioco.tbl"
    JSON_OUT = "testo_estratto.json"

    # Inserisci il Banco dove risiede la tabella (es. 0x806000 o 0xC06000)
    TABLE_SNES_ADDRESS = 0x806000  # $6000 combinato con il banco corrente
    NUM_ENTRIES = 100               # Numero di frasi da estrarre
    IS_HIROM = False                # Imposta True se il gioco è HiROM
    HAS_HEADER = False              # Imposta True se la ROM ha l'header di 512 byte

    extract_game_text(ROM_FILE, TABLE_SNES_ADDRESS, NUM_ENTRIES, TBL_FILE, JSON_OUT, IS_HIROM, HAS_HEADER)