"""swarm_examples — cosa aspettarsi da ogni casella della matrice.

Partendo dall'idea dell'utente, descrive per ognuna delle 9 combinazioni
difficoltà × qualità il risultato concreto che si ottiene: cosa c'è, cosa non
c'è, quanto è curato. In modalità reale chiede a `claude -p` (Haiku, effort
basso, nessuno strumento: costa pochi centesimi); in demo costruisce esempi
finti con delle regole.

Formato restituito:
  {"idea": str, "cost": float, "demo": bool,
   "items": {"1-1": {"title": str, "get": [str], "not": [str], "polish": str}, ...}}
"""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import tempfile
from typing import Dict, List

from swarm_advice import DIFFICULTY, QUALITY, QUALITY_BRIEF
from swarm_core import CLAUDE_MISSING, ENV_STRIP, find_claude

KEYS = [f"{d}-{q}" for d in (1, 2, 3) for q in (1, 2, 3)]
TIMEOUT = 120  # secondi

SYSTEM = ("Sei un consulente che spiega a una persona non esperta cosa otterrà da una squadra "
          "di agenti che programmano. Rispondi in italiano, solo con un oggetto JSON valido, "
          "senza testo prima o dopo.")

def _clean_list(v, n: int) -> List[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    out = [re.sub(r"\s+", " ", str(x)).strip()[:160] for x in v if str(x).strip()]
    return out[:n]


PROMPT = """\
L'utente vuole costruire questo:
<<<
{idea}
>>>

Una squadra di agenti Claude Code lo costruirà. L'utente sceglie una casella di una
matrice 3×3: GRANDEZZA (quante funzioni costruiamo) × CURA (quanto sono rifinite).

Grandezze:
{diffs}

Cura:
{quals}

Devi far capire cosa cambia davvero tra una casella e l'altra, con FATTI su QUESTA idea.
La grandezza cambia solo le funzioni; la cura cambia solo come si comportano quando
qualcosa va storto, i test, la grafica, la documentazione.

Per ogni grandezza ("1", "2", "3"):
- "nome": la funzione che la distingue, 2-3 parole con un verbo (es. "salvare le preferite").
- "funzioni": 2-3 cose che l'utente può FARE a quella grandezza, max 10 parole ciascuna.
  La 1 ha solo la funzione principale; la 2 aggiunge funzioni nuove alla 1; la 3 aggiunge
  funzioni nuove alla 2. Non ripetere le funzioni della grandezza prima.
  Le grandezze sono relative all'idea: se l'idea è uno script, la 2 è uno script con più
  opzioni, non per forza un'app con schermate.
- "cura": 3 fatti brevi (al massimo 6 parole, con un verbo) su una situazione tipica di QUELLE
  funzioni, uno per livello di cura: [prototipo: cosa va storto, solida: come lo gestisce,
  rifinita: un caso limite che regge o un dettaglio di grafica]. Ogni grandezza usa una
  situazione diversa. Esempio di forma, per un ricettario: ["si blocca se mancano le dosi",
  "avvisa quali dosi mancano", "converte grammi e tazze"].
- solo per la 3, "limite": una cosa che anche la casella più grande non farà, max 10 parole.

Per ogni livello di cura ("1", "2", "3"):
- "manca": 1-2 cose che a quel livello mancano, max 10 parole ciascuna (es. "niente test:
  una modifica può rompere altro"); per la cura 3 una sola voce: il prezzo da pagare.
- "polish": una frase, max 18 parole, su cosa succede in pratica usandolo, per QUESTA idea.

Mai giudizi al posto dei fatti: non usare curato, rifinito, completo, solido, affidabile,
ordinato, robusto, semplice, base, avanzato, pronto, professionale, sistema, piattaforma.
Niente gergo tecnico: scrivi come a un amico.

Rispondi solo con questo JSON:
{{"grandezze": {{"1": {{"nome": "...", "funzioni": ["..."], "cura": ["...", "...", "..."]}},
               "2": {{...}}, "3": {{"nome": "...", "funzioni": ["..."], "cura": ["...", "...", "..."], "limite": "..."}}}},
 "cura": {{"1": {{"manca": ["..."], "polish": "..."}}, "2": {{...}}, "3": {{...}}}}}}
"""


def _prompt(idea: str) -> str:
    diffs = "\n".join(f"{d}. {a}: {b}" for d, (a, b) in DIFFICULTY.items())
    quals = "\n".join(f"{q}. {QUALITY[q][0]}: {QUALITY_BRIEF[q].split('. ', 1)[-1]}" for q in QUALITY)
    return PROMPT.format(idea=idea.strip()[:2000], diffs=diffs, quals=quals)


def _cap(s: str) -> str:
    s = re.sub(r"\s+", " ", str(s)).strip().rstrip(".")
    return s[:1].upper() + s[1:]


def _short(s: str, n: int = 80) -> str:
    """Accorcia a parola intera: meglio un titolo corto che uno tagliato a metà."""
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _new_only(funcs: List[str], seen: List[str]) -> List[str]:
    """Toglie le funzioni già dette in una grandezza più piccola (il modello a volte le ripete)."""
    old = [x.lower() for x in seen]
    fresh = [f for f in funcs
             if not any(difflib.SequenceMatcher(None, f.lower(), o).ratio() > 0.8 for o in old)]
    return fresh or funcs


def assemble(raw: dict) -> dict:
    """Dalla risposta compatta (funzioni per riga, cura per colonna) costruisce le 9 caselle.

    Le tre caselle di una riga hanno per forza le stesse funzioni, e le tre di una
    colonna la stessa descrizione della cura: la coerenza la garantisce il codice.
    Una risposta già nel formato a 9 caselle passa così com'è.
    """
    sizes, care = raw.get("grandezze"), raw.get("cura")
    if not isinstance(sizes, dict) or not isinstance(care, dict):
        return raw
    def size(d):
        v = sizes.get(str(d))
        return v if isinstance(v, dict) else {}
    def qual(q):
        v = care.get(str(q))
        return v if isinstance(v, dict) else {}
    funcs_by, seen = {}, []
    for d in (1, 2, 3):
        f = _new_only([_cap(x) for x in _clean_list(size(d).get("funzioni"), 3)], seen)
        funcs_by[d] = f
        seen += f
    out = {}
    for d in (1, 2, 3):
        s = size(d)
        name = re.sub(r"\s+", " ", str(s.get("nome") or "")).strip().rstrip(".")
        funcs = funcs_by[d]
        facts = _clean_list(s.get("cura"), 3)
        if d < 3:
            later = funcs_by[d + 1][:2]
        else:
            later = [_cap(x) for x in _clean_list(s.get("limite"), 1)]
        prev = f"Tutto quello di {DIFFICULTY[d - 1][0]}" if d > 1 else None
        for q in (1, 2, 3):
            c = qual(q)
            fact = facts[q - 1].rstrip(".") if len(facts) >= q else ""
            title = _short(f"{_cap(name)}, {fact[:1].lower() + fact[1:]}" if name and fact else _cap(name))
            get = ([prev] if prev else []) + funcs
            if q > 1 and fact:
                get = get[:3] + [_cap(fact)]
            nots = later[:1] + [_cap(x) for x in _clean_list(c.get("manca"), 1 if q == 3 else 2)]
            out[f"{d}-{q}"] = {"title": title, "get": get[:4], "not": nots[:3],
                               "polish": str(c.get("polish") or "")}
    return out


def normalize(raw: dict, idea: str) -> Dict[str, dict]:
    """Tiene solo le 9 caselle nel formato giusto; quelle mancanti le riempie a regole."""
    if isinstance(raw.get("items"), dict):
        raw = raw["items"]
    raw = assemble(raw)
    fallback = fake_items(idea)
    out: Dict[str, dict] = {}
    for k in KEYS:
        v = raw.get(k) or raw.get(k.replace("-", "x")) or {}
        if not isinstance(v, dict):
            v = {}
        item = {
            "title": _short(re.sub(r"\s+", " ", str(v.get("title") or "")).strip()),
            "get": _clean_list(v.get("get"), 4),
            "not": _clean_list(v.get("not"), 3),
            "polish": re.sub(r"\s+", " ", str(v.get("polish") or "")).strip()[:200],
        }
        for f in ("title", "get", "not", "polish"):
            if not item[f]:
                item[f] = fallback[k][f]
        out[k] = item
    return out


def _extract(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("la risposta non contiene gli esempi")
    return json.loads(text[a:b + 1])


async def real_examples(idea: str, claude_bin: str = "claude") -> dict:
    claude = find_claude(claude_bin)
    if not claude:
        raise RuntimeError(CLAUDE_MISSING)
    cmd = [claude, "-p", _prompt(idea), "--output-format", "json",
           "--model", "haiku", "--effort", "low", "--system-prompt", SYSTEM,
           "--tools", "", "--strict-mcp-config", "--no-session-persistence",
           "--permission-mode", "dontAsk"]
    env = {k: v for k, v in os.environ.items() if k not in ENV_STRIP}
    with tempfile.TemporaryDirectory(prefix="swarm-esempi-") as cwd:  # niente CLAUDE.md di progetto
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("gli esempi ci mettono troppo: riprova tra poco")
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
            raise
    try:
        res = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        tail = err.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else f"claude è uscito con codice {proc.returncode}")
    text = str(res.get("result") or "")
    if res.get("is_error") or proc.returncode != 0:
        raise RuntimeError(text or f"claude è uscito con codice {proc.returncode}")
    try:
        items = normalize(_extract(text), idea)
    except ValueError as e:
        raise RuntimeError(f"esempi non leggibili ({e}): riprova") from None
    return {"idea": idea, "cost": float(res.get("total_cost_usd") or 0), "demo": False, "items": items}


# demo: esempi a regole ----------------------------------------------------
def _subject(idea: str) -> str:
    s = re.sub(r"\s+", " ", idea).strip().rstrip(".!")
    s = re.split(r"[,;:]| dove | che | con | per ", s, maxsplit=1)[0].strip()
    s = re.sub(r"^(vorrei|voglio|fammi|crea|creare|costruisci|costruire|fare|un'|una|uno|un|il|la|lo|l')\s*",
               "", s, flags=re.I)
    s = re.sub(r"^(un'|una|uno|un|il|la|lo|l')\s*", "", s, flags=re.I)
    words = s.split()
    return " ".join(words[:5]) or "il progetto"


SIZE_GET = {
    1: ["la funzione principale di {s}, e basta", "una sola schermata, senza account"],
    2: ["{s} con più schermate collegate tra loro", "dati salvati, ritrovi tutto quando torni",
        "impostazioni e ricerca"],
    3: ["{s} completo: interfaccia, server e dati", "account utente e dati che restano tra un uso e l'altro",
        "tante funzioni oltre a quella principale"],
}
SIZE_NOT = {
    1: "niente account, niente dati salvati, niente extra",
    2: "niente server vero per molte persone insieme",
    3: "",
}
QUAL_GET = {1: [], 2: ["messaggi chiari quando qualcosa va storto", "README con i comandi per avviarlo"],
            3: ["test automatici su tutte le funzioni", "grafica curata, si usa bene da telefono",
                "documentazione e casi limite gestiti"]}
QUAL_NOT = {1: ["niente test: se cambi qualcosa può rompersi", "grafica essenziale, da sistemare"],
            2: ["grafica pulita ma semplice, non rifinita nei dettagli"],
            3: []}
POLISH = {
    1: "Grezzo: funziona sul percorso principale, ma basta un dato strano per vederlo inciampare.",
    2: "Ordinato e affidabile per l'uso di tutti i giorni, con qualche test sulle parti delicate.",
    3: "Curato: riletto da una revisione, testato anche nei casi strani, pronto da mostrare.",
}
TITLE = {
    (1, 1): "Una bozza che funziona", (1, 2): "Piccolo ma affidabile", (1, 3): "Piccolo e rifinito",
    (2, 1): "Più schermate, ancora grezze", (2, 2): "Un'app vera, ordinata", (2, 3): "Un'app curata in ogni parte",
    (3, 1): "Tutto il sistema, di corsa", (3, 2): "Sistema completo e solido", (3, 3): "Prodotto quasi da lancio",
}


def fake_items(idea: str) -> Dict[str, dict]:
    s = _subject(idea)
    out = {}
    for d in (1, 2, 3):
        for q in (1, 2, 3):
            get = [g.format(s=s) for g in SIZE_GET[d][:2 if q == 1 else 3]] + QUAL_GET[q]
            nots = ([SIZE_NOT[d]] if SIZE_NOT[d] else []) + QUAL_NOT[q]
            if not nots:
                nots = ["costa di più e ci mette di più delle altre caselle"]
            get, nots = [x[:1].upper() + x[1:] for x in get], [x[:1].upper() + x[1:] for x in nots]
            out[f"{d}-{q}"] = {"title": TITLE[(d, q)], "get": get[:4], "not": nots[:3], "polish": POLISH[q]}
    return out


async def fake_examples(idea: str) -> dict:
    await asyncio.sleep(1.2)
    return {"idea": idea, "cost": 0.0, "demo": True, "items": fake_items(idea)}
