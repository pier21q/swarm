"""swarm_web — dashboard web locale per swarm.

Server HTTP minimale (solo libreria standard), solo su 127.0.0.1:
  GET  /                  la pagina (web/index.html)
  GET  /events            Server-Sent Events: hello, state, bus, pulse
  GET  /api/state         stato attuale in JSON
  GET  /api/advice        tabella dei consigli (agenti, effort, modello)
  POST /api/plan          {idea, path, model, budget, max_workers, allow_build,
                           difficulty, quality}
  POST /api/replan        {feedback}
  POST /api/examples      {idea}  cosa aspettarsi da ognuna delle 9 caselle della matrice
  POST /api/start         {plan}
  POST /api/stop          ferma pianificazione o agenti
  POST /api/agent/stop    {idx}
  POST /api/message       {idx | null, text}
  POST /api/open          {what: folder|terminal|file, rel}  apre il progetto finito
  POST /api/github/check  ricontrolla gh, login e file segreti
  POST /api/github        {name, private, description, remove_secrets}  crea il repo e carica
  POST /api/form          torna al modulo dell'idea
  POST /api/reset         nuovo progetto da zero
Tutte le POST accettano solo richieste dalla pagina stessa (stessa origine).
"""
from __future__ import annotations

import asyncio
import json
import signal
import webbrowser
from pathlib import Path
from typing import Dict, Optional, Set

from swarm_advice import advice_table
from swarm_examples import fake_examples, real_examples
from swarm_project import Project

WEB_DIR = Path(__file__).resolve().parent / "web"
STATE_EVERY = 0.7  # secondi tra un aggiornamento di stato e l'altro


class WebDashboard:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.clients: Set[asyncio.Queue] = set()
        self.writers: Set[asyncio.StreamWriter] = set()
        self.closing = False
        self.examples: Dict[str, dict] = {}  # idea → esempi (si rifà la richiesta solo se l'idea cambia)
        self._examples_lock = asyncio.Lock()
        project.listeners.append(self._broadcast)

    def state(self) -> dict:
        p = self.project
        return {"project": p.snapshot(), "core": p.core.snapshot() if p.core else None}

    def _hello(self) -> dict:
        return {**self.state(), "history": list(self.project.history)}

    def _broadcast(self, kind: str, payload: dict) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait((kind, payload))
            except asyncio.QueueFull:
                pass

    def close_clients(self) -> None:
        """Chiude le connessioni live dei browser, così il processo può uscire."""
        self.closing = True
        for q in list(self.clients):
            try:
                q.put_nowait((None, None))
            except asyncio.QueueFull:
                pass
        for w in list(self.writers):
            if not w.is_closing():
                w.close()

    # http -----------------------------------------------------------------
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, _ = lines[0].split(" ", 2)
        except ValueError:
            writer.close()
            return
        headers = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        body = b""
        length = int(headers.get("content-length") or 0)
        if length:
            if length > 1 << 20:
                writer.close()
                return
            body = await reader.readexactly(length)
        path = target.split("?", 1)[0]

        try:
            if method == "GET" and path in ("/", "/index.html"):
                await self._send(writer, 200, (WEB_DIR / "index.html").read_bytes(),
                                 "text/html; charset=utf-8")
            elif method == "GET" and path == "/events":
                await self._sse(writer)
                return
            elif method == "GET" and path == "/api/state":
                await self._send_json(writer, self.state())
            elif method == "GET" and path == "/api/advice":
                await self._send_json(writer, advice_table())
            elif method == "POST" and path.startswith("/api/"):
                if not self._same_origin(headers):
                    await self._send_json(writer, {"ok": False, "error": "richiesta non permessa"}, 403)
                    return
                try:
                    data = json.loads(body or b"{}")
                    if not isinstance(data, dict):
                        raise ValueError("corpo non valido")
                    extra = await self._api(path, data)
                except Exception as e:  # noqa: BLE001  (errori mostrati nella pagina)
                    await self._send_json(writer, {"ok": False, "error": str(e)}, 400)
                else:
                    await self._send_json(writer, {"ok": True, **(extra or {})})
            else:
                await self._send(writer, 404, b"not found", "text/plain")
        except ConnectionError:
            pass
        finally:
            if not writer.is_closing():
                writer.close()

    async def _api(self, path: str, d: dict) -> Optional[dict]:
        p = self.project
        if path == "/api/examples":
            return await self._examples(str(d.get("idea") or ""))
        if path == "/api/plan":
            p.begin_plan(d)
        elif path == "/api/replan":
            fb = str(d.get("feedback") or "").strip()
            if not fb:
                raise ValueError("scrivi cosa cambiare nel piano")
            p.begin_plan(p.settings, feedback=fb)
        elif path == "/api/start":
            p.start(d.get("plan"))
        elif path == "/api/stop":
            await p.stop()
        elif path == "/api/agent/stop":
            if p.stage != "running" or not p.core:
                raise ValueError("nessun agente al lavoro")
            await p.core.stop_agent(int(d["idx"]))
        elif path == "/api/message":
            if p.stage != "running" or not p.core:
                raise ValueError("nessun agente al lavoro")
            idx = d.get("idx")
            p.core.send_message(None if idx is None else int(idx), str(d.get("text") or ""))
        elif path == "/api/open":
            await p.open_target(str(d.get("what") or ""), str(d.get("rel") or ""))
        elif path == "/api/github/check":
            await p.github_check()
        elif path == "/api/github":
            p.publish_github(d)
        elif path == "/api/form":
            p.back_to_form()
        elif path == "/api/reset":
            p.reset()
        else:
            raise ValueError("azione sconosciuta")
        return None

    async def _examples(self, idea: str) -> dict:
        idea = " ".join(idea.split())
        if len(idea) < 8:
            raise ValueError("scrivi prima l'idea, anche in poche parole")
        if len(idea) > 2000:
            raise ValueError("l'idea è troppo lunga per gli esempi: accorciala un po'")
        async with self._examples_lock:  # una richiesta alla volta: le altre aspettano e usano la cache
            hit = self.examples.get(idea)
            if hit:
                return {**hit, "cached": True}
            p = self.project
            res = await (fake_examples(idea) if p.demo else real_examples(idea, p.claude_bin))
            if len(self.examples) > 30:
                self.examples.clear()
            self.examples[idea] = res
            return {**res, "cached": False}

    @staticmethod
    def _same_origin(headers: dict) -> bool:
        origin = headers.get("origin", "")
        host = headers.get("host", "")
        return bool(host) and origin == f"http://{host}"

    @staticmethod
    async def _send(writer, status: int, body: bytes, ctype: str) -> None:
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found"}.get(status, "OK")
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
            f"Connection: close\r\n\r\n".encode() + body)
        await writer.drain()

    async def _send_json(self, writer, obj, status: int = 200) -> None:
        await self._send(writer, status, json.dumps(obj).encode(), "application/json")

    async def _sse(self, writer: asyncio.StreamWriter) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                     b"Cache-Control: no-store\r\nConnection: keep-alive\r\n\r\n")
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.clients.add(q)
        self.writers.add(writer)

        def frame(kind: str, payload) -> bytes:
            return f"event: {kind}\ndata: {json.dumps(payload)}\n\n".encode()

        loop = asyncio.get_running_loop()
        try:
            writer.write(frame("hello", self._hello()))
            await writer.drain()
            last_state = loop.time()
            while not self.closing and not writer.is_closing():
                try:
                    kind, payload = await asyncio.wait_for(q.get(), timeout=STATE_EVERY)
                except asyncio.TimeoutError:
                    kind = payload = None
                else:
                    if kind is None:  # segnale di chiusura
                        break
                    writer.write(frame(kind, payload))
                if loop.time() - last_state >= STATE_EVERY:
                    writer.write(frame("state", self.state()))
                    last_state = loop.time()
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            self.clients.discard(q)
            self.writers.discard(writer)
            if not writer.is_closing():
                writer.close()


async def _main(project: Project, cfg: Optional[dict], port: int, open_browser: bool) -> None:
    dash = WebDashboard(project)
    server = None
    for p in range(port, port + 20):
        try:
            server = await asyncio.start_server(dash.handle, "127.0.0.1", p)
        except OSError:
            continue
        if p != port:
            print(f"porta {port} occupata, uso la {p}")
        port = p
        break
    if server is None:
        raise SystemExit(f"nessuna porta libera tra {port} e {port + 19}: prova con --port")
    url = f"http://localhost:{port}"
    print(f"swarm · dashboard su {url}  (Ctrl-C per fermare tutto e uscire)", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    if cfg:
        project.start_from_config(cfg)
    if open_browser:
        loop.call_later(0.5, webbrowser.open, url)
    await stop.wait()
    print("\nchiusura: fermo gli agenti…")
    server.close()
    dash.close_clients()
    await project.shutdown()
    try:
        await asyncio.wait_for(server.wait_closed(), timeout=2)
    except asyncio.TimeoutError:
        pass
    if project.core:
        print("\n".join(project.core.summary()))
    if project.stage == "done" and project.final:
        print(f"\nprogetto pronto in {project.final.get('path')}")


def run_web(cfg: Optional[dict], demo: bool, port: int = 8765, open_browser: bool = True) -> None:
    """cfg = None → studio (nuovo progetto dalla pagina); cfg → esegue subito quel config."""
    project = Project(demo=demo, claude_bin=(cfg or {}).get("claude_bin", "claude"))
    try:
        asyncio.run(_main(project, cfg, port, open_browser))
    except KeyboardInterrupt:
        pass
