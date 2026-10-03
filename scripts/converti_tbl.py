import re
import sys


def convert_report_to_tbl(report_path, output_tbl_path='output.tbl'):
  try:
    with open(report_path, 'r', encoding='utf-8') as f:
      lines = f.readlines()
  except FileNotFoundError:
    print(f'Errore: File {report_path} non trovato.')
    return

  tbl_entries = []

  # Regex per catturare l'Hex (es. 0x84) e il carattere dentro le parentesi [ x ]
  pattern = re.compile(
      r'0x([0-9A-Fa-f]{2})\s+\|\s+\d+\s+\|\s+[\d.]+\s+\|\s+[\d.]+%\s+\|\s+\[\s*([^\s\]]+)\s*\]'
  )

  for line in lines:
    match = pattern.search(line)
    if match:
      hex_val = match.group(1).upper()
      char_val = match.group(2)
      # Esclude le parentesi se sono rimaste vuote o con segnaposto non validi
      if char_val and char_val not in ['?', '']:
        tbl_entries.append(f'{hex_val}={char_val}')

  if not tbl_entries:
    print('[Attenzione] Nessun carattere mappato trovato nel report.')
    print(
        'Assicurati di aver inserito i caratteri desiderati dentro le quadre'
        ' (es. [ a ], [ b ]) nel file di report.'
    )
    return

  with open(output_tbl_path, 'w', encoding='utf-8') as out:
    for entry in tbl_entries:
      out.write(entry + '\n')

  print(
      f'[Successo] File TBL generato con successo: {output_tbl_path}'
      f' ({len(tbl_entries)} associazioni scritte).'
  )


if __name__ == '__main__':
  if len(sys.argv) < 2:
    print('Uso: python convert_to_tbl.py <report_compilato.txt> [output.tbl]')
  else:
    report_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'output.tbl'
    convert_report_to_tbl(report_file, output_file)