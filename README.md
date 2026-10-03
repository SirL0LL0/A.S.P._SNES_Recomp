# A.S.P. — Air Strike Patrol (SNES): traduzione italiana + recompilazione

Progetto in due parti:

1. **Traduzione italiana** della ROM SNES *A.S.P. – Air Strike Patrol (USA)*: testo compresso, stringhe in chiaro, grafica disegnata, titoli di coda. Stato: v7 (vedi `docs/`).
2. **Recompilazione statica** del gioco (con la traduzione incorporata) basata sul Retro Porting Toolkit: `snesrecomp`, `recomp-ui`, `Retro-Runtime`.

## Struttura

| Cartella | Contenuto |
|---|---|
| `tools/` | `asp_core.py` (compressore/iniettore), `asp_import.py` (allineamento traduzione), tool grafico v7 |
| `translation/` | progetti `.json`, `correzioni.json`, patch IPS, testo sorgente, revisione |
| `scripts/` | script di estrazione/scansione usati durante la ricerca |
| `emulator/` | sorgenti di `asp_run` (emulatore senza finestra, tracciamento runtime) |
| `examples/` | scenari tasti e riferimenti |
| `docs/` | scoperte tecniche, tabelle verificate sul codice |
| `recomp/` | (da creare) integrazione con snesrecomp |

## ROM

La ROM **non è inclusa** (copyright). Metti la tua dump in `rom/` (ignorata da git). Si distribuiscono solo sorgenti e patch IPS.

## Costruire la ROM italiana

    python tools/ASP_Translation_Tool_v7.py

(Python 3.13, carica la ROM originale e il progetto `translation/ASP_ITA_v7_progetto.json`.)
