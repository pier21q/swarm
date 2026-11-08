#!/usr/bin/env python3
"""Finto `claude -p` per i test: legge il compito da stdin e lo esegue alla lettera.

Nel compito:
  SCRIVI file=contenuto   scrive il file nel worktree (anche più volte)
  FALLISCI                dopo le scritture esce con codice 1, senza l'evento finale
  "per conflitti: a, b."  (testo che swarm aggiunge) unisce quei branch con git merge -X theirs
Salva argv e stdin in $FAKE_CLAUDE_LOG/<nome del worktree>.json.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

task = sys.stdin.read()
cwd = Path.cwd()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    Path(log, cwd.name + ".json").write_text(json.dumps({"argv": sys.argv[1:], "stdin": task}), encoding="utf-8")

print(json.dumps({"type": "system", "subtype": "init", "model": "finto"}), flush=True)
m = re.search(r"per conflitti: (.+?)\. Uniscili", task)
if m:
    for b in [x.strip() for x in m.group(1).split(",")]:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "merge", "-q", "--no-edit",
                        "-X", "theirs", b], check=True)
for name, content in re.findall(r"SCRIVI (\S+?)=(\S+)", task):
    (cwd / name).write_text(content + "\n", encoding="utf-8")
if "FALLISCI" in task:
    sys.exit(1)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 0.01, "result": "fatto"}), flush=True)
