def decomprimi_blocco_lz(file_compresso, file_decompresso):
    with open(file_compresso, 'rb') as f:
        dati = f.read()
    
    # Salta l'header di 4 byte (00 BF 8C 00) se presente all'inizio del blocco estratto
    if len(dati) >= 4 and dati[0:4] == b'\x00\xBF\x8C\x00':
        pos = 4
    else:
        pos = 0
        
    out = bytearray()
    
    while pos < len(dati):
        flag = dati[pos]
        pos += 1
        for bit in range(8):
            if pos >= len(dati):
                break
                
            # Bit = 1: byte letterale; Bit = 0: riferimento LZ
            if (flag >> bit) & 1:
                out.append(dati[pos])
                pos += 1
            else:
                if pos + 1 >= len(dati):
                    break
                b1 = dati[pos]
                b2 = dati[pos+1]
                pos += 2
                
                length = (b2 >> 4) + 3
                magnitude = b1 | ((b2 & 0x0F) << 8)
                distanza = 0x1000 - magnitude
                
                src_pos = len(out) - distanza
                for _ in range(length):
                    if src_pos < 0:
                        out.append(0x20) # Riempimento con spazio se il puntatore è fuori buffer
                    else:
                        out.append(out[src_pos])
                    src_pos += 1
                    
    with open(file_decompresso, 'wb') as f:
        f.write(out)
        
    print(f"[+] Decompressione completata: {len(out)} byte generati in '{file_decompresso}'")

# Esecuzione della funzione
decomprimi_blocco_lz('blocco_compresso.bin', 'blocco_decompresso_output.bin')