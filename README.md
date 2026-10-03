# A.S.P. — Air Strike Patrol (SNES): traduzione italiana + recompilazione nativa

![Schermata del titolo in italiano, dal binario recompilato](docs/img/recomp_titolo_ita.png)

Il progetto è diviso in due parti:

1. **Traduzione italiana** della ROM SNES *A.S.P. – Air Strike Patrol (USA)*: testo compresso, stringhe in chiaro, grafica disegnata e titoli di coda. La distribuzione è una patch IPS.
2. **Recompilazione statica** del gioco già tradotto, fatta con [snesrecomp](https://github.com/RetroPortingToolKit/snesrecomp) e con il launcher/menu in-game [recomp-ui](https://github.com/mstan/recomp-ui). Il risultato è un eseguibile nativo per Windows, Linux e macOS.

> **La ROM non è inclusa.** Serve una copia legittima del gioco originale. In questa repo non c'è nessun dato della ROM: anche il C generato dal recompilatore (`src/gen/`) resta fuori da git.

## Stato

| Parte | Stato |
|---|---|
| Traduzione | v7. `translation/patches/ASP_ITA.ips` ricrea esattamente la ROM italiana di riferimento (CRC32 `7d4c26e3`) |
| Frame driver | Ancorato al raggio: NMI alla riga 225, IRQ dello scheduler a task gestito con il vero cambio di contesto. Arriva a briefing, mappa missione, Comando HQ e volo come l'emulatore di riferimento (`src/game_rtl.c`) |
| Codice nativo (AOT) | Promosso in automatico da `tools/asp_auto.py`: copertura, poi promozione, validazione e bisezione delle funzioni che rompono il gioco (vedi [docs/AUTOMAZIONE.md](docs/AUTOMAZIONE.md)) |

## 1. Creare la ROM italiana

```sh
mkdir rom
python tools/apply_ips.py "percorso/A.S.P. - Air Strike Patrol (USA).sfc" translation/patches/ASP_ITA.ips rom/ASP_ITA.sfc --crc 7d4c26e3
```

ROM originale attesa: CRC32 `05c0da54`, SHA-256 `69c5805a…b7f9`. La cartella `rom/` è ignorata da git.

## 2. Compilare la recomp

Requisiti: Python 3.9+, Rust (la versione è fissata da `snesrecomp/rust-toolchain.toml`), CMake, Ninja oppure MSVC, Git. Su Windows conviene Git Bash per eseguire `tools/regen.sh`.

```sh
git clone --recursive https://github.com/SirL0LL0/A.S.P._SNES_Recomp
cd A.S.P._SNES_Recomp
bash tools/regen.sh --rom rom/ASP_ITA.sfc          # verifica la ROM e genera src/gen/*.c
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
./build/ASPAirStrikePatrolItaSNESRecomp            # si apre il launcher recomp-ui
```

`regen.sh` rifiuta qualsiasi ROM con digest diverso da quelli in `rom_identity.txt`. Ogni volta che la traduzione cambia, bisogna aggiornare quel file con i nuovi digest e rigenerare.

## Struttura

| Percorso | Contenuto |
|---|---|
| `translation/` | progetti `.json`, `correzioni.json`, patch IPS, testo sorgente, revisione |
| `tools/` | `asp_auto.py` (pipeline automatica), `asp_disasm.py` (disassemblatore 65816), `asp_core.py` (compressore e iniettore), `asp_import.py`, tool grafico v7, `apply_ips.py`, `regen.sh` |
| `scripts/` | script di estrazione e scansione usati durante la ricerca |
| `emulator/` | sorgenti di `asp_run`, l'emulatore di riferimento senza finestra, basato su Snaggletooth (submodule) con `--irqlog` |
| `examples/` | scenari di input e riferimenti |
| `docs/` | note tecniche e tabelle verificate. `RECOMP_SCAFFOLD_README.md` è la guida originale di snesrecomp |
| `recomp/` | input dell'analisi: `bank*.cfg`, `symbols.toml` |
| `src/` | codice host specifico del gioco: `main.c`, `game_rtl.c`. `src/gen/` viene generato e non va in git |
| `rom_identity.txt` | digest della ROM italiana, letti da build, regen e CI |
| `snesrecomp/`, `recomp-ui/` | submodule del framework, fissati ai commit elencati in `framework_pins.txt` |

## Automazione

```sh
python tools/asp_auto.py cycle examples/scenario_missione1.txt --orig "percorso/ROM USA.sfc"
```

Crea la ROM, compila, cattura la copertura, promuove il codice a nativo, lo valida e isola da sola le funzioni che non reggono. I dettagli sono in [docs/AUTOMAZIONE.md](docs/AUTOMAZIONE.md).

## Prossimi passi della recomp

1. Aggiungere scenari che coprano tutte le missioni: ogni scenario estende la copertura e quindi il codice nativo validato.
2. Capire, una per una, le funzioni escluse da `asp_auto` (blocco in `recomp/symbols.toml`) e correggerne la causa in snesrecomp.
3. Chiudere lo scarto di temporizzazione residuo usando un terzo emulatore di riferimento (snesref con bsnes o Mesen).
4. Non modificare mai `src/gen/` a mano.

## Licenza

Il codice originale di questo progetto è sotto PolyForm Noncommercial 1.0.0 (`LICENSE`), che è anche la licenza di snesrecomp: **niente uso commerciale**. I framework hanno le proprie condizioni, riportate in `snesrecomp/LICENSE`, `snesrecomp/THIRD_PARTY_ATTRIBUTION.md` e `recomp-ui/LICENSE` (MIT). Nessuna di queste licenze copre i dati del gioco, che restano di SETA.
