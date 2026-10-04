#!/usr/bin/env python3
"""asp_auto — pipeline automatica della recompilazione di A.S.P. (traduzione italiana).

Un solo strumento, multipiattaforma (Windows, Linux, macOS), per tutto il ciclo:

    rom       ROM USA + patch IPS -> rom/ASP_ITA.sfc, aggiorna rom_identity.txt
    build     configura e compila la recomp (e, con --emulator, asp_run)
    regen     rigenera src/gen dal ROM, con i profili di copertura accettati
    ref       esegue uno scenario sull'emulatore di riferimento (asp_run)
    run       esegue uno scenario sulla recomp (headless, senza limite di velocita')
    check     run + confronto con la baseline interpretata e con il riferimento
    promote   cattura copertura -> promuove ad AOT -> valida -> bisezione automatica
              delle funzioni che rompono il gioco -> le esclude in recomp/symbols.toml
    cycle     rom + regen + build + promote + check: tutto in fila

Gli scenari sono i file di examples/ (formato asp_run: "frame N tasti").
Ogni risultato finisce in runs/ (ignorata da git); le esclusioni AOT e i profili
accettati finiscono nella repo (recomp/symbols.toml, coverage/accepted/).

Requisiti: Python 3.9+, numpy, Pillow (pip install numpy pillow), CMake, Ninja
o MSVC, Rust (per l'analizzatore di snesrecomp), la ROM USA originale.
ASP_RUN_EXE=<percorso> usa un asp_run gia' compilato.

Esempi:
    python tools/asp_auto.py rom --orig "C:/roms/A.S.P. - Air Strike Patrol (USA).sfc" --update-identity
    python tools/asp_auto.py build --emulator
    python tools/asp_auto.py check examples/scenario_missione1.txt
    python tools/asp_auto.py promote examples/scenario_missione1.txt examples/scenario_volo.txt
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXE_SUFFIX = ".exe" if os.name == "nt" else ""
RECOMP_EXE_NAME = "ASPAirStrikePatrolItaSNESRecomp"
ROM_ITA = ROOT / "rom" / "ASP_ITA.sfc"
IPS_DEFAULT = ROOT / "translation" / "patches" / "ASP_ITA.ips"
RUNS = ROOT / "runs"
COVERAGE_ACCEPTED = ROOT / "coverage" / "accepted"
SYMBOLS = ROOT / "recomp" / "symbols.toml"
CLI = ROOT / "snesrecomp" / "snesrecomp_cli.py"
PY = sys.executable

# Righe del log della recomp che significano "il gioco e' uscito dai binari".
FATAL_LOG_PATTERNS = [
    r"\[brk\] architectural BRK",
    r"watchdog",
    r"\bDie\(",
    r"interpreter cap",
    r"step.cap",
    r"timed out after",
    r"fatal",
    r"Segmentation",
]


# ─────────────────────────────── utilita' ────────────────────────────────

def log(msg: str) -> None:
    print(f"[asp_auto {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"[asp_auto] ERRORE: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd, *, cwd=None, env=None, timeout=None, logfile=None, check=True) -> int:
    """Esegue un comando; con logfile salva stdout+stderr li' invece che a video."""
    cmd = [str(c) for c in cmd]
    full_env = dict(os.environ)
    if env:
        full_env.update({k: str(v) for k, v in env.items()})
    if logfile:
        pathlib.Path(logfile).parent.mkdir(parents=True, exist_ok=True)
        with open(logfile, "w", encoding="utf-8", errors="replace") as f:
            try:
                p = subprocess.run(cmd, cwd=cwd, env=full_env, stdout=f,
                                   stderr=subprocess.STDOUT, timeout=timeout)
                rc = p.returncode
            except subprocess.TimeoutExpired:
                f.write(f"\n[asp_auto] TIMEOUT dopo {timeout}s\n")
                rc = 124
    else:
        try:
            rc = subprocess.run(cmd, cwd=cwd, env=full_env, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            rc = 124
    if check and rc != 0:
        die(f"comando fallito ({rc}): {' '.join(cmd)}" + (f"  — log: {logfile}" if logfile else ""))
    return rc


def digests(data: bytes) -> dict:
    return {
        "crc32": f"{zlib.crc32(data) & 0xFFFFFFFF:08x}",
        "md5": hashlib.md5(data).hexdigest(),
        "sha1": hashlib.sha1(data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def recomp_exe(build_dir: pathlib.Path) -> pathlib.Path:
    for cand in (build_dir / (RECOMP_EXE_NAME + EXE_SUFFIX),
                 build_dir / "Release" / (RECOMP_EXE_NAME + EXE_SUFFIX)):
        if cand.exists():
            return cand
    die(f"eseguibile della recomp non trovato in {build_dir} — esegui prima: asp_auto.py build")
    return build_dir


def asp_run_exe() -> pathlib.Path:
    env = os.environ.get("ASP_RUN_EXE")
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env)
    for cand in (ROOT / "build-asp_run" / ("asp_run" + EXE_SUFFIX),
                 ROOT / "build-asp_run" / "Release" / ("asp_run" + EXE_SUFFIX)):
        if cand.exists():
            return cand
    die("asp_run non trovato — esegui: asp_auto.py build --emulator")
    return ROOT


# ───────────────────────────────── rom ───────────────────────────────────

def apply_ips(rom: bytearray, ips: bytes) -> bytearray:
    sys.path.insert(0, str(ROOT / "tools"))
    from apply_ips import apply_ips as _apply  # noqa: E402
    return _apply(rom, ips)


def update_identity(data: bytes) -> None:
    path = ROOT / "rom_identity.txt"
    text = path.read_text(encoding="utf-8")
    d = digests(data)
    for key, val in (("expected_crc32", d["crc32"]), ("expected_md5", d["md5"]),
                     ("expected_sha1", d["sha1"]), ("expected_sha256", d["sha256"])):
        text = re.sub(rf"^({key}\s*=\s*).*$", rf"\g<1>{val}", text, flags=re.M)
    text = re.sub(r"^(rom_size\s*=\s*).*$", rf"\g<1>{len(data)}", text, flags=re.M)
    path.write_text(text, encoding="utf-8")
    log(f"rom_identity.txt aggiornato: crc32 {d['crc32']} sha256 {d['sha256'][:16]}…")


def cmd_rom(a) -> None:
    orig = pathlib.Path(a.orig)
    if not orig.exists():
        die(f"ROM originale non trovata: {orig}")
    data = bytearray(orig.read_bytes())
    if len(data) % 0x8000 == 0x200:
        data = data[0x200:]
    od = digests(bytes(data))
    if od["crc32"] != "05c0da54":
        log(f"ATTENZIONE: la ROM originale ha CRC32 {od['crc32']}, attesa 05c0da54 (USA)")
    out = apply_ips(data, pathlib.Path(a.ips).read_bytes())
    ROM_ITA.parent.mkdir(parents=True, exist_ok=True)
    ROM_ITA.write_bytes(out)
    d = digests(bytes(out))
    log(f"scritto {ROM_ITA.relative_to(ROOT)}  CRC32 {d['crc32']}")
    if a.update_identity:
        update_identity(bytes(out))
    else:
        ident = (ROOT / "rom_identity.txt").read_text(encoding="utf-8")
        if d["crc32"] not in ident:
            log("la ROM non corrisponde a rom_identity.txt: rilancia con --update-identity "
                "(la traduzione e' cambiata) e poi rigenera")


# ──────────────────────────────── build ──────────────────────────────────

def cmd_build(a) -> None:
    build_dir = ROOT / a.build_dir
    if not (build_dir / "CMakeCache.txt").exists():
        gen = ["-G", "Ninja"] if shutil.which("ninja") else []
        run(["cmake", "-S", ROOT, "-B", build_dir, *gen, f"-DCMAKE_BUILD_TYPE={a.config}"],
            logfile=RUNS / "logs" / "cmake_configure.log")
    log("compilo la recomp…")
    run(["cmake", "--build", build_dir, "--config", a.config, "-j", str(os.cpu_count() or 4)],
        logfile=RUNS / "logs" / "cmake_build.log")
    log(f"ok: {recomp_exe(build_dir)}")
    if a.emulator:
        eb = ROOT / "build-asp_run"
        if not (eb / "CMakeCache.txt").exists():
            gen = ["-G", "Ninja"] if shutil.which("ninja") else []
            run(["cmake", "-S", ROOT / "emulator", "-B", eb, *gen, "-DCMAKE_BUILD_TYPE=Release"],
                logfile=RUNS / "logs" / "asp_run_configure.log")
        run(["cmake", "--build", eb, "--config", "Release", "-j", str(os.cpu_count() or 4)],
            logfile=RUNS / "logs" / "asp_run_build.log")
        log(f"ok: {asp_run_exe()}")


# ──────────────────────────────── regen ──────────────────────────────────

def accepted_profiles() -> list[pathlib.Path]:
    return sorted(COVERAGE_ACCEPTED.glob("*.json")) + sorted(COVERAGE_ACCEPTED.glob("*.jsonl"))


def identity_value(key: str) -> str:
    text = (ROOT / "rom_identity.txt").read_text(encoding="utf-8")
    m = re.search(rf"^{key}\s*=\s*(\S+)", text, flags=re.M)
    return m.group(1).strip('"') if m else ""


def regen(extra_profiles=(), use_accepted=True, logfile=None) -> None:
    """Rigenera src/gen con v2_emit.

    I profili accettati (catture di build precedenti) entrano come
    --historical-profile-manifest: v2_emit rifiuta di fondere come profilo
    corrente catture di build diverse, ma ne conserva i semi come storici.
    Le catture nuove (stessa build) entrano come --profile-manifest."""
    if not ROM_ITA.exists():
        die("rom/ASP_ITA.sfc mancante — esegui: asp_auto.py rom --orig <ROM USA>")
    d = digests(ROM_ITA.read_bytes())
    if d["crc32"] != identity_value("expected_crc32") or d["sha256"] != identity_value("expected_sha256"):
        die("rom/ASP_ITA.sfc non corrisponde a rom_identity.txt — se la traduzione e' cambiata: "
            "asp_auto.py rom --orig <ROM USA> --update-identity")
    logfile = logfile or (RUNS / "logs" / "regen.log")
    tools = ROOT / "snesrecomp" / "tools"
    cmd = [PY, tools / "v2_emit.py", "--rom", ROM_ITA, "--cfg-dir", ROOT / "recomp",
           "--out-dir", ROOT / "src" / "gen", "--analysis-backend", "auto"]
    hist = [p for p in accepted_profiles() if p.suffix == ".json"] if use_accepted else []
    for p in hist:
        cmd += ["--historical-profile-manifest", p]
    for p in extra_profiles:
        cmd += ["--profile-manifest", pathlib.Path(p).resolve()]
    log(f"rigenero src/gen ({len(hist)} profili storici, {len(list(extra_profiles))} nuovi)…")
    run(cmd, cwd=ROOT / "snesrecomp", logfile=logfile)
    run([PY, tools / "v2_sync_funcs_h.py", "--cfg-dir", ROOT / "recomp", "--out", ROOT / "recomp" / "funcs.h"],
        cwd=ROOT / "snesrecomp", logfile=pathlib.Path(str(logfile) + ".funcs_h"))


def cmd_regen(a) -> None:
    regen(extra_profiles=a.profile or [], use_accepted=not a.no_profiles)
    log("fatto. Ricompila con: asp_auto.py build")


# ────────────────────────────── scenari ──────────────────────────────────

def parse_scenario(path: pathlib.Path) -> list[tuple[int, str | None]]:
    """Formato asp_run: 'frame N [1|2] tasti...' ; 'none' rilascia. Commenti con ';' o '#'."""
    events = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = re.split(r"[;#]", raw, maxsplit=1)[0].strip()
        if not line or not line.startswith("frame"):
            continue
        parts = line.split()
        frame = int(parts[1])
        btn = [b for b in parts[2:] if b not in ("1",)]
        port2 = "2" in btn
        btn = [b for b in btn if b != "2"]
        if not btn or btn == ["none"]:
            events.append((frame, None))
        else:
            name = "+".join(("p2:" + b) if port2 else b for b in btn)
            events.append((frame, name))
    return sorted(events, key=lambda e: e[0])


def scenario_length(events) -> int:
    return (events[-1][0] if events else 0) + 600


def to_recomp_script(events, total: int, dump_every: int) -> str:
    """Script per la recomp: wait/press con 'dump' ogni dump_every frame.

    'press X N' tiene premuto per N frame piu' un frame vuoto; 'dump' non
    consuma frame. Le pressioni tengono la loro durata, i dump cadono nelle
    attese (spostati dopo la pressione se ci finirebbero dentro)."""
    holds = []
    for i, (f, b) in enumerate(events):
        if b is None:
            continue
        end = events[i + 1][0] if i + 1 < len(events) else f + 1
        holds.append((f, b, max(1, end - f)))
    out, t = [], 0
    dumps = list(range(0, total + 1, dump_every)) if dump_every else []
    di = 0

    def wait_until(target):
        nonlocal t, di
        while di < len(dumps) and dumps[di] <= target:
            d = max(dumps[di], t)
            if d > t:
                out.append(f"wait {d - t}")
                t = d
            out.append(f"dump k{dumps[di]:06d}")
            di += 1
        if target > t:
            out.append(f"wait {target - t}")
            t = target

    for f, b, n in holds:
        if f < t:
            f = t
        wait_until(f)
        out.append(f"press {b} {n}")
        t = f + n + 1
    wait_until(total)
    out.append("quit")
    return "\n".join(out) + "\n"


# ───────────────────────────── esecuzioni ────────────────────────────────

def run_dir(scenario: pathlib.Path, label: str) -> pathlib.Path:
    d = RUNS / scenario.stem / label
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def run_reference(scenario: pathlib.Path, every: int, frames: int | None = None) -> pathlib.Path:
    events = parse_scenario(scenario)
    frames = frames or scenario_length(events)
    out = RUNS / scenario.stem / "ref"
    stamp = out / "done.json"
    key = {"scenario": hashlib.sha256(scenario.read_bytes()).hexdigest(), "frames": frames,
           "every": every, "rom": digests(ROM_ITA.read_bytes())["sha256"]}
    if stamp.exists() and json.loads(stamp.read_text()) == key:
        log(f"riferimento gia' pronto: {out.relative_to(ROOT)}")
        return out
    out = run_dir(scenario, "ref")
    log(f"riferimento asp_run: {scenario.name} ({frames} frame)…")
    t0 = time.time()
    run([asp_run_exe(), ROM_ITA, out, "--frames", frames, "--input", scenario,
         "--every", every, "--irqlog"], logfile=out / "asp_run.log", timeout=7200)
    stamp.write_text(json.dumps(key))
    log(f"riferimento pronto in {time.time() - t0:.0f}s")
    return out


def run_recomp(scenario: pathlib.Path, label: str, every: int, *, coverage=False,
               frames: int | None = None, build_dir="build", timeout=3600) -> dict:
    events = parse_scenario(scenario)
    frames = frames or scenario_length(events)
    out = run_dir(scenario, label)
    script = out / "input.script"
    script.write_text(to_recomp_script(events, frames, every), encoding="utf-8")
    cfg = out / "config.ini"
    cfg.write_text("[General]\nDisableFrameDelay = 1\nSkipLauncher = 1\n", encoding="utf-8")
    dumps = out / "dumps"
    dumps.mkdir()
    env = {"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
           "SNESRECOMP_DUMP_DIR": dumps}
    cov = {}
    if coverage:
        cov = {"json": out / "coverage.json", "jsonl": out / "coverage.jsonl"}
        env.update({"SNESRECOMP_TIER2_CAPTURE": "1",
                    "SNESRECOMP_TIER2_MANIFEST": cov["json"],
                    "SNESRECOMP_TIER2_JOURNAL": cov["jsonl"]})
    exe = recomp_exe(ROOT / build_dir)
    log(f"recomp [{label}]: {scenario.name} ({frames} frame)…")
    t0 = time.time()
    rc = run([exe, "--config", cfg, "--no-launcher", "--script", script, ROM_ITA],
             cwd=exe.parent, env=env, logfile=out / "recomp.log", timeout=timeout, check=False)
    elapsed = time.time() - t0
    # Tiene solo schermo e WRAM: il resto dei dump occupa spazio e non serve al confronto.
    for f in dumps.iterdir():
        if not (f.name.endswith(".fb.bmp") or f.name.endswith(".wram.bin") or f.name.endswith(".info.json")):
            f.unlink()
    logtext = (out / "recomp.log").read_text(encoding="utf-8", errors="replace")
    fatal = [ln for ln in logtext.splitlines()
             if any(re.search(p, ln, re.I) for p in FATAL_LOG_PATTERNS)]
    m = re.search(r"script quit after (\d+) frames", logtext)
    result = {"label": label, "scenario": scenario.name, "exit_code": rc,
              "frames_run": int(m.group(1)) if m else None, "frames_expected": frames,
              "seconds": round(elapsed, 1), "fatal_log_lines": fatal[:20], "dir": str(out)}
    if coverage and cov["json"].exists():
        result["coverage"] = {k: str(v) for k, v in cov.items()}
        result["interpreted"] = coverage_summary([cov["json"], cov["jsonl"]])
    (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    ok = rc == 0 and not fatal and result["frames_run"] == frames
    log(f"recomp [{label}] {'ok' if ok else 'PROBLEMI'} in {elapsed:.0f}s"
        + (f" — interpretate {result['interpreted']['instructions']:,} istruzioni"
           if result.get("interpreted") else "")
        + ("" if not fatal else f" — {fatal[0][:90]}"))
    return result


def coverage_summary(paths) -> dict:
    cmd = [PY, ROOT / "snesrecomp" / "tools" / "tier2_ingest.py", *paths, "--cfg-dir",
           ROOT / "recomp", "--json"]
    pm = ROOT / "src" / "gen" / "program_manifest.json"
    if pm.exists():
        cmd += ["--program-manifest", pm]
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=600)
        d = json.loads(p.stdout)
        return {"instructions": d.get("interpreted_instructions", 0),
                "guest_cycles": d.get("interpreted_guest_cycles", 0),
                "discoveries": len(d.get("discoveries", [])),
                "warnings": d.get("warnings", [])[:5]}
    except Exception as e:  # noqa: BLE001
        return {"instructions": 0, "guest_cycles": 0, "error": str(e)}


# ───────────────────────────── confronto ─────────────────────────────────

def _load_small(path: pathlib.Path):
    import numpy as np
    from PIL import Image
    im = Image.open(path).convert("RGB").resize((64, 56))
    return np.asarray(im, dtype=np.int16)


def keyframes_ref(d: pathlib.Path) -> dict[int, pathlib.Path]:
    out = {}
    for p in d.glob("shot_*.png"):
        out[int(p.stem.split("_")[1])] = p
    return out


def keyframes_rc(d: pathlib.Path) -> dict[int, pathlib.Path]:
    """Dump della recomp: frame reale letto da info.json (il tag e' il frame voluto)."""
    out = {}
    for info in (d / "dumps").glob("*.info.json"):
        tag = info.name[:-len(".info.json")]
        bmp = info.with_name(tag + ".fb.bmp")
        if not bmp.exists():
            continue
        try:
            frame = int(json.loads(info.read_text()).get("frame"))
        except Exception:  # noqa: BLE001
            frame = int(tag[1:])
        out[frame] = bmp
    return out


def align(a: dict, b: dict, window: int, threshold: float) -> dict:
    """Per ogni fotogramma chiave di A cerca il piu' simile in B entro +-window frame,
    procedendo in avanti (le due esecuzioni vanno nello stesso ordine, a velocita'
    leggermente diverse). Restituisce differenze, scarti e fotogrammi divergenti."""
    import numpy as np
    bf = sorted(b)
    cache = {}

    def B(f):
        if f not in cache:
            cache[f] = _load_small(b[f])
        return cache[f]

    rows, last = [], -10**9
    for f in sorted(a):
        img = _load_small(a[f])
        cands = [g for g in bf if f - window <= g <= f + window and g >= last - window // 4]
        if not cands:
            continue
        g = min(cands, key=lambda g: float(np.abs(img - B(g)).mean()))
        diff = float(np.abs(img - B(g)).mean())
        bright = float(img.mean())
        rows.append({"a": f, "b": g, "offset": g - f, "diff": round(diff, 2),
                     "a_mean": round(bright, 1), "bad": diff > threshold})
        if diff <= threshold:
            last = g
    bad = [r for r in rows if r["bad"]]
    return {"rows": rows, "keyframes": len(rows), "divergent": len(bad),
            "divergent_pct": round(100.0 * len(bad) / max(1, len(rows)), 1),
            "median_offset": (sorted(r["offset"] for r in rows)[len(rows) // 2] if rows else 0)}


def write_html_report(path: pathlib.Path, title: str, sections: list[dict]) -> None:
    """Pagina HTML locale con le schermate divergenti affiancate."""
    from PIL import Image
    img_dir = path.parent / "img"
    img_dir.mkdir(exist_ok=True)
    parts = [f"<!doctype html><meta charset='utf-8'><title>{title}</title>",
             "<style>body{font:14px system-ui;margin:24px;background:#111;color:#ddd}"
             "table{border-collapse:collapse}td,th{padding:4px 8px;border-bottom:1px solid #333}"
             ".bad{color:#f66}.ok{color:#6c6}img{image-rendering:pixelated;width:256px}</style>",
             f"<h1>{title}</h1>"]
    for s in sections:
        st = s["stats"]
        cls = "bad" if st["divergent"] else "ok"
        parts.append(f"<h2>{s['name']}</h2><p class='{cls}'>{st['divergent']} fotogrammi divergenti "
                     f"su {st['keyframes']} ({st['divergent_pct']}%), scarto mediano {st['median_offset']:+d} frame</p>")
        bad = [r for r in st["rows"] if r["bad"]][:12]
        if bad:
            parts.append("<table><tr><th>A</th><th>B</th><th>diff</th><th>immagini</th></tr>")
            for r in bad:
                pa, pb = s["a"][r["a"]], s["b"][r["b"]]
                na, nb = f"{s['key']}_{r['a']}_a.png", f"{s['key']}_{r['b']}_b.png"
                Image.open(pa).convert("RGB").resize((256, 224)).save(img_dir / na)
                Image.open(pb).convert("RGB").resize((256, 224)).save(img_dir / nb)
                parts.append(f"<tr><td>{r['a']}</td><td>{r['b']}</td><td>{r['diff']}</td>"
                             f"<td><img src='img/{na}'> <img src='img/{nb}'></td></tr>")
            parts.append("</table>")
    path.write_text("\n".join(parts), encoding="utf-8")


# ─────────────────────────────── check ───────────────────────────────────

# Variabili di stato del gioco la cui SEQUENZA di valori deve coincidere con
# la baseline. Sono robuste ai piccoli scarti di temporizzazione (che con
# input a frame fissi cambiano solo in quale istante di un menu cade un
# tasto), ma cambiano se il gioco si rompe: es. la build con un BRK nel volo
# passa 0 -> 2 e non arriva mai a 1 (volo).
SEMANTIC_WRAM = {"modalita' di gioco ($0200)": 0x0200}

# Oltre questa quota di schermate divergenti il candidato e' rotto anche se
# la sequenza di stato coincide (es. schermo nero in forced blank: $0200=2
# come la baseline, ma 88% di schermate diverse).
CATASTROPHIC_PCT = 40.0


def wram_sequences(run_dir: pathlib.Path) -> dict[str, list[int]]:
    import numpy as np
    vals: dict[str, list[int]] = {k: [] for k in SEMANTIC_WRAM}
    items = []
    for info in (run_dir / "dumps").glob("*.info.json"):
        try:
            f = int(json.loads(info.read_text()).get("frame"))
        except Exception:  # noqa: BLE001
            continue
        items.append((f, run_dir / "dumps" / (info.name[:-len(".info.json")] + ".wram.bin")))
    for _, w in sorted(items):
        if not w.exists():
            continue
        ram = np.fromfile(w, np.uint8)
        for k, addr in SEMANTIC_WRAM.items():
            v = int(ram[addr])
            if not vals[k] or vals[k][-1] != v:
                vals[k].append(v)
    return vals


def validate_against(base: dict, cand: dict, window=180, threshold=6.0) -> dict:
    """Valida un'esecuzione candidata contro la baseline interpretata.

    Bloccanti: uscita anomala, righe fatali nel log, frame mancanti, sequenza
    delle variabili di stato diversa, schermate divergenti oltre il
    CATASTROPHIC_PCT. Le divergenze minori sono riportate ma non bloccano:
    con input a frame fissi un minimo scarto di tempo sposta un tasto di uno
    stato del menu e il percorso diverge pur essendo il codice corretto."""
    a = keyframes_rc(pathlib.Path(base["dir"]))
    b = keyframes_rc(pathlib.Path(cand["dir"]))
    st = align(a, b, window, threshold)
    problems, notes = [], []
    if cand["exit_code"] != 0:
        problems.append(f"uscita {cand['exit_code']}")
    if cand["fatal_log_lines"]:
        problems.append("log: " + cand["fatal_log_lines"][0][:100])
    if cand["frames_run"] != cand["frames_expected"]:
        problems.append(f"frame eseguiti {cand['frames_run']} su {cand['frames_expected']}")
    sa, sb = wram_sequences(pathlib.Path(base["dir"])), wram_sequences(pathlib.Path(cand["dir"]))
    for k in SEMANTIC_WRAM:
        if sa.get(k) != sb.get(k):
            problems.append(f"{k}: {sb.get(k)} invece di {sa.get(k)}")
    if st["divergent_pct"] > CATASTROPHIC_PCT:
        problems.append(f"{st['divergent']} schermate divergenti ({st['divergent_pct']}%)")
    elif st["divergent"]:
        notes.append(f"{st['divergent']} schermate divergenti ({st['divergent_pct']}%), non bloccante")
    return {"ok": not problems, "problems": problems, "notes": notes, "stats": st, "a": a, "b": b}


def save_divergence_sheet(v: dict, path: pathlib.Path, n: int = 6) -> None:
    """Prime n coppie divergenti: sopra la baseline, sotto il candidato."""
    from PIL import Image
    bad = [r for r in v["stats"]["rows"] if r["bad"]][:n]
    if not bad:
        return
    sheet = Image.new("RGB", (256 * len(bad), 224 * 2 + 4), (60, 0, 0))
    for i, r in enumerate(bad):
        sheet.paste(Image.open(v["a"][r["a"]]).convert("RGB").resize((256, 224)), (256 * i, 0))
        sheet.paste(Image.open(v["b"][r["b"]]).convert("RGB").resize((256, 224)), (256 * i, 228))
    sheet.save(path)


def cmd_run(a) -> None:
    for sc in a.scenario:
        run_recomp(pathlib.Path(sc), a.label, a.every, coverage=a.coverage)


def cmd_ref(a) -> None:
    for sc in a.scenario:
        run_reference(pathlib.Path(sc), a.every)


def cmd_check(a) -> None:
    """Esecuzione corrente vs riferimento hardware (fedelta') e vs baseline interpretata
    se esiste (correttezza dell'AOT)."""
    failures = 0
    sections = []
    for sc in a.scenario:
        sc = pathlib.Path(sc)
        cur = run_recomp(sc, "current", a.every)
        if a.reference:
            refd = run_reference(sc, a.every)
            st = align(keyframes_ref(refd), keyframes_rc(pathlib.Path(cur["dir"])), 240, 6.0)
            log(f"{sc.name}: vs riferimento {st['divergent']}/{st['keyframes']} divergenti, "
                f"scarto mediano {st['median_offset']:+d}")
            sections.append({"name": f"{sc.name}: riferimento (A) vs recomp (B)", "key": sc.stem + "_ref",
                             "stats": st, "a": keyframes_ref(refd),
                             "b": keyframes_rc(pathlib.Path(cur["dir"]))})
        base = RUNS / sc.stem / "baseline_lle" / "result.json"
        if base.exists():
            v = validate_against(json.loads(base.read_text()), cur)
            log(f"{sc.name}: vs baseline interpretata -> {'OK' if v['ok'] else 'FALLITO: ' + '; '.join(v['problems'])}")
            failures += not v["ok"]
            sections.append({"name": f"{sc.name}: baseline interpretata (A) vs corrente (B)",
                             "key": sc.stem + "_lle", "stats": v["stats"], "a": v["a"], "b": v["b"]})
        elif cur["fatal_log_lines"] or cur["exit_code"]:
            failures += 1
    rep = RUNS / "report" / "check.html"
    rep.parent.mkdir(parents=True, exist_ok=True)
    write_html_report(rep, "A.S.P. recomp — check", sections)
    log(f"report: {rep}")
    sys.exit(1 if failures else 0)


# ───────────────────────── promozione + bisezione ─────────────────────────

SYM_BEGIN = "# >>> asp_auto: esclusioni AOT (generato, non modificare a mano)"
SYM_END = "# <<< asp_auto"


def read_exclusions() -> dict[int, str]:
    text = SYMBOLS.read_text(encoding="utf-8")
    m = re.search(re.escape(SYM_BEGIN) + r"(.*?)" + re.escape(SYM_END), text, flags=re.S)
    out = {}
    if m:
        for blk in re.finditer(r'addr = "([0-9a-fA-F]{4})"\s*\nbank = (0x[0-9a-fA-F]+|\d+)\s*\nemit = false\s*\nnote = "([^"]*)"', m.group(1)):
            out[(int(blk.group(2), 0) << 16) | int(blk.group(1), 16)] = blk.group(3)
    return out


def write_exclusions(excl: dict[int, str]) -> None:
    text = SYMBOLS.read_text(encoding="utf-8")
    text = re.sub(r"\n?" + re.escape(SYM_BEGIN) + r".*?" + re.escape(SYM_END) + r"\n?", "\n",
                  text, flags=re.S).rstrip() + "\n"
    if excl:
        lines = ["", SYM_BEGIN,
                 "# Funzioni che, compilate AOT, hanno rotto la validazione automatica:",
                 "# restano interpretate. Rimuovile da qui per riprovarle dopo un",
                 "# aggiornamento di snesrecomp."]
        for pc24 in sorted(excl):
            bank, addr = pc24 >> 16, pc24 & 0xFFFF
            lines += ["", "[[func]]", f'name = "asp_auto_lle_{bank:02X}{addr:04X}"',
                      f'addr = "{addr:04x}"', f"bank = 0x{bank:02x}", "emit = false",
                      f'note = "{excl[pc24]}"']
        lines += [SYM_END, ""]
        text += "\n".join(lines)
    SYMBOLS.write_text(text, encoding="utf-8")


def aot_nodes() -> set[int]:
    """Indirizzi (pc24) delle funzioni compilate AOT nell'ultima generazione."""
    pm = json.loads((ROOT / "src" / "gen" / "program_manifest.json").read_text())
    out = set()
    for v in pm["nodes"].values():
        if v.get("disposition") != "lle_only":
            out.add(v["key"]["pc24"] & 0xFFFFFF)
    return out


# Schemi riconosciuti nella ROM che il codice AOT non puo' eseguire: la
# funzione che li contiene viene esclusa subito, senza bisezione.
#
# "Chiamata via RTL": la libreria C del gioco ("DESERT SWORD SYSTEM (C) 1992
# OPUS CORP.") chiama codice calcolato a runtime spingendo un indirizzo di
# ritorno COSTANTE che punta a un RTL della funzione stessa, poi spinge il
# bersaglio e fa RTL. Lo usa per:
#   - le copie di memoria: scrive MVN/MVP + RTL in direct page con i banchi
#     decisi a runtime e ci "ritorna" ($80:91EE, $80:9235, $80:927C, ...);
#   - le chiamate tramite puntatore a funzione ($80:9386).
# L'interprete lo segue; un corpo AOT no (BRK a meta' istruzione o percorso
# diverso). Firme: LDA #bank / PHA / REP #$20 / LDA #ret / PHA, oppure
# PHK / PEA ret, con un RTL all'indirizzo ret dello stesso banco.
WAIT_NOTE = "attesa attiva su WRAM: lo scheduler IRQ deve poterla interrompere a ogni istruzione"
RTL_CALL_NOTE = "chiamata via RTL a codice calcolato (libreria C: copie MVN/MVP in RAM, puntatori a funzione)"


def _lorom_off(bank: int, addr: int) -> int:
    return ((bank & 0x7F) << 15) | (addr & 0x7FFF)


def lorom_pc24(offset: int) -> int:
    return ((0x80 | (offset >> 15)) << 16) | (0x8000 | (offset & 0x7FFF))


def pattern_sites() -> list[tuple[int, str]]:
    rom = ROM_ITA.read_bytes()
    out = []
    lda_pha = re.compile(rb"\xA9([\x00-\x3F\x80-\xBF])\x48\xC2\x20\xA9(..)\x48", re.S)
    for m in lda_pha.finditer(rom):
        bank, ret = m.group(1)[0], int.from_bytes(m.group(2), "little")
        o = _lorom_off(bank, ret)
        if ret >= 0x8000 and o < len(rom) and rom[o] == 0x6B and (o >> 15) == (m.start() >> 15):
            out.append((lorom_pc24(m.start()), RTL_CALL_NOTE))
    # Attese attive su WRAM bassa (LDA abs / CMP abs / BEQ -5, LDA/CMP/BEQ+3/JMP,
    # LDA abs / Bxx -5). Dentro un task girano con I=0 e lo scheduler IRQ deve
    # poterle interrompere a ogni istruzione: il loro corpo AOT non cede il
    # turno ($80:91DF, attesa del tick di logica $027A, trovata con la
    # bisezione sul volo). Con I=1 l'interprete le salta comunque (game_rtl.c).
    # Le attese dell'NMI su $0202 (LDA/CMP/BEQ -5) girano con I=1 nel ciclo
    # principale: compilate restano corrette (validato su missione 1 e volo) e
    # le funzioni che le contengono sono grandi, quindi non si escludono.
    waits = [rb"\xAD(..)\xCD\1[\xF0\xD0]\x03\x4C",
             rb"\xAD(..)[\xF0\xD0\x10\x30]\xFB"]
    for rx in waits:
        for m in re.finditer(rx, rom, flags=re.S):
            if int.from_bytes(m.group(1), "little") < 0x2000:
                out.append((lorom_pc24(m.start()), WAIT_NOTE))
    phk_pea = re.compile(rb"\x4B\xF4(..)", re.S)
    for m in phk_pea.finditer(rom):
        ret = int.from_bytes(m.group(1), "little")
        o = (m.start() & ~0x7FFF) | (ret & 0x7FFF)
        if ret >= 0x8000 and o < len(rom) and rom[o] == 0x6B:
            out.append((lorom_pc24(m.start()), RTL_CALL_NOTE))
    return out


def nodes_covering(sites: list[tuple[int, str]]) -> dict[int, str]:
    """Funzioni AOT dell'ultima generazione che contengono uno dei siti."""
    pm = json.loads((ROOT / "src" / "gen" / "program_manifest.json").read_text())
    out = {}
    for v in pm["nodes"].values():
        if v.get("disposition") == "lle_only":
            continue
        lo, hi = v.get("min_pc24", 0) & 0x7FFFFF, v.get("max_pc24", 0) & 0x7FFFFF
        for pc, note in sites:
            if lo <= (pc & 0x7FFFFF) <= hi:
                out[v["key"]["pc24"] & 0xFFFFFF] = note
    return out


def apply_pattern_exclusions(excl: dict[int, str], profiles) -> dict[int, str]:
    """Genera con i profili, esclude le funzioni che contengono schemi noti,
    ripete finche' nessuna nuova funzione ne contiene (escluderne una puo'
    farne nascere un'altra che copre lo stesso sito)."""
    sites = pattern_sites()
    # Ricalcolo da capo: le esclusioni nate da uno schema che non c'e' piu'
    # (o e' stato ristretto) vengono tolte; quelle della bisezione restano.
    excl = {pc: n for pc, n in excl.items() if WAIT_NOTE not in n and RTL_CALL_NOTE not in n}
    if not sites:
        write_exclusions(excl)
        return excl
    for i in range(6):
        write_exclusions(excl)
        regen(extra_profiles=profiles, logfile=RUNS / "logs" / f"regen_patterns{i}.log")
        hit = {pc: n for pc, n in nodes_covering(sites).items() if pc not in excl}
        if not hit:
            break
        log(f"   schemi noti: escludo {len(hit)} funzioni "
            + ", ".join(f"${p >> 16:02X}:{p & 0xFFFF:04X}" for p in sorted(hit)))
        stamp = time.strftime("%Y-%m-%d")
        for pc, note in hit.items():
            excl[pc] = f"asp_auto {stamp}: {note}"
    return excl


def build_quiet(tag: str) -> bool:
    rc = run(["cmake", "--build", ROOT / "build", "--config", "Release", "-j", str(os.cpu_count() or 4)],
             logfile=RUNS / "logs" / f"build_{tag}.log", check=False)
    return rc == 0


def candidate_ok(scenarios, baselines, profiles, excl, tag, every) -> tuple[bool, list]:
    write_exclusions(excl)
    regen(extra_profiles=profiles, logfile=RUNS / "logs" / f"regen_{tag}.log")
    if not build_quiet(tag):
        return False, ["compilazione fallita"]
    problems = []
    for sc in scenarios:
        res = run_recomp(sc, f"cand_{tag}", every)
        v = validate_against(baselines[sc], res)
        (pathlib.Path(res["dir"]) / "validation.json").write_text(
            json.dumps({k: v[k] for k in ("ok", "problems", "notes")} | {"stats": {
                k: v["stats"][k] for k in ("keyframes", "divergent", "divergent_pct", "median_offset")}},
                indent=2), encoding="utf-8")
        if not v["ok"]:
            save_divergence_sheet(v, pathlib.Path(res["dir"]) / "divergenze.png")
        shutil.rmtree(pathlib.Path(res["dir"]) / "dumps", ignore_errors=True)
        if not v["ok"]:
            problems += [f"{sc.name}: {p}" for p in v["problems"]]
            log(f"   -> scartato: {problems[-1]}")
            break
    return not problems, problems


def mirror_key(pc24: int) -> int:
    """LoROM: $00-$3F e $80-$BF sono la stessa ROM. Il framework compila una
    funzione separatamente per ogni banco da cui la raggiunge, quindi una
    colpevole va esclusa in entrambi."""
    bank = (pc24 >> 16) & 0xFF
    return pc24 & 0x7FFFFF if bank < 0x40 or 0x80 <= bank < 0xC0 else pc24


def find_culprits(scenarios, baselines, profiles, excl, suspects, every, max_rounds, tagp):
    """Bisezione: le funzioni fra `suspects` che, compilate AOT, fanno fallire
    la validazione. Le copie mirror di una funzione formano un'unica unita'.
    Invariante per ogni colpevole: proven_ok da solo passa, proven_ok + lo
    fallisce; ogni prova abilita proven_ok + meta' di lo ed esclude il resto.
    Ripete finche' la validazione passa."""
    groups: dict[int, set[int]] = {}
    for pc in suspects:
        groups.setdefault(mirror_key(pc), set()).add(pc)
    units = sorted(groups)
    culprits: list[int] = []
    rounds = 0
    good, problems = False, []

    def trial_with(disabled_units, culprit_units):
        t = dict(excl)
        for u in disabled_units:
            for pc in groups[u]:
                t[pc] = "bisezione"
        for u in culprit_units:
            for pc in groups[u]:
                t[pc] = "colpevole"
        return t

    found: list[int] = []
    while units and rounds < max_rounds:
        proven_ok: set[int] = set()
        lo = units
        while len(lo) > 1 and rounds < max_rounds:
            rounds += 1
            half, rest = lo[: len(lo) // 2], lo[len(lo) // 2:]
            enabled = proven_ok | set(half)
            disabled = [u for u in units if u not in enabled]
            log(f"   giro {rounds}: abilito {len(enabled)} funzioni, ne escludo {len(disabled)}")
            ok_half, _ = candidate_ok(scenarios, baselines, profiles, trial_with(disabled, found),
                                      f"{tagp}{rounds}", every)
            if ok_half:
                proven_ok |= set(half)
                lo = rest
            else:
                lo = half
        found.append(lo[0])
        culprits += sorted(groups[lo[0]])
        log("   colpevole: " + ", ".join(f"${pc >> 16:02X}:{pc & 0xFFFF:04X}" for pc in sorted(groups[lo[0]])))
        rounds += 1
        good, problems = candidate_ok(scenarios, baselines, profiles, trial_with([], found),
                                      f"{tagp}v{rounds}", every)
        if good:
            break
        units = [u for u in units if u not in found]
    return culprits, good, problems


def mark_culprits(excl, culprits, why):
    stamp = time.strftime("%Y-%m-%d")
    for pc in culprits:
        excl[pc] = f"asp_auto {stamp}: {why}"
        bank = (pc >> 16) & 0xFF
        if bank < 0x40 or 0x80 <= bank < 0xC0:      # anche il mirror, se compilato in futuro
            excl.setdefault(pc ^ 0x800000, f"asp_auto {stamp}: {why} (mirror)")
    return excl


def cmd_promote(a) -> None:
    scenarios = [pathlib.Path(s) for s in a.scenario]
    every = a.every
    t_start = time.time()
    excl = read_exclusions()
    culprits: list[int] = []

    # 1) baseline interpretata: nessun profilo, solo le radici del progetto
    if not a.reuse_baseline or not all((RUNS / s.stem / "baseline_lle" / "result.json").exists() for s in scenarios):
        log("== 1/5 baseline interpretata (riferimento di correttezza)")
        write_exclusions(excl)
        regen(use_accepted=False, logfile=RUNS / "logs" / "regen_baseline.log")
        if not build_quiet("baseline"):
            die("la baseline non compila")
        for sc in scenarios:
            r = run_recomp(sc, "baseline_lle", every)
            if r["fatal_log_lines"] or r["exit_code"]:
                die(f"la baseline interpretata fallisce gia' su {sc.name}: {r['fatal_log_lines'][:1]} — "
                    "problema del frame driver, non della promozione")
    baselines = {sc: json.loads((RUNS / sc.stem / "baseline_lle" / "result.json").read_text()) for sc in scenarios}

    # 2) cattura copertura con il codice attualmente accettato. Lo stato
    # accettato deve passare la validazione su TUTTI gli scenari: uno nuovo
    # (es. il volo) puo' esercitare funzioni gia' promosse in percorsi che i
    # precedenti non toccavano. In quel caso si bisezionano le funzioni gia'
    # compilate e le colpevoli vengono escluse prima di andare avanti.
    log("== 2/5 cattura copertura")
    excl = apply_pattern_exclusions(excl, [])
    for attempt in range(4):
        if not build_quiet(f"current{attempt}"):
            die("la build corrente non compila")
        failing = []
        new_profiles, interp_before = [], 0
        for sc in scenarios:
            r = run_recomp(sc, "capture", every, coverage=True)
            v = validate_against(baselines[sc], r)
            if not v["ok"]:
                save_divergence_sheet(v, pathlib.Path(r["dir"]) / "divergenze.png")
                log(f"   lo stato accettato fallisce su {sc.name}: {'; '.join(v['problems'])}")
                failing.append(sc)
            elif "coverage" in r:
                new_profiles += [pathlib.Path(r["coverage"]["json"]), pathlib.Path(r["coverage"]["jsonl"])]
                interp_before += r["interpreted"]["instructions"]
        if not failing:
            break
        log(f"   bisezione sulle {len(aot_nodes())} funzioni gia' accettate (scenari: "
            f"{', '.join(s.name for s in failing)})")
        c, good, probs = find_culprits(failing, baselines, [], excl, aot_nodes(), every, a.max_rounds, f"a{attempt}_")
        culprits += c
        excl = mark_culprits(excl, c, "rompe uno scenario se compilata AOT (stato accettato)")
        excl = apply_pattern_exclusions(excl, [])
        if not good and not c:
            die(f"stato accettato non riparabile: {probs}")
    else:
        die("lo stato accettato non si ripara in 4 tentativi")
    before = aot_nodes()

    # 3) promozione con i nuovi profili (prima escludendo gli schemi noti)
    log("== 3/5 promozione AOT con i nuovi profili")
    excl = apply_pattern_exclusions(excl, new_profiles)
    ok, problems = candidate_ok(scenarios, baselines, new_profiles, excl, "full", every)
    added = aot_nodes() - before
    log(f"nuove funzioni AOT: {len(added)}")

    # 4) bisezione sulle funzioni appena promosse
    if not ok:
        log(f"== 4/5 validazione fallita ({'; '.join(problems)}): bisezione su {len(added)} funzioni")
        c, _, _ = find_culprits(scenarios, baselines, new_profiles, excl, added, every, a.max_rounds, "b")
        culprits += c
        excl = mark_culprits(excl, c, "rompe la validazione se compilata AOT")
        ok, problems = candidate_ok(scenarios, baselines, new_profiles, excl, "final", every)
    else:
        log("== 4/5 validazione superata al primo colpo")

    # 5) accetta i profili e misura il guadagno
    log("== 5/5 risultato")
    report = {"ok": ok, "problems": problems, "culprits": [f"{c:06X}" for c in culprits],
              "aot_functions": len(aot_nodes()), "interpreted_before": interp_before,
              "minutes": round((time.time() - t_start) / 60, 1)}
    if ok:
        COVERAGE_ACCEPTED.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for p in new_profiles:
            shutil.copy2(p, COVERAGE_ACCEPTED / f"{stamp}_{p.parent.parent.name}{p.suffix}")
        after = 0
        for sc in scenarios:
            r = run_recomp(sc, "after", every, coverage=True)
            after += r.get("interpreted", {}).get("instructions", 0)
        report["interpreted_after"] = after
        if interp_before:
            report["interpreted_reduction_pct"] = round(100.0 * (1 - after / interp_before), 1)
        pct = report.get("interpreted_reduction_pct")
        log(f"OK: {report['aot_functions']} funzioni AOT, istruzioni interpretate "
            f"{interp_before:,} -> {after:,}" + (f" ({-pct:+.1f}%)" if pct is not None else ""))
    else:
        log(f"FALLITO: {problems} — le esclusioni trovate restano in recomp/symbols.toml")
    (RUNS / "report").mkdir(parents=True, exist_ok=True)
    (RUNS / "report" / "promote.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    sys.exit(0 if ok else 1)


# ──────────────────────────────── cycle ──────────────────────────────────

def cmd_cycle(a) -> None:
    if a.orig:
        cmd_rom(argparse.Namespace(orig=a.orig, ips=str(IPS_DEFAULT), update_identity=True))
    cmd_build(argparse.Namespace(build_dir="build", config="Release", emulator=True))
    cmd_promote(argparse.Namespace(scenario=a.scenario, every=a.every, reuse_baseline=False,
                                   max_rounds=a.max_rounds))


# ──────────────────────────────── main ───────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rom", help="crea rom/ASP_ITA.sfc dalla ROM USA + patch")
    p.add_argument("--orig", required=True)
    p.add_argument("--ips", default=str(IPS_DEFAULT))
    p.add_argument("--update-identity", action="store_true")
    p.set_defaults(fn=cmd_rom)

    p = sub.add_parser("build", help="compila la recomp (e asp_run con --emulator)")
    p.add_argument("--build-dir", default="build")
    p.add_argument("--config", default="Release")
    p.add_argument("--emulator", action="store_true")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("regen", help="rigenera src/gen (con i profili accettati)")
    p.add_argument("--profile", action="append")
    p.add_argument("--no-profiles", action="store_true")
    p.set_defaults(fn=cmd_regen)

    for name, fn, hlp in (("ref", cmd_ref, "esegue scenari sul riferimento asp_run"),
                          ("run", cmd_run, "esegue scenari sulla recomp")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("scenario", nargs="+")
        p.add_argument("--every", type=int, default=30)
        if name == "run":
            p.add_argument("--label", default="manual")
            p.add_argument("--coverage", action="store_true")
        p.set_defaults(fn=fn)

    p = sub.add_parser("check", help="esegue e confronta con baseline e riferimento")
    p.add_argument("scenario", nargs="+")
    p.add_argument("--every", type=int, default=30)
    p.add_argument("--reference", action="store_true", help="confronta anche con asp_run")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("promote", help="copertura -> AOT -> validazione -> bisezione")
    p.add_argument("scenario", nargs="+")
    p.add_argument("--every", type=int, default=30)
    p.add_argument("--reuse-baseline", action="store_true")
    p.add_argument("--max-rounds", type=int, default=40)
    p.set_defaults(fn=cmd_promote)

    p = sub.add_parser("cycle", help="rom + build + promote, tutto in fila")
    p.add_argument("scenario", nargs="+")
    p.add_argument("--orig", help="ROM USA (se serve ricreare quella italiana)")
    p.add_argument("--every", type=int, default=30)
    p.add_argument("--max-rounds", type=int, default=40)
    p.set_defaults(fn=cmd_cycle)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
