import os

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
    senza applicare alcun padding (i dati residui nello slot restano invariati).
    """
    with open(bin_ricompresso, "rb") as f:
        dati_nuovi = f.read()
    
    len_nuovi = len(dati_nuovi)
    
    # 1. Controllo limite massimo dello slot
    if len_nuovi > dimensione_slot:
        eccedenza = len_nuovi - dimensione_slot
        raise ValueError(
            f"❌ Errore: Il blocco ricompresso ({len_nuovi} byte) "
            f"supera lo spazio disponibile ({dimensione_slot} byte) di {eccedenza} byte."
        )

    with open(rom_originale, "rb") as f:
        rom = bytearray(f.read())

    # 2. Rilevamento automatico Header SMC (512 byte)
    offset_effettivo = offset
    if gestisci_header_smc and (len(rom) % 0x8000 == 0x200):
        offset_effettivo += 0x200
        print("ℹ️ Rilevato Header SMC (+512 byte). Scostamento applicato automaticamente.")

    # 3. Iniezione diretta dei soli byte nuovi (senza padding)
    rom[offset_effettivo : offset_effettivo + len_nuovi] = dati_nuovi

    # 4. Salvataggio file
    with open(rom_modificata, "wb") as f:
        f.write(rom)

    percentuale_usata = (len_nuovi / dimensione_slot) * 100
    print(f"✅ Iniezione completata con successo in '{rom_modificata}'")
    print(f" ├─ Offset scrittura PC: 0x{offset_effettivo:06X}")
    print(f" ├─ Dati scritti:        {len_nuovi} byte ({percentuale_usata:.1f}% dello slot)")
    print(f" └─ Spazio non toccato:  {dimensione_slot - len_nuovi} byte rimanenti nello slot")


# --- ESECUZIONE SCRIPT ---
ROM_ORIGINALE = "gioco.sfc"
ROM_MODIFICATA = "gioco_tradotto.sfc"
BIN_RICOMPRESSO = "blocco_ricompresso.bin"

ricomponi_e_inietta(
    rom_originale=ROM_ORIGINALE,
    rom_modificata=ROM_MODIFICATA,
    bin_ricompresso=BIN_RICOMPRESSO,
    offset=0x02A6FF,
    dimensione_slot=0x3607
)