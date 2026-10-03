import os

def compress_snes_optimal_fast(uncompressed_data: bytes) -> bytes:
    """
    Compressore LZSS ottimizzato con ricerca rapida a finestre (Window Search)
    e programmazione dinamica per la massima riduzione della dimensione.
    """
    n = len(uncompressed_data)
    if n == 0:
        return bytes([0x00, 0x00, 0x00, 0x00])

    dp = [float('inf')] * (n + 1)
    next_choice = [None] * (n + 1)
    dp[n] = 0

    # 1. Programmazione dinamica a ritroso (Backwards DP)
    for i in range(n - 1, -1, -1):
        # Opzione 1: Byte literale (9 bit = 1 bit flag + 8 bit dato)
        dp[i] = 9 + dp[i + 1]
        next_choice[i] = (1, 0)

        # Opzione 2: Riferimento LZSS (17 bit = 1 bit flag + 16 bit offset/len)
        max_len = min(n - i, 18)
        if max_len >= 3:
            window_start = max(0, i - 4095)
            window = uncompressed_data[window_start:i]
            prefix3 = uncompressed_data[i:i+3]
            
            # Ricerca ottimizzata delle occorrenze
            pos = window.find(prefix3)
            while pos != -1:
                match_offset = (i - window_start) - pos
                match_len = 3
                while match_len < max_len and uncompressed_data[i + match_len] == uncompressed_data[i - match_offset + match_len]:
                    match_len += 1
                
                # Valutazione di tutte le sotto-lunghezze
                for l in range(3, match_len + 1):
                    cost = 17 + dp[i + l]
                    if cost < dp[i]:
                        dp[i] = cost
                        next_choice[i] = (l, match_offset)
                
                pos = window.find(prefix3, pos + 1)

    # 2. Ricostruzione della sequenza di token
    tokens = []
    pos = 0
    while pos < n:
        l, offset = next_choice[pos]
        tokens.append((l, offset, pos))
        pos += l

    # 3. Codifica dello stream LZSS per SNES
    out = bytearray()
    out.append(0x00)                            # Byte di modalità compresso
    out.append(n & 0xFF)                        # Lunghezza decompressa LSB
    out.append((n >> 8) & 0xFF)                 # Lunghezza decompressa MSB
    out.append(0x00)                            # Byte di skip/allineamento

    token_idx = 0
    num_tokens = len(tokens)

    while token_idx < num_tokens:
        flag_byte_index = len(out)
        out.append(0)                           # Byte di flag (8 bit di controllo)
        flags = 0

        for bit in range(8):
            if token_idx >= num_tokens:
                break

            l, offset, p = tokens[token_idx]
            token_idx += 1

            if l == 1:
                # Bit = 1: Literale
                flags |= (1 << bit)
                out.append(uncompressed_data[p])
            else:
                # Bit = 0: Riferimento LZSS
                raw_offset = (-offset) & 0x0FFF
                len_bits = (l - 3) & 0x0F
                b1 = raw_offset & 0xFF
                b2 = ((raw_offset >> 8) & 0x0F) | (len_bits << 4)
                out.append(b1)
                out.append(b2)

        out[flag_byte_index] = flags

    return bytes(out)


def comprimi_file(input_file: str, output_file: str):
    if not os.path.exists(input_file):
        print(f"❌ Impossibile trovare il file: '{input_file}'")
        print(" Modifica la variabile FILE_INPUT nel codice con il percorso esatto del tuo file.")
        return

    print(f"⏳ Lettura di '{input_file}'...")
    with open(input_file, "rb") as f:
        dati_decompressi = f.read()

    print(f"⚡ Compressione di {len(dati_decompressi)} byte in corso...")
    dati_compressi = compress_snes_optimal_fast(dati_decompressi)

    with open(output_file, "wb") as f:
        f.write(dati_compressi)

    print(f"✅ File generato con successo: '{output_file}'")
    print(f" ├─ Dimensione originale:  {len(dati_decompressi)} byte")
    print(f" ├─ Dimensione compressa:  {len(dati_compressi)} byte")
    print(f" └─ Rapporto compressione: {len(dati_decompressi) / len(dati_compressi):.2f}x")


# --- CONFIGURAZIONE NOMI FILE ED ESECUZIONE ---
if __name__ == "__main__":
    # Sostituisci "blocco_decompresso_output.bin" con il nome esatto del tuo file su disco
    FILE_INPUT = "ASP_test_DISTRUGGI_RADAR_decompressed.bin"
    FILE_OUTPUT = "blocco_ricompresso.bin"

    comprimi_file(FILE_INPUT, FILE_OUTPUT)