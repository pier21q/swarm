"""swarm_core — il motore dell'orchestratore, senza interfaccia.

Gestisce i worker (processi `claude -p` o finti in modalità demo), legge i loro
eventi stream-json, i file di progress/outbox, fa i commit, e notifica chi è in
ascolto (interfaccia terminale o web) tramite `listeners`.

Eventi emessi ai listener: ("bus", entry) e ("pulse", {...}).
Tipi di messaggio sul bus (`kind`): info, tool, msg, ok, err, warn.
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Deque, List, Optional, Tuple

MAX_WORKERS = 8

# dove l'installer di Claude Code mette di solito il comando, se non è nel PATH
CLAUDE_PLACES = ("~/.local/bin/claude", "~/.claude/local/claude",
                 "/opt/homebrew/bin/claude", "/usr/local/bin/claude")


def find_bin(name: str, places=()) -> Optional[str]:
    """Percorso di un comando: prima il PATH, poi i posti soliti dove lo installano."""
    found = shutil.which(name)
    if found:
        return found
    for place in places:
        p = os.path.expanduser(place)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


GH_PLACES = ("~/.local/bin/gh", "/opt/homebrew/bin/gh", "/usr/local/bin/gh")


def find_claude(name: str = "claude") -> Optional[str]:
    """Percorso del comando claude: prima il PATH, poi i posti soliti."""
    name = os.path.expanduser(os.environ.get("SWARM_CLAUDE") or name or "claude")
    return find_bin(name, CLAUDE_PLACES if os.path.basename(name) == "claude" else ())


CLAUDE_MISSING = ("comando `claude` non trovato: installa Claude Code "
                  "(https://claude.com/claude-code) oppure indica il percorso con "
                  "SWARM_CLAUDE=/percorso/claude")

# variabili d'ambiente di una sessione Claude Code "madre" che non devono
# finire nei worker (se lanci swarm da dentro Claude Code)
ENV_STRIP = {
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_PID",
    "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_SESSION_ATTENDED",
}

WORKER_PROMPT = """\
Sei il worker "{label}" ({name}) di uno sciame di {n} agenti Claude coordinati
da un orchestratore. Lavori in parallelo agli altri.

Obiettivo generale del progetto: {goal}

La tua cartella di lavoro è {cwd} (git worktree, branch {branch}).
Gli altri worker e i loro compiti:
{others}

Regole:
1. Fai SOLO il tuo compito. Non modificare file che appartengono chiaramente
   al compito di un altro worker.
2. Appena hai capito il lavoro, scrivi il tuo piano come checklist Markdown nel
   file {progress} (una riga per passo: "- [ ] passo"). Man mano che completi
   un passo, aggiorna il file segnandolo "- [x]". L'orchestratore usa questo
   file per mostrare la tua percentuale di avanzamento.
3. Per mandare un messaggio agli altri agenti aggiungi una riga in fondo al
   file {outbox}, nel formato "@claude-N testo" (a un agente) oppure
   "@all testo" (a tutti). Messaggi brevi, solo quando servono davvero
   (interfacce condivise, nomi di funzioni, formati di dati, problemi).
   Puoi leggere progress e outbox degli altri nella cartella {swarm_dir}.
4. Il file {inbox} è la tua casella di posta: ci arrivano istruzioni
   dell'utente (righe "[you] ...") e messaggi degli altri agenti. Leggilo
   all'inizio e DOPO OGNI PASSO completato del piano. Le istruzioni di [you]
   hanno la priorità su tutto il resto: adatta il piano se serve.
5. Non fare git commit: ci pensa l'orchestratore quando hai finito.
6. Niente segreti nel repository: chiavi e password vanno in un file .env
   elencato in .gitignore; nel repository metti solo un .env.example con
   valori finti.
7. Quando hai finito, chiudi con un riepilogo di 2-3 righe di cosa hai fatto.
"""

DEMO_NAMES = ["billing", "queue", "ledger", "notify", "db", "search", "auth", "cache"]


# ─────────────────────────────────────────────────────────────── modello ──

@dataclass
class Worker:
    idx: int
    name: str
    task: str
    label: str
    cwd: Optional[Path] = None
    branch: str = ""
    progress_file: Optional[Path] = None
    outbox_file: Optional[Path] = None
    inbox_file: Optional[Path] = None
    status: str = "waiting"  # waiting|starting|running|done|error|stopped
    steps: List[Tuple[bool, str]] = field(default_factory=list)
    progress_from_file: bool = False
    turns: int = 0
    tools: int = 0
    cost: float = 0.0
    last: str = ""
    last_text: str = ""
    result_text: str = ""
    model: str = ""
    outbox_seen: int = 0
    started: float = 0.0
    ended: float = 0.0
    proc: Optional[asyncio.subprocess.Process] = None
    committed: Optional[bool] = None

    @property
    def active(self) -> bool:
        return self.status in ("starting", "running")

    @property
    def done_steps(self) -> int:
        return sum(1 for d, _ in self.steps if d)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def percent(self) -> Optional[int]:
        if self.status == "done":
            return 100
        if self.steps:
            return int(100 * self.done_steps / self.total_steps)
        return None

    def to_dict(self) -> dict:
        return {
            "idx": self.idx, "label": self.label, "name": self.name, "task": self.task,
            "status": self.status, "percent": self.percent,
            "done_steps": self.done_steps, "total_steps": self.total_steps,
            "steps": [{"done": d, "text": t} for d, t in self.steps],
            "turns": self.turns, "tools": self.tools, "cost": round(self.cost, 4),
            "last": self.last, "last_text": self.last_text[-2000:],
            "result_text": self.result_text[-4000:], "model": self.model,
            "branch": self.branch, "started": self.started, "ended": self.ended,
            "committed": self.committed,
        }


# ──────────────────────────────────────────────────────────────── utilità ──

def short(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe_tool(name: str, inp: dict) -> str:
    """Trasforma una tool_use in una descrizione breve tipo 'Edit lib.rs'."""
    inp = inp or {}
    path = inp.get("file_path") or inp.get("notebook_path") or inp.get("path")
    if name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit") and path:
        return f"{name} {Path(path).name}"
    if name == "Bash":
        return "$ " + short(inp.get("command", ""), 48)
    if name in ("Grep", "Glob"):
        return f"{name} {short(inp.get('pattern', ''), 40)}"
    if name in ("Task", "Agent"):
        return f"subagent · {short(inp.get('description', ''), 40)}"
    if name in ("WebFetch", "WebSearch"):
        return f"{name} {short(inp.get('url') or inp.get('query', ''), 40)}"
    return name


STEP_RE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.*)$")


def parse_checklist(text: str) -> List[Tuple[bool, str]]:
    out = []
    for line in text.splitlines():
        m = STEP_RE.match(line)
        if m:
            out.append((m.group(1).lower() == "x", m.group(2).strip()))
    return out


def git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def git_id(cwd: Path) -> List[str]:
    """Se l'utente non ha configurato nome/email in git, usa un'identità 'swarm'
    solo per i commit fatti dall'orchestratore (altrimenti git rifiuta il commit)."""
    if git(cwd, "config", "user.email", check=False) and git(cwd, "config", "user.name", check=False):
        return []
    return ["-c", "user.name=swarm", "-c", "user.email=swarm@localhost"]


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent
    cfg["repo"] = str((base / cfg.get("repo", ".")).resolve())
    ws = cfg.get("workers") or []
    if not ws:
        raise SystemExit("config: nessun worker in \"workers\"")
    if len(ws) > MAX_WORKERS:
        raise SystemExit(f"config: massimo {MAX_WORKERS} worker")
    for w in ws:
        if not w.get("name") or not w.get("task"):
            raise SystemExit("config: ogni worker deve avere \"name\" e \"task\"")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", w["name"]):
            raise SystemExit(f"config: nome non valido {w['name']!r} (solo lettere, numeri, . _ -)")
    names = [w["name"] for w in ws]
    if len(set(names)) != len(names):
        raise SystemExit("config: i nomi dei worker devono essere diversi")
    return cfg


def demo_config(n: int) -> dict:
    n = max(1, min(MAX_WORKERS, n))
    return {"goal": "porting in Rust", "workers": [
        {"name": DEMO_NAMES[i], "task": f"porta il modulo {DEMO_NAMES[i]} in Rust"} for i in range(n)]}


# ────────────────────────────────────────────────────────────────── motore ──

Listener = Callable[[str, dict], None]


class Swarm:
    def __init__(self, cfg: dict, demo: bool = False) -> None:
        self.claude: Optional[str] = None
        self.cfg = cfg
        self.demo = demo
        self.agents: List[Worker] = [
            Worker(idx=i, name=w["name"], task=w["task"], label=f"claude-{i + 1}")
            for i, w in enumerate(cfg["workers"])
        ]
        self.listeners: List[Listener] = []
        self.history: Deque[dict] = collections.deque(maxlen=1000)
        self.messages = 0
        self.finished = False
        self.repo: Optional[Path] = Path(cfg["repo"]) if cfg.get("repo") else None
        self.swarm_dir: Optional[Path] = None
        self.run_id = cfg.get("run_id") or time.strftime("%Y%m%d-%H%M%S")
        self.started_at = time.time()
        self._bus_file = None
        self._tasks: List[asyncio.Future] = []
        self._done: Optional[asyncio.Event] = None  # creato in start(), dentro il loop

    # notifiche -------------------------------------------------------------
    def emit(self, kind: str, payload: dict) -> None:
        for fn in list(self.listeners):
            try:
                fn(kind, payload)
            except Exception:  # noqa: BLE001  un listener rotto non ferma il motore
                pass

    def bus(self, src: str, dst: str, msg: str, kind: str = "info") -> None:
        if kind != "tool":
            self.messages += 1
        entry = {"ts": time.time(), "src": src, "dst": dst, "msg": msg, "kind": kind}
        self.history.append(entry)
        if self._bus_file:
            arrow = "  ⚙ " if kind == "tool" else f" → {dst}  "
            self._bus_file.write(time.strftime("%H:%M:%S ") + src + arrow + msg + "\n")
            self._bus_file.flush()
        self.emit("bus", entry)

    def pulse(self, wk: Worker, outbound: bool, kind: str) -> None:
        self.emit("pulse", {"worker": wk.idx, "outbound": outbound, "kind": kind})

    @property
    def total_cost(self) -> float:
        return sum(w.cost for w in self.agents)

    def snapshot(self) -> dict:
        return {
            "run_id": self.run_id, "demo": self.demo, "goal": self.cfg.get("goal", ""),
            "model": self.cfg.get("model", ""), "repo": str(self.repo or ""),
            "finished": self.finished, "messages": self.messages,
            "cost": round(self.total_cost, 4), "started_at": self.started_at,
            "agents": [w.to_dict() for w in self.agents],
        }

    # ciclo di vita ---------------------------------------------------------
    async def wait(self) -> None:
        """Aspetta che tutti i worker abbiano chiuso."""
        if self._done is None:
            self._done = asyncio.Event()
        if self.finished:
            return
        await self._done.wait()

    async def start(self) -> None:
        if self._done is None:
            self._done = asyncio.Event()
        if self.demo:
            self.bus("orchestrator", "all", f"modalità demo · {len(self.agents)} worker finti")
        else:
            try:
                self.prepare_run()
            except Exception as e:  # noqa: BLE001
                self.bus("orchestrator", "you", f"errore di setup: {e}", "err")
                for wk in self.agents:
                    wk.status = "error"
                self.finished = True
                self._done.set()
                return
        for wk in self.agents:
            coro = self.run_fake(wk) if self.demo else self.run_agent(wk)
            self._tasks.append(asyncio.ensure_future(coro))
        self._tasks.append(asyncio.ensure_future(self._poll_loop()))

    async def _poll_loop(self) -> None:
        while not self.finished:
            self.poll_files()
            await asyncio.sleep(1.0)

    async def stop_all(self) -> None:
        for wk in self.agents:
            if wk.status in ("waiting", "starting", "running"):
                wk.status = "stopped"
                if wk.proc and wk.proc.returncode is None:
                    try:
                        wk.proc.terminate()
                    except ProcessLookupError:
                        pass
                self.bus("orchestrator", wk.label, "fermato", "err")
        self.check_finished()

    async def shutdown(self) -> None:
        await self.stop_all()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        if self._bus_file:
            self._bus_file.close()
            self._bus_file = None

    def check_finished(self) -> None:
        if not self.finished and all(w.status in ("done", "error", "stopped") for w in self.agents):
            # aspetta anche i commit in corso, così chi fa wait() trova i branch pronti
            if any(w.status == "done" and w.committed is None and not self.demo for w in self.agents):
                return
            self.finished = True
            if self._done:
                self._done.set()
            self.bus("orchestrator", "you",
                     f"tutti i worker hanno chiuso · costo stimato ${self.total_cost:.2f}", "ok")

    # interazione -----------------------------------------------------------
    def send_message(self, idx: Optional[int], text: str, src: str = "you") -> None:
        """Manda un messaggio a un agente (idx) o a tutti (None), via inbox."""
        text = " ".join(text.split())[:1000]
        if not text:
            return
        targets = [w for w in self.agents if (idx is None or w.idx == idx) and w.active]
        if not targets:
            self.bus("orchestrator", "you", "nessun agente attivo a cui consegnare il messaggio", "warn")
            return
        dst = "all" if idx is None else targets[0].label
        self.bus(src, dst, text, "msg")
        for w in targets:
            self._deliver(w, f"[{src}] {text}")
            self.pulse(w, True, "msg")
            if self.demo:
                self._tasks.append(asyncio.ensure_future(self._fake_reply(w, text)))

    def _deliver(self, wk: Worker, line: str) -> None:
        if wk.inbox_file:
            with open(wk.inbox_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    async def stop_agent(self, idx: int) -> None:
        for wk in self.agents:
            if wk.idx == idx and wk.status in ("waiting", "starting", "running"):
                wk.status = "stopped"
                if wk.proc and wk.proc.returncode is None:
                    try:
                        wk.proc.terminate()
                    except ProcessLookupError:
                        pass
                self.bus("orchestrator", wk.label, "fermato dall'utente", "err")
        self.check_finished()

    def summary(self) -> List[str]:
        title = "demo (dati finti, nessun commit e nessuna spesa reale)" if self.demo \
            else f"costo stimato ${self.total_cost:.2f}"
        s = [f"\nswarm {self.run_id} · {title}\n"]
        for w in self.agents:
            extra = "" if self.demo else {True: "commit fatto", False: "nessuna modifica", None: ""}[w.committed]
            s.append(f"  {w.label:9} {w.name:14} {w.status:8} ${w.cost:5.2f}  {w.branch}  {extra}")
        if self.demo or not self.repo:
            return s
        ready = [w for w in self.agents if w.committed]
        if ready:
            s.append(f"\nPer unire il lavoro nel tuo branch attuale (da {self.repo}):")
            s += [f"  git merge {w.branch}" for w in ready]
        if any(w.cwd for w in self.agents):
            s.append("\nPer eliminare i worktree quando hai finito:")
            s += [f"  git worktree remove --force {w.cwd}" for w in self.agents if w.cwd]
        if self.swarm_dir:
            s.append(f"\nLog del bus: {self.swarm_dir / 'bus.log'}")
        return s

    # setup run reale -------------------------------------------------------
    def prepare_run(self) -> None:
        repo = self.repo
        if not repo or not (repo / ".git").exists():
            raise RuntimeError(f"{repo} non è un repository git (fai `git init` e un primo commit)")
        self.claude = find_claude(self.cfg.get("claude_bin", "claude"))
        if not self.claude:
            raise RuntimeError(CLAUDE_MISSING)
        git(repo, "rev-parse", "HEAD")  # serve almeno un commit
        if git(repo, "status", "--porcelain"):
            self.bus("orchestrator", "you",
                     "attenzione: ci sono modifiche non committate, i worker partono dall'ultimo commit",
                     "warn")
        self.swarm_dir = repo.parent / f".{repo.name}-swarm" / self.run_id
        (self.swarm_dir / "wt").mkdir(parents=True, exist_ok=True)
        self._bus_file = open(self.swarm_dir / "bus.log", "a", encoding="utf-8")
        for wk in self.agents:
            wk.branch = f"swarm/{self.run_id}/{wk.name}"
            wk.cwd = self.swarm_dir / "wt" / wk.name
            wk.progress_file = self.swarm_dir / f"{wk.label}.progress.md"
            wk.outbox_file = self.swarm_dir / f"{wk.label}.outbox"
            wk.outbox_file.touch()
            wk.inbox_file = self.swarm_dir / f"{wk.label}.inbox"
            wk.inbox_file.touch()
            git(repo, "worktree", "add", "-q", "-b", wk.branch, str(wk.cwd), "HEAD")
        self.bus("orchestrator", "all", f"creati {len(self.agents)} worktree in {self.swarm_dir}")

    def build_cmd(self, wk: Worker) -> List[str]:
        others = "\n".join(f"- {o.label} ({o.name}): {short(o.task, 160)}"
                           for o in self.agents if o is not wk) or "- nessuno"
        sys_prompt = WORKER_PROMPT.format(
            label=wk.label, name=wk.name, n=len(self.agents),
            goal=self.cfg.get("goal", "(non specificato)"), cwd=wk.cwd,
            branch=wk.branch, others=others, progress=wk.progress_file,
            outbox=wk.outbox_file, inbox=wk.inbox_file, swarm_dir=self.swarm_dir)
        cmd = [self.claude or find_claude(self.cfg.get("claude_bin", "claude")) or "claude", "-p", wk.task,
               "--output-format", "stream-json", "--verbose",
               "--permission-mode", self.cfg.get("permission_mode", "acceptEdits"),
               "--add-dir", str(self.swarm_dir),
               "--append-system-prompt", sys_prompt]
        if self.cfg.get("model"):
            cmd += ["--model", self.cfg["model"]]
        if self.cfg.get("max_budget_usd"):
            cmd += ["--max-budget-usd", str(self.cfg["max_budget_usd"])]
        if self.cfg.get("effort"):
            cmd += ["--effort", str(self.cfg["effort"])]
        if self.cfg.get("allowed_tools"):
            cmd += ["--allowedTools", *self.cfg["allowed_tools"]]
        return cmd

    async def run_agent(self, wk: Worker) -> None:
        wk.status = "starting"
        wk.started = time.time()
        self.bus("orchestrator", wk.label, f"assegna · {short(wk.task, 90)}")
        self.pulse(wk, True, "assign")
        env = {k: v for k, v in os.environ.items() if k not in ENV_STRIP}
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.build_cmd(wk), cwd=str(wk.cwd), env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=64 * 1024 * 1024)
        except Exception as e:  # noqa: BLE001
            wk.status = "error"
            self.bus(wk.label, "orchestrator", f"non parte: {e}", "err")
            self.check_finished()
            return
        wk.proc = proc
        err_task = asyncio.ensure_future(proc.stderr.read())
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            self.handle_event(wk, ev)
        rc = await proc.wait()
        stderr = (await err_task).decode("utf-8", "replace").strip()
        wk.ended = time.time()
        if wk.status not in ("done", "error", "stopped"):
            wk.status = "done" if rc == 0 else "error"
            if rc != 0:
                msg = stderr.splitlines()[-1] if stderr else f"exit code {rc}"
                self.bus(wk.label, "orchestrator", f"errore: {short(msg, 120)}", "err")
        self.poll_files()
        if wk.status == "done":
            await self.commit(wk)
        self.check_finished()

    async def commit(self, wk: Worker) -> None:
        def _do() -> bool:
            if not git(wk.cwd, "status", "--porcelain"):
                return False
            git(wk.cwd, "add", "-A")
            git(wk.cwd, *git_id(wk.cwd), "commit", "-q", "--no-verify", "-m",
                f"swarm({wk.name}): {short(wk.task, 60)}\n\n{wk.result_text[:2000]}")
            return True
        try:
            wk.committed = await asyncio.get_running_loop().run_in_executor(None, _do)
        except Exception as e:  # noqa: BLE001
            wk.committed = False
            self.bus("orchestrator", wk.label, f"commit fallito: {short(str(e), 200)}", "err")
            return
        if wk.committed:
            self.bus("orchestrator", wk.label, f"commit su {wk.branch}", "ok")
        else:
            self.bus("orchestrator", wk.label, "nessuna modifica da committare")

    # eventi stream-json ----------------------------------------------------
    def handle_event(self, wk: Worker, ev: dict) -> None:
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            wk.status = "running"
            wk.model = ev.get("model", "")
            self.bus(wk.label, "orchestrator", f"online · {wk.model or 'claude'}")
            self.pulse(wk, False, "tool")
        elif t == "assistant":
            if wk.status == "starting":
                wk.status = "running"
            wk.turns += 1
            for c in (ev.get("message") or {}).get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    self._tool_use(wk, c.get("name", "?"), c.get("input") or {})
                elif c.get("type") == "text" and c.get("text", "").strip():
                    wk.last_text = c["text"].strip()
        elif t == "result":
            wk.cost = float(ev.get("total_cost_usd") or 0)
            wk.result_text = str(ev.get("result") or "")
            ok = not ev.get("is_error") and ev.get("subtype", "success") == "success"
            if wk.status != "stopped":
                wk.status = "done" if ok else "error"
            first = wk.result_text.splitlines()[0] if wk.result_text else ev.get("subtype", "")
            self.bus(wk.label, "orchestrator", ("✓ " if ok else "✗ ") + short(first, 140),
                     "ok" if ok else "err")
            self.pulse(wk, False, "ok" if ok else "err")

    def _tool_use(self, wk: Worker, name: str, inp: dict) -> None:
        wk.tools += 1
        if name == "TodoWrite":
            if not wk.progress_from_file:
                wk.steps = [(x.get("status") == "completed", str(x.get("content", "")))
                            for x in inp.get("todos") or []]
            return
        # le scritture su progress/outbox sono "rumore": il bus le mostra già
        path = str(inp.get("file_path") or "")
        if self.swarm_dir and path.startswith(str(self.swarm_dir)) \
                and not path.startswith(str(self.swarm_dir / "wt")):
            return
        wk.last = describe_tool(name, inp)
        self.bus(wk.label, "tool", wk.last, "tool")
        self.pulse(wk, False, "tool")

    # file progress / outbox ------------------------------------------------
    def poll_files(self) -> None:
        for wk in self.agents:
            if wk.progress_file and wk.progress_file.exists():
                try:
                    steps = parse_checklist(wk.progress_file.read_text(encoding="utf-8"))
                except OSError:
                    steps = []
                if steps:
                    wk.steps = steps
                    wk.progress_from_file = True
            if wk.outbox_file and wk.outbox_file.exists():
                try:
                    lines = wk.outbox_file.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                for line in lines[wk.outbox_seen:]:
                    if line.strip():
                        self.agent_message(wk, line.strip())
                wk.outbox_seen = len(lines)

    def agent_message(self, wk: Worker, line: str) -> None:
        m = re.match(r"@(\S+)\s*:?\s*(.*)", line)
        dst, msg = (m.group(1), m.group(2)) if m else ("all", line)
        self.bus(wk.label, dst, msg, "msg")
        self.pulse(wk, False, "msg")
        for o in self.agents:
            if o is not wk and (dst == "all" or dst == o.label or dst == o.name):
                self._deliver(o, f"[{wk.label}] {msg}")
                self.pulse(o, True, "msg")

    # demo ------------------------------------------------------------------
    async def run_fake(self, wk: Worker) -> None:
        rnd = random.Random(wk.idx * 7919 + int(time.time()))
        await asyncio.sleep(0.4 + wk.idx * 0.5)
        if wk.status == "stopped":
            return
        wk.status = "starting"
        wk.started = time.time()
        self.bus("orchestrator", wk.label, f"assegna · {wk.task}")
        self.pulse(wk, True, "assign")
        await asyncio.sleep(0.8)
        demo_model = {"haiku": "claude-haiku-5-5", "opus": "claude-opus-5-5"}.get(self.cfg.get("model") or "", "claude-sonnet-5-5")
        self.handle_event(wk, {"type": "system", "subtype": "init", "model": demo_model})
        rust = "rust" in (self.cfg.get("goal", "") + wk.task).lower()
        if rust:
            plan = ["leggi il modulo esistente", "definisci i tipi", "porta la logica principale",
                    "gestione errori", "scrivi i test", "cargo clippy pulito", "aggiorna la doc"]
            files = [f"{wk.name}/mod.rs", f"{wk.name}/types.rs", f"{wk.name}/tests.rs", "Cargo.toml", "lib.rs"]
            cmds = ["cargo check", "cargo test -p " + wk.name, "cargo clippy -- -D warnings",
                    "rg 'fn ' src/" + wk.name]
        else:
            plan = ["leggi INTERFACCE.md e il codice esistente", "prepara la struttura dei file",
                    "scrivi la logica principale", "collega le parti", "gestisci gli errori",
                    "aggiungi i test", "rifinisci e documenta"]
            files = [f"{wk.name}.js", f"{wk.name}.css", f"{wk.name}.test.js", "index.html",
                     "INTERFACCE.md", "package.json"]
            cmds = ["npm test", "npm run build", "ls src", "git diff --stat"]
        steps = rnd.randint(4, 7)
        todos = [{"content": plan[k], "status": "pending"} for k in range(steps)]
        others = [o for o in self.agents if o is not wk]
        chat = [
            "ho cambiato la firma di {x}::new(), ora prende &Config",
            "mi serve lo schema della tabella {x} prima di andare avanti",
            "ho esposto pub fn {x}_id() nel modulo comune",
            "i test di {x} rompono su main, ci guardi?",
            "ok, mi adeguo alla tua interfaccia",
        ] if rust else [
            "ho aggiunto get{X}() in INTERFACCE.md, usala da lì",
            "che formato hanno i dati che passi a {x}? mi serve un esempio",
            "ho rinominato il file {x}.js, aggiorna l'import",
            "i test di {x} falliscono sul mio branch, ci guardi?",
            "ok, mi adeguo alla tua interfaccia",
        ]
        self.handle_event(wk, {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "TodoWrite", "input": {"todos": todos}}]}})
        for k in range(steps):
            for _ in range(rnd.randint(2, 4)):
                await asyncio.sleep(rnd.uniform(0.4, 1.6))
                if wk.status == "stopped":
                    return
                tool = rnd.choice(["Read", "Edit", "Edit", "Bash", "Grep", "Write"])
                if tool == "Bash":
                    inp = {"command": rnd.choice(cmds)}
                elif tool == "Grep":
                    inp = {"pattern": rnd.choice(["impl ", "TODO", "unwrap()", "pub struct"])}
                else:
                    inp = {"file_path": "/src/" + rnd.choice(files)}
                self.handle_event(wk, {"type": "assistant", "message": {
                    "content": [{"type": "tool_use", "name": tool, "input": inp}]}})
            todos[k]["status"] = "completed"
            self.handle_event(wk, {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "TodoWrite", "input": {"todos": todos}}]}})
            if others and rnd.random() < 0.35:
                o = rnd.choice(others)
                self.agent_message(wk, f"@{o.label} " + rnd.choice(chat).format(
                    x=o.name, X=o.name[:1].upper() + o.name[1:]))
        await asyncio.sleep(rnd.uniform(0.5, 1.5))
        if wk.status == "stopped":
            return
        fail = rnd.random() < 0.1
        wk.ended = time.time()
        self.handle_event(wk, {
            "type": "result", "subtype": "error_during_execution" if fail else "success",
            "is_error": fail, "total_cost_usd": rnd.uniform(0.08, 0.6),
            "result": (f"Completato: {short(wk.task, 80)}\n"
                       f"Modificati {rnd.randint(3, 9)} file, aggiunti {rnd.randint(6, 20)} test, tutti verdi.")
            if not fail else f"i test di {wk.name} falliscono, serve una mano"})
        wk.committed = not fail
        self.check_finished()

    async def _fake_reply(self, wk: Worker, text: str) -> None:
        await asyncio.sleep(random.uniform(1.0, 2.5))
        if wk.active:
            self.agent_message(wk, f"@you ricevuto, mi adeguo: {short(text, 60)}")
