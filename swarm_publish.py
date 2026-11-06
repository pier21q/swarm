"""swarm_publish — a progetto finito: aprirlo, capire come avviarlo, salvarlo su GitHub.

Tutto sincrono (da chiamare in un executor): usa git e il comando `gh`.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from swarm_core import GH_PLACES, find_bin, git, git_id

REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
SECRET_RE = re.compile(r"(^|/)(\.env(\.[^/]+)?|id_rsa|id_ed25519|[^/]+\.(pem|p12|pfx|key))$", re.I)
SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".pub")
ENTRY_CANDIDATES = ("index.html", "frontend/index.html", "public/index.html", "web/index.html",
                    "dist/index.html", "src/index.html", "docs/index.html")
START_HEAD_RE = re.compile(r"^#{1,4}\s.*(avvi|start|esegu|lanci|run|usa|uso|install)", re.I | re.M)
CODE_RE = re.compile(r"```(?:bash|sh|shell|zsh|console)?[ \t]*\n(.*?)```", re.S)
URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/[^\s)`>\]\"'*]*)?")


def repo_slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name or "").strip("-.")
    return s[:100] or "progetto"


def tracked_secrets(repo: Path) -> List[str]:
    files = git(repo, "ls-files", check=False).splitlines()
    return [f for f in files if SECRET_RE.search(f) and not f.lower().endswith(SAFE_SUFFIXES)]


def start_info(repo: Path) -> dict:
    """Dal README: i comandi per avviarlo e l'indirizzo da aprire. Più un index.html, se c'è."""
    out: dict = {"cmds": "", "url": "", "entry": "", "readme": ""}
    for name in ("README.md", "README.MD", "Readme.md", "README.txt", "README"):
        if (repo / name).is_file():
            out["readme"] = name
            text = (repo / name).read_text("utf-8", "replace")[:60000]
            m = START_HEAD_RE.search(text)
            block = CODE_RE.search(text, m.end()) if m else None
            block = block or CODE_RE.search(text)
            if block:
                out["cmds"] = block.group(1).strip()[:600]
            u = URL_RE.search(text)
            if u:
                out["url"] = u.group(0).rstrip(".,;:")
            break
    for c in ENTRY_CANDIDATES:
        if (repo / c).is_file():
            out["entry"] = c
            break
    return out


def open_target(repo: Path, what: str, rel: str = "") -> None:
    if not repo or not repo.is_dir():
        raise ValueError("la cartella del progetto non esiste più")
    mac = sys.platform == "darwin"
    opener = "open" if mac else "xdg-open"
    if what == "folder":
        cmd = [opener, str(repo)]
    elif what == "terminal":
        if not mac:
            raise ValueError("su questo sistema apri il terminale a mano nella cartella")
        cmd = ["open", "-a", "Terminal", str(repo)]
    elif what == "file":
        target = (repo / rel).resolve()
        if repo.resolve() not in target.parents or not target.is_file():
            raise ValueError("file non valido")
        cmd = [opener, str(target)]
    else:
        raise ValueError("cosa devo aprire?")
    subprocess.run(cmd, check=True, timeout=15, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _gh(args: List[str], cwd: Optional[Path] = None, timeout: int = 60) -> subprocess.CompletedProcess:
    gh = find_bin("gh", GH_PLACES)
    if not gh:
        raise RuntimeError("comando `gh` non trovato")
    return subprocess.run([gh, *args], cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def github_status(repo: Path) -> dict:
    st: dict = {"state": "ready", "user": "", "remote": "", "name": repo_slug(repo.name),
                "secrets": tracked_secrets(repo), "dirty": bool(git(repo, "status", "--porcelain", check=False)),
                "url": "", "error": ""}
    st["remote"] = git(repo, "remote", "get-url", "origin", check=False).strip()
    if not find_bin("gh", GH_PLACES):
        st["state"] = "missing"
        return st
    try:
        r = _gh(["api", "user", "--jq", ".login"], timeout=20)
    except (subprocess.SubprocessError, OSError) as e:
        st["state"], st["error"] = "noauth", str(e)[:200]
        return st
    if r.returncode != 0:
        st["state"], st["error"] = "noauth", (r.stderr or r.stdout).strip()[:300]
        return st
    st["user"] = r.stdout.strip()
    return st


def untrack_secrets(repo: Path, files: List[str]) -> None:
    """Toglie i file segreti dal repository (restano sul disco) e li mette in .gitignore."""
    gi = repo / ".gitignore"
    old = gi.read_text("utf-8", "replace") if gi.exists() else ""
    lines = set(old.splitlines())
    add = [f for f in files if f not in lines and "/" + f not in lines]
    if add:
        sep = "" if not old or old.endswith("\n") else "\n"
        gi.write_text(old + sep + "\n# segreti, tolti da swarm prima di caricare su GitHub\n"
                      + "\n".join(add) + "\n", "utf-8")
    git(repo, "rm", "--cached", "--quiet", "--", *files)
    git(repo, "add", ".gitignore")
    git(repo, *git_id(repo), "commit", "--no-verify", "-q", "-m",
        "toglie i file segreti dal repository (restano in locale, in .gitignore)")


def publish(repo: Path, name: str, private: bool, description: str, remove_secrets: bool) -> str:
    """Crea il repo su GitHub (o usa origin se c'è già) e carica il branch corrente. Ritorna l'URL."""
    if not REPO_NAME_RE.match(name or ""):
        raise ValueError("nome del repository non valido: solo lettere, numeri, punto, trattino e _")
    secrets = tracked_secrets(repo)
    if secrets:
        if not remove_secrets:
            raise ValueError("nel repository ci sono file segreti (" + ", ".join(secrets)
                             + "): toglili prima di caricare")
        untrack_secrets(repo, secrets)
    remote = git(repo, "remote", "get-url", "origin", check=False).strip()
    if remote and "github.com" not in remote:
        raise ValueError(f"il progetto ha già un remote origin che non è GitHub ({remote})")
    if remote:
        r = subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=str(repo), capture_output=True,
                           text=True, timeout=180, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or "push non riuscito")
    else:
        args = ["repo", "create", name, "--private" if private else "--public",
                "--source", str(repo), "--remote", "origin", "--push"]
        if description.strip():
            args += ["--description", description.strip()[:300]]
        r = _gh(args, cwd=repo, timeout=180)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or "creazione del repository non riuscita")
    v = _gh(["repo", "view", "--json", "url", "--jq", ".url"], cwd=repo, timeout=30)
    url = v.stdout.strip() if v.returncode == 0 else ""
    if not url:
        url = git(repo, "remote", "get-url", "origin", check=False).strip()
        url = re.sub(r"^git@github\.com:", "https://github.com/", url)
        url = url[:-4] if url.endswith(".git") else url
    return url
