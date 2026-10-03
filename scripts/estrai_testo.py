import json
import struct
import os

def snes_lorom_to_pc(snes_address):
    """Converte un indirizzo SNES LoROM (24-bit) in un offset file PC."""
    bank = (snes_address & 0xFF0000) >> 16
    offset = snes_address & 0xFFFF
    
    # Rimuovi l'header SMC se presente (questo script assume una ROM senza header, aggiungi +512 se ha l'header)
    if offset < 0x8000:
        raise ValueError("Indirizzo non valido per LoROM (deve essere >= 0x8000)")
        
    return ((bank & 0x7F) * 0x8000) + (offset - 0x8000)

def load_table_file(tbl_path):
    """Carica un file .tbl (Hex=Testo)."""
    table = {}
    if not os.path.exists(tbl_path):
        print(f"File TBL non trovato: {tbl_path}. Verrà stampato l'esadecimale grezzo.")
        return table

    with open(tbl_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '=' in line:
                hex_str, char = line.split('=', 1)
                # Supporta byte singoli (es. 4A=A) o doppi (es. 054A=A)
                table[bytes.fromhex(hex_str)] = char
    return table

def decode_text(raw_bytes, table):
    """Decodifica i byte usando la tabella fornita."""
    if not table:
        return raw_bytes.hex(' ').upper()
    
    decoded = ""
    i = 0
    while i < len(raw_bytes):
        # Tenta di leggere 2 byte prima (se il gioco usa codifiche multi-byte)
        if i < len(raw_bytes) - 1 and raw_bytes[i:i+2] in table:
            decoded += table[raw_bytes[i:i+2]]
            i += 2
        elif bytes([raw_bytes[i]]) in table:
            decoded += table[bytes([raw_bytes[i]])]
            i += 1
        else:
            decoded += f"[{hex(raw_bytes[i])}]" # Byte sconosciuto
            i += 1
    return decoded

def extract_text(rom_path, pointer_table_pc_offset, num_pointers, text_bank, terminator_byte, tbl_path, output_json):
    table = load_table_file(tbl_path)
    extracted_data = []

    with open(rom_path, 'rb') as rom:
        rom_data = rom.read()

        for i in range(num_pointers):
            # Leggi il puntatore a 16 bit (Little Endian)
            ptr_offset = pointer_table_pc_offset + (i * 2)
            pointer = struct.unpack('<H', rom_data[ptr_offset:ptr_offset+2])[0]
            
            # Ricostruisci l'indirizzo SNES completo unendo la Banca e il Puntatore
            snes_address = (text_bank << 16) | pointer
            
            try:
                text_pc_offset = snes_lorom_to_pc(snes_address)
            except ValueError:
                continue

            # Leggi il testo fino al byte terminatore
            raw_text_bytes = bytearray()
            current_offset = text_pc_offset
            while True:
                byte = rom_data[current_offset]
                if byte == terminator_byte:
                    break
                raw_text_bytes.append(byte)
                current_offset += 1

            # Crea l'oggetto per il JSON (utile per il reinserimento)
            text_entry = {
                "id": i,
                "pointer_address_pc": hex(ptr_offset),
                "pointer_value_hex": hex(pointer),
                "text_snes_address": hex(snes_address),
                "text_pc_address": hex(text_pc_offset),
                "max_bytes_length": len(raw_text_bytes), # Cruciale per reinserire il testo senza sovrascrivere
                "raw_hex": raw_text_bytes.hex(' ').upper(),
                "decoded_text": decode_text(raw_text_bytes, table)
            }
            extracted_data.append(text_entry)

    # Salva il file JSON
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(extracted_data, f, indent=4, ensure_ascii=False)
    
    print(f"Estratte {len(extracted_data)} stringhe in {output_json}")

# --- PARAMETRI DI CONFIGURAZIONE (DA CAMBIARE IN BASE AL GIOCO) ---
if __name__ == "__main__":
    ROM_FILE = "gioco.smc"
    TBL_FILE = "gioco.tbl"
    JSON_OUT = "testo_estratto.json"
    
    # Esempio: La tabella dei puntatori inizia all'indirizzo PC 0x10000
    POINTER_TABLE_OFFSET = 0x10000 
    NUM_STRINGS = 150 # Quante stringhe vuoi leggere
    TEXT_BANK = 0xC0 # In quale banco SNES risiede il testo (es. C0, C1, C2)
    TERMINATOR = 0x00 # Il byte che indica la fine di una stringa (spesso 00 o FF)

    # Decommenta la riga sotto per eseguire lo script
    extract_text(ROM_FILE, POINTER_TABLE_OFFSET, NUM_STRINGS, TEXT_BANK, TERMINATOR, TBL_FILE, JSON_OUT)