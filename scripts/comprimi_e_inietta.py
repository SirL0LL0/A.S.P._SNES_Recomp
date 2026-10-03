import os

# =========================================================
# FASE 1: FUNZIONI DI COMPRESSIONE
# =========================================================

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


def comprimi_file(input_file: str, output_file: str) -> bool:
    """Legge il file, lo comprime e lo salva. Restituisce True se ha successo."""
    if not os.path.exists(input_file):
        print(f"❌ Impossibile trovare il file: '{input_file}'")
        print("   Modifica la variabile FILE_INPUT nel codice con il percorso esatto del tuo file.")
        return False

    print(f"⏳ Lettura di '{input_file}'...")
    with open(input_file, "rb") as f:
        dati_decompressi = f.read()

    print(f"⚡ Compressione di {len(dati_decompressi)} byte in corso (potrebbe richiedere qualche secondo)...")
    dati_compressi = compress_snes_optimal_fast(dati_decompressi)

    with open(output_file, "wb") as f:
        f.write(dati_compressi)

    print(f"✅ File compresso generato con successo: '{output_file}'")
    print(f" ├─ Dimensione originale:  {len(dati_decompressi)} byte")
    print(f" ├─ Dimensione compressa:  {len(dati_compressi)} byte")
    print(f" └─ Rapporto compressione: {len(dati_decompressi) / len(dati_compressi):.2f}x")
    return True


# =========================================================
# FASE 2: FUNZIONI DI INIEZIONE
# =========================================================

def ricomponi_e_inietta(
    rom_originale: str,
    rom_modificata: str,
    bin_ricompresso: str,
    offset: int,
    dimensione_slot: int,
    gestisci_header_smc: bool = True
):
    """
    Inietta un file binario ricompresso all'interno di una ROM SNES
    senza applicare alcun padding.
    """
    if not os.path.exists(rom_originale):
        raise FileNotFoundError(f"Impossibile trovare la ROM originale: '{rom_originale}'")

    with open(bin_ricompresso, "rb") as f:
        dati_nuovi = f.read()
    
    len_nuovi = len(dati_nuovi)
    
    # Controllo limite massimo dello slot
    if len_nuovi > dimensione_slot:
        eccedenza = len_nuovi - dimensione_slot
        raise ValueError(
            f"Errore: Il blocco ricompresso ({len_nuovi} byte) "
            f"supera lo spazio disponibile ({dimensione_slot} byte) di {eccedenza} byte."
        )

    with open(rom_originale, "rb") as f:
        rom = bytearray(f.read())

    # Rilevamento automatico Header SMC (512 byte)
    offset_effettivo = offset
    if gestisci_header_smc and (len(rom) % 0x8000 == 0x200):
        offset_effettivo += 0x200
        print("ℹ️ Rilevato Header SMC (+512 byte). Scostamento applicato automaticamente.")

    # Iniezione diretta
    rom[offset_effettivo : offset_effettivo + len_nuovi] = dati_nuovi

    # Salvataggio file
    with open(rom_modificata, "wb") as f:
        f.write(rom)

    percentuale_usata = (len_nuovi / dimensione_slot) * 100
    print(f"✅ Iniezione completata con successo in '{rom_modificata}'")
    print(f" ├─ Offset scrittura PC: 0x{offset_effettivo:06X}")
    print(f" ├─ Dati scritti:        {len_nuovi} byte ({percentuale_usata:.1f}% dello slot)")
    print(f" └─ Spazio non toccato:  {dimensione_slot - len_nuovi} byte rimanenti nello slot")


# =========================================================
# CONFIGURAZIONE ED ESECUZIONE COMBINATA
# =========================================================

if __name__ == "__main__":
    
    # --- VARIABILI DI CONFIGURAZIONE ---
    FILE_INPUT      = "ASP_test_DISTRUGGI_RADAR_decompressed.bin"
    FILE_COMPRESSO  = "blocco_ricompresso.bin" # File temporaneo creato dalla Fase 1
    
    ROM_ORIGINALE   = "gioco.sfc"
    ROM_MODIFICATA  = "gioco_tradotto.sfc"
    OFFSET          = 0x02A6FF
    DIMENSIONE_SLOT = 0x3607
    # -----------------------------------

    print("=============================================")
    print("      FASE 1: COMPRESSIONE DEL FILE")
    print("=============================================")
    
    successo_compressione = comprimi_file(FILE_INPUT, FILE_COMPRESSO)

    if successo_compressione:
        print("\n=============================================")
        print("      FASE 2: INIEZIONE NELLA ROM")
        print("=============================================")
        
        try:
            ricomponi_e_inietta(
                rom_originale=ROM_ORIGINALE,
                rom_modificata=ROM_MODIFICATA,
                bin_ricompresso=FILE_COMPRESSO,
                offset=OFFSET,
                dimensione_slot=DIMENSIONE_SLOT
            )
            print("\n🎉 Operazione completata totalmente!")
        except Exception as e:
            print(f"❌ Iniezione fallita: {e}")
    else:
        print("\n❌ L'iniezione nella ROM è stata annullata a causa di un errore nella fase di compressione.")