"""swarm_project — da un'idea a un progetto, a fasi.

1. Il *pianificatore* (un Claude in sola lettura) trasforma l'idea in fasi:
   fondamenta (1 agente) → sviluppo in parallelo (N agenti) → integrazione.
2. L'utente rivede e modifica il piano.
3. La pipeline esegue le fasi una dopo l'altra: ogni fase è uno Swarm; alla
   fine di ogni fase i branch degli agenti vengono uniti nel branch corrente
   del progetto, così la fase dopo parte dal lavoro di quella prima.
   Se un merge va in conflitto viene annullato e il branch viene passato al
   primo agente della fase successiva, che lo unisce a mano.
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import random
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Deque, List, Optional

from swarm_advice import EFFORTS, MODELS, assign_efforts, phase_budget, planner_brief, recommend
import swarm_publish as pub
from swarm_core import CLAUDE_MISSING, ENV_STRIP, MAX_WORKERS, Swarm, describe_tool, find_claude, git, git_id, short

STAGES = ("idle", "planning", "review", "running", "done", "stopped", "failed")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,30}$")

# comandi sempre permessi agli agenti (solo lettura su git)
BASE_TOOLS = ["Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(ls:*)"]
# comandi di build/test, solo se l'utente lo sceglie
BUILD_TOOLS = ["Bash(npm:*)", "Bash(npx:*)", "Bash(node:*)", "Bash(python3:*)",
               "Bash(pytest:*)", "Bash(pip:*)", "Bash(cargo:*)", "Bash(go:*)", "Bash(make:*)"]
# per chi deve unire a mano branch in conflitto
MERGE_TOOLS = ["Bash(git merge:*)", "Bash(git add:*)", "Bash(git checkout:*)"]

PLANNER_PROMPT = """\
Sei il pianificatore di "swarm": un orchestratore che fa lavorare più agenti
Claude Code in parallelo, ognuno in un git worktree separato. Il tuo compito è
trasformare l'idea dell'utente in un piano a fasi. NON scrivi codice.

Idea dell'utente:
<<<
{idea}
>>>
{feedback}
Cartella del progetto: {path} ({state})
Massimo agenti in parallelo: {max_workers}
Comandi di build/test permessi agli agenti: {build}

{brief}

{explore}

Regole del piano:
- Fase 1 "Fondamenta": UN solo agente. Sceglie le tecnologie (preferisci
  soluzioni semplici, senza dipendenze se possibile), crea la struttura delle
  cartelle, i file base e un file INTERFACCE.md che descrive in modo preciso
  come si parlano i pezzi: nomi dei file, funzioni/componenti esposti con firme,
  formato dei dati. Le fasi successive si basano su questo file.
- Fase 2 "Sviluppo": da 1 a {max_workers} agenti in parallelo (segui la
  struttura consigliata qui sopra, salvo che il progetto chieda altro). Ogni agente
  possiede file DIVERSI (scrivi esplicitamente nel compito quali file/cartelle
  può toccare) così i merge non vanno in conflitto. Tutti devono rispettare
  INTERFACCE.md.
- Fase 3 "Integrazione": UN solo agente. Collega i pezzi, sistema ciò che non
  combacia, verifica che il progetto funzioni{test_hint} e scrive nel README
  come avviarlo.
- Adatta la cura dei compiti alla qualità richiesta qui sopra.
- Se il progetto è piccolo puoi usare meno agenti; se è un progetto esistente
  adatta le fasi a quello che c'è già (la fase Fondamenta può diventare
  "Analisi e preparazione").
- Ogni compito ("task") deve essere autosufficiente: l'agente non vede l'idea
  originale, solo l'obiettivo generale e il suo compito. Scrivilo in italiano,
  concreto, 3-8 frasi.
- "name": breve, minuscolo, solo lettere, numeri e trattini (es. "api-meteo").

Rispondi SOLO con un oggetto JSON valido, senza testo prima o dopo, in questo
formato:
{{"goal": "una frase che descrive il progetto",
  "phases": [
    {{"name": "Fondamenta", "workers": [{{"name": "fondamenta", "task": "..."}}]}},
    {{"name": "Sviluppo", "workers": [{{"name": "...", "task": "..."}}, ...]}},
    (eventuale fase "Revisione", se richiesta qui sopra)
    {{"name": "Integrazione", "workers": [{{"name": "integrazione", "task": "..."}}]}}
  ]}}
"""


# dove finiscono i progetti quando scrivi solo il nome (o niente)
PROJECTS_DIR = Path(os.path.expanduser(os.environ.get("SWARM_PROJECTS") or "~/Desktop/Progetti swarm"))


def project_slug(text: str) -> str:
    """Nome di cartella dalle prime parole dell'idea: 'App meteo con…' → 'app-meteo-con'."""
    words = re.findall(r"[a-z0-9àèéìòù]+", (text or "").lower())
    stop = {"un", "una", "uno", "il", "lo", "la", "i", "gli", "le", "di", "da", "del", "della", "per",
            "con", "che", "e", "a", "in", "su", "voglio", "vorrei", "crea", "creami", "fammi"}
    keep = [w for w in words if w not in stop][:4] or words[:4]
    return "-".join(keep)[:40].strip("-") or "progetto"


def free_project_dir(name: str) -> Path:
    base = PROJECTS_DIR / name
    p, n = base, 2
    while p.exists() and any(p.iterdir()):
        p = PROJECTS_DIR / f"{name}-{n}"
        n += 1
    return p


def resolve_project_path(raw: str, idea: str = "") -> Path:
    raw = (raw or "").strip()
    if not raw:  # niente: nome dall'idea, dentro la cartella dei progetti
        return free_project_dir(project_slug(idea)).resolve()
    if "/" not in raw and not raw.startswith("~"):  # solo un nome: dentro la cartella dei progetti
        if raw in (".", ".."):
            raise ValueError("nome della cartella non valido")
        return (PROJECTS_DIR / raw).resolve()
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        p = Path.home() / p
    p = p.resolve()
    home = Path.home().resolve()
    if p == home or home not in p.parents:
        raise ValueError(f"la cartella deve stare dentro la tua home ({home}), es. ~/Progetti/nome")
    return p


def validate_plan(plan: dict, max_workers: int = MAX_WORKERS) -> dict:
    if not isinstance(plan, dict):
        raise ValueError("il piano non è un oggetto JSON")
    phases = plan.get("phases")
    if not isinstance(phases, list) or not 1 <= len(phases) <= 6:
        raise ValueError("il piano deve avere da 1 a 6 fasi")
    out = {"goal": short(str(plan.get("goal") or ""), 300), "phases": []}
    for i, ph in enumerate(phases):
        if not isinstance(ph, dict):
            raise ValueError(f"fase {i + 1}: formato non valido")
        ws = ph.get("workers")
        if not isinstance(ws, list) or not ws:
            raise ValueError(f"fase {i + 1}: serve almeno un agente")
        if len(ws) > max(1, min(MAX_WORKERS, max_workers)):
            raise ValueError(f"fase {i + 1}: massimo {max_workers} agenti")
        names, workers = set(), []
        for w in ws:
            name = str(w.get("name") or "").strip().lower().replace(" ", "-")
            task = str(w.get("task") or "").strip()
            if not NAME_RE.match(name):
                raise ValueError(f"fase {i + 1}: nome non valido {name!r} (minuscole, numeri, trattini)")
            if name in names:
                raise ValueError(f"fase {i + 1}: nome ripetuto {name!r}")
            if len(task) < 10:
                raise ValueError(f"fase {i + 1}, {name}: il compito è troppo corto")
            names.add(name)
            workers.append({"name": name, "task": task[:4000]})
        item = {"name": short(str(ph.get("name") or f"Fase {i + 1}"), 40), "workers": workers}
        eff = ph.get("effort")
        if eff in EFFORTS or eff == "":
            item["effort"] = eff  # "" = effort predefinito di Claude Code
        if ph.get("model") in MODELS:
            item["model"] = ph["model"]
        out["phases"].append(item)
    return out


def extract_json(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("il pianificatore non ha restituito un piano JSON")
    return json.loads(text[a:b + 1])


class Project:
    def __init__(self, demo: bool = False, claude_bin: str = "claude") -> None:
        self.demo = demo
        self.claude_bin = claude_bin
        if not demo:
            try:
                PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        self.listeners: List[Callable[[str, dict], None]] = []
        self.history: Deque[dict] = collections.deque(maxlen=3000)
        self.messages = 0
        self._reset_state()

    def _reset_state(self) -> None:
        self.stage = "idle"
        self.settings: dict = {}
        self.plan: Optional[dict] = None
        self.phase_idx = -1
        self.phases: List[dict] = []
        self.core: Optional[Swarm] = None
        self.repo: Optional[Path] = None
        self.repo_state = ""
        self.base_commit = ""
        self.final: Optional[dict] = None
        self.error = ""
        self.cost_done = 0.0
        self.planner_cost = 0.0
        self.started_at = 0.0
        self.ended_at = 0.0
        self.stop_requested = False
        self._prev_plan: Optional[dict] = None
        self.advice: Optional[dict] = None
        self.github: Optional[dict] = None
        self._gh_task: Optional[asyncio.Future] = None
        self._planner_proc: Optional[asyncio.subprocess.Process] = None
        self._task: Optional[asyncio.Future] = None
        # cresce a ogni pianificazione e a ogni annullamento: un pianificatore vecchio che
        # finisce in ritardo non deve toccare lo stato di quello nuovo
        self._plan_gen = getattr(self, "_plan_gen", 0) + 1

    # notifiche --------------------------------------------------------------
    def emit(self, kind: str, payload: dict) -> None:
        for fn in list(self.listeners):
            try:
                fn(kind, payload)
            except Exception:  # noqa: BLE001
                pass

    def bus(self, src: str, dst: str, msg: str, kind: str = "info") -> None:
        if kind != "tool":
            self.messages += 1
        entry = {"ts": time.time(), "src": src, "dst": dst, "msg": msg, "kind": kind}
        self.history.append(entry)
        self.emit("bus", entry)

    def _forward(self, kind: str, payload: dict) -> None:
        """Eventi dello Swarm della fase corrente → ascoltatori del progetto."""
        if kind == "bus":
            if payload["kind"] != "tool":
                self.messages += 1
            self.history.append(payload)
        self.emit(kind, payload)

    @property
    def cost(self) -> float:
        cur = self.core.total_cost if self.core and self.stage == "running" else 0.0
        return self.cost_done + self.planner_cost + cur

    def snapshot(self) -> dict:
        return {
            "stage": self.stage, "demo": self.demo, "settings": self.settings,
            "plan": self.plan, "phase_idx": self.phase_idx, "phases": self.phases,
            "repo": str(self.repo or ""), "repo_state": self.repo_state,
            "final": self.final, "error": self.error, "cost": round(self.cost, 4),
            "messages": self.messages, "started_at": self.started_at, "ended_at": self.ended_at,
            "advice": self.advice, "github": self.github,
            "projects_dir": str(PROJECTS_DIR),
        }

    def _cap(self) -> int:
        """Massimo agenti per fase: quello scelto, ma almeno quanti ne servono alla revisione."""
        cap = int(self.settings.get("max_workers") or MAX_WORKERS)
        if self.advice:
            cap = max(cap, max(p["agents"] for p in self.advice["phases"] if p["role"] != "sviluppo"))
        return cap

    def _fail(self, msg: str) -> None:
        self.error = msg
        self.stage = "failed"
        self.ended_at = time.time()
        self.bus("orchestrator", "you", msg, "err")

    # 1. pianificazione ------------------------------------------------------
    def begin_plan(self, settings: dict, feedback: str = "") -> None:
        if self.stage in ("planning", "running"):
            raise ValueError("c'è già qualcosa in corso")
        idea = str(settings.get("idea") or "").strip()
        if len(idea) < 10:
            raise ValueError("descrivi l'idea con almeno una frase")
        model = settings.get("model") or "sonnet"
        if model not in MODELS:
            raise ValueError("modello non valido")
        try:
            budget = float(settings.get("budget") or 2)
            max_workers = int(settings.get("max_workers") or 3)
        except (TypeError, ValueError):
            raise ValueError("budget o numero di agenti non validi")
        if not 0.1 <= budget <= 50:
            raise ValueError("il budget per agente deve stare tra 0.1 e 50 dollari")
        max_workers = max(1, min(6, max_workers))
        rec = recommend(settings.get("difficulty", 2), settings.get("quality", 2))
        path = settings.get("path") or ("~/Desktop/Progetti swarm/demo" if self.demo else "")
        repo = Path(os.path.expanduser(path)) if self.demo else resolve_project_path(path, idea)
        if not self.demo:
            path = str(repo)
        self.settings = {"idea": idea[:4000], "path": path, "model": model, "budget": budget,
                         "max_workers": max_workers, "allow_build": bool(settings.get("allow_build")),
                         "difficulty": rec["difficulty"], "quality": rec["quality"]}
        self.advice = rec
        self.repo = repo
        self._prev_plan = self.plan if feedback else None
        self.plan = None
        self.error = ""
        self.stage = "planning"
        self.stop_requested = False
        self._plan_gen += 1
        self._task = asyncio.ensure_future(self._plan(feedback.strip()[:2000], self._plan_gen))

    async def _plan(self, feedback: str, gen: int) -> None:
        try:
            if not self.demo:
                await asyncio.get_running_loop().run_in_executor(None, self._prepare_repo)
            self.bus("orchestrator", "pianificatore",
                     ("ripianifica con il tuo feedback: " + short(feedback, 120)) if feedback
                     else "prepara il piano a fasi per: " + short(self.settings["idea"], 120))
            raw = await (self._fake_planner() if self.demo else self._run_planner(feedback))
            if gen != self._plan_gen:  # annullata (ed eventualmente già ripartita)
                return
            plan = assign_efforts(validate_plan(extract_json(raw), self._cap()), self.advice,
                                  self.settings["model"])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            if gen == self._plan_gen:
                self._fail(f"pianificazione fallita: {short(str(e), 300)}")
            return
        self.plan = plan
        self.stage = "review"
        n = sum(len(p["workers"]) for p in plan["phases"])
        self.bus("pianificatore", "you",
                 f"piano pronto: {len(plan['phases'])} fasi, {n} agenti in tutto. Rivedilo e premi Avvia.", "ok")

    def _prepare_repo(self) -> None:
        """Crea la cartella e il repository git se servono."""
        p = self.repo
        idea = self.settings["idea"]
        if not p.exists():
            p.mkdir(parents=True)
        if not (p / ".git").exists():
            others = [x for x in p.iterdir() if x.name != ".DS_Store"]
            if others:
                raise RuntimeError(
                    f"{p} esiste, non è vuota e non è un repository git. Scegli una cartella nuova, "
                    "oppure fai `git init` e un primo commit lì dentro.")
            git(p, "init", "-q")
            self.repo_state = "nuovo progetto"
        else:
            self.repo_state = "progetto esistente"
        if not git(p, "rev-parse", "--verify", "-q", "HEAD", check=False):
            readme = p / "README.md"
            if not readme.exists():
                readme.write_text(f"# {p.name}\n\n{idea}\n", encoding="utf-8")
            git(p, "add", "-A")
            git(p, *git_id(p), "commit", "-q", "--no-verify", "-m", "swarm: inizio progetto")
            self.bus("orchestrator", "you", f"creato il repository in {p}", "info")
        elif git(p, "status", "--porcelain"):
            self.bus("orchestrator", "you",
                     "attenzione: il progetto ha modifiche non committate. Gli agenti partono "
                     "dall'ultimo commit e i merge potrebbero fallire: meglio fare un commit prima.", "warn")

    async def _run_planner(self, feedback: str) -> str:
        claude = find_claude(self.claude_bin)
        if not claude:
            raise RuntimeError(CLAUDE_MISSING)
        s = self.settings
        is_new = self.repo_state == "nuovo progetto"
        prompt = PLANNER_PROMPT.format(
            idea=s["idea"],
            feedback=(f"\nFeedback dell'utente sul piano precedente (tienine conto):\n<<<\n{feedback}\n>>>\n"
                      + (f"Piano precedente:\n{json.dumps(self._prev_plan, ensure_ascii=False)}\n"
                         if self._prev_plan else "")
                      ) if feedback else "",
            path=self.repo, state=self.repo_state, max_workers=s["max_workers"],
            brief=planner_brief(self.advice),
            build="sì (npm, node, python, pytest, cargo, go, make)" if s["allow_build"] else "no, solo modifiche ai file",
            explore=("La cartella è vuota: è un progetto nuovo." if is_new else
                     "È un progetto esistente: prima di pianificare esplora il codice con Read, Glob e Grep "
                     "per capire struttura e tecnologie."),
            test_hint=" (lanciando build e test)" if s["allow_build"] else " (rileggendo il codice: non può lanciare comandi)",
        )
        cmd = [claude, "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--model", s["model"], "--permission-mode", "dontAsk",
               "--effort", self.advice["planner_effort"],
               "--allowedTools", "Read", "Glob", "Grep"]
        env = {k: v for k, v in os.environ.items() if k not in ENV_STRIP}
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(self.repo), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024)
        self._planner_proc = proc
        err_task = asyncio.ensure_future(proc.stderr.read())
        result, is_error = "", False
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                for c in (ev.get("message") or {}).get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        self.bus("pianificatore", "tool", describe_tool(c.get("name", "?"), c.get("input") or {}), "tool")
            elif ev.get("type") == "result":
                result = str(ev.get("result") or "")
                is_error = bool(ev.get("is_error"))
                self.planner_cost += float(ev.get("total_cost_usd") or 0)
        rc = await proc.wait()
        stderr = (await err_task).decode("utf-8", "replace").strip()
        if self._planner_proc is proc:
            self._planner_proc = None
        if self.stop_requested:
            return ""
        if is_error or rc != 0:
            raise RuntimeError(result or (stderr.splitlines()[-1] if stderr else f"exit code {rc}"))
        return result

    async def _fake_planner(self) -> str:
        for d in ("Glob **/*", "Read README.md", "Grep TODO"):
            await asyncio.sleep(random.uniform(0.4, 0.9))
            self.bus("pianificatore", "tool", d, "tool")
        await asyncio.sleep(0.6)
        self.planner_cost += 0.04
        idea = self.settings["idea"]
        dev_rec = next(p["agents"] for p in self.advice["phases"] if p["role"] == "sviluppo")
        n = max(1, min(self.settings["max_workers"], dev_rec, 3))
        rev = next((p["agents"] for p in self.advice["phases"] if p["role"] == "revisione"), 0)
        dev = [("dati", "Scrivi il modulo dati (src/dati/): recupera e normalizza le informazioni "
                        "necessarie all'app, con gestione degli errori e cache. Esponi le funzioni "
                        "descritte in INTERFACCE.md. Tocca solo src/dati/."),
               ("interfaccia", "Costruisci l'interfaccia (src/ui/ e index.html): pagine, componenti e "
                               "stile responsive da telefono. Usa solo le funzioni del modulo dati "
                               "descritte in INTERFACCE.md. Tocca solo src/ui/ e index.html."),
               ("test", "Scrivi i test (tests/) per il modulo dati e per i componenti principali, "
                        "basandoti su INTERFACCE.md, e configura il comando per lanciarli. Tocca solo tests/.")][:n]
        return json.dumps({
            "goal": short(idea, 160),
            "phases": [
                {"name": "Fondamenta", "workers": [{"name": "fondamenta", "task":
                    "Crea la struttura del progetto: cartelle src/, tests/, un index.html di base e un "
                    "file INTERFACCE.md che descrive con precisione le funzioni del modulo dati, i "
                    "componenti dell'interfaccia e il formato dei dati. Scegli tecnologie semplici."}]},
                {"name": "Sviluppo", "workers": [{"name": a, "task": b} for a, b in dev]},
            ] + ([{"name": "Revisione", "workers": [
                    {"name": "test", "task": "Scrivi e lancia i test in tests/ per tutto il progetto, "
                                             "compresi i casi limite. Tocca solo tests/."},
                    {"name": "revisore", "task": "Rileggi tutto il codice e scrivi REVISIONE.md con i "
                                                 "problemi trovati e le correzioni da fare. Tocca solo REVISIONE.md."},
                ][:rev]}] if rev else []) + [
                {"name": "Integrazione", "workers": [{"name": "integrazione", "task":
                    "Collega dati e interfaccia, sistema ciò che non combacia con INTERFACCE.md, fai "
                    "passare i test e scrivi nel README come avviare il progetto."}]},
            ]})

    # 2. esecuzione ----------------------------------------------------------
    def start(self, plan: Optional[dict] = None) -> None:
        if self.stage not in ("review",):
            raise ValueError("non c'è un piano pronto da avviare")
        if plan is not None:
            plan = validate_plan(plan, self._cap())
            self.plan = assign_efforts(plan, self.advice, self.settings.get("model")) if self.advice else plan
        self.stage = "running"
        self.stop_requested = False
        self.started_at = time.time()
        self.ended_at = 0.0
        self.final = None
        self.phases = [{"name": p["name"], "status": "pending", "merged": [], "conflicts": [],
                        "errors": [], "cost": 0.0} for p in self.plan["phases"]]
        self._task = asyncio.ensure_future(self._run())

    def start_from_config(self, cfg: dict) -> None:
        """Modalità `swarm.py config.json --web`: una sola fase, parte subito."""
        self.settings = {"idea": cfg.get("goal", ""), "path": cfg.get("repo", ""),
                         "model": cfg.get("model", ""), "budget": cfg.get("max_budget_usd") or 0,
                         "max_workers": len(cfg["workers"]), "allow_build": False,
                         "permission_mode": cfg.get("permission_mode"),
                         "allowed_tools": cfg.get("allowed_tools")}
        self.repo = Path(cfg["repo"])
        self.repo_state = "progetto esistente"
        self.plan = {"goal": cfg.get("goal", ""), "phases": [
            {"name": "Esecuzione", "workers": [{"name": w["name"], "task": w["task"]} for w in cfg["workers"]]}]}
        self.stage = "review"
        self.start()

    async def _run(self) -> None:
        try:
            await self._run_phases()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._fail(f"errore nella pipeline: {short(str(e), 300)}")

    async def _run_phases(self) -> None:
        s = self.settings
        loop = asyncio.get_running_loop()
        if not self.demo:
            self.base_commit = git(self.repo, "rev-parse", "HEAD")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        carry: List[str] = []  # branch in conflitto da passare alla fase dopo
        n = len(self.plan["phases"])
        for i, ph in enumerate(self.plan["phases"]):
            self.phase_idx = i
            res = self.phases[i]
            res["status"] = "running"
            workers = [dict(w) for w in ph["workers"]]
            tools = list(s.get("allowed_tools") or (BASE_TOOLS + (BUILD_TOOLS if s.get("allow_build") else [])))
            if carry:
                workers[0]["task"] += (
                    "\n\nIMPORTANTE, prima di tutto: questi branch della fase precedente non si sono uniti "
                    "automaticamente per conflitti: " + ", ".join(carry) + ". Uniscili nel tuo worktree "
                    "con `git merge <branch>`, risolvi i conflitti tenendo il lavoro di entrambe le parti "
                    "e controlla che tutto torni. Poi fai il tuo compito.")
                tools += MERGE_TOOLS
                carry = []
            model = ph.get("model") or s.get("model") or ""
            budget = s.get("budget") or 0
            if budget and model in MODELS and s.get("model") in MODELS:
                budget = phase_budget(budget, model, s["model"])
            cfg = {"repo": str(self.repo) if self.repo else "", "run_id": f"{stamp}-f{i + 1}",
                   "goal": f"{self.plan.get('goal') or s.get('idea', '')} — fase {i + 1} di {n}: {ph['name']}",
                   "model": model or None,
                   "permission_mode": s.get("permission_mode") or "acceptEdits",
                   "max_budget_usd": budget or None, "allowed_tools": tools,
                   "effort": ph.get("effort") or None,
                   "workers": workers}
            self.bus("orchestrator", "all", f"fase {i + 1}/{n} · {ph['name']} · {len(workers)} agent{'e' if len(workers) == 1 else 'i'}"
                     + (f" · {model}" if model else "")
                     + (f" · effort {ph['effort']}" if ph.get("effort") else "")
                     + (f" · fino a ${budget:g} per agente" if budget else ""), "info")
            core = Swarm(cfg, demo=self.demo)
            core.listeners.append(self._forward)
            self.core = core
            await core.start()
            await core.wait()
            res["cost"] = round(core.total_cost, 4)
            self.cost_done += core.total_cost
            res["errors"] = [w.label for w in core.agents if w.status == "error"]
            if self.stop_requested:
                res["status"] = "stopped"
                self.stage = "stopped"
                self.ended_at = time.time()
                self.bus("orchestrator", "you", "progetto fermato", "warn")
                await loop.run_in_executor(None, self._cleanup, core)
                return
            ok = [w for w in core.agents if w.status == "done"]
            if not ok:
                res["status"] = "failed"
                await loop.run_in_executor(None, self._cleanup, core)
                self._fail(f"fase {ph['name']}: nessun agente ha completato il lavoro, mi fermo qui")
                return
            if not self.demo:
                merged, conflicts = await loop.run_in_executor(None, self._merge, core)
            else:
                merged = [w.branch or f"swarm/{core.run_id}/{w.name}" for w in ok]
                conflicts = []
            res["merged"], res["conflicts"] = merged, conflicts
            await loop.run_in_executor(None, self._cleanup, core)
            if conflicts:
                carry = conflicts
                if i == n - 1:
                    self.bus("orchestrator", "you",
                             "conflitti nell'ultima fase: unisci a mano " + ", ".join(conflicts), "warn")
            res["status"] = "done"
            self.bus("orchestrator", "you",
                     f"fase {i + 1} completata · uniti {len(merged)} branch"
                     + (f" · {len(conflicts)} in conflitto" if conflicts else ""), "ok")
        self.final = await loop.run_in_executor(None, self._final_summary)
        self.stage = "done"
        await self.github_check()
        self.ended_at = time.time()
        self.bus("orchestrator", "you", f"progetto completato · costo stimato ${self.cost:.2f}", "ok")

    def _merge(self, core: Swarm):
        merged, conflicts = [], []
        for w in core.agents:
            if not w.committed:
                continue
            try:
                git(self.repo, *git_id(self.repo), "merge", "--no-ff", "--no-edit", "-m",
                    f"swarm: unisce {w.name} ({core.cfg['goal'].rsplit(' — ', 1)[-1]})", w.branch)
                merged.append(w.branch)
                self.bus("orchestrator", w.label, f"unito {w.branch}", "ok")
            except RuntimeError as e:
                git(self.repo, "merge", "--abort", check=False)
                conflicts.append(w.branch)
                self.bus("orchestrator", w.label, f"merge in conflitto, lo passo alla fase dopo: {short(str(e), 120)}", "warn")
        return merged, conflicts

    def _cleanup(self, core: Swarm) -> None:
        """Rimuove i worktree (i branch restano, così nulla va perso)."""
        if self.demo or not self.repo:
            return
        for w in core.agents:
            if w.cwd and w.cwd.exists():
                git(self.repo, "worktree", "remove", "--force", str(w.cwd), check=False)
        git(self.repo, "worktree", "prune", check=False)

    def _final_summary(self) -> dict:
        if self.demo:
            files = ["INTERFACCE.md", "README.md", "index.html", "src/dati/index.js", "src/dati/cache.js",
                     "src/ui/app.js", "src/ui/style.css", "tests/dati.test.js", "package.json"]
            return {"files": [{"status": "A", "path": f} for f in files],
                    "stat": f"{len(files)} files changed, 742 insertions(+)", "path": str(self.repo),
                    "start": {"cmds": "npm install\nnpm start", "url": "http://localhost:3000/",
                              "entry": "index.html", "readme": "README.md"}}
        names = git(self.repo, "diff", "--name-status", f"{self.base_commit}..HEAD", check=False)
        files = []
        for ln in names.splitlines():
            parts = ln.split("\t")
            if len(parts) >= 2:
                files.append({"status": parts[0][:1], "path": parts[-1]})
        stat = git(self.repo, "diff", "--shortstat", f"{self.base_commit}..HEAD", check=False)
        return {"files": files[:300], "stat": stat.strip(), "path": str(self.repo),
                "start": pub.start_info(self.repo)}

    # 3. a progetto finito: aprirlo e salvarlo su GitHub ----------------------
    def _finished_repo(self) -> Path:
        if self.stage != "done" or not self.repo:
            raise ValueError("il progetto non è ancora finito")
        return self.repo

    async def open_target(self, what: str, rel: str = "") -> None:
        if what == "projects":  # la cartella con tutti i progetti, sempre apribile
            PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
            await asyncio.get_running_loop().run_in_executor(None, pub.open_target, PROJECTS_DIR, "folder")
            return
        repo = self._finished_repo()
        if self.demo:
            raise ValueError("in demo non c'è niente da aprire: il progetto è finto")
        await asyncio.get_running_loop().run_in_executor(None, pub.open_target, repo, what, rel)

    async def github_check(self) -> None:
        repo = self._finished_repo()
        if self.demo:
            self.github = {"state": "ready", "user": "demo", "remote": "", "name": pub.repo_slug(repo.name),
                           "secrets": ["backend/.env"], "dirty": False, "url": "", "error": "", "demo": True}
            return
        try:
            self.github = await asyncio.get_running_loop().run_in_executor(None, pub.github_status, repo)
        except Exception as e:  # noqa: BLE001
            self.github = {"state": "noauth", "error": short(str(e), 300), "name": pub.repo_slug(repo.name),
                           "secrets": [], "remote": "", "user": "", "url": "", "dirty": False}

    def publish_github(self, d: dict) -> None:
        self._finished_repo()
        gh = self.github or {}
        if gh.get("state") == "working":
            raise ValueError("sto già caricando su GitHub")
        if gh.get("state") not in ("ready", "error", "done"):
            raise ValueError("GitHub non è pronto: controlla gh e il login")
        name = str(d.get("name") or "").strip()
        if not pub.REPO_NAME_RE.match(name):
            raise ValueError("nome del repository non valido: solo lettere, numeri, punto, trattino e _")
        opts = {"name": name, "private": d.get("private", True) is not False,
                "description": str(d.get("description") or "")[:300],
                "remove_secrets": bool(d.get("remove_secrets"))}
        if gh.get("secrets") and not opts["remove_secrets"] and not gh.get("remote"):
            raise ValueError("prima togli dal repository i file segreti: " + ", ".join(gh["secrets"]))
        self.github = {**gh, "state": "working", "error": ""}
        self._gh_task = asyncio.ensure_future(self._publish(opts))

    async def _publish(self, o: dict) -> None:
        where = "privato" if o["private"] else "pubblico"
        self.bus("orchestrator", "you", f"creo il repository GitHub {where} {o['name']} e carico il progetto")
        try:
            if self.demo:
                await asyncio.sleep(1.5)
                url = f"https://github.com/demo/{o['name']}"
            else:
                url = await asyncio.get_running_loop().run_in_executor(
                    None, pub.publish, self.repo, o["name"], o["private"], o["description"], o["remove_secrets"])
        except Exception as e:  # noqa: BLE001
            self.github = {**(self.github or {}), "state": "error", "error": short(str(e), 400)}
            self.bus("orchestrator", "you", "GitHub: " + short(str(e), 200), "err")
            return
        if not self.demo:
            await self.github_check()
        self.github = {**(self.github or {}), "state": "done", "url": url, "error": "",
                       "private": o["private"]}
        self.bus("orchestrator", "you", f"salvato su GitHub: {url}")

    # controllo --------------------------------------------------------------
    async def stop(self) -> None:
        self.stop_requested = True
        if self.stage == "planning":
            self._plan_gen += 1
            if self._planner_proc and self._planner_proc.returncode is None:
                self._planner_proc.terminate()
            elif self._task and not self._task.done():
                self._task.cancel()
            self._planner_proc = None
            # annullare una ripianificazione riporta al piano di prima, non al modulo vuoto
            if self._prev_plan:
                self.plan, self._prev_plan = self._prev_plan, None
                self.stage = "review"
            else:
                self.stage = "idle"
            self.bus("orchestrator", "you", "pianificazione annullata", "warn")
        elif self.stage == "running" and self.core:
            await self.core.stop_all()

    def reset(self) -> None:
        if self.stage in ("planning", "running"):
            raise ValueError("ferma prima quello che è in corso")
        listeners = self.listeners
        self._reset_state()
        self.listeners = listeners
        self.history.clear()
        self.messages = 0

    def back_to_form(self) -> None:
        """Torna al modulo dell'idea, tenendo le impostazioni (per correggere e riprovare)."""
        if self.stage in ("planning", "running"):
            raise ValueError("ferma prima quello che è in corso")
        self.stage = "idle"
        self.plan = None
        self.error = ""
        # via i dati della corsa precedente: altrimenti la nuova somma i costi vecchi,
        # mostra un tempo sbagliato (ended_at vecchio) e un fallimento del piano apre la pagina finale
        self.phases = []
        self.phase_idx = -1
        self.core = None
        self.final = None
        self.github = None
        self.cost_done = 0.0
        self.planner_cost = 0.0
        self.started_at = 0.0
        self.ended_at = 0.0

    async def shutdown(self) -> None:
        await self.stop()
        if self.core:
            await self.core.shutdown()
        if self._task and not self._task.done():
            self._task.cancel()
