"""swarm_advice — quanti agenti, che effort e che modello usare.

Due manopole, da 1 a 3:
  difficoltà (input):  quanto è grande e intricato quello che chiedi
  qualità (output):    quanto deve essere curato il risultato finale

Regole, in breve:
- la difficoltà decide quanti pezzi si possono fare in parallelo (agenti nello
  sviluppo) e quanto ragionamento serve dove si decide l'architettura
  (fondamenta) e dove si rimette tutto insieme (integrazione);
- la qualità alza o abbassa l'effort di tutte le fasi e, al livello più alto,
  aggiunge una fase di Revisione (test + rilettura) prima dell'integrazione;
- più agenti non vuol dire più qualità: ogni agente in più è un pezzo in più da
  far combaciare, quindi lo sviluppo non supera mai 5 agenti.
"""
from __future__ import annotations

from typing import Dict, List

EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODELS = ("haiku", "sonnet", "opus")
MODEL_IT = {"haiku": "Haiku", "sonnet": "Sonnet", "opus": "Opus"}
# fasi dove si decide (struttura) e dove si ricuce (integrazione): qui serve il modello più capace
STRONG_ROLES = ("fondamenta", "integrazione")
EFFORT_IT = {"low": "basso", "medium": "medio", "high": "alto", "xhigh": "molto alto", "max": "massimo"}

DIFFICULTY = {
    1: ("Semplice", "una pagina, uno script, un piccolo tool"),
    2: ("Media", "un'app con più schermate, o un backend semplice"),
    3: ("Complessa", "frontend + backend + dati, tante funzioni, o un grosso progetto esistente"),
}
QUALITY = {
    1: ("Prototipo", "deve funzionare, anche se grezzo"),
    2: ("Solida", "ordinata, con gestione degli errori e qualche test"),
    3: ("Rifinita", "test completi, casi limite, interfaccia curata, documentata"),
}

# agenti nella fase di sviluppo [difficoltà][qualità]
DEV_AGENTS = {1: {1: 1, 2: 2, 3: 2}, 2: {1: 2, 2: 3, 3: 3}, 3: {1: 3, 2: 4, 3: 5}}
# effort di partenza per ruolo, per difficoltà (indice in EFFORTS)
EFFORT_BASE = {
    "fondamenta": {1: 1, 2: 2, 3: 3},
    "sviluppo": {1: 0, 2: 1, 3: 2},
    "revisione": {1: 1, 2: 2, 3: 2},
    "integrazione": {1: 1, 2: 2, 3: 2},
}
QUALITY_SHIFT = {1: -1, 2: 0, 3: 1}
MODEL_BUDGET = {"haiku": 0.5, "sonnet": 2.0, "opus": 5.0}
EFFORT_COST = {"low": 0.6, "medium": 1.0, "high": 1.5, "xhigh": 2.0, "max": 3.0}

QUALITY_BRIEF = {
    1: ("Qualità richiesta: PROTOTIPO. Deve funzionare, niente di più: codice semplice e "
        "diretto, niente test, niente rifiniture grafiche."),
    2: ("Qualità richiesta: SOLIDA. Codice ordinato, gestione degli errori sulle parti che "
        "possono fallire, qualche test sulle parti critiche, README chiaro."),
    3: ("Qualità richiesta: RIFINITA. Test completi, casi limite gestiti, interfaccia curata "
        "e accessibile, documentazione. Il lavoro viene riletto da una fase di Revisione."),
}


def _clamp(v: int, lo: int = 1, hi: int = 3) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return 2
    return max(lo, min(hi, v))


def _effort(role: str, d: int, q: int) -> str:
    i = EFFORT_BASE[role][d] + QUALITY_SHIFT[q]
    return EFFORTS[max(0, min(3, i))]  # "max" mai di default: costa tanto e rende poco in più


def phase_model(role: str, model: str) -> str:
    """Con Opus, sviluppo e revisione passano a Sonnet: lavorano su compiti già definiti
    da INTERFACCE.md, dove Sonnet basta e costa meno. Le altre scelte restano uguali."""
    if model == "opus" and role not in STRONG_ROLES:
        return "sonnet"
    return model


def phase_budget(budget: float, model: str, project_model: str) -> float:
    """Spesa massima per agente in una fase: scende in proporzione se la fase usa un modello
    più economico di quello del progetto, ma non sale mai sopra quella scelta dall'utente."""
    a, b = MODEL_BUDGET.get(model), MODEL_BUDGET.get(project_model)
    if not a or not b or a >= b:
        return budget
    return min(budget, max(0.5, int(budget * a / b * 2 + 0.5) / 2))  # come Math.round nella pagina


def recommend(difficulty: int, quality: int) -> dict:
    d, q = _clamp(difficulty), _clamp(quality)
    dev = DEV_AGENTS[d][q]
    review = (1 if d == 1 else 2) if q == 3 else 0
    phases: List[dict] = [
        {"role": "fondamenta", "name": "Fondamenta", "agents": 1, "effort": _effort("fondamenta", d, q),
         "why": "un solo agente sceglie struttura e interfacce: qui un errore si paga in tutte le fasi dopo"},
        {"role": "sviluppo", "name": "Sviluppo", "agents": dev, "effort": _effort("sviluppo", d, q),
         "why": ("un agente basta per un progetto così piccolo" if dev == 1 else
                 f"{dev} pezzi indipendenti in parallelo, ognuno sui suoi file")},
    ]
    if review:
        phases.append({"role": "revisione", "name": "Revisione", "agents": review,
                       "effort": _effort("revisione", d, q),
                       "why": ("un agente rilegge tutto e scrive i test" if review == 1 else
                               "uno scrive e lancia i test, uno rilegge il codice e annota i problemi")})
    phases.append({"role": "integrazione", "name": "Integrazione", "agents": 1,
                   "effort": _effort("integrazione", d, q),
                   "why": "un solo agente unisce i pezzi e corregge ciò che non combacia"})

    score = d + q
    model = "haiku" if score == 2 else "opus" if score == 6 else "sonnet"
    top = max(EFFORTS.index(p["effort"]) for p in phases)
    budget = max(0.3, round(MODEL_BUDGET[model] * EFFORT_COST[EFFORTS[top]] * 2) / 2)
    for p in phases:
        p["model"] = phase_model(p["role"], model)
        p["budget"] = phase_budget(budget, p["model"], model)
    total = sum(p["agents"] for p in phases)

    notes = []
    if model == "haiku":
        notes.append("Haiku basta: il progetto è piccolo e serve solo che funzioni.")
    elif model == "opus":
        notes.append("Opus per fondamenta e integrazione, dove si decide la struttura e si ricuce "
                     "tutto. Sviluppo e revisione usano Sonnet: i compiti sono già definiti, Sonnet "
                     "basta e il tetto di spesa per quegli agenti scende.")
    else:
        notes.append("Sonnet è il miglior compromesso tra capacità e costo per questo livello.")
    if review:
        notes.append("Qualità rifinita: c'è una fase di Revisione prima dell'integrazione.")
    if d == 3 and q == 1:
        notes.append("Progetto grande ma solo prototipo: tanti agenti per fare in fretta, effort contenuto.")
    if d == 1 and q == 3:
        notes.append("Progetto piccolo ma curato: pochi agenti, ma ragionano di più.")
    if q >= 2 and d >= 2:
        notes.append("Conviene attivare i comandi di build e test, così gli agenti verificano il lavoro.")

    return {"difficulty": d, "quality": q, "model": model, "budget": budget,
            "max_workers": max(dev, review, 1), "phases": phases, "total_agents": total,
            "max_cost": round(sum(p["agents"] * p["budget"] for p in phases), 2), "allow_build": q >= 2 and d >= 2,
            "planner_effort": "medium" if d == 1 else "high", "notes": notes}


def phase_role(name: str, i: int, n: int) -> str:
    low = (name or "").lower()
    if "revis" in low or "review" in low:
        return "revisione"
    if "integr" in low:
        return "integrazione"
    if i == 0 and n > 1:
        return "fondamenta"
    if i == n - 1 and n > 1:
        return "integrazione"
    return "sviluppo"


def assign_efforts(plan: dict, rec: dict, model: str = "") -> dict:
    """Mette effort e modello consigliati su ogni fase che non ne ha già uno valido.
    Il modello parte da quello scelto per il progetto (`model`), non da quello consigliato."""
    by_role: Dict[str, str] = {p["role"]: p["effort"] for p in rec["phases"]}
    model = model if model in MODELS else rec["model"]
    phases = plan.get("phases") or []
    for i, ph in enumerate(phases):
        role = phase_role(ph.get("name", ""), i, len(phases))
        ph["role"] = role
        if ph.get("effort") not in EFFORTS and ph.get("effort") != "":
            ph["effort"] = by_role.get(role) or by_role.get("sviluppo", "medium")
        if ph.get("model") not in MODELS:
            ph["model"] = phase_model(role, model)
    return plan


def planner_brief(rec: dict) -> str:
    parts = [f'{p["name"]} {p["agents"]} agent{"e" if p["agents"] == 1 else "i"}' for p in rec["phases"]]
    lines = [QUALITY_BRIEF[rec["quality"]],
             f'Difficoltà stimata dall\'utente: {DIFFICULTY[rec["difficulty"]][0].upper()} '
             f'({DIFFICULTY[rec["difficulty"]][1]}).',
             "Struttura consigliata: " + " → ".join(parts) + "."]
    if any(p["role"] == "revisione" for p in rec["phases"]):
        n = next(p["agents"] for p in rec["phases"] if p["role"] == "revisione")
        lines.append(
            'Aggiungi una fase "Revisione" tra Sviluppo e Integrazione con '
            + ("1 agente che rilegge tutto il codice, scrive REVISIONE.md con i problemi trovati e "
               "le correzioni da fare, e scrive i test in tests/ (tocca solo REVISIONE.md e tests/)."
               if n == 1 else
               "2 agenti: \"test\" scrive i test in tests/ (tocca solo tests/); \"revisore\" rilegge "
               "tutto il codice e scrive REVISIONE.md con i problemi trovati e le correzioni da fare "
               "(tocca solo REVISIONE.md).")
            + " L'agente di Integrazione deve correggere tutto ciò che è in REVISIONE.md e far "
              "passare i test.")
    return "\n".join(lines)


def advice_table() -> dict:
    return {
        "difficulty": {k: {"label": a, "hint": b} for k, (a, b) in DIFFICULTY.items()},
        "quality": {k: {"label": a, "hint": b} for k, (a, b) in QUALITY.items()},
        "efforts": list(EFFORTS), "effort_it": EFFORT_IT,
        "models": list(MODELS), "model_budget": MODEL_BUDGET, "strong_roles": list(STRONG_ROLES),
        "table": {f"{d}-{q}": recommend(d, q) for d in (1, 2, 3) for q in (1, 2, 3)},
    }
