#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A.S.P. Air Strike Patrol (USA) — Italian Translation Tool v4
- logica ROM in asp_core.py (testabile senza interfaccia)
- stringhe in chiaro (blocco ASCII $85 + tabelle verificate sul codice)
- ROM espansa automatica a 2 MB quando il blocco compresso non entra nello slot
- emulatore integrato (asp_run, core Snaggletooth): confronto schermate e tracciamento testo
- salvataggio/apertura progetto (.json) con tutte le traduzioni
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os, sys, re, json, threading, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── PALETTE ──────────────────────────────────────────────────────────────────
BG="#0D1117"; PANEL="#161B22"; BORDER="#30363D"; DARK="#1C2128"
GREEN="#39D353"; AMBER="#F0A500"; RED="#F85149"; BLUE="#388BFD"
CYAN="#79C0FF"; WHITE="#E6EDF3"; GRAY="#8B949E"; PURPLE="#D2A8FF"
FM=("Consolas",10) if sys.platform=="win32" else ("Monospace",10)
FH=("Consolas",12,"bold") if sys.platform=="win32" else ("Monospace",12,"bold")
FS=("Consolas",9) if sys.platform=="win32" else ("Monospace",9)

from asp_core import *
from asp_import import importa_traduzione, scrivi_confronto   # costanti, LZ, parser, catalogo stringhe, build, IPS, emulatore

# ── WIDGET: RIGA CON COUNTER ─────────────────────────────────────────────────
class LineEdit(tk.Frame):
    def __init__(self, parent, label, maxc, var, colon=None, **kw):
        super().__init__(parent, bg=PANEL, **kw)
        self.maxc=maxc; self.colon=colon; self.var=var
        tk.Label(self,text=label,bg=PANEL,fg=GRAY,font=FS,width=10,
                  anchor='e').pack(side='left',padx=(2,3))
        self.ent=tk.Entry(self,textvariable=var,bg=DARK,fg=WHITE,font=FM,
                          insertbackground=GREEN,relief='flat',width=maxc+2,
                          highlightthickness=1,highlightbackground=BORDER,
                          highlightcolor=BLUE)
        self.ent.pack(side='left',padx=(0,4))
        self.ctr=tk.Label(self,text="",bg=PANEL,fg=GREEN,font=FS,width=7)
        self.ctr.pack(side='left')
        self.dot=tk.Label(self,text="●",bg=PANEL,fg=GREEN,font=FS)
        self.dot.pack(side='left',padx=2)
        self._trace=var.trace_add('write',self._upd)
        self._upd()
    def _upd(self,*_):
        t=self.var.get(); n=len(t)
        self.ctr.config(text=f"{n}/{self.maxc}")
        ok = n<=self.maxc and (not self.colon or (n>=self.colon and t[self.colon-1]==':'))
        c=GREEN if ok else (AMBER if n<=self.maxc else RED)
        self.ctr.config(fg=c); self.dot.config(fg=c)

# ── WIDGET: DOPPIA COLONNA EN/IT ─────────────────────────────────────────────
class BiEditor(tk.Frame):
    def __init__(self, parent, height=12, width_hint=48, **kw):
        super().__init__(parent,bg=PANEL,**kw)
        self.width_hint=width_hint
        h=tk.Frame(self,bg=PANEL); h.pack(fill='x',padx=4,pady=(4,0))
        tk.Label(h,text="◄ ORIGINALE (EN) — sola lettura",bg=PANEL,fg=GRAY,
                  font=FS).pack(side='left')
        tk.Label(h,text="TRADUZIONE (IT) — editabile ►",bg=PANEL,fg=AMBER,
                  font=FS).pack(side='right')
        body=tk.Frame(self,bg=PANEL); body.pack(fill='both',expand=True,padx=4,pady=3)
        lf=tk.Frame(body,bg=DARK,bd=1,relief='solid')
        lf.pack(side='left',fill='both',expand=True,padx=(0,3))
        self.orig=tk.Text(lf,height=height,bg=DARK,fg=GRAY,font=FM,wrap='none',
                          state='disabled',relief='flat',highlightthickness=0)
        s1=ttk.Scrollbar(lf,command=self.orig.yview); s1.pack(side='right',fill='y')
        self.orig.pack(fill='both',expand=True); self.orig.config(yscrollcommand=s1.set)
        rf=tk.Frame(body,bg=DARK,bd=1,relief='solid')
        rf.pack(side='left',fill='both',expand=True,padx=(3,0))
        self.it=tk.Text(rf,height=height,bg="#0E1620",fg=GREEN,font=FM,wrap='none',
                         insertbackground=GREEN,relief='flat',highlightthickness=0)
        s2=ttk.Scrollbar(rf,command=self.it.yview); s2.pack(side='right',fill='y')
        self.it.pack(fill='both',expand=True); self.it.config(yscrollcommand=s2.set)
        bar=tk.Frame(self,bg=PANEL); bar.pack(fill='x',padx=4,pady=(0,3))
        self.info=tk.Label(bar,text="",bg=PANEL,fg=GRAY,font=FS); self.info.pack(side='left')
        self.it.bind('<KeyRelease>',self._chk)
    def set_orig(self,t):
        self.orig.config(state='normal'); self.orig.delete('1.0','end')
        self.orig.insert('1.0',t); self.orig.config(state='disabled')
    def set_it(self,t):
        self.it.delete('1.0','end'); self.it.insert('1.0',t); self._chk()
    def get_it(self): return self.it.get('1.0','end-1c')
    def _chk(self,*_):
        lines=self.get_it().splitlines()
        bad=[f"R{i+1}={len(l)}" for i,l in enumerate(lines) if len(l)>self.width_hint]
        if bad: self.info.config(text=f"⚠ righe >{self.width_hint}: "+", ".join(bad[:5]),fg=RED)
        else: self.info.config(text=f"✓ {len(lines)} righe — max {self.width_hint} char",fg=GREEN)

# ═══════════════════════════════════════════════════════════════════════════
#  TAB: PANORAMICA
# ═══════════════════════════════════════════════════════════════════════════
class TabOverview(tk.Frame):
    def __init__(self,p,app):
        super().__init__(p,bg=BG); self.app=app
        tk.Label(self,text="◉ PANORAMICA ROM",bg=BG,fg=CYAN,font=FH).pack(pady=(10,6))
        f=tk.LabelFrame(self,text=" MAPPA ",bg=PANEL,fg=AMBER,font=FS,bd=1,relief='solid')
        f.pack(fill='x',padx=12,pady=3)
        rows=[("0x000000-0x028246","160 KB","Codice 65816","—",GRAY),
              ("0x028247-0x02A6FE","9.4 KB","Testo ASCII bank $85 (scheda Stringhe)","MODIFICABILE",GREEN),
              (f"0x{LZ_OFFSET:06X}","0x3607","Blocco LZ compresso (slot, margine 12 B!)","→ $A0:8000 se serve",AMBER),
              ("  └ decompresso","36031 B","8 titoli+missioni+sergente+news+13 finali","",AMBER),
              ("0x058000-0x0C0000","416 KB","Grafica 2bpp/4bpp","Solo YY-CHR",GRAY),
              ("0x100000-0x107FFF","32 KB","ROM espansa: blocco LZ tradotto ($A0:8000)","AUTOMATICO",CYAN),
              ("0x108000-...","—","ROM espansa: mappe grafiche cresciute ($A1:8000+)","AUTOMATICO",CYAN)]
        for o,s,d,st,c in rows:
            r=tk.Frame(f,bg=PANEL); r.pack(fill='x',padx=4)
            for t,w in [(o,20),(s,9),(d,46),(st,14)]:
                tk.Label(r,text=t,bg=PANEL,fg=c,font=FS,width=w,anchor='w').pack(side='left')
        f2=tk.LabelFrame(self,text=" VINCOLI FORMATTAZIONE ",bg=PANEL,fg=AMBER,font=FS,
                          bd=1,relief='solid'); f2.pack(fill='x',padx=12,pady=3)
        for h in ["Titoli (8): R1=30 char centrata | R2=lunghezza variabile 20-27, centrata",
                  "Screen 1 briefing: R1-R3 max 56 | R4 max 32",
                  "Screen 2 intel: 14 spazi + R1-R3 max 42 | 14 spazi + R4 max 29",
                  "Screen 3 stats: testo utile 19 char, ':' obbligatorio in posizione 16",
                  "Sergente S1: R1-R3 max 56 | R4 max 45   —   S2: R1-R3 max 56 | R4 max 19",
                  "FINALI: righe da 48 char (verificato 8x48=384), testo CENTRATO"]:
            tk.Label(f2,text="• "+h,bg=PANEL,fg=GRAY,font=FS,anchor='w').pack(fill='x',padx=6)
        self.stats=tk.LabelFrame(self,text=" STATO ",bg=PANEL,fg=AMBER,font=FS,bd=1,relief='solid')
        self.stats.pack(fill='both',expand=True,padx=12,pady=3)
        self.stats_lbl=tk.Label(self.stats,text="Carica la ROM per l'analisi.",
                                 bg=PANEL,fg=GRAY,font=FM,justify='left',anchor='nw')
        self.stats_lbl.pack(fill='both',expand=True,padx=6,pady=4)
    def refresh(self):
        a=self.app
        if not a.bin_data:
            return
        t=a.data
        lines=[f"Decompresso: {len(a.bin_data):,} byte (atteso {DECOMP_SIZE:,})",
               f"Titoli trovati:    {len(t['titles'])}",
               f"Schermate missione:{len(t['screens'])}",
               f"Finali trovati:    {len(t['finali'])}",
               ""]
        tr=sum(1 for v in t['tr_screens'].values() if v.strip())
        lines.append(f"Schermate tradotte: {tr}/{len(t['screens'])}")
        tt=sum(1 for v in t['tr_titles'] if v[0].get().strip() or v[1].get().strip())
        lines.append(f"Titoli tradotti:    {tt}/{len(t['titles'])}")
        tf=sum(1 for v in t['tr_finali'] if v.strip())
        lines.append(f"Finali tradotti:    {tf}/{len(t['finali'])}")
        ts=sum(1 for s in a.strings if s['tr'])
        rt=sum(1 for s in a.strings if s['runtime'])
        lines.append(f"Stringhe in chiaro: {ts}/{len(a.strings)} tradotte ({rt} confermate a runtime)")
        tg=sum(1 for e in a.gfx if e['tr'])
        lines.append(f"Scritte grafiche:   {tg}/{len(a.gfx)} tradotte")
        self.stats_lbl.config(text="\n".join(lines),fg=WHITE)

# ═══════════════════════════════════════════════════════════════════════════
#  TAB: TITOLI
# ═══════════════════════════════════════════════════════════════════════════
class TabTitles(tk.Frame):
    def __init__(self,p,app):
        super().__init__(p,bg=BG); self.app=app
        tk.Label(self,text="TITOLI MISSIONE — riga piu' lunga centrata nello schermo, l'altra centrata su di essa",
                  bg=BG,fg=CYAN,font=FH).pack(pady=(10,4))
        cv=tk.Canvas(self,bg=BG,highlightthickness=0)
        sb=ttk.Scrollbar(self,orient='vertical',command=cv.yview)
        sb.pack(side='right',fill='y'); cv.pack(fill='both',expand=True)
        cv.configure(yscrollcommand=sb.set)
        self.inner=tk.Frame(cv,bg=BG)
        cv.create_window((0,0),window=self.inner,anchor='nw')
        self.inner.bind('<Configure>',lambda e:cv.configure(scrollregion=cv.bbox('all')))
        self.built=False
    def refresh(self):
        if self.built or not self.app.bin_data: return
        self.built=True
        d=self.app.data
        for i,t in enumerate(d['titles']):
            f=tk.LabelFrame(self.inner,text=f"  Titolo {i+1}  (0x{t['off']:04X}, R2={t['r2len']} char)  ",
                             bg=PANEL,fg=CYAN,font=FS,bd=1,relief='solid')
            f.pack(fill='x',padx=10,pady=3)
            of=tk.Frame(f,bg=PANEL); of.pack(fill='x',padx=6,pady=(2,0))
            tk.Label(of,text="EN:",bg=PANEL,fg=GRAY,font=FS,width=4,anchor='e').pack(side='left')
            tk.Label(of,text=f"[{t['r1']}]",bg=PANEL,fg=GRAY,font=FM).pack(side='left')
            of2=tk.Frame(f,bg=PANEL); of2.pack(fill='x',padx=6)
            tk.Label(of2,text="",bg=PANEL,font=FS,width=4).pack(side='left')
            tk.Label(of2,text=f"[{t['r2']}]",bg=PANEL,fg=GRAY,font=FM).pack(side='left')
            v1=d['tr_titles'][i][0]; v2=d['tr_titles'][i][1]
            LineEdit(f,"IT R1:",30,v1).pack(fill='x',padx=6,pady=1)
            LineEdit(f,"IT R2:",t['r2len'],v2).pack(fill='x',padx=6,pady=1)
            pf=tk.Frame(f,bg="#0D1117",bd=1,relief='solid'); pf.pack(fill='x',padx=6,pady=(0,4))
            pl=tk.Label(pf,text="",bg="#0D1117",fg=GREEN,font=FM,justify='left')
            pl.pack(fill='x')
            def mk(a,b,l,w2,t=t):
                def u(*_):
                    r1,r2=title_rows(a.get() or t['r1'],b.get() or t['r2'])
                    ok=len(r2)<=w2
                    l.config(text=f"[{r1}]\n[{r2.ljust(30)}]"+("" if ok else f"   (il titolo cresce di {len(r2)-w2}: ok)"),
                             fg=GREEN if ok else AMBER)
                a.trace_add('write',u); b.trace_add('write',u); u()
            mk(v1,v2,pl,t['r2len'])

# ═══════════════════════════════════════════════════════════════════════════
#  TAB: SCHERMATE (modello dati separato — niente cache di widget)
# ═══════════════════════════════════════════════════════════════════════════
class TabScreens(tk.Frame):
    def __init__(self,p,app):
        super().__init__(p,bg=BG); self.app=app
        hdr=tk.Frame(self,bg=BG); hdr.pack(fill='x',padx=10,pady=(8,2))
        tk.Label(hdr,text="Schermata:",bg=BG,fg=WHITE,font=FS).pack(side='left')
        self.cb=ttk.Combobox(hdr,width=62,state='readonly')
        self.cb.pack(side='left',padx=4)
        self.cb.bind('<<ComboboxSelected>>',lambda e:self._show())
        tk.Button(hdr,text="◄",command=lambda:self._step(-1),bg=BORDER,fg=WHITE,
                   font=FS,width=3,relief='flat').pack(side='left',padx=2)
        tk.Button(hdr,text="►",command=lambda:self._step(1),bg=BORDER,fg=WHITE,
                   font=FS,width=3,relief='flat').pack(side='left')
        tk.Button(hdr,text="📋 Copia EN→IT",command=self._copy,bg=BLUE,fg=WHITE,
                   font=FS,relief='flat').pack(side='left',padx=8)
        self.info=tk.Label(self,text="",bg=BG,fg=AMBER,font=FS); self.info.pack(pady=2)
        self.editor=BiEditor(self,height=10,width_hint=ROW_MISSION)
        self.editor.pack(fill='both',expand=True,padx=10,pady=4)
        self.cur=0; self.built=False
        self.editor.it.bind('<KeyRelease>',self._save_cur,add='+')
    def refresh(self):
        if not self.app.bin_data: return
        if not self.built:
            self.built=True
            names=[]
            for i,s in enumerate(self.app.data['screens']):
                prev=s['text'][:40].strip().replace('\n',' ')
                names.append(f"{i:03d} @0x{s['off']:04X} ({s['len']}B) {prev}")
            self.cb['values']=names
            if names: self.cb.current(0)
        self._show(save=False)   # v4: non salvare l'editor al refresh (sovrascriveva la traduzione)
    def _save_cur(self,*_):
        if self.app.bin_data and self.app.data['screens']:
            self.app.data['tr_screens'][self.cur]=self.editor.get_it()
    def _step(self,d):
        self._save_cur()
        n=len(self.app.data['screens'])
        if n:
            self.cur=(self.cur+d)%n; self.cb.current(self.cur); self._show(save=False)
    def _copy(self):
        s=self.app.data['screens'][self.cur]
        self.editor.set_it(self._fmt(s['text']))
        self._save_cur()
    def _fmt(self,txt):
        return '\n'.join(txt[i:i+ROW_MISSION] for i in range(0,len(txt),ROW_MISSION))
    def _show(self,save=True):
        if save: self._save_cur()
        if not self.app.data['screens']: return
        self.cur=self.cb.current() if self.cb.current()>=0 else 0
        s=self.app.data['screens'][self.cur]
        lead=' '.join(f'{b:02X}' for b in s['lead']) or '—'
        self.info.config(text=f"Offset 0x{s['off']:04X} | {s['len']} byte | lead: [{lead}] | righe da {ROW_MISSION} char")
        self.editor.set_orig(self._fmt(s['text']))
        self.editor.set_it(self.app.data['tr_screens'].get(self.cur,''))

# ═══════════════════════════════════════════════════════════════════════════
#  TAB: FINALI (righe da 48, centratura automatica)
# ═══════════════════════════════════════════════════════════════════════════
class TabFinali(tk.Frame):
    def __init__(self,p,app):
        super().__init__(p,bg=BG); self.app=app
        tk.Label(self,text=f"FINALI — righe da {ROW_FINALE} caratteri, testo CENTRATO",
                  bg=BG,fg=CYAN,font=FH).pack(pady=(10,2))
        hdr=tk.Frame(self,bg=BG); hdr.pack(fill='x',padx=10,pady=2)
        tk.Label(hdr,text="Finale:",bg=BG,fg=WHITE,font=FS).pack(side='left')
        self.cb=ttk.Combobox(hdr,width=50,state='readonly'); self.cb.pack(side='left',padx=4)
        self.cb.bind('<<ComboboxSelected>>',lambda e:self._show())
        tk.Button(hdr,text="📋 Copia EN→IT",command=self._copy,bg=BLUE,fg=WHITE,
                   font=FS,relief='flat').pack(side='left',padx=6)
        tk.Button(hdr,text="⊞ Centra automaticamente",command=self._center,
                   bg=AMBER,fg=BG,font=FS,relief='flat').pack(side='left',padx=4)
        self.info=tk.Label(self,text="",bg=BG,fg=AMBER,font=FS); self.info.pack(pady=2)
        self.editor=BiEditor(self,height=12,width_hint=ROW_FINALE)
        self.editor.pack(fill='both',expand=True,padx=10,pady=4)
        tk.Label(self,text="Scrivi il testo liberamente, poi premi 'Centra automaticamente': "
                            "il tool riflusserà e centrerà ogni riga a 48 caratteri.",
                  bg=BG,fg=GRAY,font=FS).pack(pady=(0,4))
        self.cur=0; self.built=False
        self.editor.it.bind('<KeyRelease>',self._save,add='+')
    def refresh(self):
        if not self.app.bin_data: return
        if not self.built:
            self.built=True
            names=[]
            for i,f in enumerate(self.app.data['finali']):
                prev=f['text'][:34].strip()
                names.append(f"Finale {i+1:02d} @0x{f['off']:04X} ({f['len']}B) — {prev}")
            self.cb['values']=names
            if names: self.cb.current(0)
        self._show(save=False)   # v4: non salvare l'editor al refresh (sovrascriveva la traduzione)
    def _save(self,*_):
        if self.app.data['finali']:
            self.app.data['tr_finali'][self.cur]=self.editor.get_it()
    def _copy(self):
        f=self.app.data['finali'][self.cur]
        self.editor.set_it(self._fmt(f['text'])); self._save()
    def _center(self):
        txt=self.editor.get_it()
        f=self.app.data['finali'][self.cur]
        nrows=f['len']//ROW_FINALE
        lines=wrap_center(txt,ROW_FINALE,nrows)
        self.editor.set_it('\n'.join(lines)); self._save()
    def _fmt(self,t):
        return '\n'.join(t[i:i+ROW_FINALE] for i in range(0,len(t),ROW_FINALE))
    def _show(self,save=True):
        if save: self._save()
        if not self.app.data['finali']: return
        self.cur=self.cb.current() if self.cb.current()>=0 else 0
        f=self.app.data['finali'][self.cur]
        nrows=f['len']//ROW_FINALE
        self.info.config(text=f"Offset 0x{f['off']:04X} | {f['len']} byte | {nrows} righe da {ROW_FINALE} char")
        self.editor.set_orig(self._fmt(f['text']))
        self.editor.set_it(self.app.data['tr_finali'][self.cur])

# ═══════════════════════════════════════════════════════════════════════════
#  TAB: RELOCAZIONE PUNTATORI (generico)
# ═══════════════════════════════════════════════════════════════════════════
class TabPointers(tk.Frame):
    def __init__(self,p,app):
        super().__init__(p,bg=BG); self.app=app
        tk.Label(self,text="RELOCAZIONE TESTI TRAMITE PUNTATORI",
                  bg=BG,fg=CYAN,font=FH).pack(pady=(10,4))

        # ── Scanner spazio libero ──
        f1=tk.LabelFrame(self,text=" 1. Scanner spazio libero ",bg=PANEL,fg=AMBER,
                          font=FS,bd=1,relief='solid'); f1.pack(fill='x',padx=12,pady=3)
        r=tk.Frame(f1,bg=PANEL); r.pack(fill='x',padx=6,pady=3)
        tk.Label(r,text="Min byte:",bg=PANEL,fg=WHITE,font=FS).pack(side='left')
        self.minlen=tk.StringVar(value="32")
        tk.Entry(r,textvariable=self.minlen,bg=DARK,fg=GREEN,font=FM,width=6,
                  insertbackground=GREEN).pack(side='left',padx=4)
        tk.Button(r,text="🔍 Scansiona ROM",command=self._scan,bg=BLUE,fg=WHITE,
                   font=FS,relief='flat').pack(side='left',padx=6)
        self.scan_info=tk.Label(r,text="",bg=PANEL,fg=GREEN,font=FS); self.scan_info.pack(side='left',padx=6)
        cols=('pc','snes','len','fill')
        self.tree=ttk.Treeview(f1,columns=cols,show='headings',height=6)
        for c,t,w in zip(cols,['PC offset','SNES addr','Byte liberi','Fill'],[100,110,100,60]):
            self.tree.heading(c,text=t); self.tree.column(c,width=w,anchor='w')
        self.tree.pack(fill='x',padx=6,pady=3)

        # ── Relocazione ──
        f2=tk.LabelFrame(self,text=" 2. Sposta una stringa e aggiorna il puntatore ",
                          bg=PANEL,fg=AMBER,font=FS,bd=1,relief='solid')
        f2.pack(fill='x',padx=12,pady=3)
        grid=tk.Frame(f2,bg=PANEL); grid.pack(fill='x',padx=6,pady=3)
        self.vars={}
        fields=[("ptr_addr","Indirizzo puntatore (PC hex):","0x0082A1",
                  "Dove sta il puntatore da riscrivere"),
                ("ptr_size","Dimensione puntatore:","3",
                  "2 = solo offset (16 bit) | 3 = offset+bank (24 bit)"),
                ("new_pc","Nuovo offset PC destinazione:","0x06EC74",
                  "Dallo scanner sopra"),
                ("new_text","Testo da scrivere:","FORZA!!!!",
                  "Verrà terminato con 0x00"),
                ("bank_pc","Byte bank nel codice (PC hex, opz.):","",
                  "es. 0x008263 per FORZA/ULTIMO VOLO (LDA #$81 @ $81:8262)")]
        for k,lbl,dflt,hint in fields:
            row=tk.Frame(grid,bg=PANEL); row.pack(fill='x',pady=2)
            tk.Label(row,text=lbl,bg=PANEL,fg=WHITE,font=FS,width=30,anchor='e').pack(side='left')
            v=tk.StringVar(value=dflt); self.vars[k]=v
            tk.Entry(row,textvariable=v,bg=DARK,fg=GREEN,font=FM,width=22,
                      insertbackground=GREEN).pack(side='left',padx=4)
            tk.Label(row,text=hint,bg=PANEL,fg=GRAY,font=FS).pack(side='left',padx=4)
        br=tk.Frame(f2,bg=PANEL); br.pack(fill='x',padx=6,pady=4)
        tk.Button(br,text="➕ Aggiungi alla lista relocazioni",command=self._add_reloc,
                   bg=GREEN,fg=BG,font=FS,relief='flat').pack(side='left')
        tk.Button(br,text="🗑 Svuota lista",command=self._clear_reloc,
                   bg=BORDER,fg=WHITE,font=FS,relief='flat').pack(side='left',padx=6)
        self.reloc_info=tk.Label(br,text="",bg=PANEL,fg=GREEN,font=FS)
        self.reloc_info.pack(side='left',padx=6)

        # ── Lista relocazioni ──
        f3=tk.LabelFrame(self,text=" 3. Relocazioni da applicare ",bg=PANEL,fg=AMBER,
                          font=FS,bd=1,relief='solid'); f3.pack(fill='both',expand=True,padx=12,pady=3)
        cols2=('ptr','newloc','text','bytes')
        self.rtree=ttk.Treeview(f3,columns=cols2,show='headings',height=6)
        for c,t,w in zip(cols2,['Puntatore @','Nuova posizione','Testo','Byte scritti'],
                          [110,130,240,110]):
            self.rtree.heading(c,text=t); self.rtree.column(c,width=w,anchor='w')
        self.rtree.pack(fill='both',expand=True,padx=6,pady=3)
        tk.Label(f3,text="Le relocazioni vengono applicate quando generi la ROM/IPS.",
                  bg=PANEL,fg=GRAY,font=FS).pack(anchor='w',padx=6,pady=(0,3))

    def _scan(self):
        if not self.app.rom_data:
            messagebox.showwarning("Attenzione","Carica prima la ROM."); return
        try: ml=int(self.minlen.get())
        except: ml=32
        regs=find_free_space(self.app.rom_data,ml)
        for i in self.tree.get_children(): self.tree.delete(i)
        tot=0
        for r in regs[:200]:
            bank,addr=pc_to_snes(r['off'])
            self.tree.insert('','end',values=(f"0x{r['off']:06X}",
                                                f"${bank:02X}:{addr:04X}",
                                                f"{r['len']:,}",
                                                f"0x{r['fill']:02X}"))
            tot+=r['len']
        self.scan_info.config(text=f"{len(regs)} regioni — {tot:,} byte totali liberi")

    def _add_reloc(self):
        try:
            pa=int(self.vars['ptr_addr'].get(),16)
            ps=int(self.vars['ptr_size'].get())
            np_=int(self.vars['new_pc'].get(),16)
            txt=self.vars['new_text'].get()
            data=txt.encode('ascii','replace')+b'\x00'
            bank,addr=pc_to_snes(np_)
            bp=self.vars['bank_pc'].get().strip()
            self.app.relocations.append({'ptr':pa,'size':ps,'dest':np_,
                                          'text':txt,'data':data,
                                          'bank':bank,'addr':addr,
                                          'bank_patches':[int(bp,16)] if bp else []})
            self.rtree.insert('','end',values=(f"0x{pa:06X} ({ps}B)",
                                                f"0x{np_:06X} = ${bank:02X}:{addr:04X}",
                                                repr(txt),f"{len(data)} B"))
            self.reloc_info.config(text=f"{len(self.app.relocations)} relocazioni in coda")
        except Exception as e:
            messagebox.showerror("Errore",str(e))

    def _clear_reloc(self):
        self.app.relocations.clear()
        for i in self.rtree.get_children(): self.rtree.delete(i)
        self.reloc_info.config(text="Lista svuotata")

    def refresh(self): pass


# ═══════════════════════════════════════════════════════════════════════════
#  TAB: STRINGHE IN CHIARO (blocco ASCII bank $85 + tabelle verificate)
# ═══════════════════════════════════════════════════════════════════════════
class TabStrings(tk.Frame):
    def __init__(self, p, app):
        super().__init__(p, bg=BG); self.app = app; self.cur = None
        top = tk.Frame(self, bg=BG); top.pack(fill='x', padx=10, pady=(8, 2))
        tk.Label(top, text="STRINGHE IN CHIARO", bg=BG, fg=CYAN, font=FH).pack(side='left')
        tk.Label(top, text="  Gruppo:", bg=BG, fg=WHITE, font=FS).pack(side='left')
        self.gvar = tk.StringVar(value="(tutti)")
        self.gbox = ttk.Combobox(top, textvariable=self.gvar, width=30, state='readonly', font=FS)
        self.gbox.pack(side='left', padx=4); self.gbox.bind('<<ComboboxSelected>>', lambda e: self._fill())
        tk.Label(top, text="Cerca:", bg=BG, fg=WHITE, font=FS).pack(side='left', padx=(8, 0))
        self.qvar = tk.StringVar(); self.qvar.trace_add('write', lambda *_: self._fill())
        tk.Entry(top, textvariable=self.qvar, bg=DARK, fg=GREEN, font=FS, width=18,
                 insertbackground=GREEN).pack(side='left', padx=4)
        self.only_rt = tk.BooleanVar(value=False)
        tk.Checkbutton(top, text="solo viste a runtime", variable=self.only_rt, command=self._fill,
                       bg=BG, fg=WHITE, selectcolor=DARK, activebackground=BG, font=FS).pack(side='left', padx=6)
        self.info = tk.Label(top, text="", bg=BG, fg=GRAY, font=FS); self.info.pack(side='right')

        cols = ('snes', 'grp', 'max', 'orig', 'tr', 'rt')
        fr = tk.Frame(self, bg=BG); fr.pack(fill='both', expand=True, padx=10, pady=3)
        self.tree = ttk.Treeview(fr, columns=cols, show='headings', height=16)
        for c, t, w in zip(cols, ['SNES', 'Gruppo', 'Max', 'Originale', 'Traduzione', 'Letta da (runtime)'],
                           [80, 140, 45, 330, 330, 150]):
            self.tree.heading(c, text=t); self.tree.column(c, width=w, anchor='w', stretch=(c in ('orig', 'tr')))
        sb = ttk.Scrollbar(fr, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True); sb.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self._select)
        self.tree.tag_configure('done', foreground=GREEN)
        self.tree.tag_configure('err', foreground=RED)

        ed = tk.LabelFrame(self, text=" Modifica ", bg=PANEL, fg=AMBER, font=FS, bd=1, relief='solid')
        ed.pack(fill='x', padx=10, pady=(2, 8))
        self.hdr = tk.Label(ed, text="Seleziona una stringa", bg=PANEL, fg=CYAN, font=FS, anchor='w', justify='left')
        self.hdr.pack(fill='x', padx=6, pady=(3, 0))
        self.orig = tk.Text(ed, height=3, bg=DARK, fg=GRAY, font=FM, wrap='none', state='disabled')
        self.orig.pack(fill='x', padx=6, pady=2)
        self.edit = tk.Text(ed, height=3, bg=DARK, fg=WHITE, font=FM, wrap='none', insertbackground=GREEN)
        self.edit.pack(fill='x', padx=6, pady=2)
        self.edit.bind('<KeyRelease>', lambda e: self._check())
        br = tk.Frame(ed, bg=PANEL); br.pack(fill='x', padx=6, pady=(0, 4))
        self.cnt = tk.Label(br, text="", bg=PANEL, fg=GREEN, font=FS); self.cnt.pack(side='left')
        tk.Button(br, text="✔ Salva (Ctrl+Invio)", command=self._save, bg=GREEN, fg=BG, font=FS,
                  relief='flat').pack(side='right')
        tk.Button(br, text="↺ Ripristina originale", command=self._revert, bg=BORDER, fg=WHITE, font=FS,
                  relief='flat').pack(side='right', padx=6)
        tk.Button(br, text="⇪ Copia originale", command=self._copy, bg=BORDER, fg=WHITE, font=FS,
                  relief='flat').pack(side='right')
        self.edit.bind('<Control-Return>', lambda e: (self._save(), 'break')[1])

    def refresh(self):
        S = self.app.strings
        groups = sorted({s['group'] for s in S})
        self.gbox['values'] = ["(tutti)"] + groups
        self._fill()

    def _fill(self):
        S = self.app.strings
        g = self.gvar.get(); q = self.qvar.get().upper().strip()
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        n = done = 0
        for i, s in enumerate(S):
            if g != "(tutti)" and s['group'] != g: continue
            if q and q not in s['orig'].upper() and q not in s['tr'].upper(): continue
            if self.only_rt.get() and not s['runtime']: continue
            tag = ()
            if s['tr']:
                tag = ('err',) if check_translation(s, s['tr']) else ('done',); done += 1
            self.tree.insert('', 'end', iid=str(i), tags=tag,
                             values=(fmt_snes(s['snes']), s['group'], s['maxlen'],
                                     s['orig'], s['tr'], s['runtime']))
            n += 1
        tot = sum(1 for s in S if s['tr'])
        self.info.config(text=f"{n} visibili — {tot}/{len(S)} tradotte")
        for x in sel:
            if self.tree.exists(x): self.tree.selection_set(x)

    def _rows(self, s, text):
        return "\n".join(split_rows(text, s['rowlen'])) if s['rowlen'] else text

    def _select(self, *_):
        sel = self.tree.selection()
        if not sel: return
        self.cur = int(sel[0]); s = self.app.strings[self.cur]
        lay = f"righe da {s['rowlen']} char" if s['rowlen'] else "riga singola"
        pad = "spazi" if (s['pad'] == 'S' or s['fixed']) else "0x00 (termina prima)"
        extra = []
        if s['lead']: extra.append(f"codici iniziali {s['lead'].hex(' ')} preservati")
        if s['tail']: extra.append(f"codici finali {s['tail'].hex(' ')} preservati")
        self.hdr.config(text=f"{fmt_snes(s['snes'])}  PC 0x{s['pc']:06X}  —  {s['group']}  —  max {s['maxlen']} byte, "
                             f"{lay}, riempimento {pad}\n{s['note']}  {'; '.join(extra)}")
        h = max(1, len(split_rows(s['orig'], s['rowlen'])))
        for w in (self.orig, self.edit): w.config(height=max(2, h + 1))
        self.orig.config(state='normal'); self.orig.delete('1.0', 'end')
        self.orig.insert('1.0', self._rows(s, s['orig'])); self.orig.config(state='disabled')
        self.edit.delete('1.0', 'end'); self.edit.insert('1.0', self._rows(s, s['tr']))
        self._check()

    def _value(self):
        s = self.app.strings[self.cur]
        lines = self.edit.get('1.0', 'end-1c').split("\n")
        if s['rowlen']:
            return join_rows([l.rstrip() for l in lines], s['rowlen'])
        return " ".join(lines) if len(lines) > 1 else lines[0]

    def _check(self):
        if self.cur is None: return
        s = self.app.strings[self.cur]; v = self._value()
        if s['rowlen']:
            bad = [k + 1 for k, l in enumerate(self.edit.get('1.0', 'end-1c').split("\n")) if len(l.rstrip()) > s['rowlen']]
        else:
            bad = []
        errs = check_translation(s, v) if v else []
        if bad: errs.append(f"righe troppo lunghe: {bad}")
        self.cnt.config(text=(f"{len(v)}/{s['maxlen']}  " + (" | ".join(errs) if errs else "OK")),
                        fg=RED if errs else GREEN)
        return not errs

    def _save(self):
        if self.cur is None: return
        s = self.app.strings[self.cur]; v = self._value()
        if v and not self._check():
            messagebox.showerror("Non valida", self.cnt.cget('text')); return
        s['tr'] = v.upper() if v == v.upper() else v
        self._fill(); self.app.st(f"Salvata {fmt_snes(s['snes'])}", GREEN)
        # passa alla successiva
        items = self.tree.get_children(); k = str(self.cur)
        if k in items and items.index(k) + 1 < len(items):
            nxt = items[items.index(k) + 1]; self.tree.selection_set(nxt); self.tree.see(nxt)

    def _revert(self):
        if self.cur is None: return
        self.app.strings[self.cur]['tr'] = ""; self._fill(); self._select()

    def _copy(self):
        if self.cur is None: return
        s = self.app.strings[self.cur]
        self.edit.delete('1.0', 'end'); self.edit.insert('1.0', self._rows(s, s['orig'])); self._check()


# ═══════════════════════════════════════════════════════════════════════════
#  TAB: EMULATORE (asp_run su core Snaggletooth — nessuna finestra, solo schermate)
# ═══════════════════════════════════════════════════════════════════════════
class TabEmu(tk.Frame):
    def __init__(self, p, app):
        super().__init__(p, bg=BG); self.app = app
        self.imgs = []; self.pairs = []
        tk.Label(self, text="EMULATORE — test automatico e tracciamento", bg=BG, fg=CYAN, font=FH).pack(pady=(8, 2))
        f = tk.LabelFrame(self, text=" Impostazioni ", bg=PANEL, fg=AMBER, font=FS, bd=1, relief='solid')
        f.pack(fill='x', padx=10, pady=3)
        here = os.path.dirname(os.path.abspath(__file__))
        exe = os.path.join(here, "asp_run.exe" if os.name == "nt" else "asp_run")
        self.v_exe = tk.StringVar(value=exe if os.path.exists(exe) else "")
        self.v_frames = tk.StringVar(value="6000")
        self.v_shots = tk.StringVar(value=",".join(map(str, DEFAULT_SHOTS)))
        rows = [("asp_run:", self.v_exe, self._pick_exe), ("Fotogrammi:", self.v_frames, None),
                ("Schermate ai fotogrammi:", self.v_shots, None)]
        for lbl, v, cmd in rows:
            r = tk.Frame(f, bg=PANEL); r.pack(fill='x', padx=6, pady=2)
            tk.Label(r, text=lbl, bg=PANEL, fg=WHITE, font=FS, width=24, anchor='e').pack(side='left')
            tk.Entry(r, textvariable=v, bg=DARK, fg=CYAN, font=FS, width=70, insertbackground=GREEN).pack(side='left', padx=4)
            if cmd: tk.Button(r, text="...", command=cmd, bg=BORDER, fg=WHITE, font=FS, relief='flat').pack(side='left')
        sc = tk.Frame(f, bg=PANEL); sc.pack(fill='x', padx=6, pady=2)
        tk.Label(sc, text="Scenario (tasti porta 1):", bg=PANEL, fg=WHITE, font=FS, width=24, anchor='ne').pack(side='left', anchor='n')
        self.script = tk.Text(sc, height=5, bg=DARK, fg=GREEN, font=FS, insertbackground=GREEN)
        self.script.pack(side='left', fill='x', expand=True, padx=4)
        self.script.insert('1.0', DEFAULT_SCENARIO)
        sr = tk.Frame(f, bg=PANEL); sr.pack(fill='x', padx=6, pady=2)
        tk.Label(sr, text="Scenari pronti:", bg=PANEL, fg=WHITE, font=FS, width=24, anchor='e').pack(side='left')
        for name, (txt, shots, frames) in SCENARIOS.items():
            tk.Button(sr, text=name, bg=BORDER, fg=WHITE, font=FS, relief='flat',
                      command=lambda t=txt, s=shots, fr=frames: self._use(t, s, fr)).pack(side='left', padx=3)
        b = tk.Frame(self, bg=BG); b.pack(fill='x', padx=10, pady=4)
        tk.Button(b, text="▶ Confronta ORIGINALE vs PATCHATA", command=self._compare, bg=GREEN, fg=BG,
                  font=FS, relief='flat', padx=10, pady=4).pack(side='left')
        tk.Button(b, text="🔎 Tracciamento testo (ROM originale)", command=self._trace, bg=BLUE, fg=WHITE,
                  font=FS, relief='flat', padx=10, pady=4).pack(side='left', padx=6)
        tk.Button(b, text="📂 Importa reads.csv", command=self._import, bg=BORDER, fg=WHITE,
                  font=FS, relief='flat', padx=10, pady=4).pack(side='left')
        self.stat = tk.Label(b, text="", bg=BG, fg=GRAY, font=FS); self.stat.pack(side='left', padx=8)
        nav = tk.Frame(self, bg=BG); nav.pack(fill='x', padx=10)
        tk.Button(nav, text="◀", command=lambda: self._show(self.k - 1), bg=BORDER, fg=WHITE, font=FS, relief='flat').pack(side='left')
        tk.Button(nav, text="▶", command=lambda: self._show(self.k + 1), bg=BORDER, fg=WHITE, font=FS, relief='flat').pack(side='left', padx=4)
        self.lbl = tk.Label(nav, text="", bg=BG, fg=AMBER, font=FS); self.lbl.pack(side='left', padx=8)
        self.canvas = tk.Frame(self, bg=BG); self.canvas.pack(fill='both', expand=True, padx=10, pady=4)
        self.left = tk.Label(self.canvas, bg=BG); self.left.pack(side='left', padx=4)
        self.right = tk.Label(self.canvas, bg=BG); self.right.pack(side='left', padx=4)
        self.k = 0

    def refresh(self): pass

    def _use(self, txt, shots, frames):
        self.script.delete('1.0', 'end'); self.script.insert('1.0', txt)
        self.v_shots.set(",".join(map(str, shots))); self.v_frames.set(str(frames))

    def _pick_exe(self):
        p = filedialog.askopenfilename(title="asp_run", filetypes=[("Eseguibile", "*.exe *"), ("Tutti", "*.*")])
        if p: self.v_exe.set(p)

    def _args(self):
        exe = self.v_exe.get()
        if not exe or not os.path.exists(exe):
            raise RuntimeError("Indica il percorso di asp_run(.exe) — è nella cartella del tool.")
        shots = [int(x) for x in self.v_shots.get().replace(" ", "").split(",") if x]
        return exe, int(self.v_frames.get()), shots, self.script.get('1.0', 'end-1c')

    def _bg(self, work, done):
        def run():
            try: res = work(); self.after(0, lambda: done(res))
            except Exception as e: self.after(0, lambda: (self.stat.config(text=f"✗ {e}", fg=RED),
                                                          messagebox.showerror("Emulatore", str(e))))
        threading.Thread(target=run, daemon=True).start()

    def _compare(self):
        a = self.app
        if not a.rom_data: messagebox.showwarning("Attenzione", "Carica la ROM originale."); return
        if not a.patched_rom: messagebox.showwarning("Attenzione", "Genera prima la ROM nella scheda Build."); return
        try: exe, frames, shots, script = self._args()
        except Exception as e: messagebox.showerror("Emulatore", str(e)); return
        base = tempfile.mkdtemp(prefix="asp_emu_")
        po = os.path.join(base, "orig.sfc"); pp = os.path.join(base, "patch.sfc")
        open(po, 'wb').write(a.rom_data); open(pp, 'wb').write(a.patched_rom)
        self.stat.config(text=f"Emulazione di {frames} fotogrammi x2 in corso...", fg=AMBER)

        def work():
            d1 = os.path.join(base, "orig"); d2 = os.path.join(base, "patch")
            run_emulator(exe, po, d1, frames, script, shots)
            run_emulator(exe, pp, d2, frames, script, shots)
            return [(os.path.join(d1, f), os.path.join(d2, f)) for f in sorted(os.listdir(d2)) if f.startswith("shot_")]

        def done(pairs):
            self.pairs = pairs; self.stat.config(text=f"✓ {len(pairs)} schermate — cartella {base}", fg=GREEN)
            self._show(0)
        self._bg(work, done)

    def _show(self, k):
        if not self.pairs: return
        self.k = max(0, min(k, len(self.pairs) - 1))
        a, b = self.pairs[self.k]
        imgs = []
        for path, w in ((a, self.left), (b, self.right)):
            if os.path.exists(path):
                im = tk.PhotoImage(file=path)
                im = im.zoom(2, 2) if im.width() <= 256 else im.zoom(1, 2)   # 512 px = modo hi-res
                w.config(image=im); imgs.append(im)
            else:
                w.config(image='', text="(manca)")
        self.imgs = imgs
        self.lbl.config(text=f"{os.path.basename(b)}   —   sinistra ORIGINALE, destra PATCHATA   ({self.k + 1}/{len(self.pairs)})")

    def _trace(self):
        a = self.app
        if not a.rom_data: messagebox.showwarning("Attenzione", "Carica la ROM originale."); return
        try: exe, frames, shots, script = self._args()
        except Exception as e: messagebox.showerror("Emulatore", str(e)); return
        base = tempfile.mkdtemp(prefix="asp_trace_"); po = os.path.join(base, "orig.sfc")
        open(po, 'wb').write(a.rom_data)
        self.stat.config(text="Tracciamento in corso (più lento)...", fg=AMBER)

        def work():
            run_emulator(exe, po, base, frames, script, [], watch=["808000-FFFFFF"], timeout=3600)
            return os.path.join(base, "reads.csv")
        self._bg(work, self._imported)

    def _import(self):
        p = filedialog.askopenfilename(title="reads.csv", filetypes=[("CSV", "*.csv")])
        if p: self._imported(p)

    def _imported(self, path):
        found = discover_strings(self.app.rom_data, path)
        hit = mark_runtime(self.app.strings, found)
        known = {s['pc'] for s in self.app.strings}
        new = [pc for pc in found if pc not in known]
        self.stat.config(text=f"✓ tracciamento: {hit} stringhe del catalogo confermate a runtime, "
                              f"{len(new)} letture fuori catalogo — {path}", fg=GREEN)
        self.app.st(f"Tracciamento importato: {hit} stringhe confermate", GREEN)


# ═══════════════════════════════════════════════════════════════════════════
#  TAB: SCRITTE GRAFICHE (font a tile: titolo, menu opzioni, SETA PRESENTS)
# ═══════════════════════════════════════════════════════════════════════════
class TabGfx(tk.Frame):
    def __init__(self, p, app):
        super().__init__(p, bg=BG); self.app = app; self.cur = None
        top = tk.Frame(self, bg=BG); top.pack(fill='x', padx=10, pady=(8, 2))
        tk.Label(top, text="SCRITTE GRAFICHE", bg=BG, fg=CYAN, font=FH).pack(side='left')
        tk.Label(top, text="   font: A-Z 0-9 spazio : - / . , > @(©) ^ v   —   NIENTE apostrofo né accenti   —   "
                           "· = cella grafica, resta com'è", bg=BG, fg=GRAY, font=FS).pack(side='left')
        self.info = tk.Label(top, text="", bg=BG, fg=GRAY, font=FS); self.info.pack(side='right')
        cols = ('id', 'grp', 'orig', 'tr')
        fr = tk.Frame(self, bg=BG); fr.pack(fill='both', expand=True, padx=10, pady=3)
        self.tree = ttk.Treeview(fr, columns=cols, show='headings', height=18)
        for c, t, w in zip(cols, ['Voce', 'Schermata', 'Originale', 'Traduzione'], [110, 260, 380, 380]):
            self.tree.heading(c, text=t); self.tree.column(c, width=w, anchor='w', stretch=(c in ('orig', 'tr')))
        sb = ttk.Scrollbar(fr, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True); sb.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self._select)
        self.tree.tag_configure('done', foreground=GREEN); self.tree.tag_configure('err', foreground=RED)
        self.tree.tag_configure('warn', foreground=AMBER)
        ed = tk.LabelFrame(self, text=" Modifica (lunghezza fissa: le posizioni sullo schermo contano) ",
                           bg=PANEL, fg=AMBER, font=FS, bd=1, relief='solid')
        ed.pack(fill='x', padx=10, pady=(2, 8))
        self.hdr = tk.Label(ed, text="Seleziona una voce", bg=PANEL, fg=CYAN, font=FS, anchor='w')
        self.hdr.pack(fill='x', padx=6, pady=(3, 0))
        self.ruler = tk.Label(ed, text="", bg=PANEL, fg=GRAY, font=FM, anchor='w'); self.ruler.pack(fill='x', padx=6)
        self.orig = tk.Label(ed, text="", bg=DARK, fg=GRAY, font=FM, anchor='w'); self.orig.pack(fill='x', padx=6)
        self.var = tk.StringVar(); self.var.trace_add('write', lambda *_: self._check())
        self.entry = tk.Entry(ed, textvariable=self.var, bg=DARK, fg=WHITE, font=FM, insertbackground=GREEN)
        self.entry.pack(fill='x', padx=6, pady=2)
        self.entry.bind('<Return>', lambda e: self._save())
        br = tk.Frame(ed, bg=PANEL); br.pack(fill='x', padx=6, pady=(0, 4))
        self.cnt = tk.Label(br, text="", bg=PANEL, fg=GREEN, font=FS); self.cnt.pack(side='left')
        tk.Button(br, text="✔ Salva (Invio)", command=self._save, bg=GREEN, fg=BG, font=FS, relief='flat').pack(side='right')
        tk.Button(br, text="↺ Ripristina", command=self._revert, bg=BORDER, fg=WHITE, font=FS, relief='flat').pack(side='right', padx=6)
        tk.Button(br, text="⇔ Centra", command=self._center, bg=BLUE, fg=WHITE, font=FS, relief='flat').pack(side='right')
        tk.Button(br, text="⇪ Copia originale", command=self._copy, bg=BORDER, fg=WHITE, font=FS, relief='flat').pack(side='right', padx=6)
        # grafica disegnata: sempre applicata in build
        art = tk.LabelFrame(self, text=" Grafica disegnata — applicata a OGNI build (pannello HQ: DATE→DATA e "
                                       "TIME→ORA sono fissi) ", bg=PANEL, fg=AMBER, font=FS, bd=1, relief='solid')
        art.pack(fill='x', padx=10, pady=(0, 8))
        r1 = tk.Frame(art, bg=PANEL); r1.pack(fill='x', padx=6, pady=2)
        tk.Label(r1, text="Mesi (sotto DATA, 3 lettere):", bg=PANEL, fg=WHITE, font=FS).pack(side='left')
        self.mvars = []
        for k in range(12):
            v = tk.StringVar(); v.trace_add('write', lambda *_: self._art_check())
            tk.Entry(r1, textvariable=v, width=4, bg=DARK, fg=WHITE, font=FM,
                     insertbackground=GREEN).pack(side='left', padx=1)
            self.mvars.append(v)
        r2 = tk.Frame(art, bg=PANEL); r2.pack(fill='x', padx=6, pady=2)
        tk.Label(r2, text="Carburante in volo  — in alto (FULL, max 16 px):", bg=PANEL, fg=WHITE, font=FS).pack(side='left')
        self.fvars = [tk.StringVar(), tk.StringVar()]
        tk.Entry(r2, textvariable=self.fvars[0], width=8, bg=DARK, fg=WHITE, font=FM).pack(side='left', padx=3)
        tk.Label(r2, text="in basso (EMPTY, max 19 px):", bg=PANEL, fg=WHITE, font=FS).pack(side='left')
        tk.Entry(r2, textvariable=self.fvars[1], width=8, bg=DARK, fg=WHITE, font=FM).pack(side='left', padx=3)
        for v in self.fvars: v.trace_add('write', lambda *_: self._art_check())
        tk.Button(r2, text="↺ Predefiniti", command=self._art_default, bg=BORDER, fg=WHITE, font=FS,
                  relief='flat').pack(side='right')
        r3 = tk.Frame(art, bg=PANEL); r3.pack(fill='x', padx=6, pady=2)
        tk.Label(r3, text="Hangar, sopra F-15:", bg=PANEL, fg=WHITE, font=FS).pack(side='left')
        self.cvars = [tk.StringVar(), tk.StringVar()]
        tk.Entry(r3, textvariable=self.cvars[0], width=24, bg=DARK, fg=WHITE, font=FM).pack(side='left', padx=3)
        tk.Label(r3, text="sopra A-10:", bg=PANEL, fg=WHITE, font=FS).pack(side='left')
        tk.Entry(r3, textvariable=self.cvars[1], width=24, bg=DARK, fg=WHITE, font=FM).pack(side='left', padx=3)
        tk.Label(r3, text=f"(max {ART_CAP_MAX}, lettere: {ART_CAP_LETTERS} spazio)", bg=PANEL, fg=GRAY,
                 font=FS).pack(side='left')
        for v in self.cvars: v.trace_add('write', lambda *_: self._art_check())
        self.artmsg = tk.Label(art, text="", bg=PANEL, fg=GREEN, font=FS, anchor='w')
        self.artmsg.pack(fill='x', padx=6, pady=(0, 3))
        self._art_loading = False
        self._art_load()

    def _art_load(self):
        cfg = self.app.art_cfg
        self._art_loading = True
        for v, m in zip(self.mvars, cfg['mesi']): v.set(m)
        for v, w in zip(self.fvars, cfg['carburante']): v.set(w)
        for v, w in zip(self.cvars, cfg.get('didascalie', ART_CAPTIONS_DEFAULT)): v.set(w)
        self._art_loading = False
        self._art_check()

    def _art_default(self):
        self.app.art_cfg = {'mesi': list(ART_MONTHS_IT), 'carburante': list(ART_FUEL_DEFAULT),
                            'didascalie': list(ART_CAPTIONS_DEFAULT)}
        self._art_load()

    def _art_check(self):
        if getattr(self, '_art_loading', False) or not hasattr(self, 'fvars'): return
        months = [v.get().upper().strip() for v in self.mvars]
        fuel = [v.get().upper().strip() for v in self.fvars]
        caps = [v.get().upper().strip() for v in self.cvars]
        errs = check_months(months)
        for t in caps:
            e = check_caption(t) if t else "vuota"
            if e: errs.append(f"{t or '(didascalia)'}: {e}")
        for w, where in zip(fuel, ("top", "bottom")):
            e = check_fuel_word(w, where) if w else "vuota"
            if e: errs.append(f"{w or '(carburante)'}: {e}")
        if errs:
            self.artmsg.config(text="✗ " + " | ".join(errs), fg=RED)
        else:
            self.app.art_cfg = {'mesi': months, 'carburante': fuel, 'didascalie': caps}
            w = [len(hud_word_rows(x)[0]) for x in fuel]
            self.artmsg.config(text=f"OK — mesi: {' '.join(months)} — carburante: {fuel[0]} ({w[0]} px) / "
                                    f"{fuel[1]} ({w[1]} px) — hangar: {caps[0]} / {caps[1]}", fg=GREEN)

    def refresh(self):
        self._art_load(); self._fill()

    def _fill(self):
        sel = self.tree.selection(); self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(self.app.gfx):
            tag = ()
            if e['tr']:
                tag = ('err',) if check_gfx(e, e['tr']) else (('warn',) if gfx_warnings(e, e['tr']) else ('done',))
            self.tree.insert('', 'end', iid=str(i), tags=tag,
                             values=(e['id'], e['group'], f"[{e['orig']}]", f"[{e['tr']}]" if e['tr'] else ""))
        done = sum(1 for e in self.app.gfx if e['tr'])
        self.info.config(text=f"{done}/{len(self.app.gfx)} tradotte")
        for x in sel:
            if self.tree.exists(x): self.tree.selection_set(x)

    def _select(self, *_):
        sel = self.tree.selection()
        if not sel: return
        self.cur = int(sel[0]); e = self.app.gfx[self.cur]
        self.hdr.config(text=f"{e['group']}  —  {e['note']}  —  esattamente {e['maxlen']} caratteri")
        self.ruler.config(text="".join(str(i % 10) for i in range(e['maxlen'])))
        self.orig.config(text=e['orig'])
        self.var.set(e['tr'] or "")
        self.entry.focus_set()

    def _check(self):
        if self.cur is None: return False
        e = self.app.gfx[self.cur]; v = self.var.get().upper()
        errs = check_gfx(e, v) if v else []
        warn = gfx_warnings(e, v) if (v and not errs) else []
        txt = " | ".join(errs) if errs else ("OK — attenzione: " + "; ".join(warn) if warn else "OK")
        self.cnt.config(text=f"{len(v)}/{e['maxlen']}  " + txt, fg=RED if errs else (AMBER if warn else GREEN))
        return not errs

    def _save(self):
        if self.cur is None: return
        v = self.var.get().upper()
        if v and not self._check():
            messagebox.showerror("Non valida", self.cnt.cget('text')); return
        self.app.gfx[self.cur]['tr'] = v; self._fill()
        items = self.tree.get_children(); k = str(self.cur)
        if k in items and items.index(k) + 1 < len(items):
            nxt = items[items.index(k) + 1]; self.tree.selection_set(nxt); self.tree.see(nxt)

    def _revert(self):
        if self.cur is None: return
        self.app.gfx[self.cur]['tr'] = ""; self._fill(); self._select()

    def _copy(self):
        if self.cur is not None: self.var.set(self.app.gfx[self.cur]['orig'])

    def _center(self):
        if self.cur is None: return
        e = self.app.gfx[self.cur]; v = self.var.get().upper().strip()
        if UNK in v:
            messagebox.showinfo("Centra", "La riga contiene celle grafiche (·): centrala a mano."); return
        self.var.set(v.center(e['maxlen'])[:e['maxlen']])

# ═══════════════════════════════════════════════════════════════════════════
#  TAB: BUILD
# ═══════════════════════════════════════════════════════════════════════════
class TabBuild(tk.Frame):
    def __init__(self,p,app):
        super().__init__(p,bg=BG); self.app=app
        tk.Label(self,text="BUILD — ROM patchata + patch IPS",bg=BG,fg=CYAN,font=FH).pack(pady=(10,4))

        # File selectors
        f=tk.LabelFrame(self,text=" File ",bg=PANEL,fg=AMBER,font=FS,bd=1,relief='solid')
        f.pack(fill='x',padx=12,pady=3)
        self.path_vars={}
        rows=[("rom_in","ROM originale (.sfc):",self.app.load_rom,"Carica..."),
              ("bin_in","BIN decompresso (opz.):",self.app.load_bin,"Carica..."),
              ("rom_out","ROM patchata (output):",self._pick_rom_out,"Scegli..."),
              ("ips_out","Patch IPS (output):",self._pick_ips_out,"Scegli...")]
        for k,lbl,cmd,btn in rows:
            r=tk.Frame(f,bg=PANEL); r.pack(fill='x',padx=6,pady=2)
            tk.Label(r,text=lbl,bg=PANEL,fg=WHITE,font=FS,width=24,anchor='e').pack(side='left')
            v=tk.StringVar(); self.path_vars[k]=v
            tk.Entry(r,textvariable=v,bg=DARK,fg=CYAN,font=FS,width=58,
                      insertbackground=GREEN).pack(side='left',padx=4)
            tk.Button(r,text=btn,command=cmd,bg=BORDER,fg=WHITE,font=FS,
                       relief='flat',width=9).pack(side='left')

        # Opzioni
        f2=tk.LabelFrame(self,text=" Opzioni ",bg=PANEL,fg=AMBER,font=FS,bd=1,relief='solid')
        f2.pack(fill='x',padx=12,pady=3)
        tk.Label(f2,text="Iniezione sempre SENZA padding; header SMC rilevato in automatico; checksum ricalcolato.",
                  bg=PANEL,fg=GRAY,font=FS).pack(anchor='w',padx=6)
        self.opt_exp=tk.StringVar(value="auto")
        er=tk.Frame(f2,bg=PANEL); er.pack(anchor='w',padx=6)
        tk.Label(er,text="Blocco compresso:",bg=PANEL,fg=WHITE,font=FS).pack(side='left')
        for val,t in (("auto","in place se entra, altrimenti ROM 2 MB ($A0:8000)"),
                      ("always","sempre ROM 2 MB"),("never","mai espandere (errore se non entra)")):
            tk.Radiobutton(er,text=t,variable=self.opt_exp,value=val,bg=PANEL,fg=WHITE,selectcolor=DARK,
                           activebackground=PANEL,font=FS).pack(side='left',padx=4)
        self.opt_reloc=tk.BooleanVar(value=True)
        tk.Checkbutton(f2,text="Applica relocazioni puntatori dalla scheda 'Puntatori'",
                        variable=self.opt_reloc,bg=PANEL,fg=WHITE,selectcolor=DARK,
                        activebackground=PANEL,font=FS).pack(anchor='w',padx=6)
        self.opt_art=tk.BooleanVar(value=True)
        tk.Checkbutton(f2,text="Applica la grafica disegnata tradotta (DATA/ORA, mesi, PIENO/VUOTO — scheda Grafica)",
                        variable=self.opt_art,bg=PANEL,fg=WHITE,selectcolor=DARK,
                        activebackground=PANEL,font=FS).pack(anchor='w',padx=6)

        # Azioni
        f3=tk.Frame(self,bg=BG); f3.pack(fill='x',padx=12,pady=6)
        tk.Button(f3,text="🔨 COSTRUISCI TUTTO (ROM + IPS)",command=self._build_all,
                   bg=GREEN,fg=BG,font=FH,relief='flat',padx=14,pady=6).pack(side='left')
        tk.Button(f3,text="💾 Salva solo BIN tradotto",command=self._save_bin,
                   bg=BLUE,fg=WHITE,font=FS,relief='flat',padx=10,pady=6).pack(side='left',padx=8)

        # Log
        f4=tk.LabelFrame(self,text=" Log ",bg=PANEL,fg=AMBER,font=FS,bd=1,relief='solid')
        f4.pack(fill='both',expand=True,padx=12,pady=3)
        self.log=scrolledtext.ScrolledText(f4,height=12,bg="#0D1117",fg=GREEN,
                                            font=FS,state='disabled')
        self.log.pack(fill='both',expand=True,padx=4,pady=4)

    def _pick_rom_out(self):
        p=filedialog.asksaveasfilename(title="ROM patchata di output",
            defaultextension=".sfc",initialfile="ASP_ITA.sfc",
            filetypes=[("SNES ROM","*.sfc")])
        if p: self.path_vars['rom_out'].set(p)
    def _pick_ips_out(self):
        p=filedialog.asksaveasfilename(title="Patch IPS di output",
            defaultextension=".ips",initialfile="ASP_ITA.ips",
            filetypes=[("IPS","*.ips")])
        if p: self.path_vars['ips_out'].set(p)

    def lg(self,m):
        self.log.config(state='normal'); self.log.insert('end',m+'\n')
        self.log.see('end'); self.log.config(state='disabled'); self.update_idletasks()

    def _save_bin(self):
        if not self.app.bin_data:
            messagebox.showwarning("Attenzione","Carica prima ROM o BIN."); return
        p=filedialog.asksaveasfilename(defaultextension=".bin",
            initialfile="ASP_decomp_ITA.bin",filetypes=[("Binary","*.bin")])
        if not p: return
        b=self.app.build_bin()
        open(p,'wb').write(b)
        self.lg(f"[BIN] Salvato: {p} ({len(b):,} byte)")

    def _build_all(self):
        a=self.app
        if not a.rom_data:
            messagebox.showwarning("Attenzione","Carica la ROM originale."); return
        self.lg("="*64)
        try:
            # 1. BIN tradotto
            new_bin=a.build_bin()
            self.lg(f"[1/5] BIN tradotto: {len(new_bin):,} byte")
            if len(new_bin)!=len(a.bin_data):
                self.lg(f"      ⚠ dimensione cambiata! era {len(a.bin_data):,}")

            # 2-5. Compressione, verifica, iniezione/espansione, stringhe, puntatori
            rel=a.relocations if self.opt_reloc.get() else []
            patched,info=build_rom(a.rom_data,new_bin,a.strings,rel,
                                    expand=self.opt_exp.get(),log=self.lg,gfx=a.gfx,
                                    art=self.opt_art.get(),art_cfg=a.art_cfg)
            comp=info['comp']; pct=100*comp/LZ_SLOT
            len_comp=comp
            a.patched_rom=patched

            # Salvataggi
            rp=self.path_vars['rom_out'].get()
            if not rp:
                rp=os.path.splitext(a.rom_path)[0]+"_ITA.sfc"
                self.path_vars['rom_out'].set(rp)
            open(rp,'wb').write(patched)
            self.lg(f"[OUT] ROM patchata: {rp}")

            ip=self.path_vars['ips_out'].get()
            if not ip:
                ip=os.path.splitext(rp)[0]+".ips"
                self.path_vars['ips_out'].set(ip)
            ips=make_ips(a.rom_data,patched)
            open(ip,'wb').write(ips)
            self.lg(f"[OUT] Patch IPS:   {ip} ({len(ips):,} byte)")
            self.lg("✓ COMPLETATO")
            messagebox.showinfo("Completato",
                f"ROM patchata e patch IPS create!\n\n{rp}\n{ip}\n\n"
                f"Blocco compresso: {comp:,} byte ({pct:.1f}% dello slot originale)\n"
                f"ROM: {info['size']//1024} KB, blocco a {fmt_snes(info['lz_at'])}\n\n"
                "Provala subito nella scheda Emulatore.")
        except Exception as e:
            self.lg(f"✗ ERRORE: {e}")
            messagebox.showerror("Errore",str(e))

    def refresh(self):
        if self.app.rom_path and not self.path_vars['rom_in'].get():
            self.path_vars['rom_in'].set(self.app.rom_path)
        if self.app.bin_path and not self.path_vars['bin_in'].get():
            self.path_vars['bin_in'].set(self.app.bin_path)

# ═══════════════════════════════════════════════════════════════════════════
#  APP
# ═══════════════════════════════════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("A.S.P. — Italian Translation Tool v7")
        self.geometry("1180x820"); self.minsize(960,640); self.configure(bg=BG)
        self.rom_data=None; self.rom_path=None
        self.bin_data=None; self.bin_path=None; self.bin_orig=None
        self.patched_rom=None
        self.relocations=[]
        self.strings=[]
        self.gfx=[]
        self.art_cfg={'mesi':list(ART_MONTHS_IT),'carburante':list(ART_FUEL_DEFAULT),
                      'didascalie':list(ART_CAPTIONS_DEFAULT)}
        self.data={'titles':[],'screens':[],'finali':[],
                    'tr_titles':[],'tr_screens':{},'tr_finali':[]}
        st=ttk.Style(self); st.theme_use('default')
        st.configure('TNotebook',background=BG,borderwidth=0)
        st.configure('TNotebook.Tab',background=PANEL,foreground=GRAY,padding=[10,4],font=FS)
        st.map('TNotebook.Tab',background=[('selected',BG)],foreground=[('selected',CYAN)])
        st.configure('Treeview',background=DARK,foreground=WHITE,fieldbackground=DARK,font=FS)
        st.configure('Treeview.Heading',background=PANEL,foreground=AMBER,font=FS)
        st.map('Treeview',background=[('selected',BLUE)])
        self._header(); self._nb(); self._status()

    def _header(self):
        h=tk.Frame(self,bg=PANEL,height=36); h.pack(fill='x'); h.pack_propagate(False)
        tk.Label(h,text="◉ A.S.P. TRANSLATION TOOL v7",bg=PANEL,fg=CYAN,font=FH).pack(side='left',padx=12)
        tk.Button(h,text="📂 ROM",command=self.load_rom,bg=BORDER,fg=WHITE,font=FS,
                   relief='flat',padx=8).pack(side='right',padx=4,pady=5)
        tk.Button(h,text="📂 BIN",command=self.load_bin,bg=BORDER,fg=WHITE,font=FS,
                   relief='flat',padx=8).pack(side='right',padx=4,pady=5)
        tk.Button(h,text="💾 Salva progetto",command=self.save_project,bg=GREEN,fg=BG,font=FS,
                   relief='flat',padx=8).pack(side='right',padx=4,pady=5)
        tk.Button(h,text="📁 Apri progetto",command=self.open_project,bg=BLUE,fg=WHITE,font=FS,
                   relief='flat',padx=8).pack(side='right',padx=4,pady=5)
        tk.Button(h,text="📥 Importa traduzione",command=self.import_translation,bg=AMBER,fg=BG,font=FS,
                   relief='flat',padx=8).pack(side='right',padx=4,pady=5)

    def _nb(self):
        self.nb=ttk.Notebook(self); self.nb.pack(fill='both',expand=True)
        self.tabs=[]
        for cls,label in [(TabOverview," Panoramica "),(TabTitles," Titoli "),
                           (TabScreens," Schermate "),(TabFinali," Finali "),
                           (TabStrings," Stringhe "),(TabGfx," Grafica "),(TabPointers," Puntatori "),
                           (TabBuild," Build "),(TabEmu," Emulatore ")]:
            t=cls(self.nb,self); self.nb.add(t,text=label); self.tabs.append(t)
        self.nb.bind('<<NotebookTabChanged>>',self._tabchg)

    def _tabchg(self,*_):
        cur=self.nb.select()
        for t in self.tabs:
            if str(t)==cur:
                try: t.refresh()
                except Exception as e: print("refresh err:",e)

    def _status(self):
        s=tk.Frame(self,bg=BORDER,height=20); s.pack(fill='x',side='bottom'); s.pack_propagate(False)
        self.sl=tk.Label(s,text="Pronto — carica la ROM originale per iniziare.",
                          bg=BORDER,fg=GRAY,font=FS); self.sl.pack(side='left',padx=8)

    def st(self,m,c=GRAY): self.sl.config(text=m,fg=c)

    # ── I/O ───────────────────────────────────────────────────────────────────
    def load_rom(self):
        p=filedialog.askopenfilename(title="ROM originale",
            filetypes=[("SNES ROM","*.sfc *.smc"),("Tutti","*.*")])
        if not p: return
        self.rom_data=open(p,'rb').read(); self.rom_path=p
        try: self.strings=load_strings(self.rom_data)
        except Exception as e: self.strings=[]; print("stringhe:",e)
        try: self.gfx=load_gfx_texts(self.rom_data)
        except Exception as e: self.gfx=[]; print("grafica:",e)
        try:
            off=LZ_OFFSET+(0x200 if len(self.rom_data)%0x8000==0x200 else 0)
            self.bin_data=lz_decomp(self.rom_data,off)
            self.bin_orig=self.bin_data
            self.bin_path=p+" (decompresso)"
            self._parse()
            self.st(f"ROM + decompressione OK — {len(self.bin_data):,} byte",GREEN)
        except Exception as e:
            self.st(f"ROM caricata, decompressione fallita: {e}",AMBER)
        self._reset_tabs()

    def load_bin(self):
        p=filedialog.askopenfilename(title="File decompresso",
            filetypes=[("Binary","*.bin"),("Tutti","*.*")])
        if not p: return
        self.bin_data=open(p,'rb').read(); self.bin_path=p
        self._parse(); self._reset_tabs()
        self.st(f"BIN caricato — {len(self.bin_data):,} byte",GREEN)

    def _reset_tabs(self):
        for t in self.tabs:
            if hasattr(t,'built'): t.built=False
        self._tabchg()

    def _parse(self):
        d=self.bin_data
        titles=parse_titles(d)
        first_screen = titles[-1]['off']+titles[-1]['len']+2 if titles else 0x01BF
        screens=parse_missions(d,first_screen)
        finali=find_finali(d)
        self.data={
            'titles':titles,'screens':screens,'finali':finali,
            'tr_titles':[(tk.StringVar(),tk.StringVar()) for _ in titles],
            'tr_screens':{},'tr_finali':['' for _ in finali],
        }

    # ── BUILD ─────────────────────────────────────────────────────────────────
    def build_bin(self):
        """Ricostruisce il file decompresso applicando tutte le traduzioni."""
        out=bytearray(self.bin_data)
        D=self.data

        # Titoli: applicati alla fine (possono allungarsi, vedi apply_titles)
        title_texts=[]
        for t,(v1,v2) in zip(D['titles'],D['tr_titles']):
            s1=v1.get().strip(); s2=v2.get().strip()
            title_texts.append((s1,s2) if (s1 or s2) else None)

        # Schermate
        for idx,s in enumerate(D['screens']):
            it=D['tr_screens'].get(idx,'')
            if not it.strip(): continue
            flat=''.join(l.ljust(ROW_MISSION)[:ROW_MISSION] for l in it.splitlines())
            b=flat.encode('ascii','replace')[:s['len']].ljust(s['len'],b' ')
            out[s['off']:s['off']+s['len']]=b

        # Finali (centrati su 48)
        for f,it in zip(D['finali'],D['tr_finali']):
            if not it.strip(): continue
            nrows=f['len']//ROW_FINALE
            lines=[l for l in it.splitlines()]
            # se l'utente non ha centrato, centriamo noi
            flat=''.join(l.ljust(ROW_FINALE)[:ROW_FINALE] for l in lines)
            b=flat.encode('ascii','replace')[:f['len']].ljust(f['len'],b' ')
            out[f['off']:f['off']+f['len']]=b

        return apply_titles(bytes(out),D['titles'],title_texts)

    # ── IMPORT TRADUZIONE ─────────────────────────────────────────────────────
    def import_translation(self):
        if not self.bin_data:
            messagebox.showwarning("Attenzione","Carica prima la ROM originale."); return
        p=filedialog.askopenfilename(title="File di traduzione (testo continuo)",
            filetypes=[("Testo","*.txt"),("Tutti","*.*")])
        if not p: return
        base=self.bin_orig or self.bin_data
        corr=None
        for cand in (os.path.join(os.path.dirname(p),"correzioni.json"),
                      os.path.join(os.path.dirname(os.path.abspath(__file__)),"correzioni.json")):
            if os.path.exists(cand):
                try: corr=json.load(open(cand,encoding='utf-8')).get('sostituzioni'); break
                except Exception: pass
        try:
            testo=open(p,encoding='utf-8',errors='replace').read()
            nuovo,rep,info=importa_traduzione(base,testo,corr)
        except Exception as e:
            messagebox.showerror("Import",f"Non riesco a importare:\n{e}"); return
        self.bin_orig=base; self.bin_data=nuovo; self.bin_path=p+" (importato)"
        self._parse(); self._reset_tabs()
        conf=os.path.splitext(p)[0]+"_confronto.txt"
        try: scrivi_confronto(conf,base,rep)
        except Exception: conf=None
        self.st(f"Traduzione importata: {info['record']} record, {info['avvisi']} avvisi",GREEN)
        messagebox.showinfo("Traduzione importata",
            f"Record allineati: {info['record']}\nAncore usate: {info['ancore']}\n"
            f"Record con avvisi: {info['avvisi']}\n"
            + (f"Refusi corretti: {sum(info['correzioni'].values())} "
               f"({len(info['correzioni'])} regole da correzioni.json)\n\n" if info.get('correzioni') else "\n")
            + (f"Confronto originale/traduzione:\n{conf}\n\n" if conf else "")
            + "Controlla le schede Titoli / Schermate / Finali, poi vai su Build.")

    # ── PROGETTO ──────────────────────────────────────────────────────────────
    def project_dict(self):
        D=self.data
        return {"tool":"ASP v7","rom_sha_hint":len(self.rom_data or b""),
                "blocco":(self.bin_data or b"").hex(),
                "arte":self.art_cfg,
                "titles":[[a.get(),b.get()] for a,b in D['tr_titles']],
                "screens":{str(k):v for k,v in D['tr_screens'].items() if v.strip()},
                "finali":D['tr_finali'],
                "strings":{s['id']:s['tr'] for s in self.strings if s['tr']},
                "gfx":{e['id']:e['tr'] for e in self.gfx if e['tr']},
                "relocations":[{k:(v.hex() if isinstance(v,bytes) else v) for k,v in r.items()}
                               for r in self.relocations]}

    def save_project(self):
        if not self.bin_data:
            messagebox.showwarning("Attenzione","Carica prima la ROM."); return
        p=filedialog.asksaveasfilename(title="Salva progetto",defaultextension=".json",
            initialfile="ASP_ITA_progetto.json",filetypes=[("Progetto","*.json")])
        if not p: return
        json.dump(self.project_dict(),open(p,'w',encoding='utf-8'),indent=1,ensure_ascii=False)
        self.st(f"Progetto salvato: {p}",GREEN)

    def load_project_dict(self,j):
        if j.get('blocco'):
            # il blocco decompresso tradotto (import + correzioni) e' salvato nel progetto
            self.bin_data=bytes.fromhex(j['blocco']); self.bin_path="(dal progetto)"
            self._parse()
        if j.get('arte'):
            self.art_cfg={'mesi':list(j['arte'].get('mesi',ART_MONTHS_IT)),
                          'carburante':list(j['arte'].get('carburante',ART_FUEL_DEFAULT)),
                          'didascalie':list(j['arte'].get('didascalie',ART_CAPTIONS_DEFAULT))}
        D=self.data
        for (a,b),(x,y) in zip(D['tr_titles'],j.get('titles',[])): a.set(x); b.set(y)
        D['tr_screens']={int(k):v for k,v in j.get('screens',{}).items()}
        fin=j.get('finali',[])
        D['tr_finali']=(fin+['']*len(D['finali']))[:len(D['finali'])]
        tr=j.get('strings',{})
        for s in self.strings: s['tr']=tr.get(s['id'],'')
        g=j.get('gfx',{})
        for e in self.gfx: e['tr']=g.get(e['id'],'')
        self.relocations=[{k:(bytes.fromhex(v) if k=='data' else v) for k,v in r.items()}
                          for r in j.get('relocations',[])]

    def open_project(self):
        if not self.bin_data:
            messagebox.showwarning("Attenzione","Carica prima la ROM originale, poi il progetto."); return
        p=filedialog.askopenfilename(title="Apri progetto",filetypes=[("Progetto","*.json")])
        if not p: return
        self.load_project_dict(json.load(open(p,encoding='utf-8')))
        self._reset_tabs(); self.st(f"Progetto caricato: {p}",GREEN)

    def make_ips(self,orig,patched):
        buf=bytearray(b'PATCH'); i=0; n=min(len(orig),len(patched))
        while i<n:
            if orig[i]==patched[i]: i+=1; continue
            j=i
            while j<n and orig[j]!=patched[j] and (j-i)<0xFFFF: j+=1
            buf+=i.to_bytes(3,'big')+(j-i).to_bytes(2,'big')+patched[i:j]
            i=j
        buf+=b'EOF'
        return bytes(buf)

if __name__=='__main__':
    App().mainloop()
