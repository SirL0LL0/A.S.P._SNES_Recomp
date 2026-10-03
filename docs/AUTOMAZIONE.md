# Automazione della recomp (`tools/asp_auto.py`)

Un solo comando porta la recomp dalla ROM al codice nativo validato. Gira su Windows, Linux e macOS.

## Requisiti

- Python 3.9+ con `numpy` e `pillow` (`pip install numpy pillow`)
- CMake, Ninja (oppure Visual Studio), Git, Rust (rustup: la versione la sceglie `snesrecomp/rust-toolchain.toml`)
- Su Windows: Git for Windows (fornisce `bash`)
- La ROM USA originale: CRC32 `05c0da54`

## Il ciclo completo

```sh
python tools/asp_auto.py cycle examples/scenario_missione1.txt examples/scenario_volo.txt \
       --orig "C:/roms/A.S.P. - Air Strike Patrol (USA).sfc"
```

Fa, in ordine:

1. **rom**: ROM USA + `translation/patches/ASP_ITA.ips` → `rom/ASP_ITA.sfc`, e aggiorna i digest in `rom_identity.txt`.
2. **build**: compila la recomp e l'emulatore di riferimento `asp_run`.
3. **promote**:
   1. *baseline interpretata*: genera la recomp senza profili, quindi quasi tutto passa dall'interprete, e la esegue sugli scenari. È il riferimento di correttezza.
   2. *cattura*: esegue gli scenari con la cattura di copertura di snesrecomp, che registra dove lavora l'interprete.
   3. *promozione*: rigenera con i profili, così il codice eseguito diventa C nativo (AOT), e ricompila.
   4. *validazione*: riesegue gli scenari. Il risultato deve finire senza `BRK`, watchdog o timeout, eseguire tutti i frame e mostrare le stesse schermate della baseline, allineate nel tempo entro ±180 frame.
   5. *bisezione automatica*: se la validazione fallisce, divide a metà le funzioni appena promosse finché non trova quelle che rompono il gioco. Le scrive in `recomp/symbols.toml` (blocco `asp_auto`, `emit = false`), dove restano interpretate. Poi riconvalida.
   6. accetta i profili in `coverage/accepted/` e misura quanto lavoro è passato dall'interprete al codice nativo.

Il resoconto finisce in `runs/report/promote.json`.

## Comandi singoli

| Comando | Cosa fa |
|---|---|
| `rom --orig <usa.sfc> [--update-identity]` | crea la ROM italiana; con `--update-identity` aggiorna i digest (da usare quando cambia la traduzione) |
| `build [--emulator]` | compila la recomp (e `asp_run`) |
| `regen [--no-profiles]` | rigenera `src/gen` con i profili accettati |
| `ref <scenario>` | esegue lo scenario su `asp_run`; il risultato va in cache finché scenario e ROM non cambiano |
| `run <scenario> [--coverage]` | esegue lo scenario sulla recomp, senza finestra e alla massima velocità |
| `check <scenario> [--reference]` | esegue e confronta con la baseline interpretata (correttezza dell'AOT) e, se richiesto, con `asp_run` (fedeltà all'hardware); scrive il report HTML `runs/report/check.html` |
| `promote <scenari…>` | il ciclo copertura → AOT → validazione → bisezione |

## Scenari

Sono i file di `examples/` nel formato di `asp_run` (`frame N tasti`, `none` per rilasciare). `asp_auto` li converte in script per la recomp (`wait`/`press`) e inserisce un'istantanea (`dump`) ogni `--every` frame.

Per coprire nuove parti del gioco basta aggiungere uno scenario: tutto il codice che esegue diventa candidato alla promozione.

Gli scenari usano frame assoluti, mentre la recomp e l'hardware hanno uno scarto di qualche frame (vedi sotto). I tasti vanno quindi premuti quando il gioco aspetta l'input, non in finestre strette.

## Cosa resta locale

- `runs/`: tutte le esecuzioni, i log e i report.
- `coverage/`: i profili di copertura. Possono contenere byte di codice che il gioco copia dalla ROM in RAM, quindi **non** vanno pubblicati.
- `rom/`, `src/gen/`: la ROM e il C generato, entrambi derivati dalla ROM.

Nella repo vanno solo le **decisioni**: le esclusioni in `recomp/symbols.toml`.

## Variabili utili del frame driver (`src/game_rtl.c`)

| Variabile | Effetto |
|---|---|
| `ASP_RTL_TRACE=1` | registra ogni NMI/IRQ consegnato: riga del raggio, PC, flag I, cambi di task |
| `ASP_RTL_TRACE=2` | registra anche ogni istruzione interpretata dentro gli handler IRQ |
| `ASP_RTL_TRACE_FROM=n`, `ASP_RTL_TRACE_TO=m` | registra le istruzioni del programma principale nei frame n..m |
| `ASP_HDMA_STEAL=0` | disattiva l'addebito del tempo HDMA alla CPU (per confronti A/B) |

## Limiti noti

- **Temporizzazione**: rispetto a `asp_run` la recomp resta in anticipo di circa 7 frame all'avvio, nell'attesa che il driver audio SPC700 sia pronto, e accumula circa l'1% durante le animazioni. Per stabilire chi dei due sbaglia serve un terzo riferimento, come bsnes o Mesen tramite `snesrecomp/tools/snesref`.
- **Copertura**: la validazione è forte solo quanto gli scenari. Una funzione promossa che gli scenari non eseguono non viene verificata.
