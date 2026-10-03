# -*- coding: utf-8 -*-
"""asp_import — porta un file di traduzione continuo dentro il blocco compresso.

Funziona cosi':
 1. il blocco originale viene diviso nei suoi record (testi fra i codici di controllo);
 2. il file italiano viene agganciato ai record con ancore (sigle, numeri, radici comuni)
    e una programmazione dinamica che taglia solo nelle pause di spazi;
 3. ogni record viene reimpaginato nella sua geometria (larghezza riga, rientro,
    centratura dei titoli) e puo' crescere fino al numero massimo di righe della sua regione;
 4. il blocco viene ricomposto con gli stessi codici di controllo e la stessa lunghezza.

Le lettere accentate non esistono nel font: vengono convertite (E' A' I' O' U').
"""
import re, bisect

import unicodedata

# larghezza riga per regione (offset iniziale del record -> larghezza)
REGIONS = [(0x0000, 0x01C0, 30, "titoli"),
           (0x01C0, 0x381F, 56, "schermate missione"),
           (0x381F, 0x53D9, 56, "sergente"),
           (0x53D9, 0x6B22, 52, "notiziari"),
           (0x6B22, 0x7E1E, 48, "finali"),
           (0x7E1E, 0x10000, 48, "credits")]

ACCENTI = {'À': "A'", 'È': "E'", 'É': "E'", 'Ì': "I'", 'Ò': "O'", 'Ù': "U'",
           'à': "a'", 'è': "e'", 'é': "e'", 'ì': "i'", 'ò': "o'", 'ù': "u'",
           '’': "'", '‘': "'", '“': '"', '”': '"', '–': '-', '—': '-', '…': '...'}


def de_accent(s):
    out = []
    for ch in s:
        if ch in ACCENTI:
            out.append(ACCENTI[ch])
        elif ord(ch) < 127:
            out.append(ch)
        else:
            d = unicodedata.normalize('NFD', ch)
            out.append(d[0] + "'" if len(d) > 1 and ord(d[0]) < 127 else '?')
    return "".join(out)


def split_records(block):
    recs = []; i = 0
    while i < len(block):
        if block[i] < 0x20:
            i += 1; continue
        j = i
        while j < len(block) and block[j] >= 0x20:
            j += 1
        recs.append({'off': i, 'len': j - i, 'txt': block[i:j].decode('latin1')})
        i = j
    for r in recs:
        for a, b, w, name in REGIONS:
            if a <= r['off'] < b:
                r['w'] = w; r['region'] = name; break
        rows = [r['txt'][k:k + r['w']] for k in range(0, r['len'], r['w'])]
        ind = [len(x) - len(x.lstrip(' ')) for x in rows if x.strip()]
        r['rows'] = rows
        r['indent'] = min(ind) if ind else 0
    return recs


def align(recs, text, band=420, cut_pen=40.0, big=1e6):
    """Programmazione dinamica: taglia il testo italiano nei 256 record. Ritorna la lista dei pezzi."""
    n = len(text)
    E = [0]
    for r in recs:
        E.append(E[-1] + r['len'])
    drifts = list(range(-band, band + 1))
    idx = {d: i for i, d in enumerate(drifts)}
    INF = float('inf')

    def cutcost(p):
        if p <= 0 or p >= n:
            return 0.0
        if text[p - 1] == ' ' or text[p] == ' ':
            return 0.0
        return cut_pen

    dp = [INF] * len(drifts); dp[idx[0]] = 0.0
    back = []
    for k in range(len(recs)):
        # trasformata di distanza: costo |drift' - drift|
        cur = dp[:]
        prev = [None] * len(drifts)
        for i in range(1, len(cur)):
            if cur[i - 1] + 1 < cur[i]:
                cur[i] = cur[i - 1] + 1; prev[i] = i - 1
        for i in range(len(cur) - 2, -1, -1):
            if cur[i + 1] + 1 < cur[i]:
                cur[i] = cur[i + 1] + 1; prev[i] = i + 1
        # risolvi i puntatori alla sorgente originale
        src = [None] * len(drifts)
        for i in range(len(drifts)):
            j = i
            seen = 0
            while prev[j] is not None and seen < 2 * band + 2:
                j = prev[j]; seen += 1
            src[i] = j
        nxt = [INF] * len(drifts)
        for i, d in enumerate(drifts):
            if cur[i] == INF:
                continue
            p = E[k + 1] + d
            if p < 0 or p > n:
                continue
            c = cur[i] + cutcost(p)
            # vincoli forti: i record "(" devono coincidere
            start = E[k] + drifts[src[i]] if src[i] is not None else None
            if recs[k]['txt'] == '(':
                seg = text[start:p] if start is not None and 0 <= start <= p else ''
                c += 0.0 if seg.strip() == '(' else big
            nxt[i] = c
        back.append(src)
        dp = nxt
    # ricostruzione
    endi = min(range(len(drifts)), key=lambda i: dp[i] + (0 if E[-1] + drifts[i] == n else big))
    path = [endi]
    for k in range(len(recs) - 1, -1, -1):
        path.append(back[k][path[-1]])
    path.reverse()
    cuts = [E[k] + drifts[path[k]] for k in range(len(recs) + 1)]
    return [text[cuts[k]:cuts[k + 1]] for k in range(len(recs))], cuts


def wrap_record(rec, italian):
    """Riformatta il testo italiano nella geometria del record (stessa lunghezza esatta)."""
    it = de_accent(italian).strip()
    L, W, ind = rec['len'], rec['w'], rec['indent']
    nrows = len(rec['rows'])
    last = L - W * (nrows - 1)
    if rec['region'] == 'titoli':
        # due righe centrate: riga1 30 char, riga2 il resto
        parts = re.split(r'\s{3,}', it)
        if len(parts) < 2:
            parts = [it, '']
        r1 = parts[0].strip(); r2 = " ".join(p.strip() for p in parts[1:]).strip()
        w2 = L - 30
        return (r1.center(30)[:30] + r2.center(w2)[:w2]), []
    usable = W - ind
    words = it.split()
    lines = []; cur = ""
    for wd in words:
        if not cur:
            cur = wd
        elif len(cur) + 1 + len(wd) <= usable:
            cur += " " + wd
        else:
            lines.append(cur); cur = wd
    if cur:
        lines.append(cur)
    warn = []
    if len(lines) > nrows:
        warn.append(f"{len(lines)-nrows} righe in eccesso (testo troppo lungo)")
        lines = lines[:nrows]
    out = ""
    for k in range(nrows):
        line = (" " * ind + lines[k]) if k < len(lines) else ""
        width = W if k < nrows - 1 else last
        if len(line) > width:
            warn.append(f"riga {k+1} tagliata")
        out += line[:width].ljust(width)
    return out, warn



WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.'%-]*")
BIG = 1e9
STRONG = re.compile(r"[0-9]|\.")          # sigle e numeri: ancore forti


def norm(tok):
    """Forma normalizzata: sigle/numeri esatti, parole lunghe ridotte alla radice."""
    t = tok.strip(".'-").upper()
    if len(t) < 3:
        return None
    if STRONG.search(t):
        return t
    if len(t) >= 6:
        return t[:4]
    return t


def tokens(text):
    out = []
    for m in WORD.finditer(text):
        t = norm(m.group(0))
        if t:
            out.append((t, m.start(), len(m.group(0))))
    for m in re.finditer(r'\((?= )|\((?=\s)', text):
        out.append(('(', m.start(), 1))
    return sorted(out, key=lambda x: x[1])


def rom_tokens(recs):
    out = []
    for k, r in enumerate(recs):
        if r['txt'].strip() == '(':
            out.append(('(', k)); continue
        for m in WORD.finditer(r['txt']):
            t = norm(m.group(0))
            if t:
                out.append((t, k))
    return out


def lcs(a, b):
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, nxt = dp[i], dp[i + 1]
        ai = a[i]
        for j in range(m - 1, -1, -1):
            row[j] = nxt[j + 1] + 1 if ai == b[j] else (nxt[j] if nxt[j] >= row[j + 1] else row[j + 1])
    out = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.append((i, j)); i += 1; j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def match_anchors(recs, text):
    en = rom_tokens(recs)
    it = tokens(text)
    pairs = lcs([t for t, _ in en], [t for t, _, _ in it])
    per_rec = {}
    for ei, ii in pairs:
        k = en[ei][1]; p = it[ii][1]
        per_rec.setdefault(k, []).append(p)
    for k in per_rec:
        per_rec[k].sort()
    return per_rec, len(pairs), len(en)


def capacity(rec):
    """Caratteri utili del record: righe x (larghezza - rientro)."""
    nrows = len(rec['rows'])
    return nrows * (rec['w'] - rec['indent'])


def need(text):
    """Caratteri necessari: parole separate da un solo spazio."""
    return len(" ".join(text.split()))


def align(recs, text, band=340, min_run=2, w_anchor=80.0, w_over=6.0,
          credits_marker="AIR STRIKE PATROL STAFF"):
    K = len(recs)
    ci = text.find(credits_marker)
    tb = ci if ci >= 0 else len(text)
    anc, nmatch, ntot = match_anchors(recs, text)
    # stima iniziale: interpolazione monotona sulle ancore
    knots = [(0, 0)]
    for k in sorted(anc):
        p = anc[k][0]
        if p >= knots[-1][1] and recs[k]['off'] > knots[-1][0]:
            knots.append((recs[k]['off'], p))
    total_en = recs[K - 1]['off']
    knots.append((total_en, tb))
    xs = [x for x, _ in knots]

    def interp(off):
        i = bisect.bisect_left(xs, off)
        if i <= 0:
            return 0
        if i >= len(knots):
            return tb
        x0, y0 = knots[i - 1]; x1, y1 = knots[i]
        return y0 + (y1 - y0) * (off - x0) / max(1, x1 - x0)

    est = [int(round(interp(recs[k]['off']))) for k in range(K)] + [tb]
    cand = [0] + [(m.start() + m.end()) // 2 for m in re.finditer(r' {%d,}' % min_run, text)
                  if 0 < (m.start() + m.end()) // 2 < tb] + [tb]
    cand = sorted(set(cand))
    # premio ai tagli che cadono dopo la fine di una frase
    bonus = {}
    for m in re.finditer(r'[.!?]["\')]*\s{2,}', text):
        p = (m.end() + m.start() + len(m.group(0).rstrip())) // 2
        i = bisect.bisect_left(cand, m.start()); 
        for q in cand[max(0, i - 1):i + 2]:
            if m.start() <= q <= m.end():
                bonus[q] = -35.0
    sets = []
    for k in range(K):
        a = bisect.bisect_left(cand, est[k] - band); b = bisect.bisect_right(cand, est[k] + band)
        sets.append(cand[a:b] or [min(max(est[k], 0), tb)])
    sets[0] = [0]; sets.append([tb])
    dp = [0.0]; back = []
    for k in range(K - 1):                       # l'ultimo record (credits) resta originale
        cur, nxt = sets[k], sets[k + 1]
        L = recs[k]['len']
        cap = capacity(recs[k])
        A = anc.get(k, [])
        vals, bk = [], []
        for p2 in nxt:
            best = (BIG, 0)
            for j, p1 in enumerate(cur):
                if p2 < p1 or dp[j] >= BIG:
                    continue
                out = len(A) - (bisect.bisect_left(A, p2) - bisect.bisect_left(A, p1))
                over = max(0, need(text[p1:p2]) - cap)
                c = dp[j] + abs((p2 - p1) - L) + w_anchor * out + w_over * over + bonus.get(p2, 0.0)
                if c < best[0]:
                    best = (c, j)
            vals.append(best[0]); bk.append(best[1])
        dp = vals; back.append(bk)
    j = min(range(len(dp)), key=lambda i: dp[i])
    path = [j]
    for k in range(len(back) - 1, -1, -1):
        j = back[k][j]; path.append(j)
    path.reverse()
    bounds = [sets[k][path[k]] for k in range(K)] + [tb, len(text)]
    for k in range(1, len(bounds)):
        bounds[k] = max(bounds[k], bounds[k - 1])
    pieces = [text[bounds[k]:bounds[k + 1]] for k in range(K)]
    pieces[K - 1] = None                          # credits: originale
    return pieces, bounds, {'ancore': nmatch, 'token': ntot}


PROPER = re.compile(r"[0-9]|[A-Z]\.")


def check(recs, pieces):
    """Record sospetti: ancore forti (numeri/sigle) presenti nell'originale ma non nella traduzione."""
    bad = []
    for k, (r, p) in enumerate(zip(recs, pieces)):
        if p is None:
            continue
        en = {t for t in WORD.findall(r['txt']) if PROPER.search(t) and len(t) > 2}
        it = {t for t in WORD.findall(p) if PROPER.search(t) and len(t) > 2}
        if len(en) >= 2 and not (en & it):
            bad.append(k)
    return bad



INF = float('inf')


def layout(words, widths):
    """Distribuisce le parole nelle righe (larghezze diverse). Ritorna (righe, parole_non_entrate)."""
    n = len(words)
    R = len(widths)
    # f[r][i] = costo minimo per sistemare words[i:] nelle righe r:
    f = [[INF] * (n + 1) for _ in range(R + 1)]
    ch = [[None] * (n + 1) for _ in range(R + 1)]
    for r in range(R + 1):
        f[r][n] = 0.0
    for r in range(R - 1, -1, -1):
        w = widths[r]
        for i in range(n - 1, -1, -1):
            ln = -1; j = i; best = INF; bj = i
            while j < n:
                ln += 1 + len(words[j])
                if ln > w:
                    break
                j += 1
                slack = w - ln
                c = slack * slack + f[r + 1][j]
                if c < best:
                    best = c; bj = j
            if best < INF:
                f[r][i] = best; ch[r][i] = bj
            if i == j:            # parola piu' lunga della riga: va spezzata a forza
                f[r][i] = INF
    if f[0][0] == INF:
        return None, words
    lines = []; i = 0
    for r in range(R):
        j = ch[r][i] if ch[r][i] is not None else i
        lines.append(" ".join(words[i:j])); i = j
    return lines, words[i:]


def greedy(words, widths):
    """Riempimento massimo riga per riga: conserva piu' testo possibile."""
    lines = []; i = 0
    for w in widths:
        cur = ""
        while i < len(words):
            add = words[i] if not cur else " " + words[i]
            if len(cur) + len(add) > w:
                break
            cur += add; i += 1
        lines.append(cur)
    return lines, words[i:]


REGION_MAX_ROWS = {'titoli': 2, 'schermate missione': 4, 'sergente': 5,
                   'notiziari': 2, 'finali': 9, 'credits': 78}


def render(rec, italian, keep_breaks=True):
    """Testo del record (lunghezza variabile entro il massimo di righe della regione) + avvisi."""
    L, W, ind = rec['len'], rec['w'], rec['indent']
    nrows = max(len(rec['rows']), REGION_MAX_ROWS.get(rec['region'], len(rec['rows'])))
    last = L - W * (nrows - 1)
    widths = [W - ind] * nrows          # il record puo' crescere: ultima riga a larghezza piena
    warn = []
    if rec['txt'].strip() == '(':
        return rec['txt'], warn
    txt = de_accent(italian).strip().strip('(').strip()
    if rec['region'] == 'titoli':
        parts = [p.strip() for p in re.split(r' {3,}', txt) if p.strip()]
        r1 = parts[0] if parts else ""
        r2 = " ".join(parts[1:]) if len(parts) > 1 else ""
        w2 = L - 30
        if len(r1) > 30:
            warn.append(f"riga 1 di {len(r1)} caratteri (max 30)")
        if len(r2) > w2:
            warn.append(f"riga 2 di {len(r2)} caratteri (max {w2})")
        L = max(len(r1), len(r2)); st = (30 - L) // 2
        row1 = (' ' * (st + (L - len(r1)) // 2) + r1).ljust(30)[:30]
        row2 = (' ' * (st + (L - len(r2)) // 2) + r2) if r2 else ''
        if len(row2) > w2:
            warn.append(f"riga 2 centrata occupa {len(row2)} colonne (spazio {w2}): il record cresce")
        return row1 + row2.ljust(w2), warn

    lines = None; left = []
    if keep_breaks:
        # prova le interruzioni del traduttore a soglie decrescenti, tenendo la spaziatura interna
        for soglia in (12, 6, 3):
            segs = [s.strip() for s in re.split(r' {%d,}' % soglia, txt) if s.strip()]
            if 1 < len(segs) <= nrows and all(len(s) <= widths[i] for i, s in enumerate(segs)):
                lines = segs + [""] * (nrows - len(segs))
                break
    segs = [s.strip() for s in re.split(r' {3,}', txt) if s.strip()]
    words = txt.split()
    if lines is None:
        lines, left = layout(words, widths)     # 1) impaginazione ottimale
    if lines is None:
        lines, left = greedy(words, widths)     # 2) riempimento massimo
    if left:
        lines2, left2 = greedy(words, widths)   # 3) ricontrolla col riempimento massimo
        if len(left2) < len(left):
            lines, left = lines2, left2
    if left:
        warn.append("non entra: " + " ".join(left)[:60])
    rows_txt = []
    for k in range(nrows):
        line = (" " * ind + lines[k]) if k < len(lines) and lines[k] else ""
        rows_txt.append(line[:W])
    while len(rows_txt) > 1 and not rows_txt[-1].strip():
        rows_txt.pop()                                # niente righe vuote in coda
    out = "".join(r.ljust(W) for r in rows_txt[:-1]) + rows_txt[-1].rstrip()
    if not out:
        out = " "
    return out, warn


def build_block(block, recs, pieces):
    out = bytearray(block); report = []
    for k, (r, p) in enumerate(zip(recs, pieces)):
        if p is None or not p.strip():
            continue
        txt, warn = render(r, p)
        assert len(txt) == r['len'], (k, len(txt), r['len'])
        try:
            b = txt.encode('ascii')
        except UnicodeEncodeError:
            b = txt.encode('ascii', 'replace'); warn.append("caratteri non ASCII")
        out[r['off']:r['off'] + r['len']] = b
        if warn:
            report.append((k, r['off'], warn, txt))
    return bytes(out), report


def assemble(block, recs, rendered, target=None):
    """Ricompone il blocco con record di lunghezza variabile, mantenendo i codici di controllo.
    La differenza di lunghezza viene assorbita dagli spazi dei titoli di coda."""
    target = target or len(block)
    out = bytearray()
    prev_end = 0
    for r, txt in zip(recs, rendered):
        out += block[prev_end:r['off']]                  # codici di controllo
        out += txt.encode('ascii', 'replace')
        prev_end = r['off'] + r['len']
    out += block[prev_end:]
    delta = target - len(out)
    if delta > 0:                                        # avanza spazio: riempi DOPO il terminatore finale
        out += b' ' * delta
    elif delta < 0:                                      # avanza: accorcia le pause dei credits
        need = -delta
        import re as _re
        for m in sorted(_re.finditer(rb' {6,}', bytes(out)), key=lambda m: -(m.end() - m.start())):
            if need <= 0:
                break
            take = min(need, (m.end() - m.start()) - 4)
            if take > 0:
                out[m.start():m.start() + take] = b''
                need -= take
        if need > 0:
            raise ValueError(f"il testo eccede il blocco di {need} byte")
    return bytes(out)


# ── API ──────────────────────────────────────────────────────────────────────
def applica_correzioni(testo, correzioni):
    """Sostituzioni (refusi) prima dell'allineamento. Ritorna (testo, conteggi)."""
    fatte = {}
    for a, b in (correzioni or {}).items():
        n = testo.count(a)
        if n:
            testo = testo.replace(a, b); fatte[a + " -> " + b] = n
    return testo, fatte


def importa_traduzione(block, testo, correzioni=None):
    """Ritorna (blocco_tradotto, resoconto, info). resoconto: un dict per record."""
    testo, fatte = applica_correzioni(testo, correzioni)
    recs = split_records(block)
    pieces, bounds, info = align(recs, testo)
    rendered = []; resoconto = []
    for k, (r, p) in enumerate(zip(recs, pieces)):
        if p is None or not p.strip():
            rendered.append(r['txt'])
            resoconto.append({'record': k, 'regione': r['region'], 'avvisi': [],
                              'en': r['txt'], 'it': None})
            continue
        txt, warn = render(r, p)
        rendered.append(txt)
        resoconto.append({'record': k, 'regione': r['region'], 'avvisi': warn,
                          'en': r['txt'], 'it': txt})
    nuovo = assemble(block, recs, rendered)
    info['record'] = len(recs)
    info['avvisi'] = sum(1 for x in resoconto if x['avvisi'])
    info['correzioni'] = fatte
    return nuovo, resoconto, info


def scrivi_confronto(path, block, resoconto):
    """File di controllo: originale e traduzione affiancati, riga per riga."""
    recs = split_records(block)
    with open(path, 'w', encoding='utf-8') as f:
        for x, r in zip(resoconto, recs):
            f.write(f"=== record {x['record']:3}  {x['regione']}  (righe da {r['w']})\n")
            if x['avvisi']:
                f.write("    AVVISI: " + "; ".join(x['avvisi']) + "\n")
            en = [x['en'][i:i + r['w']] for i in range(0, len(x['en']), r['w'])]
            it = ([x['it'][i:i + r['w']] for i in range(0, len(x['it']), r['w'])]
                  if x['it'] is not None else ["(originale)"])
            for i in range(max(len(en), len(it))):
                a = en[i] if i < len(en) else ""
                b = it[i] if i < len(it) else ""
                f.write(f"  EN |{a:<{r['w']}}|\n  IT |{b:<{r['w']}}|\n")
            f.write("\n")
