"""Pipeline vera su un repository temporaneo, con un finto `claude` (tests/fake_claude.py).

Copre: branch di lavoro nuovo in un progetto esistente, merge e conflitti, pausa quando un
agente va in errore e ripresa, branch in conflitto unito dall'agente dopo (solo il commit di
merge), pulizia di branch e cartelle, compito passato su stdin, regola che vieta --output,
messaggi del bus consegnati dal thread del loop.
"""
import asyncio
import json
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import swarm_project as sp

FAKE = Path(__file__).resolve().parent / "fake_claude.py"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def branches(repo):
    return set(git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split())


class Pipeline(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / "proj"
        self.log = self.root / "log"
        self.log.mkdir()
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("# proj\n")
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "inizio")
        self.start_commit = git(self.repo, "rev-parse", "HEAD")
        FAKE.chmod(FAKE.stat().st_mode | stat.S_IXUSR)
        env = mock.patch.dict(os.environ, {"SWARM_CLAUDE": str(FAKE), "FAKE_CLAUDE_LOG": str(self.log)})
        env.start()
        self.addCleanup(env.stop)
        gh = mock.patch.object(sp.pub, "github_status", return_value={"state": "noauth"})
        gh.start()
        self.addCleanup(gh.stop)

    async def until(self, p, stages, timeout=40):
        t = time.time()
        while p.stage not in stages:
            if time.time() - t > timeout:
                self.fail(f"bloccato in {p.stage}")
            await asyncio.sleep(0.1)

    def test_pausa_ripresa_conflitti_pulizia(self):
        asyncio.run(self._run())

    async def _run(self):
        p = sp.Project(demo=False)
        threads = set()
        p.listeners.append(lambda kind, payload: threads.add(threading.get_ident()))
        p._loop = asyncio.get_running_loop()
        p.settings = {"idea": "progetto di prova per i test", "path": str(self.repo), "model": "",
                      "budget": 0, "max_workers": 3, "allow_build": False}
        p.repo = self.repo
        await asyncio.get_running_loop().run_in_executor(None, p._prepare_repo)
        self.assertEqual((p.repo_state, p.base_branch), ("progetto esistente", "main"))
        p.plan = {"goal": "prova", "phases": [
            {"name": "Sviluppo", "workers": [
                {"name": "a", "task": "SCRIVI shared.txt=A SCRIVI a.txt=a"},
                {"name": "b", "task": "SCRIVI shared.txt=B SCRIVI b.txt=b"},
                {"name": "c", "task": "-x inizia con un trattino SCRIVI c.txt=c FALLISCI"}]},
            {"name": "Integrazione", "workers": [{"name": "unisci", "task": "unisci il lavoro e basta"}]}]}
        p.stage = "review"
        p.start()
        await self.until(p, ("paused", "done", "failed", "stopped"))
        self.assertEqual(p.stage, "paused", p.error)

        run = p._stamp + "-f1"
        ph = p.phases[0]
        self.assertEqual(ph["merged"], [f"swarm/{run}/a"])
        self.assertEqual(ph["conflicts"], [f"swarm/{run}/b"])
        self.assertEqual(ph["errors"], ["claude-3"])
        self.assertEqual(ph["saved"], [f"swarm/{run}/c"])
        self.assertEqual(p.phases[1]["status"], "pending")
        # il branch dell'utente non si tocca: il lavoro va in swarm/<corsa>
        self.assertEqual(p.work_branch, f"swarm/{p._stamp}")
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), p.work_branch)
        self.assertEqual(git(self.repo, "rev-parse", "main"), self.start_commit)
        # il branch unito è tolto, restano quello in conflitto e quello non finito
        self.assertEqual(branches(self.repo) & {f"swarm/{run}/{n}" for n in "abc"},
                         {f"swarm/{run}/b", f"swarm/{run}/c"})
        self.assertFalse((self.root / ".proj-swarm" / run).exists())
        # compito su stdin, anche se inizia con "-"; --output vietato
        c = json.loads((self.log / "c.json").read_text())
        self.assertTrue(c["stdin"].startswith("-x inizia"))
        self.assertFalse(any("trattino" in a for a in c["argv"]))
        i = c["argv"].index("--disallowedTools")
        self.assertEqual(c["argv"][i + 1], "Bash(git * --output*)")

        p.resume()
        await self.until(p, ("paused", "done", "failed", "stopped"))
        self.assertEqual(p.stage, "done", p.error)
        ph2 = p.phases[1]
        # l'agente ha solo fatto il merge del branch in conflitto: conta come lavoro da unire
        self.assertEqual(ph2["merged"], [f"swarm/{p._stamp}-f2/unisci"])
        self.assertEqual((self.repo / "shared.txt").read_text(), "B\n")
        self.assertTrue((self.repo / "a.txt").exists() and (self.repo / "b.txt").exists())
        self.assertFalse((self.repo / "c.txt").exists())
        self.assertEqual({b for b in branches(self.repo) if b.startswith("swarm/")},
                         {p.work_branch, f"swarm/{run}/c"})
        self.assertFalse((self.root / ".proj-swarm").exists())
        self.assertEqual(git(self.repo, "worktree", "list").count("\n"), 0)
        files = {f["path"] for f in p.final["files"]}
        self.assertEqual(files, {"a.txt", "b.txt", "shared.txt"})
        self.assertEqual(threads, {threading.get_ident()})

    def test_pulizia_corse_vecchie(self):
        ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
        old = "swarm/20261101-100000-f1"
        git(self.repo, "branch", f"{old}/unito")  # punta al commit attuale: già unito
        git(self.repo, "branch", "swarm/20261101-100000")  # branch di lavoro: resta
        git(self.repo, "switch", "-q", "-c", f"{old}/non-unito")
        (self.repo / "x.txt").write_text("x\n")
        git(self.repo, "add", "-A")
        git(self.repo, *ident, "commit", "-q", "-m", "lavoro non unito")
        git(self.repo, "switch", "-q", "main")
        swarm_dir = self.root / ".proj-swarm"
        (swarm_dir / "20261101-100000-f1").mkdir(parents=True)
        (swarm_dir / "20261101-100000-f1" / "bus.log").write_text("vecchio\n")
        vivo = swarm_dir / "20261110-090000-f1"
        git(self.repo, "worktree", "add", "-q", "-b", "swarm/20261110-090000-f1/vivo", str(vivo / "wt" / "vivo"))

        p = sp.Project(demo=False)
        msgs = []
        p.listeners.append(lambda kind, payload: msgs.append(payload.get("msg", "")))
        p.repo = self.repo
        p._sweep_old()
        left = branches(self.repo)
        self.assertNotIn(f"{old}/unito", left)
        self.assertIn(f"{old}/non-unito", left)
        self.assertIn("swarm/20261101-100000", left)
        self.assertIn("swarm/20261110-090000-f1/vivo", left)  # aperto in un worktree
        self.assertFalse((swarm_dir / "20261101-100000-f1").exists())
        self.assertTrue((vivo / "wt" / "vivo").exists())
        self.assertIn("tolti da corse precedenti: 1 branch già unito, 1 cartella", msgs)

    def test_stop_in_pausa(self):
        async def go():
            p = sp.Project(demo=True)
            p._loop = asyncio.get_running_loop()
            p.stage, p._resume_at = "paused", 1
            with self.assertRaises(ValueError):
                p.back_to_form()
            await p.stop()
            self.assertEqual(p.stage, "stopped")
            with self.assertRaises(ValueError):
                p.resume()
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
