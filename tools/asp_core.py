# -*- coding: utf-8 -*-
"""
asp_core — logica ROM del tool di traduzione A.S.P. (senza interfaccia grafica).
Importabile e testabile da solo:  python asp_core.py selftest <rom.sfc>

Contiene: LZ (decompressore identico all'ASM $83:8296 + compressore dell'utente),
parser del blocco decompresso, catalogo stringhe in chiaro verificate, build della ROM
(in place senza padding oppure ROM espansa a 2 MB), checksum, IPS, runner emulatore.
"""
import os, re, csv, json, subprocess, tempfile

# ── COSTANTI ROM (tutte verificate sul codice) ───────────────────────────────
LZ_OFFSET   = 0x02A6FF   # $85:A6FF  blocco compresso originale
LZ_SLOT     = 0x3607     # 13831 byte: fine slot = $85:DD06 (dopo iniziano strutture dati!)
DECOMP_SIZE = 36031      # decompresso in WRAM $7E:6000-$7E:ECBE
# $83:925B  decompress($85:A6FF -> $7E:6000):  ...LDA #$85 / PHA / PEA $A6FF / JSL $83:8000
LZ_PTR_BANK_PC = 0x01926A   # operando di LDA #$85  ($83:926A)
LZ_PTR_ADDR_PC = 0x01926D   # operando di PEA $A6FF ($83:926D-926E)
EXP_SIZE    = 0x200000      # ROM espansa: 2 MB
EXP_LZ_PC   = 0x100000      # $A0:8000 — bank nuovo, 32 KB tutti per il blocco compresso
EXP_GFX_PC  = 0x108000      # $A1:8000 — mappe grafiche ricompresse che non entrano più al loro posto
HDR_ROMSIZE_PC = 0x7FD7
HDR_CHK_PC     = 0x7FDC

ROW_MISSION = 56
ROW_FINALE  = 48


def smc_offset(rom):
    return 0x200 if len(rom) % 0x8000 == 0x200 else 0


# ── LZ ───────────────────────────────────────────────────────────────────────
def lz_decomp_ex(data, start=0):
    """Decompressore fedele all'ASM. Ritorna (output, byte_consumati, lunghezza_header)."""
    src = start; mode = data[src]; src += 1
    dl = data[src] | (data[src + 1] << 8); src += 3
    if mode & 0x80:
        return bytes(data[src:src + dl]), 4 + dl, dl
    out = bytearray(); bc = 0; fl = 0
    while len(out) < dl:
        if bc == 0:
            fl = data[src]; src += 1; bc = 8
        lit = fl & 1; fl >>= 1; bc -= 1
        if lit:
            out.append(data[src]); src += 1
        else:
            b1, b2 = data[src], data[src + 1]; src += 2
            ov = ((b1 | (b2 << 8)) | 0xF000) - 0x10000
            for _ in range((b2 >> 4) + 3):
                rp = len(out) + ov
                out.append(out[rp] if rp >= 0 else 0x20)
    return bytes(out), src - start, dl



# ── COMPRESSORE OTTIMALE ─────────────────────────────────────────────────────
# Costo reale: 1 bit di flag + 8 bit (letterale) oppure 16 bit (riferimento).
# La lunghezza massima di corrispondenza basta: la distanza non cambia il costo.
# La DP in byte conta anche i byte di flag, quindi il risultato e' il minimo
# possibile per questo formato.
LZ_MAXLEN = 18
LZ_MINLEN = 3
LZ_WINDOW = 4095


def lz_matches(data, max_chain=1 << 30):
    """Per ogni posizione: (lunghezza massima, distanza)."""
    n = len(data)
    best_len = bytearray(n); best_dist = [0] * n
    heads = {}; prev = [0] * n
    for i in range(n):
        key = data[i:i + LZ_MINLEN] if i + LZ_MINLEN <= n else None
        bl = 0; bd = 0
        if i > 0:                                   # distanza 1: ripetizioni lunghe (spazi)
            l = 0; lim = min(LZ_MAXLEN, n - i)
            while l < lim and data[i + l] == data[i - 1 + l]:
                l += 1
            if l >= LZ_MINLEN:
                bl, bd = l, 1
        if bl < LZ_MAXLEN and key is not None:
            cp = heads.get(key, -1); seen = 0
            while cp >= 0 and i - cp <= LZ_WINDOW and seen < max_chain:
                l = 0; lim = min(LZ_MAXLEN, n - i)
                while l < lim and data[i + l] == data[cp + l]:
                    l += 1
                if l > bl:
                    bl, bd = l, i - cp
                    if bl >= LZ_MAXLEN:
                        break
                cp = prev[cp]; seen += 1
        best_len[i] = bl if bl >= LZ_MINLEN else 0
        best_dist[i] = bd
        if key is not None:
            prev[i] = heads.get(key, -1); heads[key] = i
    return best_len, best_dist


def lz_parse(data, best_len, best_dist):
    """DP esatta in byte (conta i byte di flag): token di costo minimo."""
    n = len(data); INF = float('inf')
    cost = [[INF] * 8 for _ in range(n + 1)]
    ch = [[0] * 8 for _ in range(n + 1)]
    for r in range(8):
        cost[n][r] = 0
    for i in range(n - 1, -1, -1):
        ci = cost[i]; chi = ch[i]; m = best_len[i]
        for r in range(8):
            extra = 1 if r == 0 else 0
            r2 = (r + 1) & 7
            best = extra + 1 + cost[i + 1][r2]; pick = 0
            if m >= LZ_MINLEN:
                for L in range(LZ_MINLEN, m + 1):
                    c = extra + 2 + cost[i + L][r2]
                    if c < best:
                        best = c; pick = L
            ci[r] = best; chi[r] = pick
    toks = []; i = 0; r = 0
    while i < n:
        L = ch[i][r]
        if L:
            toks.append((L, best_dist[i])); i += L
        else:
            toks.append((0, data[i])); i += 1
        r = (r + 1) & 7
    return toks


def lz_encode(data, toks):
    out = bytearray([0x00, len(data) & 0xFF, (len(data) >> 8) & 0xFF, 0x00])
    i = 0
    while i < len(toks):
        fi = len(out); out.append(0); flags = 0
        for bit in range(8):
            if i >= len(toks):
                break
            L, v = toks[i]; i += 1
            if L == 0:
                flags |= 1 << bit; out.append(v)
            else:
                raw = (-v) & 0x0FFF
                out.append(raw & 0xFF); out.append(((raw >> 8) & 0x0F) | ((L - 3) << 4))
        out[fi] = flags
    return bytes(out)


def lz_comp_ottimale(data, max_chain=1 << 30):
    bl, bd = lz_matches(data, max_chain)
    return lz_encode(data, lz_parse(data, bl, bd))


def lz_decomp(data, start=0):
    return lz_decomp_ex(data, start)[0]

def lz_comp_veloce(data):
    """Compressore ottimizzato con hash-chain (molto più veloce e più efficiente)."""
    out=bytearray([0x00, len(data)&0xFF, (len(data)>>8)&0xFF, 0x00])
    n=len(data); pos=0
    # hash-chain index: chiave = 3 byte -> lista posizioni
    heads={}
    def key(i):
        if i+2 < n: return data[i]|(data[i+1]<<8)|(data[i+2]<<16)
        return None
    while pos<n:
        fi=len(out); out.append(0); fl=0
        for bit in range(8):
            if pos>=n: break
            bl=0; bo=0
            mx=min(n-pos,18)
            if mx>=3:
                k=key(pos)
                if k is not None:
                    cands=heads.get(k,())
                    lo=pos-0xFFF
                    # scorri i candidati dal più recente
                    for cp in reversed(cands):
                        if cp < lo: break
                        ml=0
                        while ml<mx and data[pos+ml]==data[cp+ml]: ml+=1
                        if ml>bl:
                            bl=ml; bo=pos-cp
                            if bl==mx: break
            if bl>=3:
                ro=(-bo)&0xFFF; lb=(bl-3)&0xF
                out.append(ro&0xFF); out.append(((ro>>8)&0xF)|(lb<<4))
                for q in range(pos,min(pos+bl,n-2)):
                    heads.setdefault(key(q),[]).append(q)
                pos+=bl
            else:
                fl|=(1<<bit); out.append(data[pos])
                if pos<n-2: heads.setdefault(key(pos),[]).append(pos)
                pos+=1
        out[fi]=fl
    return bytes(out)

# ── PARSER STRUTTURA (dinamico, non hardcoded) ───────────────────────────────
def parse_titles(d):
    """Trova i titoli: blocchi terminati da 0x13 0x00 nella zona iniziale."""
    titles=[]; pos=0
    while pos < 0x200 and len(titles) < 10:
        end=pos
        while end < 0x200 and not (d[end]==0x13 and end+1<len(d) and d[end+1]==0x00):
            end+=1
        if end>=0x200: break
        blk=d[pos:end]
        if len(blk) < 40: break
        titles.append({'off':pos,'len':len(blk),
                        'r1':blk[:30].decode('ascii','replace'),
                        'r2':blk[30:].decode('ascii','replace'),
                        'r2len':len(blk)-30})
        pos=end+2
    return titles

def parse_missions(d, first_off):
    """
    Parsa le schermate missione a partire da first_off.
    Ogni schermata è terminata da 0x13 0x00 (o 0x13 0x1E per continuazione).
    Restituisce lista di blocchi grezzi con offset e lunghezza.
    """
    screens=[]; pos=first_off
    while pos < 0x3800 and len(screens) < 60:
        # salta lead bytes di controllo
        lead=b''
        while pos<len(d) and d[pos] in (0x00,0x1E,0x1D):
            lead+=bytes([d[pos]]); pos+=1
            if len(lead)>3: break
        start=pos
        end=pos
        while end<len(d) and d[end]!=0x13:
            end+=1
        if end>=len(d): break
        screens.append({'off':start,'len':end-start,'lead':lead,
                         'text':d[start:end].decode('ascii','replace')})
        pos=end+1
    return screens

def find_finali(d):
    """Trova i blocchi finali: sequenze lunghe terminate da 0x00, righe da 48."""
    out=[]
    pos=0x6B22
    while pos < 0x7E1E and len(out) < 20:
        end=pos
        while end<len(d) and d[end]!=0x00:
            end+=1
        ln=end-pos
        if ln < 100: 
            pos=end+1; continue
        out.append({'off':pos,'len':ln,
                     'text':d[pos:end].decode('ascii','replace')})
        pos=end+1
    return out

def find_free_space(rom, min_len=16, fill=(0xFF,0x00)):
    """Trova blocchi contigui di byte di riempimento (spazio libero) nella ROM."""
    regions=[]
    for fb in fill:
        i=0; n=len(rom)
        while i<n:
            if rom[i]==fb:
                j=i
                while j<n and rom[j]==fb: j+=1
                if j-i >= min_len:
                    regions.append({'off':i,'len':j-i,'fill':fb})
                i=j
            else: i+=1
    regions.sort(key=lambda r:-r['len'])
    return regions

def pc_to_snes(pc):
    """LoROM: PC offset -> indirizzo SNES (banco 0x80+)."""
    bank=(pc//0x8000)+0x80
    addr=(pc%0x8000)+0x8000
    return bank, addr

def snes_to_pc(bank, addr):
    return ((bank & 0x7F)*0x8000) + (addr - 0x8000)

def title_rows(t1, t2, width=30):
    """Titolo su due righe: la riga piu' lunga e' centrata nello schermo (30 colonne),
    la piu' corta e' centrata rispetto alla piu' lunga (stesso centro, fra la sua
    prima e ultima lettera). Ritorna (riga1 di 30 caratteri, riga2 senza spazi in coda)."""
    t1 = t1.strip()[:width]; t2 = t2.strip()[:width]
    L = max(len(t1), len(t2))
    s = (width - L) // 2
    s1 = s + (L - len(t1)) // 2
    s2 = s + (L - len(t2)) // 2
    return (' ' * s1 + t1).ljust(width), (' ' * s2 + t2) if t2 else ''


def apply_titles(block, titles, texts):
    """Riscrive i titoli (texts[i] = (riga1, riga2); None = invariato) con title_rows().
    Un titolo puo' allungarsi: la lunghezza totale del blocco resta uguale togliendo
    spazi di riempimento dopo l'ultimo terminatore (i puntatori fissi li ricalcola il build)."""
    out = bytearray(block)
    for t, tx in sorted(zip(titles, texts), key=lambda z: -z[0]['off']):
        if not tx:
            continue
        r1, r2 = title_rows(tx[0] or t['r1'], tx[1] or t['r2'])
        new = (r1 + r2).encode('ascii', 'replace').ljust(t['len'], b' ')
        out[t['off']:t['off'] + t['len']] = new
    delta = len(out) - len(block)
    if delta > 0:
        tail = len(out) - len(out.rstrip(b' '))
        if tail < delta:
            raise ValueError(f"I titoli crescono di {delta} byte ma il blocco ha solo {tail} byte liberi in coda.")
        del out[len(out) - delta:]
    return bytes(out)


def center_line(text, width):
    """Centra il testo in una riga di larghezza fissa."""
    t=text.strip()
    if len(t)>=width: return t[:width]
    pad=(width-len(t))//2
    return (' '*pad + t).ljust(width)

def wrap_center(text, width, nrows=None):
    """Riformatta un testo in righe centrate di larghezza fissa."""
    words=text.split()
    lines=[]; cur=""
    for w in words:
        if not cur: cur=w
        elif len(cur)+1+len(w)<=width: cur+=" "+w
        else: lines.append(cur); cur=w
    if cur: lines.append(cur)
    if nrows:
        while len(lines)<nrows: lines.append("")
        lines=lines[:nrows]
    return [center_line(l,width) for l in lines]



def lz_comp(data):
    """Compressione ottimale (vedi lz_comp_ottimale); il vecchio compressore resta
    disponibile come lz_comp_veloce."""
    return lz_comp_ottimale(data)

# ── SCRITTE GRAFICHE (testo disegnato con il font a tile del BG3) ─────────────
# Font BG3 (2bpp, base VRAM $8000). Codice "mezzo" = indice tile / 2:
#   spazio 00, '0'-'9' 01-0A, 'A'-'Z' 0B-24, '>' 25 (freccia), ':' 26, '-' 27, '/' 28,
#   '^' 29 (triangolo su), 'v' 2A (triangolo giù), '.' 2B, ',' 2C, '@' 2D (©), 2E-2F (TM)
# Nelle mappe LZ2 il codice è "mezzo" (la routine $8C:8757 lo raddoppia);
# in SETA PRESENTS e nella tabella dei valori è già raddoppiato ("pieno").
# NON c'è l'apostrofo.
GFX_CHARS = {' ': 0x00, '>': 0x25, ':': 0x26, '-': 0x27, '/': 0x28, '^': 0x29, 'v': 0x2A,
             '.': 0x2B, ',': 0x2C, '@': 0x2D}
GFX_CHARS.update({str(d): 1 + d for d in range(10)})
GFX_CHARS.update({chr(65 + i): 0x0B + i for i in range(26)})
GFX_REV = {v: k for k, v in GFX_CHARS.items()}
UNK = '·'     # cella con un tile non di testo: viene lasciata com'è

# Mappe schermo compresse LZ2 (decompressore $80:8293 -> $80:85BC) con testo
GFX_MAPS = [
    {"id": "title", "name": "Schermata titolo (START / CONTINUE / LICENSED BY NINTENDO)", "src": 0x8D8C56},
    {"id": "options", "name": "Menu opzioni (SETUP GAME LEVEL/PAD CONTROL...)", "src": 0x8DB5CA},
]
# Testo in chiaro come word di tile (codice pieno, attributo nel byte alto)
GFX_WORDS = [
    {"id": "seta", "name": "SETA PRESENTS (logo iniziale)", "pc": 0x0608AD, "cells": 13},
]
# Valori del menu opzioni: byte di tile (codice pieno), campi a larghezza fissa da $8D:806E,
# copiati nella mappa da $8C:971E-$8C:97D2 (tabelle offset a $8D:8020-$8D:806C)
GFX_POOL = [(0x8D8078, 10), (0x8D8082, 10), (0x8D808C, 10), (0x8D8096, 10),
            (0x8D80A0, 10), (0x8D80AA, 10), (0x8D80B4, 10),
            (0x8D80BE, 10), (0x8D80C8, 10), (0x8D80D2, 10), (0x8D80DC, 10),
            (0x8D80E6, 44), (0x8D8112, 44),
            (0x8D813E, 7), (0x8D8145, 7), (0x8D814C, 7), (0x8D8153, 10), (0x8D815D, 10)]


def lz2_decomp_ex(rom, pc):
    """Decompressore LZ2 fedele all'ASM $80:85BC. Ritorna (output[:n], byte consumati)."""
    n = rom[pc] | rom[pc + 1] << 8
    y = pc + 2
    if rom[y] & 0x80:
        y += 1
        return bytes(rom[y:y + n]), y + n - pc
    out = bytearray()
    while True:
        flags = rom[y]; y += 1
        for _ in range(8):
            if not flags & 0x80:
                out.append(rom[y]); y += 1
            else:
                b1 = rom[y]; b2 = rom[y + 1]; y += 2
                dist = (((b1 >> 4) << 8) | b2) + 1
                for _ in range((b1 & 0x0F) + 3):      # ADC #$01 con carry=1, +1 del ciclo
                    out.append(out[len(out) - dist])
            flags = (flags << 1) & 0xFF
        if len(out) >= n:
            break
    return bytes(out[:n]), y - pc


def lz2_comp(data):
    """Compressore LZ2 compatibile (finestra 4096, lunghezze 3..18)."""
    n = len(data)
    out = bytearray([n & 0xFF, n >> 8])
    heads = {}; pos = 0
    while pos < n:
        fpos = len(out); out.append(0); flags = 0
        for bit in range(8):
            if pos >= n:
                break
            best = 0; bdist = 0
            if pos + 3 <= n:
                for cp in reversed(heads.get(bytes(data[pos:pos + 2]), ())):
                    if pos - cp > 4096:
                        break
                    ml = 0
                    while ml < 18 and pos + ml < n and data[cp + ml] == data[pos + ml]:
                        ml += 1
                    if ml > best:
                        best, bdist = ml, pos - cp
                        if ml == 18:
                            break
            if best >= 3:
                flags |= 0x80 >> bit
                d = bdist - 1
                out += bytes([((d >> 8) << 4) | (best - 3), d & 0xFF])
                for q in range(pos, pos + best):
                    heads.setdefault(bytes(data[q:q + 2]), []).append(q)
                pos += best
            else:
                out.append(data[pos])
                heads.setdefault(bytes(data[pos:pos + 2]), []).append(pos)
                pos += 1
        out[fpos] = flags
    return bytes(out)


def lz2_refs(body, src):
    """Punti del codice che caricano la sorgente: LDX #$bbhh / LDA #$ll / XBA."""
    X = (src >> 8) & 0xFFFF
    pat = bytes([0xA2, X & 0xFF, X >> 8, 0xA9, src & 0xFF, 0xEB])
    return [m.start() for m in re.finditer(re.escape(pat), bytes(body))]


def _dec_half(t):
    return GFX_REV.get(t, UNK)


def _dec_full(b):
    return GFX_REV.get(b >> 1, UNK) if b % 2 == 0 else UNK


def load_gfx_texts(rom):
    h = smc_offset(rom); R = rom[h:]
    out = []
    for m in GFX_MAPS:
        data, used = lz2_decomp_ex(R, pc_of(m["src"]))
        W = [data[i] | data[i + 1] << 8 for i in range(0, len(data) - 1, 2)]
        for r in range(len(W) // 32):
            row = W[r * 32:(r + 1) * 32]
            txt = "".join(_dec_half(w & 0x3FF) for w in row)
            if re.search(r"[A-Z0-9]{3,}", txt) and txt.count(UNK) <= 8:
                out.append({"id": f"{m['id']}:{r}", "res": m["id"], "kind": "map", "row": r,
                            "group": m["name"], "orig": txt, "maxlen": 32, "tr": "",
                            "note": f"mappa LZ2 {fmt_snes(m['src'])} ({used} byte compressi), riga {r}"})
    for w in GFX_WORDS:
        cells = [R[w["pc"] + 2 * i] | R[w["pc"] + 2 * i + 1] << 8 for i in range(w["cells"])]
        txt = "".join(_dec_full(c & 0xFF) for c in cells)
        out.append({"id": w["id"], "res": w["id"], "kind": "words", "group": w["name"], "orig": txt,
                    "maxlen": w["cells"], "tr": "", "note": f"{w['cells']} word non compresse a {fmt_snes(snes_of(w['pc']))}"})
    for s, n in GFX_POOL:
        pc = pc_of(s)
        txt = "".join(_dec_full(b) for b in R[pc:pc + n])
        out.append({"id": f"pool:{s:06X}", "res": "pool", "kind": "pool", "group": "Menu opzioni: valori",
                    "orig": txt, "maxlen": n, "tr": "", "note": f"campo fisso di {n} celle a {fmt_snes(s)}"})
    return out


def check_gfx(e, tr):
    errs = []
    if len(tr) != e["maxlen"]:
        errs.append(f"deve essere esattamente {e['maxlen']} caratteri (ora {len(tr)})")
    bad = sorted({c for c in tr if c not in GFX_CHARS and c != UNK})
    if bad:
        errs.append("caratteri non presenti nel font: " + " ".join(bad) + " (niente apostrofo né accenti)")
    return errs


def gfx_warnings(e, tr):
    """Avvisi (non bloccanti): testo fuori dall'area occupata dall'originale.
    Serve per i riquadri evidenziati (es. GAME START = 10 celle) e per il bordo schermo."""
    if e["kind"] != "map" or not tr.strip():
        return []
    o = [i for i, c in enumerate(e["orig"]) if c not in " "]
    t = [i for i, c in enumerate(tr) if c not in " "]
    if not o or not t:
        return []
    w = []
    if t[0] < o[0] or t[-1] > o[-1]:
        w.append(f"esce dall'area dell'originale (colonne {o[0]}-{o[-1]}, ora {t[0]}-{t[-1]}): "
                 "controlla riquadri e bordo nel confronto emulatore")
    if t[-1] >= 31 or t[0] == 0:
        w.append("tocca il bordo dello schermo")
    return w


def apply_gfx_texts(body, entries, alloc, log=print):
    """Scrive le scritte grafiche. alloc(n) -> PC libero nella ROM espansa (per le mappe che crescono)."""
    todo = [e for e in entries if e.get("tr")]
    for e in todo:
        errs = check_gfx(e, e["tr"])
        if errs:
            raise ValueError(f"Grafica {e['id']}: " + "; ".join(errs))
    n = 0
    # mappe LZ2
    for m in GFX_MAPS:
        rows = [e for e in todo if e["res"] == m["id"]]
        if not rows:
            continue
        pc = pc_of(m["src"])
        data, used = lz2_decomp_ex(body, pc)
        data = bytearray(data)
        for e in rows:
            base = e["row"] * 64
            attr_default = None
            for i in range(32):
                w = data[base + 2 * i] | data[base + 2 * i + 1] << 8
                if (w & 0x3FF) and attr_default is None and GFX_REV.get(w & 0x3FF, UNK) != UNK:
                    attr_default = w & 0xFC00
            attr_default = attr_default or 0
            for i, ch in enumerate(e["tr"]):
                if ch == UNK:
                    continue
                w = data[base + 2 * i] | data[base + 2 * i + 1] << 8
                attr = w & 0xFC00 if (w & 0x3FF) else attr_default
                nw = attr | GFX_CHARS[ch] if ch != ' ' else (w & 0xFC00)
                data[base + 2 * i] = nw & 0xFF; data[base + 2 * i + 1] = nw >> 8
            n += 1
        comp = lz2_comp(bytes(data))
        back, _ = lz2_decomp_ex(comp + b"\0" * 40, 0)
        if back != bytes(data):
            raise ValueError(f"LZ2 round-trip fallito per {m['id']}")
        if len(comp) <= used:
            body[pc:pc + len(comp)] = comp
            log(f"[GFX] {m['id']}: mappa ricompressa {len(comp)}/{used} byte, sul posto")
        else:
            refs = lz2_refs(body, m["src"])
            if not refs:
                raise ValueError(f"Nessun riferimento trovato nel codice per {fmt_snes(m['src'])}")
            dst = alloc(len(comp))
            body[dst:dst + len(comp)] = comp
            s = snes_of(dst)
            for r in refs:
                body[r + 1] = (s >> 8) & 0xFF; body[r + 2] = s >> 16; body[r + 4] = s & 0xFF
            log(f"[GFX] {m['id']}: mappa {len(comp)} byte (orig {used}) spostata a {fmt_snes(s)}, "
                f"{len(refs)} riferimenti aggiornati")
    for e in todo:
        if e["kind"] == "words":
            w = next(x for x in GFX_WORDS if x["id"] == e["res"])
            for i, ch in enumerate(e["tr"]):
                if ch == UNK:
                    continue
                p = w["pc"] + 2 * i
                body[p] = GFX_CHARS[ch] * 2
            n += 1
        elif e["kind"] == "pool":
            pc = pc_of(int(e["id"].split(":")[1], 16))
            for i, ch in enumerate(e["tr"]):
                if ch != UNK:
                    body[pc + i] = GFX_CHARS[ch] * 2
            n += 1
    log(f"[GFX] {n} scritte grafiche applicate")
    return n


# ── GRAFICA DISEGNATA (applicata a OGNI build) ───────────────────────────────
# Scritte che non sono testo ne' font a tile, ma disegni:
#   1) pannello Command HQ: "DATE" / "TIME"         -> "DATA" / "ORA"
#      tileset BG1  $86:A82B (LZ, 8192 byte, tile 4bpp)   + mappa BG1 $86:B4A0 (LZ, 2048 byte,
#      32x32 in due piani: 1024 byte bassi + 1024 byte alti). Celle 16x16 (modo 5 interlacciato).
#   2) mese sotto DATA (sprite a segmenti "JAN")    -> GEN FEB MAR APR MAG GIU LUG AGO SET OTT NOV DIC
#      tabella $80:A066 (12 mesi x 3 lettere, word = tile sprite 0x1xx) + set sprite $86:9B00 (LZ,
#      16384 byte): la lettera J (non serve in italiano) viene ridisegnata come I.
#   3) indicatore carburante in volo "FULL"/"EMPTY" -> "PIENO"/"VUOTO"
#      tile BG3 2bpp in chiaro a $99:9100 (PC 0x0C9100), mappa HUD in chiaro a $99:8000 (PC 0x0C8000).
# Tutti gli indirizzi e i formati sono verificati con l'emulatore (VRAM/DMA/letture CPU).

ART_TILESET_HQ = 0x86A82B     # LZ -> VRAM BG1 tile 0x000-0x0FF
ART_MAP_HQ = 0x86B4A0         # LZ -> $7F:F600 -> mappa BG1 (piani lo/hi)
ART_SPRITES_HQ = 0x869B00     # LZ -> VRAM sprite (base 0x8000 byte)
ART_MONTHS_PC = 0x002066      # $80:A066: 12 x 3 word
ART_HUD_TILES_PC = 0x0C9100   # $99:9100: tile BG3 2bpp del cruscotto di volo
ART_HUD_MAP_PC = 0x0C8000     # $99:8000: mappa BG3 32 colonne (riga = 64 byte)

# lettere disponibili nel set sprite del mese (tile 0x140.. ; la meta' bassa e' tile+0x10)
ART_MONTH_TILES = {'A': 0x40, 'B': 0x41, 'C': 0x42, 'D': 0x43, 'E': 0x44, 'F': 0x45, 'G': 0x46,
                   'I': 0x47, 'L': 0x48, 'M': 0x49, 'N': 0x4A, 'O': 0x4B, 'P': 0x4C, 'R': 0x4D,
                   'S': 0x4E, 'T': 0x4F, 'U': 0x60, 'V': 0x61, 'Y': 0x62}
ART_MONTHS_IT = ["GEN", "FEB", "MAR", "APR", "MAG", "GIU", "LUG", "AGO", "SET", "OTT", "NOV", "DIC"]

# Glifi 16x16 nello stile delle scritte del pannello HQ (indici colore della palette 7).
# Le righe 5-9 contengono il "tubo" verde che passa dietro le lettere.
_HQ_O = """
0000000000000000
0000066666600000
0000677777760000
0006777667776000
0007770000777000
1107870110787011
2207870220787022
4407f704407f7044
2207f702207f7022
1107870110787011
0007770000777000
0007770000777000
0006777667776000
0000677777760000
0000066666600000
0000000000000000"""
_HQ_R = """
0000000000000000
0006666666600000
0007777777760000
0007776667776000
0007770000777000
1107870110787011
2207870220787022
4407f704407f7044
2207f702207f7022
1107877777776011
0007777777760000
0007770000777000
0007770000777000
0007770000777000
0006660000666000
0000000000000000"""
_BAND = [0, 0, 0, 0, 0, 1, 2, 4, 2, 1, 0, 0, 0, 0, 0, 0]

# Font 3x5 del cruscotto (colore 2 su fondo 3), come FULL/EMPTY originali.
HUD_FONT = {
    'A': ["222", "2.2", "222", "2.2", "2.2"], 'B': ["22.", "2.2", "22.", "2.2", "22."],
    'C': ["222", "2..", "2..", "2..", "222"], 'D': ["22.", "2.2", "2.2", "2.2", "22."],
    'E': ["22", "2.", "22", "2.", "22"],       'F': ["222", "2..", "222", "2..", "2.."],
    'G': ["222", "2..", "2.2", "2.2", "222"], 'H': ["2.2", "2.2", "222", "2.2", "2.2"],
    'I': ["2", "2", "2", "2", "2"],            'J': ["..2", "..2", "..2", "2.2", "222"],
    'K': ["2.2", "2.2", "22.", "2.2", "2.2"], 'L': ["2.", "2.", "2.", "2.", "22"],
    'M': ["2.2", "222", "2.2", "2.2", "2.2"], 'N': ["222", "2.2", "2.2", "2.2", "2.2"],
    'O': ["222", "2.2", "2.2", "2.2", "222"], 'P': ["222", "2.2", "222", "2..", "2.."],
    'Q': ["222", "2.2", "2.2", "222", "..2"], 'R': ["222", "2.2", "22.", "2.2", "2.2"],
    'S': ["222", "2..", "222", "..2", "222"], 'T': ["222", ".2.", ".2.", ".2.", ".2."],
    'U': ["2.2", "2.2", "2.2", "2.2", "222"], 'V': ["2.2", "2.2", "2.2", "2.2", ".2."],
    'W': ["2.2", "2.2", "2.2", "222", "2.2"], 'X': ["2.2", "2.2", ".2.", "2.2", "2.2"],
    'Y': ["2.2", "2.2", "222", ".2.", ".2."], 'Z': ["222", "..2", ".2.", "2..", "222"],
    "'": ["2", "2", ".", ".", "."], '.': [".", ".", ".", ".", "2"], '-': ["...", "...", "222", "...", "..."],
    '0': ["222", "2.2", "2.2", "2.2", "222"], '1': [".2", "22", ".2", ".2", ".2"],
    '/': ["..2", "..2", ".2.", "2..", "2.."], '%': ["2.2", "..2", ".2.", "2..", "2.2"],
}
# colonne utili: riga FULL = colonne 8..23 (tile 0x33,0x34); riga EMPTY = 7..25 (con i tile
# di bordo 0x21/0x27 liberati: sono copie identiche di 0x08 e la mappa li punta a 0x08)
ART_FUEL_DEFAULT = ("PIENO", "VUOTO")
ART_FUEL_ROWS = {"top": (18, 0x33, 0x34, 8, 23), "bottom": (25, 0x36, 0x37, 7, 25)}


def _grid(s):
    return [[int(ch, 16) for ch in row] for row in s.strip().splitlines()]


def _tile4_get(buf, t):
    px = [[0] * 8 for _ in range(8)]
    o = t * 32
    for y in range(8):
        b0, b1, b2, b3 = buf[o + 2 * y], buf[o + 2 * y + 1], buf[o + 16 + 2 * y], buf[o + 17 + 2 * y]
        for x in range(8):
            s = 7 - x
            px[y][x] = ((b0 >> s) & 1) | ((b1 >> s) & 1) << 1 | ((b2 >> s) & 1) << 2 | ((b3 >> s) & 1) << 3
    return px


def _tile4_put(buf, t, px):
    o = t * 32
    for y in range(8):
        b = [0, 0, 0, 0]
        for x in range(8):
            c = px[y][x]
            for k in range(4):
                b[k] |= ((c >> k) & 1) << (7 - x)
        buf[o + 2 * y], buf[o + 2 * y + 1], buf[o + 16 + 2 * y], buf[o + 17 + 2 * y] = b


def _cell_get(buf, t):
    g = [[0] * 16 for _ in range(16)]
    for sy in range(2):
        for sx in range(2):
            p = _tile4_get(buf, t + sx + 16 * sy)
            for y in range(8):
                for x in range(8):
                    g[sy * 8 + y][sx * 8 + x] = p[y][x]
    return g


def _cell_put(buf, t, g):
    for sy in range(2):
        for sx in range(2):
            _tile4_put(buf, t + sx + 16 * sy, [row[sx * 8:sx * 8 + 8] for row in g[sy * 8:sy * 8 + 8]])


def _tile2_get(buf, o):
    return [[((buf[o + 2 * y] >> (7 - x)) & 1) | ((buf[o + 2 * y + 1] >> (7 - x)) & 1) << 1
             for x in range(8)] for y in range(8)]


def _tile2_put(buf, o, px):
    for y in range(8):
        b0 = b1 = 0
        for x in range(8):
            b0 |= (px[y][x] & 1) << (7 - x); b1 |= ((px[y][x] >> 1) & 1) << (7 - x)
        buf[o + 2 * y], buf[o + 2 * y + 1] = b0, b1


def hud_word_rows(word):
    """5 righe di pixel ('2' = inchiostro) della parola nel font 3x5, 1 pixel fra le lettere."""
    rows = [""] * 5
    for k, ch in enumerate(word):
        g = HUD_FONT[ch]
        for r in range(5):
            rows[r] += g[r] + ("." if k < len(word) - 1 else "")
    return rows


def check_fuel_word(word, where):
    _, _, _, c0, c1 = ART_FUEL_ROWS[where]
    bad = [ch for ch in word if ch not in HUD_FONT]
    if bad:
        return f"lettere non disegnabili: {''.join(bad)}"
    w = len(hud_word_rows(word)[0]) if word else 0
    if w > c1 - c0 + 1:
        return f"troppo larga: {w} pixel, massimo {c1 - c0 + 1}"
    return ""


def check_months(months):
    errs = []
    if len(months) != 12:
        errs.append("servono 12 mesi")
    for m in months:
        if len(m) != 3:
            errs.append(f"{m!r}: servono 3 lettere")
        bad = [ch for ch in m if ch not in ART_MONTH_TILES]
        if bad:
            errs.append(f"{m!r}: lettere non disponibili {''.join(bad)} (ci sono: {''.join(sorted(ART_MONTH_TILES))})")
    return errs


def _lz_refs(body, snes):
    """Siti nel codice 'LDA #bank / PHA / PEA addr' che passano questa sorgente al decompressore."""
    bank, lo, hi = snes >> 16, snes & 0xFF, (snes >> 8) & 0xFF
    pat = bytes([0xA9, bank, 0x48, 0xF4, lo, hi])
    out = []; i = body.find(pat)
    while i >= 0:
        out.append(i); i = body.find(pat, i + 1)
    return out


def _replace_lz(body, snes, data, alloc, log, name):
    pc = pc_of(snes)
    _, used, _ = lz_decomp_ex(body, pc)
    comp = bytearray(lz_comp(data))
    comp[0] = body[pc]; comp[3] = body[pc + 3]   # byte di modo (es. 5 = mappa a due piani) e riservato
    comp = bytes(comp)
    back = lz_decomp(comp + b"\0" * 32)
    assert back == bytes(data), "round-trip LZ fallito (" + name + ")"
    if len(comp) <= used:
        body[pc:pc + len(comp)] = comp
        log(f"[ART] {name}: ricompresso {len(comp)}/{used} byte al suo posto {fmt_snes(snes)}")
        return
    refs = _lz_refs(body, snes)
    if not refs:
        raise ValueError(f"{name}: non trovo i riferimenti a {fmt_snes(snes)} per spostarlo")
    p = alloc(len(comp)); s = snes_of(p)
    body[p:p + len(comp)] = comp
    for r in refs:
        body[r + 1] = s >> 16; body[r + 4] = s & 0xFF; body[r + 5] = (s >> 8) & 0xFF
    log(f"[ART] {name}: {len(comp)} byte (> {used}) spostato a {fmt_snes(s)}, {len(refs)} riferimenti aggiornati")


def art_hq_panel(tiles, hqmap):
    """DATE->DATA (solo mappa) e TIME->ORA (4 celle ridisegnate, testo centrato)."""
    tiles = bytearray(tiles); hqmap = bytearray(hqmap)
    A = _cell_get(tiles, 0x62)
    strip = [[_BAND[y]] * 64 for y in range(16)]
    for x0, g in ((8, _grid(_HQ_O)), (24, _grid(_HQ_R)), (40, A)):
        for y in range(16):
            strip[y][x0:x0 + 16] = g[y]
    cells = (0x86, 0x88, 0x8A, 0x66)        # 0x86 vuota e inutilizzata; I, M, E non servono piu'
    for k, t in enumerate(cells):
        _cell_put(tiles, t, [row[16 * k:16 * k + 16] for row in strip])
    # mappa (piano basso): riga 15 = DATE, riga 22 = TIME, colonne 9..12
    assert list(hqmap[15 * 32 + 9:15 * 32 + 13]) == [0x60, 0x62, 0x64, 0x66], "mappa HQ inattesa (DATE)"
    assert list(hqmap[22 * 32 + 9:22 * 32 + 13]) == [0x64, 0x88, 0x8A, 0x66], "mappa HQ inattesa (TIME)"
    hqmap[15 * 32 + 12] = 0x62
    hqmap[22 * 32 + 9:22 * 32 + 13] = bytes(cells)
    return bytes(tiles), bytes(hqmap)


def art_month_sprites(spr):
    """Lettera J -> I nel set sprite (tile 0x147 sopra, 0x157 sotto; nel blocco: 0x47/0x57 + 0x2000/32)."""
    spr = bytearray(spr)
    base = 0x2000 // 32                      # il blocco parte dal tile sprite 0x100
    T = [_tile4_get(spr, base + 0x4F), _tile4_get(spr, base + 0x5F)]
    top = [r[:] for r in T[0]]; bot = [r[:] for r in T[1]]
    bot[5] = top[2][:]                       # barra in basso accesa come quella in alto
    _tile4_put(spr, base + 0x47, top); _tile4_put(spr, base + 0x57, bot)
    return bytes(spr)


def art_months_table(months):
    out = bytearray()
    for m in months:
        for ch in m:
            out += bytes([ART_MONTH_TILES[ch], 0x01])
    return bytes(out)


def art_fuel(body, top, bottom, log=print):
    """Riscrive le tile del cruscotto (in chiaro) con le due parole."""
    t0 = ART_HUD_TILES_PC
    # libera 0x21 e 0x27: identiche a 0x08, usate solo a sinistra delle righe 10 e 14 della mappa
    same = body[t0 + 0x08 * 16:t0 + 0x09 * 16]
    assert body[t0 + 0x21 * 16:t0 + 0x22 * 16] == same and body[t0 + 0x27 * 16:t0 + 0x28 * 16] == same, \
        "tile del cruscotto inattese"
    m0 = ART_HUD_MAP_PC
    for row, t in ((10, 0x21), (14, 0x27)):
        assert body[m0 + row * 64] == t
        body[m0 + row * 64] = 0x08
    for where, word in (("top", top), ("bottom", bottom)):
        row, ta, tb, c0, c1 = ART_FUEL_ROWS[where]
        ent = [body[m0 + row * 64 + 2 * k] for k in range(4)]
        tl, tr_ = ent[0], ent[3]
        if where == "bottom":
            # bordi dedicati per poter usare anche le colonne 7 e 24-25
            body[t0 + 0x21 * 16:t0 + 0x22 * 16] = body[t0 + tl * 16:t0 + tl * 16 + 16]
            body[t0 + 0x27 * 16:t0 + 0x28 * 16] = body[t0 + tr_ * 16:t0 + tr_ * 16 + 16]
            tl, tr_ = 0x21, 0x27
            body[m0 + row * 64] = tl; body[m0 + row * 64 + 6] = tr_
        strip = []
        for t in (tl, ta, tb, tr_):
            p = _tile2_get(body, t0 + t * 16)
            strip = [(strip[y] if strip else []) + p[y] for y in range(8)]
        for y in range(1, 6):                 # pulisce l'area del testo
            for x in range(c0, c1 + 1):
                strip[y][x] = 3
        rows = hud_word_rows(word)
        w = len(rows[0])
        x0 = c0 + ((c1 - c0 + 1) - w) // 2
        for y in range(5):
            for x, ch in enumerate(rows[y]):
                if ch == '2':
                    strip[1 + y][x0 + x] = 2
        for k, t in enumerate((tl, ta, tb, tr_)):
            _tile2_put(body, t0 + t * 16, [r[8 * k:8 * k + 8] for r in strip])
    log(f"[ART] carburante: {top!r} / {bottom!r}")


# 4) didascalie sopra il nome dell'aereo nell'hangar (disegnate dentro l'immagine, font 5x8, passo 9)
#    4 immagini LZ (0x44F0 byte, tile 4bpp, 14 celle 16x16 per riga): F-15 (normale / schema armi)
#    "DUAL-ROLE-FIGHTER" e A-10 (normale / schema) "CLOSE-SUPPORT-ATTACK".
ART_PLANES = [(0x86BFCF, 0), (0x86D452, 0), (0x86E9CB, 1), (0x87BD0B, 1)]
ART_CAPTIONS_EN = ["DUAL-ROLE-FIGHTER", "CLOSE-SUPPORT-ATTACK"]
ART_CAPTIONS_DEFAULT = ["CACCIA-A-DOPPIO-RUOLO", "ATTACCO-AL-SUOLO"]
ART_CAP_X0, ART_CAP_PITCH, ART_CAP_Y0, ART_CAP_H, ART_CAP_MAX = 10, 9, 13, 8, 23
ART_CAP_LETTERS = "".join(sorted(set("".join(ART_CAPTIONS_EN))))


def _pic_rows(buf, y0, h):
    """Righe di pixel (224 di larghezza) della parte alta dell'immagine."""
    rows = []
    for y in range(y0, y0 + h):
        cy, yy = divmod(y, 16)
        row = []
        for cx in range(14):
            k = cy * 14 + cx
            t = (k % 8) * 2 + (k // 8) * 0x20
            row += _cell_get(buf, t)[yy]
        rows.append(row)
    return rows


def _pic_put_rows(buf, rows, y0):
    cells = {}
    for i, row in enumerate(rows):
        cy, yy = divmod(y0 + i, 16)
        for cx in range(14):
            k = cy * 14 + cx
            t = (k % 8) * 2 + (k // 8) * 0x20
            g = cells.setdefault(t, _cell_get(buf, t))
            g[yy] = row[cx * 16:cx * 16 + 16]
    for t, g in cells.items():
        _cell_put(buf, t, g)


def check_caption(text):
    bad = sorted(set(ch for ch in text if ch not in ART_CAP_LETTERS and ch != ' '))
    if bad:
        return f"lettere non disponibili: {''.join(bad)} (ci sono: {ART_CAP_LETTERS} e lo spazio)"
    if len(text) > ART_CAP_MAX:
        return f"troppo lunga: {len(text)}/{ART_CAP_MAX}"
    return ""


def _cap_glyphs(rows, bg):
    """Segmenta la didascalia in glifi (colonne contigue non di sfondo)."""
    cols = [any(r[x] != bg for r in rows) for x in range(224)]
    runs = []; x = 0
    while x < 224:
        if cols[x]:
            s = x
            while x < 224 and cols[x]:
                x += 1
            runs.append((s, x - s))
        else:
            x += 1
    return runs


def art_captions(body, captions, alloc, log=print):
    pics = {}
    for snes, kind in ART_PLANES:
        pics[snes] = bytearray(lz_decomp(body, pc_of(snes)))
    # libreria dei glifi: per ogni lettera, dall'immagine dello stesso aereo se c'e', altrimenti dall'altro
    lib = [{}, {}]
    bg = _pic_rows(pics[ART_PLANES[0][0]], ART_CAP_Y0, 1)[0][0]
    for snes, kind in ART_PLANES:
        rows = _pic_rows(pics[snes], ART_CAP_Y0, ART_CAP_H)
        runs = _cap_glyphs(rows, bg)
        if len(runs) != len(ART_CAPTIONS_EN[kind]):
            raise ValueError(f"didascalia di {fmt_snes(snes)} inattesa ({len(runs)} glifi)")
        for (x, w), ch in zip(runs, ART_CAPTIONS_EN[kind]):
            lib[kind].setdefault(ch, [r[x:x + w] for r in rows])
    for snes, kind in ART_PLANES:
        text = captions[kind]
        rows = _pic_rows(pics[snes], ART_CAP_Y0, ART_CAP_H)
        for r in rows:
            for x in range(ART_CAP_X0, 224):
                r[x] = bg
        for i, ch in enumerate(text):
            if ch == ' ':
                continue
            g = lib[kind].get(ch) or lib[1 - kind][ch]
            w = len(g[0])
            x = ART_CAP_X0 + round(i * 8.7) + (5 - w) // 2
            for y in range(ART_CAP_H):
                rows[y][x:x + w] = g[y]
        _pic_put_rows(pics[snes], rows, ART_CAP_Y0)
        _replace_lz(body, snes, bytes(pics[snes]), alloc, log, f"immagine hangar {fmt_snes(snes)} ({text})")


def apply_art(body, alloc, log=print, cfg=None):
    """Applica tutte le scritte disegnate tradotte.
    cfg: {'mesi': [12 x 3 lettere], 'carburante': [su, giu], 'didascalie': [F-15, A-10]}."""
    cfg = cfg or {}
    months = cfg.get("mesi") or ART_MONTHS_IT
    top, bottom = cfg.get("carburante") or ART_FUEL_DEFAULT
    captions = cfg.get("didascalie") or ART_CAPTIONS_DEFAULT
    errs = check_months(months)
    for t in captions:
        e = check_caption(t)
        if e:
            errs.append(f"{t!r}: {e}")
    for w, where in ((top, "top"), (bottom, "bottom")):
        e = check_fuel_word(w, where)
        if e:
            errs.append(f"{w!r}: {e}")
    if errs:
        raise ValueError("Grafica disegnata: " + "; ".join(errs))
    # 1) pannello HQ
    tiles = lz_decomp(body, pc_of(ART_TILESET_HQ)); hqmap = lz_decomp(body, pc_of(ART_MAP_HQ))
    tiles2, hqmap2 = art_hq_panel(tiles, hqmap)
    _replace_lz(body, ART_TILESET_HQ, tiles2, alloc, log, "tile pannello HQ (DATA/ORA)")
    _replace_lz(body, ART_MAP_HQ, hqmap2, alloc, log, "mappa pannello HQ")
    # 2) mese
    spr = lz_decomp(body, pc_of(ART_SPRITES_HQ))
    _replace_lz(body, ART_SPRITES_HQ, art_month_sprites(spr), alloc, log, "sprite del mese (J->I)")
    tab = art_months_table(months)
    body[ART_MONTHS_PC:ART_MONTHS_PC + len(tab)] = tab
    log(f"[ART] mesi: {' '.join(months)}")
    # 3) carburante
    art_fuel(body, top, bottom, log)
    # 4) didascalie degli aerei nell'hangar
    art_captions(body, captions, alloc, log)

# ── INDIRIZZI ────────────────────────────────────────────────────────────────
def snes_of(pc):
    return ((pc // 0x8000) | 0x80) << 16 | (pc % 0x8000 + 0x8000)


def pc_of(snes):
    return ((snes >> 16) & 0x7F) * 0x8000 + ((snes & 0xFFFF) - 0x8000)


def fmt_snes(s):
    return f"${s >> 16:02X}:{s & 0xFFFF:04X}"


# ── CATALOGO STRINGHE IN CHIARO ─────────────────────────────────────────────
CTRL = {0x03, 0x13, 0x1D, 0x1E}
ASCII_BLOCK = (0x028247, 0x02A6FF)          # $85:8247-$85:A6FE (337 stringhe)

# Stringhe fuori dal blocco ASCII, verificate sul codice / a runtime.
# (snes, gruppo, nota, riempimento)  riempimento: 'S' = spazi, '0' = 0x00
EXTRA_STRINGS = [
    (0x8182A5, "Messaggi decollo", "tabella $81:82A1, bank fisso @ $81:8262; 12 char", 'S'),
    (0x8182B2, "Messaggi decollo", "tabella $81:82A1; 12 char", 'S'),
    (0x84DE31, "Command HQ", "letto da $80:DBC9 (runtime)", 'S'),
    (0x8CEEA4, "Area / sortite", "letto da $80:D95C (runtime), contiene %d", '0'),
    (0x84B2EF, "Nomi area", "tabella $84:B2DD, ciclo 8 voci; buffer max 8 char", 'S'),
    (0x84B2F5, "Nomi area", "", 'S'), (0x84B2FB, "Nomi area", "", 'S'),
    (0x84B301, "Nomi area", "", 'S'), (0x84B307, "Nomi area", "", 'S'),
    (0x84B30D, "Nomi area", "", 'S'), (0x84B313, "Nomi area", "", 'S'),
    (0x84B319, "Nomi area", "", 'S'), (0x84B31F, "Nomi area", "non usata dal ciclo", 'S'),
    # trovate con la scansione della ROM (fuori dal blocco ASCII)
    (0x829EBE, "HUD di volo", "durante il volo", 'S'),
    (0x829EC7, "HUD di volo", "durante il volo", 'S'),
    (0x8493BC, "HUD di volo", "contiene %03d; la prima lettera sembra mancare nell'originale", 'S'),
    (0x83A2E5, "Schermata bilancio", "fine missione", 'S'),
    (0x83A2EA, "Schermata bilancio", "lettere spaziate", 'S'),
    (0x83A2F9, "Schermata bilancio", "", 'S'),
    (0x83A2FD, "Schermata bilancio", "lettere spaziate", 'S'),
    (0x83927C, "Salvataggi", "nessun dato salvato", 'S'),
    (0x84C104, "Richieste", "risposta no/si", 'S'),
    (0x84DD53, "Notiziario GNN", "testata del notiziario", 'S'),
    (0x84DE1B, "Command HQ", "etichetta del contatto", 'S'),
    (0x84DE26, "Command HQ", "etichetta del contatto", 'S'),
    (0x84DE3C, "Command HQ", "etichetta del contatto", 'S'),
    (0x80C134, "Varie", "ore", 'S'),
] + [(a, "Menu debug nascosto: voci", "etichetta da 18 caratteri", 'S')
     for a in range(0x82A2F5, 0x82A45E, 0x13)]
# Menu di DEBUG nascosto (base $82:A45F, 19 righe: MISSION NUMBER, PLAY AREA, MIG LEVEL...):
# valori a larghezza fissa per gruppo, bank $82 fisso. NON è il menu opzioni del giocatore
# (quello è grafico: vedi GFX_MAPS/GFX_POOL).
MENU_GROUPS = [
    ("DAY/NIGHT", [0x82A4B9, 0x82A4BF]),
    ("OFF/ON", [0x82A4B1, 0x82A4B5]),
    ("AEREO", [0x82A4C5, 0x82A4C9, 0x82A4CD]),
    ("MODALITA'", [0x82A4D1, 0x82A4D9, 0x82A4E1]),
    ("PARTITA", [0x82A4E9, 0x82A4F2]),
    ("VISUALE", [0x82A4FB, 0x82A505, 0x82A50F, 0x82A519]),
    ("TASTI", [0x82A523, 0x82A528, 0x82A52D, 0x82A532]),
]
# Gruppi riconosciuti nel blocco ASCII (per indirizzo SNES)
BLOCK_GROUPS = [
    (0x858247, 0x85856B, "Descrizioni area", "tabella $85:8000 (18 voci), bank $85 fisso; layout 54+30"),
    (0x85856C, 0x85869C, "Radio / Command HQ", ""),
    (0x85869D, 0x858985, "Armi / aerei", "descrizioni armamento"),
    (0x858986, 0x8589F4, "Area / sortite", ""),
    (0x8589F5, 0x8597AF, "Armamento dettagli", ""),
    (0x8597B0, 0x85984B, "Etichette HUD", "riquadri da 10 char"),
    (0x85984C, 0x8598D2, "Esito volo", ""),
    (0x8598D3, 0x859965, "Esito missione", "tabella $85:811E (6 voci), bank $85 fisso @ $83:B45E"),
    (0x859966, 0x859B5A, "Risultati / intel", ""),
    (0x859B5B, 0x859BBB, "Radio / Command HQ", ""),
    (0x859BBC, 0x859C40, "Data / briefing", ""),
    (0x859C41, 0x85A6FE, "Varie", ""),
]
DO_NOT_TRANSLATE_HINT = re.compile(r"^(F-?15|A-?10|X-?15|AIM|AGM|MK-|%)")


SEG = '\u00a6'          # separatore mostrato al posto di un codice interno 1D xx yy


def _split_inner(body):
    """Divide il corpo sui codici interni 1D xx yy. Ritorna (testo con ¦, lista dei codici)."""
    out = []; codes = []; i = 0
    while i < len(body):
        if body[i] == 0x1D and i + 2 < len(body):
            codes.append(bytes(body[i:i + 3])); out.append(ord(SEG)); i += 3
        else:
            out.append(body[i]); i += 1
    return bytes(out).decode('latin1'), codes


def _split_codes(b):
    """Separa i codici di controllo iniziali/finali dal testo modificabile."""
    i = 0
    if b[:1] == b"\x1d" and len(b) >= 3:       # 1D xx yy = posizionamento
        i = 3
    j = len(b)
    while j > i and b[j - 1] in CTRL:
        j -= 1
    return b[:i], b[i:j], b[j:]


def _group_for(snes):
    for lo, hi, g, n in BLOCK_GROUPS:
        if lo <= snes <= hi:
            return g, n
    return "Varie", ""


def load_strings(rom):
    """Tutte le stringhe traducibili in chiaro, con limiti. Offset PC senza header."""
    h = smc_offset(rom)
    R = rom[h:]
    out = []

    def add(pc, group, note, pad, fixed_width=None, force=False):
        e = R.index(0, pc)
        raw = bytes(R[pc:e])
        lead, body, tail = _split_codes(raw)
        if not body.strip() and not fixed_width:
            return
        txt, codes = _split_inner(body)
        try:
            txt.encode("latin1")
        except UnicodeEncodeError:
            return
        if not force and not re.search(r"[A-Z]{2}", txt) and "%" not in txt and not fixed_width:
            return
        out.append({"id": f"{snes_of(pc):06X}", "pc": pc, "snes": snes_of(pc), "group": group,
                    "note": note + ("; contiene codici di posizione (¦)" if codes else ""),
                    "lead": lead, "orig": txt, "tail": tail, "codes": codes,
                    "maxlen": fixed_width or len(body), "fixed": bool(fixed_width),
                    "pad": pad, "tr": "", "runtime": ""})

    # blocco ASCII
    p = ASCII_BLOCK[0]
    while p < ASCII_BLOCK[1]:
        e = R.index(0, p)
        if e > p:
            g, n = _group_for(snes_of(p))
            raw = bytes(R[p:e])
            pad = 'S' if raw.endswith(b" ") or g in ("Etichette HUD",) else '0'
            add(p, g, n, pad)
        p = e + 1
    # fuori blocco
    for s, g, n, pad in EXTRA_STRINGS:
        add(pc_of(s), g, n, pad, force=True)
    for g, lst in MENU_GROUPS:
        w = max(len(R[pc_of(s):R.index(0, pc_of(s))]) for s in lst)
        for s in lst:
            pc = pc_of(s)
            ln = R.index(0, pc) - pc
            add(pc, "Menu debug nascosto: " + g,
                f"valore del menu di debug, larghezza {ln} (max gruppo {w}); bank $82 fisso", 'S', fixed_width=ln)
    for s in out:
        s["rowlen"] = guess_rowlen(s)
        if s["rowlen"]:
            s["pad"] = 'S'
        if DO_NOT_TRANSLATE_HINT.match(s["orig"]) and s["group"] in ("Armi / aerei",):
            s["note"] = (s["note"] + "; sigla tecnica").strip("; ")
        rel = RELOCATABLE_STRINGS.get(s["snes"])
        if rel and [R[q] | R[q + 1] << 8 | R[q + 2] << 16 for q in rel[1]] == [s["snes"]] * len(rel[1]):
            s["ptrs24"] = list(rel[1]); s["growmax"] = rel[0]
            s["note"] = (s["note"] + f"; puo' allungarsi fino a {rel[0]} (viene spostata e "
                         f"si aggiornano {len(rel[1])} puntatori)").strip("; ")
    return out


# Stringhe che possono crescere: tutte le letture passano da puntatori a 24 bit (verificati).
# snes -> (lunghezza massima, [PC dei puntatori lo/hi/bank])
RELOCATABLE_STRINGS = {
    0x859BC3: (40, [0x027005, 0x02E5E4, 0x02E6F2]),     # OPERATION DESERT CORRADO
}


def guess_rowlen(s):
    """Larghezza riga per i testi su più righe (0 = riga singola)."""
    t = s["orig"]
    if s["group"] == "Descrizioni area":
        return 54
    if s["group"] == "Armamento dettagli" and len(t) in (68, 69):
        return 36
    if len(t) < 40:
        return 0
    m = re.search(r"  +(?=\S)", t)
    if m and m.end() in (36, 52, 54, 56):
        return m.end()
    return 52 if len(t) > 56 else 0


def split_rows(text, rowlen):
    if not rowlen:
        return [text]
    return [text[i:i + rowlen].rstrip() for i in range(0, len(text), rowlen)]


def join_rows(lines, rowlen):
    if not rowlen:
        return "".join(lines)
    lines = list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    return "".join(l.ljust(rowlen) for l in lines[:-1]) + (lines[-1] if lines else "")


def check_translation(s, tr):
    """Ritorna lista di problemi (vuota = ok)."""
    errs = []
    try:
        b = tr.replace(SEG, "").encode("ascii")
    except UnicodeEncodeError:
        errs.append("caratteri non ASCII (niente accenti: usa E' / A')")
        b = tr.encode("ascii", "replace")
    lim = s.get("growmax") or s["maxlen"]
    if len(b) + 3 * tr.count(SEG) > lim:
        errs.append(f"troppo lunga: {len(b) + 3 * tr.count(SEG)}/{lim}")
    if s.get("rowlen"):
        for k, row in enumerate(split_rows(tr, s["rowlen"])):
            pass
    fo = re.findall(r"%[0-9]*d", s["orig"]); ft = re.findall(r"%[0-9]*d", tr)
    if fo != ft:
        errs.append(f"segnaposto diversi: originale {fo}, traduzione {ft}")
    if any(c in b for c in (0, 0x13, 0x1D, 0x1E)):
        errs.append("codici di controllo nel testo")
    if tr.count(SEG) != s["orig"].count(SEG):
        errs.append(f"servono {s['orig'].count(SEG)} separatori ¦ (uno per codice di posizione)")
    if not s["orig"].isupper() and False:
        pass
    if tr.upper() != tr and s["orig"].upper() == s["orig"]:
        errs.append("minuscole: l'originale è tutto maiuscolo, il font potrebbe non averle")
    return errs


def apply_strings(rom_body, strings, alloc=None, log=print):
    """Scrive le traduzioni in place (stessa area, puntatori invariati). rom_body senza header.
    Le stringhe con puntatori a 24 bit noti (ptrs24) che non entrano vengono spostate con alloc()."""
    n = 0
    for s in strings:
        tr = s.get("tr", "")
        if not tr:
            continue
        if check_translation(s, tr):
            raise ValueError(f"{fmt_snes(s['snes'])} {s['orig']!r}: " + "; ".join(check_translation(s, tr)))
        if s.get("codes"):
            parts = tr.split(SEG)
            b = parts[0].encode("ascii")
            for code, part in zip(s["codes"], parts[1:]):
                b += code + part.encode("ascii")
        else:
            b = tr.encode("ascii")
        area = s["maxlen"]
        start = s["pc"] + len(s["lead"])
        if s.get("ptrs24") and len(b) > area:
            if alloc is None:
                raise ValueError(f"{fmt_snes(s['snes'])}: serve spazio libero per spostarla")
            full = s["lead"] + b + s["tail"] + b"\x00"
            p = alloc(len(full)); new_s = snes_of(p)
            rom_body[p:p + len(full)] = full
            for q in s["ptrs24"]:
                rom_body[q] = new_s & 0xFF; rom_body[q + 1] = (new_s >> 8) & 0xFF; rom_body[q + 2] = new_s >> 16
            log(f"[STR] {s['orig']!r} -> {tr!r} spostata a {fmt_snes(new_s)} ({len(s['ptrs24'])} puntatori)")
            n += 1
            continue
        fill = b" " if (s["pad"] == 'S' or s["fixed"]) else b"\x00"
        if fill == b"\x00" and len(b) < area:
            # testo + 0x00 subito dopo; i codici di coda vanno spostati prima dello 0x00
            new = b + s["tail"]
            new = new + b"\x00" * (area + len(s["tail"]) - len(new))
        else:
            new = b.ljust(area, b" ") + s["tail"]
        assert len(new) == area + len(s["tail"])
        rom_body[start:start + len(new)] = new
        n += 1
    return n


# ── BUILD ────────────────────────────────────────────────────────────────────
def fix_checksum(rom_body):
    rom_body[HDR_CHK_PC:HDR_CHK_PC + 4] = b"\xFF\xFF\x00\x00"
    s = sum(rom_body) & 0xFFFF
    rom_body[HDR_CHK_PC + 2:HDR_CHK_PC + 4] = s.to_bytes(2, "little")
    rom_body[HDR_CHK_PC:HDR_CHK_PC + 2] = (s ^ 0xFFFF).to_bytes(2, "little")
    return s


def _expand(body, log):
    if len(body) < EXP_SIZE:
        body.extend(b"\xFF" * (EXP_SIZE - len(body)))
        body[HDR_ROMSIZE_PC] = 0x0B
        log("[ROM] espansa a 2 MB")


def build_rom(orig_rom, new_bin, strings=(), relocations=(), expand="auto", log=print, gfx=(),
              art=True, art_cfg=None):
    """
    orig_rom: ROM originale (anche .smc). new_bin: blocco decompresso tradotto.
    expand: 'auto' (espande solo se serve), 'always', 'never'.
    Ritorna (rom_patchata, info).
    """
    h = smc_offset(orig_rom)
    header = bytes(orig_rom[:h])
    body = bytearray(orig_rom[h:])
    info = {}
    if len(new_bin) > DECOMP_SIZE:
        raise ValueError(f"Il decompresso ({len(new_bin)}) supera {DECOMP_SIZE} byte: "
                         "sovrascriverebbe la WRAM oltre $7E:ECBE.")
    comp = lz_comp(new_bin)
    back, used, dl = lz_decomp_ex(comp + b"\0" * 32, 0)
    if back != bytes(new_bin) or used != len(comp):
        raise ValueError("Round-trip LZ fallito: blocco non valido, interrompo.")
    info["comp"] = len(comp)
    log(f"[LZ] compresso {len(comp):,} byte, round-trip OK, consumo decompressore {used:,} byte")
    fits = len(comp) <= LZ_SLOT
    do_exp = expand == "always" or (expand == "auto" and not fits)
    if not fits and expand == "never":
        raise ValueError(f"Il blocco compresso ({len(comp)}) supera lo slot ({LZ_SLOT}) di "
                         f"{len(comp) - LZ_SLOT} byte e l'espansione è disattivata.")
    if do_exp:
        if len(comp) > 0x8000:
            raise ValueError("Blocco compresso > 32 KB: non entra in un bank.")
        _expand(body, log)
        body[EXP_LZ_PC:EXP_LZ_PC + len(comp)] = comp
        s = snes_of(EXP_LZ_PC)
        body[LZ_PTR_BANK_PC] = s >> 16
        body[LZ_PTR_ADDR_PC:LZ_PTR_ADDR_PC + 2] = (s & 0xFFFF).to_bytes(2, "little")
        info["lz_at"] = s
        log(f"[ROM] espansa a 2 MB: blocco compresso a {fmt_snes(s)} "
            f"(puntatore $83:926A/$83:926D aggiornato), slot originale lasciato intatto")
    else:
        body[LZ_OFFSET:LZ_OFFSET + len(comp)] = comp       # nessun padding
        info["lz_at"] = snes_of(LZ_OFFSET)
        log(f"[ROM] blocco in place a $85:A6FF senza padding, margine {LZ_SLOT - len(comp)} byte")
    remap_block_pointers(body, lz_decomp(orig_rom[h:], LZ_OFFSET), new_bin, log)
    nxt = [EXP_GFX_PC]
    libero = [LZ_OFFSET + len(comp), LZ_OFFSET + LZ_SLOT] if not do_exp else [0, 0]

    def alloc(size):
        if libero[0] + size <= libero[1]:        # spazio avanzato nello slot compresso
            p = libero[0]; libero[0] += size
            log(f"[ALLOC] {size} byte nello spazio libero dello slot a {fmt_snes(snes_of(p))} "
                f"(restano {libero[1] - libero[0]})")
            return p
        _expand(body, log)
        p = nxt[0]
        if p % 0x8000 + size > 0x8000:           # i dati non devono attraversare un bank
            p = (p // 0x8000 + 1) * 0x8000
        nxt[0] = p + size
        return p
    info["alloc"] = alloc
    n = apply_strings(body, strings, alloc, log)
    log(f"[STR] {n} stringhe in chiaro scritte")
    if any(e.get("tr") for e in gfx):
        apply_gfx_texts(body, gfx, alloc, log)
    if art:
        apply_art(body, alloc, log, art_cfg)
    for r in relocations:
        dst = r["dest"]; pa = r["ptr"]
        body[dst:dst + len(r["data"])] = r["data"]
        body[pa] = r["addr"] & 0xFF; body[pa + 1] = (r["addr"] >> 8) & 0xFF
        if r["size"] == 3:
            body[pa + 2] = r["bank"]
        for extra in r.get("bank_patches", ()):         # bank immediato hardcoded nel codice
            body[extra] = r["bank"]
        log(f"[PTR] {r['text']!r} -> ${r['bank']:02X}:{r['addr']:04X} (ptr @0x{pa:06X})")
    chk = fix_checksum(body)
    log(f"[HDR] checksum {chk:04X}")
    # verifica finale: il gioco decomprimerà da qui
    src_bank = body[LZ_PTR_BANK_PC]; src_addr = body[LZ_PTR_ADDR_PC] | body[LZ_PTR_ADDR_PC + 1] << 8
    got, used2, _ = lz_decomp_ex(body, pc_of(src_bank << 16 | src_addr))
    if got != bytes(new_bin):
        raise ValueError("Verifica finale fallita: la ROM non ridecomprime il testo tradotto.")
    log(f"[OK] verifica finale: {fmt_snes(src_bank << 16 | src_addr)} -> {len(got):,} byte identici")
    info["size"] = len(body)
    info.pop("alloc", None)
    return header + bytes(body), info


# ── PUNTATORI DENTRO IL BLOCCO DECOMPRESSO ───────────────────────────────────
# Alcune schermate non scorrono il blocco in sequenza ma usano puntatori fissi a 16 bit
# nel buffer $7E:6000 (valore = $6000 + offset). Con record di lunghezza variabile vanno
# ricalcolati a ogni build. Tabelle verificate (tutti i valori puntano a inizi di record):
#   $85:8024-$85:80BC  consigli del sergente / schermate intel per missione (record 32-76)
#   $85:80BE + 12*m    struttura missione m=0..7: +0 titolo, +2 briefing, +8 esito (record 77..)
#                      (+4 +6 = sottotabelle in $85, +10 = stringa in $85: NON sono nel buffer)
#   $83:DABA-$83:DAD6  finali (record 242-254)
BLOCK_PTR_PCS = (list(range(0x028024, 0x0280BE, 2))
                 + [0x0280BE + 12 * m + d for m in range(8) for d in (0, 2, 8)]
                 + list(range(0x01DABA, 0x01DAD8, 2)))
BLOCK_BASE = 0x6000
# Notiziari GNN (puntano al codice 1D che apre la notizia, non al testo):
#   tabella di word $85:F653-$85:F662 (8 notizie scelte a caso, bank $7E fisso nel codice)
#   puntatori "spezzati" nel codice: PEA $7Ehh / SEP #$20 / LDA #$ll / PHA
#     $84:DCA3 "WE INTERRUPT...", $84:DCD0 "STANDBY FOR ANOTHER...", 13 notizie in $85:F1ED-$85:F5CC
BLOCK_PTR_PCS += list(range(0x02F653, 0x02F663, 2))
BLOCK_SPLIT_PTR_PCS = [0x025CA3, 0x025CD0, 0x02F1ED, 0x02F243, 0x02F2B3, 0x02F360, 0x02F3AD,
                       0x02F3CE, 0x02F412, 0x02F443, 0x02F474, 0x02F4E0, 0x02F54A, 0x02F58B, 0x02F5CC]


def _text_runs(block):
    """Inizi e fine dei testi fra i codici di controllo (stessa regola di asp_import.split_records)."""
    runs = []; i = 0; n = len(block)
    while i < n:
        if block[i] < 0x20:
            i += 1; continue
        j = i
        while j < n and block[j] >= 0x20:
            j += 1
        runs.append((i, j)); i = j
    return runs


def remap_block_pointers(body, orig_block, new_block, log=print):
    """Riporta i puntatori fissi sugli stessi punti del blocco tradotto (inizio di un testo o
    un codice di controllo). Ritorna il numero di puntatori aggiornati."""
    ro, rn = _text_runs(orig_block), _text_runs(new_block)
    if len(rn) < len(ro):
        raise ValueError("Il blocco tradotto ha meno record dell'originale: impossibile riallineare i puntatori.")
    for k in range(len(ro) - 1):                     # stessi codici di controllo fra i record
        if orig_block[ro[k][1]:ro[k + 1][0]] != new_block[rn[k][1]:rn[k + 1][0]]:
            raise ValueError(f"Codici di controllo diversi dopo il record {k}: puntatori non riallineabili.")
    starts = [a for a, b in ro]
    import bisect

    def mapoff(off, where):
        k = bisect.bisect_right(starts, off) - 1
        if k >= 0 and off == ro[k][0]:
            return rn[k][0]
        if k >= 0 and off < ro[k][1]:
            raise ValueError(f"Puntatore {where}: cade a meta' di un testo (offset {off}).")
        if k + 1 < len(ro):                           # dentro i codici prima del record k+1
            return rn[k + 1][0] - (ro[k + 1][0] - off)
        raise ValueError(f"Puntatore {where}: fuori dal testo.")
    n = moved = 0
    for pc in BLOCK_PTR_PCS:
        v = body[pc] | body[pc + 1] << 8
        if not v:
            continue
        nv = mapoff(v - BLOCK_BASE, f"a 0x{pc:06X}") + BLOCK_BASE
        body[pc] = nv & 0xFF; body[pc + 1] = nv >> 8
        n += 1; moved += nv != v
    for pc in BLOCK_SPLIT_PTR_PCS:                   # F4 hh 7E E2 20 A9 ll 48
        if not (body[pc] == 0xF4 and body[pc + 2] == 0x7E and body[pc + 5] == 0xA9 and body[pc + 7] == 0x48):
            raise ValueError(f"Codice inatteso a 0x{pc:06X} (puntatore notiziario).")
        v = body[pc + 1] << 8 | body[pc + 6]
        nv = mapoff(v - BLOCK_BASE, f"a 0x{pc:06X}") + BLOCK_BASE
        body[pc + 1] = nv >> 8; body[pc + 6] = nv & 0xFF
        n += 1; moved += nv != v
    log(f"[PTR] {n} puntatori fissi nel testo ricalcolati ({moved} spostati)")
    return n


def make_ips(orig, patched):
    """IPS con supporto ROM espansa (record RLE per le zone ripetute, es. 0xFF di riempimento)."""
    out = bytearray(b"PATCH"); n = len(patched); i = 0
    same = lambda k: k < len(orig) and orig[k] == patched[k]

    def emit(off, data):
        nonlocal out
        p = 0
        while p < len(data):
            q = p
            while q < len(data) and data[q] == data[p] and q - p < 0xFFFF:
                q += 1
            if q - p >= 16:
                out += (off + p).to_bytes(3, "big") + b"\x00\x00" + (q - p).to_bytes(2, "big") + data[p:p + 1]
                p = q; continue
            r = p
            while r < len(data):
                t = r
                while t < len(data) and data[t] == data[r]:
                    t += 1
                if t - r >= 16:
                    break
                r = t
            out += (off + p).to_bytes(3, "big") + (r - p).to_bytes(2, "big") + data[p:r]
            p = r

    while i < n:
        if same(i):
            i += 1; continue
        j = i
        while j < n and j - i < 0xFFF0:
            if same(j):
                k = j
                while k < n and same(k) and k - j < 6:
                    k += 1
                if k - j >= 6 or k >= n:
                    break
                j = k
            else:
                j += 1
        emit(i, bytes(patched[i:j]))
        i = j
    out += b"EOF"
    return bytes(out)


def apply_ips(rom, ips):
    rom = bytearray(rom); assert ips[:5] == b"PATCH"; i = 5
    while ips[i:i + 3] != b"EOF":
        off = int.from_bytes(ips[i:i + 3], "big"); size = int.from_bytes(ips[i + 3:i + 5], "big"); i += 5
        if size == 0:
            cnt = int.from_bytes(ips[i:i + 2], "big"); data = bytes([ips[i + 2]]) * cnt; i += 3
        else:
            data = ips[i:i + size]; i += size
        if off + len(data) > len(rom):
            rom.extend(b"\0" * (off + len(data) - len(rom)))
        rom[off:off + len(data)] = data
    return bytes(rom)


# ── EMULATORE (asp_run, basato su Snaggletooth) ──────────────────────────────
DEFAULT_SCENARIO = """; A.S.P. — titolo -> opzioni -> missione 1 -> Command HQ (porta 1)
frame 1600 start
frame 1610 none
frame 1900 start
frame 1910 none
frame 2300 start
frame 2310 none
""" + "".join(f"frame {f} a\nframe {f + 8} none\n" for f in range(3400, 9000, 240))
DEFAULT_SHOTS = [1750, 2200, 2750, 3100, 3840, 4200, 4560, 5040, 5400, 5760]
LONG_SCENARIO = """; A.S.P. — Command HQ completo: intel area, punteggio, hangar/aerei, opzioni (porta 1)
""" + 'frame 1600 start\nframe 1610 none\nframe 1900 start\nframe 1910 none\nframe 2300 start\nframe 2310 none\nframe 3400 a\nframe 3408 none\nframe 3640 a\nframe 3648 none\nframe 3880 a\nframe 3888 none\nframe 4120 a\nframe 4128 none\nframe 4360 a\nframe 4368 none\nframe 4600 a\nframe 4608 none\nframe 4840 a\nframe 4848 none\nframe 5080 a\nframe 5088 none\nframe 5320 a\nframe 5328 none\nframe 5500 left\nframe 5508 none\nframe 5620 a\nframe 5628 none\nframe 5920 a\nframe 5928 none\nframe 6270 right\nframe 6278 none\nframe 6390 a\nframe 6398 none\nframe 6690 a\nframe 6698 none\nframe 6990 b\nframe 6998 none\nframe 7240 b\nframe 7248 none\nframe 7490 b\nframe 7498 none\nframe 7740 down\nframe 7748 none\nframe 7940 a\nframe 7948 none\nframe 8240 a\nframe 8248 none\nframe 8540 b\nframe 8548 none\nframe 8790 b\nframe 8798 none\nframe 9040 right\nframe 9048 none\nframe 9240 a\nframe 9248 none\nframe 9540 down\nframe 9548 none\nframe 9740 down\nframe 9748 none\nframe 9940 up\nframe 9948 none\nframe 10140 b\nframe 10148 none\nframe 10390 b\nframe 10398 none\nframe 10640 left\nframe 10648 none\nframe 10890 start\nframe 10898 none\nframe 11140 down\nframe 11148 none\nframe 11290 a\nframe 11298 none\nframe 11590 b\nframe 11598 none\nframe 11840 down\nframe 11848 none\nframe 11990 a\nframe 11998 none\nframe 12290 b\nframe 12298 none\nframe 12540 b\nframe 12548 none\n'
SCENARIOS = {
    "Titolo + opzioni": (DEFAULT_SCENARIO, [240, 1750, 2200], 2300),
    "Missione 1 -> Command HQ": (DEFAULT_SCENARIO, DEFAULT_SHOTS, 6000),
    "Command HQ completo": (LONG_SCENARIO, [5700, 6100, 6300, 6700, 8100, 8300, 9300, 9500, 9900,
                                            11100, 11500, 11900, 12100], 13000),
}


def run_emulator(exe, rom_path, outdir, frames=6000, script_text=DEFAULT_SCENARIO,
                 shots=DEFAULT_SHOTS, every=0, watch=None, timeout=900):
    os.makedirs(outdir, exist_ok=True)
    sp = os.path.join(outdir, "scenario.txt")
    open(sp, "w").write(script_text)
    cmd = [exe, rom_path, outdir, "--frames", str(frames), "--input", sp]
    if shots:
        cmd += ["--shot", ",".join(map(str, shots))]
    if every:
        cmd += ["--every", str(every)]
    for w in (watch or []):
        cmd += ["--watch", w]
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout)
    return sorted(f for f in os.listdir(outdir) if f.startswith("shot_"))


def discover_strings(rom, reads_csv):
    """Dal tracciamento: {pc_inizio_stringa: set(lettori)} per le stringhe ASCII lette dalla CPU."""
    h = smc_offset(rom); R = rom[h:]
    ok = set(range(0x20, 0x7F)) | CTRL
    found = {}
    for r in csv.DictReader(open(reads_csv)):
        if r["who"].startswith("DMA"):
            continue
        a = int(r["addr"], 16)
        if (a & 0xFFFF) < 0x8000:
            continue
        pc = pc_of(a)
        if pc >= len(R) or LZ_OFFSET <= pc < LZ_OFFSET + LZ_SLOT or R[pc] not in ok:
            continue
        s = pc
        while s > 0 and R[s - 1] != 0 and R[s - 1] in ok and s % 0x8000:
            s -= 1
        found.setdefault(s, set()).add(int(r["who"], 16))
    return found


def mark_runtime(strings, found):
    idx = {s["pc"]: s for s in strings}
    hit = 0
    for pc, who in found.items():
        s = idx.get(pc)
        if s:
            s["runtime"] = ",".join(fmt_snes(w) for w in sorted(who)[:4])
            hit += 1
    return hit


# ── AUTOTEST (senza GUI) ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "selftest":
        rom = open(sys.argv[2], "rb").read()
        d, used, dl = lz_decomp_ex(rom, LZ_OFFSET + smc_offset(rom))
        print("decompresso", len(d), "consumo", used, "slot", LZ_SLOT)
        S = load_strings(rom)
        print("stringhe in chiaro:", len(S))
        p, info = build_rom(rom, d, [], expand="never")
        print("rebuild identico al contenuto:", lz_decomp(p, LZ_OFFSET + smc_offset(p)) == d, info)
