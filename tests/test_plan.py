"""Piano: JSON del pianificatore, controllo del piano, cartella del progetto, tetto di spesa
del pianificatore."""
import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import swarm_project as sp


def worker(name, task="un compito abbastanza lungo"):
    return {"name": name, "task": task}


class ExtractJson(unittest.TestCase):
    def test_puro(self):
        self.assertEqual(sp.extract_json('{"a": 1}'), {"a": 1})

    def test_blocco_markdown(self):
        self.assertEqual(sp.extract_json('Ecco:\n```json\n{"a": {"b": 2}}\n```\nfine'), {"a": {"b": 2}})

    def test_testo_intorno(self):
        self.assertEqual(sp.extract_json('Il piano è {"x": [1, 2]} spero vada'), {"x": [1, 2]})

    def test_niente_json(self):
        with self.assertRaises(ValueError):
            sp.extract_json("non ho un piano")

    def test_json_rotto(self):
        with self.assertRaises(ValueError):  # json.JSONDecodeError è un ValueError
            sp.extract_json("{rotto}")


class ValidatePlan(unittest.TestCase):
    def plan(self, *phases):
        return {"goal": "obiettivo", "phases": [{"name": f"F{i}", "workers": ws} for i, ws in enumerate(phases)]}

    def test_valido_e_normalizzato(self):
        out = sp.validate_plan({"goal": "  g  ", "phases": [
            {"name": "Sviluppo", "effort": "high", "model": "opus", "workers": [worker(" Api Meteo ")]}]})
        ph = out["phases"][0]
        self.assertEqual(ph["workers"][0]["name"], "api-meteo")
        self.assertEqual((ph["effort"], ph["model"]), ("high", "opus"))
        self.assertEqual(out["goal"], "g")

    def test_effort_e_modello_sconosciuti_scartati(self):
        ph = sp.validate_plan({"phases": [{"effort": "turbo", "model": "gpt", "workers": [worker("a")]}]})["phases"][0]
        self.assertNotIn("effort", ph)
        self.assertNotIn("model", ph)
        self.assertEqual(ph["name"], "Fase 1")

    def test_effort_vuoto_tenuto(self):
        ph = sp.validate_plan({"phases": [{"effort": "", "workers": [worker("a")]}]})["phases"][0]
        self.assertEqual(ph["effort"], "")

    def test_errori(self):
        bad = [
            "non un dict",
            {"phases": []},
            {"phases": [{"workers": []}] * 7},
            {"phases": [{"workers": []}]},
            {"phases": [{"workers": [worker("Nome Con/Barra")]}]},
            {"phases": [{"workers": [worker("a"), worker("a")]}]},
            {"phases": [{"workers": [worker("a", "corto")]}]},
            {"phases": ["fase"]},
        ]
        for p in bad:
            with self.subTest(p=p), self.assertRaises(ValueError):
                sp.validate_plan(p)

    def test_troppi_agenti(self):
        p = self.plan([worker(f"w{i}") for i in range(4)])
        with self.assertRaises(ValueError):
            sp.validate_plan(p, max_workers=3)
        self.assertEqual(len(sp.validate_plan(p, max_workers=4)["phases"][0]["workers"]), 4)

    def test_compito_tagliato(self):
        out = sp.validate_plan(self.plan([worker("a", "x" * 5000)]))
        self.assertEqual(len(out["phases"][0]["workers"][0]["task"]), 4000)


class ResolveProjectPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects = Path(self.tmp.name).resolve()
        patcher = mock.patch.object(sp, "PROJECTS_DIR", self.projects)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_vuoto_usa_idea(self):
        self.assertEqual(sp.resolve_project_path("", "Voglio un'app meteo con mappe"),
                         self.projects / "app-meteo-mappe")

    def test_vuoto_cartella_occupata(self):
        (self.projects / "app-meteo" / "x").mkdir(parents=True)
        self.assertEqual(sp.resolve_project_path("", "app meteo"), self.projects / "app-meteo-2")

    def test_solo_nome(self):
        self.assertEqual(sp.resolve_project_path("ricette"), self.projects / "ricette")

    def test_nomi_speciali(self):
        for raw in (".", ".."):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sp.resolve_project_path(raw)

    def test_dentro_home(self):
        home = Path.home().resolve()
        self.assertEqual(sp.resolve_project_path("~/Progetti/x"), home / "Progetti" / "x")
        self.assertEqual(sp.resolve_project_path("Progetti/x"), home / "Progetti" / "x")

    def test_fuori_home(self):
        for raw in ("~", "/etc/x", "~/../..", "a/../../.."):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sp.resolve_project_path(raw)

    def test_slug(self):
        self.assertEqual(sp.project_slug("Crea un gioco della vita"), "gioco-vita")
        self.assertEqual(sp.project_slug("!!!"), "progetto")


FAKE_PLANNER = """#!{py}
import json, os, sys
sys.stdin.read()
open(os.environ["FAKE_PLANNER_ARGV"], "w").write(json.dumps(sys.argv[1:]))
over = os.environ.get("FAKE_PLANNER_OVER") == "1"
print(json.dumps({{"type": "result", "subtype": "error_max_budget_usd" if over else "success",
                  "is_error": over, "total_cost_usd": 1.5, "result": "" if over else "{{}}"}}))
"""


class PlannerBudget(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        fake = self.root / "claude"
        fake.write_text(FAKE_PLANNER.format(py=sys.executable))
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.argv = self.root / "argv.json"
        env = mock.patch.dict(os.environ, {"SWARM_CLAUDE": str(fake), "FAKE_PLANNER_ARGV": str(self.argv)})
        env.start()
        self.addCleanup(env.stop)
        self.p = sp.Project(demo=False)
        self.p.advice = sp.recommend(3, 3)
        self.p.settings = {"idea": "un gestionale per la palestra", "path": str(self.root), "model": "opus",
                           "budget": 5, "max_workers": 5, "allow_build": False, "difficulty": 3,
                           "quality": 3, "planner_budget": 4.0}
        self.p.repo, self.p.repo_state = self.root, "nuovo progetto"

    def test_tetto_passato_a_claude(self):
        asyncio.run(self.p._run_planner(""))
        argv = json.loads(self.argv.read_text())
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "4.0")

    def test_tetto_raggiunto(self):
        with mock.patch.dict(os.environ, {"FAKE_PLANNER_OVER": "1"}):
            with self.assertRaisesRegex(RuntimeError, r"tetto di spesa \(\$4\.00\)"):
                asyncio.run(self.p._run_planner(""))
        self.assertEqual(self.p.planner_cost, 1.5)


if __name__ == "__main__":
    unittest.main()
