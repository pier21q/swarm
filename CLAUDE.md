# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

swarm orchestrates several Claude Code agents (`claude -p` subprocesses) working in parallel on a project, each in its own git worktree. A planner turns an idea into phases, the user reviews the plan in a local web dashboard, then phases run one after another and their branches get merged. All user-facing text (UI, prompts, bus messages, README, commit messages) is in Italian.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install textual   # one-time setup; textual is the only dependency (terminal UI)
.venv/bin/python swarm.py                    # web "studio" on http://localhost:8765 (real agents, spends money)
.venv/bin/python swarm.py --web --demo       # same flow with fake planner/agents: free, writes nothing to disk
.venv/bin/python swarm.py config.json --web  # run a hand-written single-phase plan (see swarm.example.json)
.venv/bin/python swarm.py --demo             # terminal demo dashboard
```

`.claude/launch.json` defines `swarm-demo` (port 8781) and `swarm-real` (port 8782) for the preview tools; both pass `--no-open`. If the port is taken the server picks the next free one.

Tests: `.venv/bin/python -m unittest discover tests` (standard library only, free). `tests/test_pipeline.py` runs a whole `Project` on a temporary git repo with `tests/fake_claude.py` standing in for `claude` (set via `SWARM_CLAUDE`); its task text drives it (`SCRIVI file=x`, `FALLISCI`). `tests/test_advice.py` also checks that `phaseModel`/`phaseBudget` in `web/index.html` match `swarm_advice.py`. No linter or build step. Verify UI changes by running the demo in the browser (desktop and 375px width, light and dark theme). Running against real agents costs money: ask first.

Environment variables: `SWARM_CLAUDE` (path to the `claude` binary), `SWARM_PROJECTS` (default projects folder, `~/Desktop/Progetti swarm`).

## Architecture

Layers, bottom to top:

- **`swarm_core.py` — `Swarm`**: runs one phase. For each worker it creates a worktree on branch `swarm/<run_id>/<name>` under `<repo-parent>/.<repo>-swarm/<run_id>/`, launches `claude -p --output-format stream-json --append-system-prompt WORKER_PROMPT ...` with the task on stdin (so a task starting with `-` isn't read as an option), parses stream-json events, and commits the worktree when the agent finishes (agents are told not to commit). An agent that errors or is stopped gets a "NON FINITO" commit on its branch (`save_unfinished`), never merged; `check_finished` waits for every agent to be `closed` so the project can't remove a worktree before that. Agents run in their own process group, and stopping one kills the whole group. An agent that closes without the final `result` event is flagged `cost_missing` (its real cost is unknown), and the UI shows totals as a minimum. Agents talk to the orchestrator through files in the swarm dir: `<label>.progress.md` (Markdown checklist → percent shown in the UI), `<label>.outbox` (lines `@claude-N msg` / `@all msg`, routed by the orchestrator), `<label>.inbox` (user and peer messages, which agents read after each plan step). `ENV_STRIP` removes the parent Claude Code session's env vars so workers start clean. `run_fake` simulates agents in demo mode.
- **`swarm_project.py` — `Project`**: the whole pipeline and its `stage` state machine (`idle → planning → review → running ⇄ paused → done/failed/stopped`; `paused` = a phase had agents in error, the user picks `/api/continue` or stop). In an existing project the work goes to a new branch `swarm/<stamp>` checked out in the repo (`_open_work_branch`); in a new project to its only branch. `bus()` may be called from `run_in_executor` threads: it hops back to the loop with `call_soon_threadsafe`. Runs the planner (`claude -p` with `PLANNER_PROMPT`, JSON output → `extract_json` → `validate_plan`), then for each phase builds a `Swarm` config (model, per-agent budget, effort, allowed tools) and merges successful branches into the repo with `--no-ff`. A conflicting merge is aborted and that branch is handed to the first agent of the next phase with extra merge tools. After each phase `_cleanup` removes worktrees and the run folder and runs `git branch -d` on the phase's branches (git keeps the unmerged ones: unfinished or conflicting). A worker counts as committed also when its branch only gained commits (e.g. the merge of a carried branch). Tool allowlists: `BASE_TOOLS` (read-only git), plus `BUILD_TOOLS` only if the user enables build/test, plus `MERGE_TOOLS` for conflict carry-over; `DENY_TOOLS` (`--disallowedTools`) always blocks `git … --output`. `_plan_gen` makes a cancelled planner that finishes late leave the new state alone.
- **`swarm_advice.py`**: pure rules for the difficulty × quality (1–3 × 1–3) matrix: agents per phase, effort, model, budget, and the brief given to the planner. Opus applies only to the `fondamenta`/`integrazione` roles; other phases drop to Sonnet with a proportionally lower budget (`phase_model`, `phase_budget`). `phase_role` infers a phase's role from its name and position.
- **`swarm_examples.py`**: per-cell example outcomes for the matrix, from a cheap Haiku call; `assemble()` builds the 9 cells from rows and columns.
- **`swarm_publish.py`**: `gh` integration (create repo and push, detect tracked secret files) and opening the finished project.
- **`swarm_web.py`**: a minimal HTTP server on raw asyncio streams (standard library only), bound to `127.0.0.1` on purpose: the dashboard has no auth and can spend money. Serves `web/index.html`, `/events` (SSE: `hello`, `state`, `bus`, `pulse`) and JSON POST endpoints (the list is in the module docstring). Every request must carry a `localhost`/`127.0.0.1` Host on the real port (DNS rebinding); POSTs also need the same origin and the per-start token the server injects into the page (`X-Swarm-Token`, sent by `api()` in the JS).
- **`web/index.html`**: the whole frontend in one file (CSS + vanilla JS, no build). Views follow `P.stage`. **`phaseModel`/`phaseBudget` in the JS mirror `swarm_advice.py`: change both together.**
- **`swarm.py`**: CLI entry point and the Textual terminal dashboard (used only with a config file or `--demo` without `--web`).

Events flow up through `listeners` callbacks: `Swarm.emit` → `Project._forward` → `WebDashboard._broadcast` → SSE clients.

## Conventions

- After a change, add an entry under "Fatto" in `DA_FARE.md`: what changed, file by file, and how it was tested (demo / real, desktop / 375px), stating plainly what wasn't tested. Update `README.md` when user-visible behaviour changes.
- Commit messages are in Italian: a short subject, then a bullet list of what changed.
- Demo mode must keep working for every feature (fake planner, fake agents, fake examples), since it's how changes are verified for free.
