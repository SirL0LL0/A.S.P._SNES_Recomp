# Titoli di coda: intestazioni tradotte, nomi invariati (righe di 48 caratteri)
HEAD = {
    "AIR STRIKE PATROL STAFF": "STAFF DI AIR STRIKE PATROL",
    "PRODUCER": "PRODUTTORE",
    "GAME DESIGN": "IDEAZIONE DEL GIOCO",
    "DIRECTOR": "REGIA",
    "PROGRAMMING / SHOOTING": "PROGRAMMAZIONE / COMBATTIMENTO",
    "SIMULATION": "SIMULAZIONE",
    "SOUND AND MUSIC": "SUONO E MUSICA",
    "GAME PRODUCER": "PRODUTTORE DEL GIOCO",
    "ART DIRECTOR": "DIREZIONE ARTISTICA",
    "ART DESIGN": "GRAFICA",
    "3D MODELS": "MODELLI 3D",
    "MASY/CASY SYSTEM": "SISTEMA MASY/CASY",
    "ENGINEERING SUPPORT": "SUPPORTO TECNICO",
    "PRODUCT MANAGER": "RESPONSABILE PRODOTTO",
    "PRODUCTION ASSISTANT": "ASSISTENTE DI PRODUZIONE",
    "QUALITY ASSURANCE": "CONTROLLO QUALITA'",
    "ADVERTISEMENT": "PUBBLICITA'",
    "ADVERTISE CG MODELING": "MODELLI CG PUBBLICITA'",
    "PRODUCT TESTING": "COLLAUDO",
    "SPECIAL THANKS TO": "RINGRAZIAMENTI",
    "PRESENTED BY SETA": "PRESENTATO DA SETA",
}

def translate_credits(txt, W=48):
    rows = [txt[i:i + W] for i in range(0, len(txt), W)]
    out = []
    for r in rows:
        s = r.strip()
        if s in HEAD and s in ("AIR STRIKE PATROL STAFF", "PRESENTED BY SETA"):
            r = HEAD[s].center(W)[:W]
        elif s == "AND":
            r = r.replace("AND", " E ")
        else:
            lead = len(r) - len(r.lstrip())
            for en, it in sorted(HEAD.items(), key=lambda kv: -len(kv[0])):
                if r.lstrip().startswith(en + " ") or r.strip() == en:
                    rest = r[lead + len(en):]
                    name = rest.strip()
                    body = " " * lead + it
                    r = body + name.rjust(W - len(body)) if name else body.ljust(W)
                    assert len(r) == W and name in r, (r, name)
                    break
        out.append(r.ljust(W)[:W])
    res = "".join(out)
    assert len(res) == len(txt)
    return res
