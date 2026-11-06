#!/usr/bin/env python3
"""swarm — orchestratore di agenti Claude Code con dashboard.

Lancia N istanze di `claude -p` in parallelo, ognuna in un git worktree
separato, e mostra cosa fanno in tempo reale: nel terminale (default) oppure
in una pagina web locale (--web).

Uso:
    python swarm.py                       # studio web: dall'idea al progetto, a fasi
    python swarm.py --web --demo          # studio web con agenti finti (non consuma nulla)
    python swarm.py swarm.json            # esegue un config, dashboard nel terminale
    python swarm.py swarm.json --web      # esegue un config, dashboard nel browser
    python swarm.py --demo [--workers 4]  # demo nel terminale
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from swarm_core import MAX_WORKERS, Swarm, demo_config, load_config, short

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
FPS = 15

ORANGE = "#d97757"   # colore "Claude"
CYAN = "#5fd7ff"
GREEN = "#87d787"
RED = "#ff6b6b"
DIM = "#5c6370"
TEXT = "#c8ccd4"
BORDER = "#3e4451"
BORDER_ON = "#7c8494"

KIND_STYLE = {"info": TEXT, "tool": DIM, "msg": f"bold {TEXT}", "ok": GREEN,
              "err": RED, "warn": ORANGE}
PULSE_COLOR = {"assign": CYAN, "tool": ORANGE, "msg": ORANGE, "ok": GREEN, "err": RED}


def run_tui(core: Swarm) -> None:
    app = build_tui(core)
    app.run()
    if app.summary:
        print("\n".join(app.summary))


def build_tui(core: Swarm):
    # import qui: la modalità web non ha bisogno di Textual
    from rich.style import Style
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widget import Widget
    from textual.widgets import RichLog, Static

    class Pulse:
        __slots__ = ("worker", "outbound", "t", "color")

        def __init__(self, worker: int, outbound: bool, color: str) -> None:
            self.worker, self.outbound, self.t, self.color = worker, outbound, 0.0, color

    class StarCanvas(Widget):
        """Orchestratore al centro, worker intorno, linee e impulsi."""

        DEFAULT_CSS = "StarCanvas { height: 1fr; }"
        BOX_W = 26
        BOX_H = 4

        def __init__(self, app_: "SwarmApp") -> None:
            super().__init__()
            self.ui = app_
            self._styles: Dict[str, Style] = {}

        def st(self, spec: str) -> Style:
            s = self._styles.get(spec)
            if s is None:
                s = self._styles[spec] = Style.parse(spec)
            return s

        def layout_boxes(self, w: int, h: int):
            n = len(core.agents)
            bw, bh = self.BOX_W, self.BOX_H
            cx, cy = w // 2, h // 2
            center = (cx - bw // 2, cy - bh // 2)
            rx = max(0, w / 2 - bw / 2 - 1)
            ry = max(0, h / 2 - bh / 2 - 0.5)
            start = -90 - 180 / n
            boxes = []
            for i in range(n):
                a = math.radians(start + 360 * i / n)
                x = int(round(cx + rx * math.cos(a) - bw / 2))
                y = int(round(cy + ry * math.sin(a) - bh / 2))
                boxes.append((min(max(0, x), max(0, w - bw)), min(max(0, y), max(0, h - bh))))
            return center, boxes

        @staticmethod
        def line(x0: int, y0: int, x1: int, y1: int) -> List[Tuple[int, int]]:
            pts = []
            dx, dy = abs(x1 - x0), -abs(y1 - y0)
            sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
            err = dx + dy
            while True:
                pts.append((x0, y0))
                if x0 == x1 and y0 == y1:
                    break
                e2 = 2 * err
                if e2 >= dy:
                    err += dy
                    x0 += sx
                if e2 <= dx:
                    err += dx
                    y0 += sy
            return pts

        def render(self) -> Text:
            w, h = self.size.width, self.size.height
            if w < 10 or h < 6:
                return Text("")
            chars = [[" "] * w for _ in range(h)]
            styles: List[List[Optional[str]]] = [[None] * w for _ in range(h)]
            occupied = [[False] * w for _ in range(h)]

            def put(x, y, ch, style=None, force=False):
                if 0 <= x < w and 0 <= y < h and (force or not occupied[y][x]):
                    chars[y][x] = ch
                    styles[y][x] = style

            def text_at(x, y, s, style):
                for i, ch in enumerate(s):
                    put(x + i, y, ch, style, force=True)

            center, boxes = self.layout_boxes(w, h)
            bw, bh = self.BOX_W, self.BOX_H
            for (bx, by) in [center] + boxes:
                for yy in range(by, min(h, by + bh)):
                    for xx in range(bx, min(w, bx + bw)):
                        occupied[yy][xx] = True

            ccx, ccy = center[0] + bw // 2, center[1] + bh // 2
            paths = []
            for i, (bx, by) in enumerate(boxes):
                pts = [p for p in self.line(ccx, ccy, bx + bw // 2, by + bh // 2)
                       if 0 <= p[0] < w and 0 <= p[1] < h and not occupied[p[1]][p[0]]]
                paths.append(pts)
                col = BORDER_ON if core.agents[i].active else BORDER
                for j, (x, y) in enumerate(pts):
                    if j % 2 == 0:
                        put(x, y, "·", col)

            for p in self.ui.pulses:
                pts = paths[p.worker] if p.worker < len(paths) else []
                if len(pts) < 2:
                    continue
                t = p.t if p.outbound else 1 - p.t
                k = int(round(t * (len(pts) - 1)))
                trail = k - (1 if p.outbound else -1)
                if 0 <= trail < len(pts):
                    put(*pts[trail], "•", f"{p.color} dim")
                put(*pts[k], "●", f"bold {p.color}")

            spin = SPINNER[self.ui.frame // 2 % len(SPINNER)]

            def box(x, y, title, title_style, l1, l2, border):
                put(x, y, "╭", border, True)
                put(x + bw - 1, y, "╮", border, True)
                put(x, y + bh - 1, "╰", border, True)
                put(x + bw - 1, y + bh - 1, "╯", border, True)
                for xx in range(x + 1, x + bw - 1):
                    put(xx, y, "─", border, True)
                    put(xx, y + bh - 1, "─", border, True)
                for yy in range(y + 1, y + bh - 1):
                    put(x, yy, "│", border, True)
                    put(x + bw - 1, yy, "│", border, True)
                    for xx in range(x + 1, x + bw - 1):
                        put(xx, yy, " ", None, True)
                text_at(x + 2, y, f" {title} ", title_style)
                inner = bw - 4
                for row, segs in ((1, l1), (2, l2)):
                    cx_ = x + 2
                    for s, style in segs:
                        s = s[: max(0, x + 2 + inner - cx_)]
                        text_at(cx_, y + row, s, style)
                        cx_ += len(s)

            n = len(core.agents)
            n_done = sum(1 for wk in core.agents if wk.status in ("done", "error", "stopped"))
            if core.finished:
                state = [("✓ ", GREEN), (f"finito · {n_done}/{n}", TEXT)]
            else:
                state = [(f"{spin} ", CYAN), (f"leading · {n_done}/{n} chiusi", DIM)]
            box(center[0], center[1], "◆ orchestrator", f"bold {CYAN}",
                [("you", f"bold {TEXT}"), (f" · {n} workers", TEXT)], state,
                GREEN if core.finished else CYAN)

            for i, (bx, by) in enumerate(boxes):
                wk = core.agents[i]
                name = short(wk.name, 12).ljust(12)
                if wk.status == "done":
                    st_seg = ("✓ 100%", GREEN)
                elif wk.status == "error":
                    st_seg = ("✗ errore", RED)
                elif wk.status == "stopped":
                    st_seg = ("■ stop", RED)
                elif wk.status == "waiting":
                    st_seg = ("· attesa", DIM)
                else:
                    pct = wk.percent
                    st_seg = (f"{spin} {pct}%" if pct is not None else f"{spin} t{wk.turns}", CYAN)
                last = wk.last or ("in attesa…" if wk.status == "waiting" else "")
                if wk.status == "done" and wk.cost:
                    last = f"${wk.cost:.2f} · {wk.tools} tool"
                border = {"done": GREEN, "error": RED, "stopped": RED}.get(
                    wk.status, BORDER_ON if wk.active else BORDER)
                box(bx, by, f"✱ {wk.label}", f"bold {ORANGE}",
                    [(name, TEXT), st_seg], [(short(last, bw - 4), DIM)], border)

            out = Text()
            for y in range(h):
                row, srow = chars[y], styles[y]
                x = 0
                while x < w:
                    s = srow[x]
                    x2 = x
                    while x2 < w and srow[x2] == s:
                        x2 += 1
                    out.append("".join(row[x:x2]), self.st(s) if s else None)
                    x = x2
                if y < h - 1:
                    out.append("\n")
            return out

    class SwarmApp(App):
        CSS = f"""
        Screen {{ background: #1e2127; }}
        #bus-title {{ height: 1; color: {DIM}; padding: 0 1; }}
        #bus {{ height: 11; border: none; background: #1e2127; padding: 0 1;
                scrollbar-size: 0 0; }}
        #status {{ height: 1; color: {DIM}; padding: 0 1; background: #16181d; }}
        """
        BINDINGS = [
            Binding("q", "quit_swarm", "esci"),
            Binding("x", "kill_all", "ferma tutti"),
        ] + [Binding(str(i), f"detail({i})", show=False) for i in range(1, MAX_WORKERS + 1)]

        def __init__(self) -> None:
            super().__init__()
            self.pulses: List[Pulse] = []
            self.frame = 0
            self.summary: List[str] = []

        def compose(self) -> ComposeResult:
            self.canvas = StarCanvas(self)
            yield self.canvas
            yield Static(f"── message bus ── orchestrator ↔ {len(core.agents)} agenti " + "─" * 200,
                         id="bus-title")
            yield RichLog(id="bus", markup=False, wrap=False, max_lines=2000)
            yield Static("", id="status")

        async def on_mount(self) -> None:
            self.bus_log = self.query_one("#bus", RichLog)
            self.status_bar = self.query_one("#status", Static)
            core.listeners.append(self.on_core_event)
            self.set_interval(1 / FPS, self.tick)
            await core.start()

        def on_core_event(self, kind: str, p: dict) -> None:
            if kind == "pulse":
                if len(self.pulses) < 60:
                    self.pulses.append(Pulse(p["worker"], p["outbound"], PULSE_COLOR.get(p["kind"], ORANGE)))
            elif kind == "bus":
                src, dst = p["src"], p["dst"]
                line = Text()
                line.append(time.strftime("%H:%M:%S ", time.localtime(p["ts"])), DIM)
                line.append(src, f"bold {CYAN}" if src == "orchestrator" else f"bold {ORANGE}")
                if p["kind"] == "tool":
                    line.append("  ⚙ ", DIM)
                else:
                    line.append(" → ", DIM)
                    line.append(dst, CYAN if dst in ("orchestrator", "all", "you") else ORANGE)
                    line.append("  ")
                line.append(p["msg"], KIND_STYLE.get(p["kind"], TEXT))
                self.bus_log.write(line)

        def tick(self) -> None:
            self.frame += 1
            for p in self.pulses:
                p.t += 0.045
            self.pulses = [p for p in self.pulses if p.t <= 1.0]
            self.canvas.refresh()
            running = sum(1 for w in core.agents if w.active)
            done = sum(1 for w in core.agents if w.status == "done")
            bits = [f"{running} agenti al lavoro", f"{done}/{len(core.agents)} finiti",
                    f"{core.messages} messaggi sul bus", f"${core.total_cost:.2f}"]
            goal = short(core.cfg.get("goal", ""), 50)
            if goal:
                bits.insert(0, goal)
            self.status_bar.update(Text(" · ".join(bits) + "   │ q esci · x stop · 1-8 info", DIM))

        def action_detail(self, i: str) -> None:
            i = int(i) - 1
            if not 0 <= i < len(core.agents):
                return
            wk = core.agents[i]
            w = self.bus_log.write
            w(Text(f"┌─ {wk.label} · {wk.name} · {wk.status} · {wk.tools} tool · ${wk.cost:.2f}", ORANGE))
            w(Text(f"│ compito: {short(wk.task, 200)}", TEXT))
            if wk.branch:
                w(Text(f"│ branch:  {wk.branch}", DIM))
            for d, t in wk.steps:
                w(Text(f"│ {'[x]' if d else '[ ]'} {short(t, 120)}", GREEN if d else DIM))
            for ln in (wk.result_text or wk.last_text).splitlines()[:8]:
                w(Text(f"│ {short(ln, 200)}", DIM))
            w(Text("└─", ORANGE))

        async def action_kill_all(self) -> None:
            await core.stop_all()

        async def action_quit_swarm(self) -> None:
            await core.shutdown()
            self.summary = core.summary()
            self.exit()

    return SwarmApp()


def main() -> None:
    ap = argparse.ArgumentParser(description="Orchestratore di agenti Claude Code con dashboard.")
    ap.add_argument("config", nargs="?", help="file JSON con repo, goal e workers")
    ap.add_argument("--demo", action="store_true", help="dati finti, non lancia Claude")
    ap.add_argument("--workers", type=int, default=6, help="numero di worker in demo (1-8)")
    ap.add_argument("--web", action="store_true", help="dashboard nel browser invece che nel terminale")
    ap.add_argument("--port", type=int, default=8765, help="porta della dashboard web")
    ap.add_argument("--no-open", action="store_true", help="non aprire il browser da solo")
    args = ap.parse_args()

    # senza config: lo "studio" web, dove parti da un'idea e crei il progetto
    if not args.config and (args.web or not args.demo):
        from swarm_web import run_web
        run_web(None, args.demo, port=args.port, open_browser=not args.no_open)
        return
    if args.config:
        cfg, demo = load_config(Path(args.config)), False
    else:
        cfg, demo = demo_config(args.workers), True
    if args.web:
        from swarm_web import run_web
        run_web(cfg, demo, port=args.port, open_browser=not args.no_open)
    else:
        run_tui(Swarm(cfg, demo=demo))


if __name__ == "__main__":
    sys.exit(main())
