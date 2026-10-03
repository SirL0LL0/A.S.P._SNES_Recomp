def estrai_blocco(rom_path, output_path, offset, dimensione):
    with open(rom_path, "rb") as f:
        f.seek(offset)
        dati = f.read(dimensione)
    with open(output_path, "wb") as f:
        f.write(dati)

# Parametri basati sui dati forniti
ROM_PATH = "gioco.sfc"
OUTPUT_PATH = "blocco_compresso.bin"
OFFSET = 0x02A6FF
DIMENSIONE_COMPRESSA = 0x3607

estrai_blocco(ROM_PATH, OUTPUT_PATH, OFFSET, DIMENSIONE_COMPRESSA)