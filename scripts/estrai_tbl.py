from collections import Counter
import sys


def analyze_snes_rom(
    rom_path, output_report_path='tbl_analysis_report.txt', top_n=100
):
  try:
    with open(rom_path, 'rb') as f:
      data = f.read()
  except FileNotFoundError:
    print(f'Errore: File {rom_path} non trovato.')
    return

  # Rilevamento e rimozione dell'header SMC (512 byte)
  header_present = False
  if len(data) % 1024 == 512:
    header_present = True
    rom_data = data[512:]
  else:
    rom_data = data

  byte_counts = Counter(rom_data)
  total_bytes = len(rom_data)

  with open(output_report_path, 'w', encoding='utf-8') as out:
    out.write('=' * 75 + '\n')
    out.write(' RAPPORTO ANALISI ROM SNES & MAKTABELLA TBL DI LAVORO\n')
    out.write('=' * 75 + '\n\n')
    out.write(f'File analizzato: {rom_path}\n')
    out.write(f'Dimensione ROM (senza header): {total_bytes} byte\n')
    out.write(
        f"Header SMC presente: {'Sì (rimosso)' if header_present else 'No'}\n\n"
    )

    out.write('-' * 75 + '\n')
    out.write(
        f' I {top_n} BYTE PIÙ FREQUENTI (Inserisci il carattere nella colonna'
        ' [ ? ])\n'
    )
    out.write('-' * 75 + '\n')
    out.write(
        f"{'Hex':<6} | {'Dec':<5} | {'Frequenza':<10} | {'Percentuale':<10} |"
        f" {'[ ? ]':<7} | {'Note ipotetiche'}\n"
    )
    out.write('-' * 75 + '\n')

    for byte_val, count in byte_counts.most_common(top_n):
      percentage = (count / total_bytes) * 100
      note = ''
      if byte_val == 0x00:
        note = 'Possibile terminatore / Padding (NULL)'
      elif byte_val == 0xFF:
        note = 'Possibile padding / Vuoto'
      elif count > total_bytes * 0.01:
        note = 'Alta frequenza (possibile Spazio o carattere comune)'

      # Colonna [ ? ] lasciata vuota per l'inserimento manuale
      out.write(
          f'0x{byte_val:02X}   | {byte_val:<5} | {count:<10} |'
          f' {percentage:5.2f}%    | [   ]   | {note}\n'
      )

    out.write('\n' + '=' * 75 + '\n')
    out.write(' SUGGERIMENTI:\n')
    out.write('=' * 75 + '\n')
    out.write(
        '1. Compila la colonna [   ] scrivendo il carattere associato al'
        ' rispettivo byte esadecimale.\n'
    )
    out.write(
        '2. Una volta identificati i caratteri chiave, potrai convertirli in'
        ' formato TBL standard (es. 00=A).\n'
    )

  print(f'[Successo] Report generato con successo: {output_report_path}')


if __name__ == '__main__':
  if len(sys.argv) < 2:
    print('Uso: python snes_tbl_analyzer.py <percorso_rom.sfc>')
  else:
    rom_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'tbl_analysis_report.txt'
    analyze_snes_rom(rom_file, output_file)