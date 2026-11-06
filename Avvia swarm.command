#!/bin/zsh
# Doppio clic per avviare swarm: si apre il browser con la dashboard.
# Per chiudere: Ctrl-C in questa finestra (o chiudi la finestra).

cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"

if [ ! -x .venv/bin/python ]; then
  echo "prima volta: preparo l'ambiente…"
  python3 -m venv .venv && .venv/bin/pip install -q textual || {
    echo "installazione non riuscita"; read -k1 "?premi un tasto per chiudere"; exit 1; }
fi

.venv/bin/python swarm.py
