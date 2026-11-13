"""Consigli: matrice difficoltà × qualità, modello e spesa per fase, e la loro copia in JS."""
import re
import unittest
from pathlib import Path

import swarm_advice as sa

PAGE = Path(__file__).resolve().parent.parent / "web" / "index.html"


class Recommend(unittest.TestCase):
    def test_tutte_le_caselle(self):
        for d in (1, 2, 3):
            for q in (1, 2, 3):
                with self.subTest(d=d, q=q):
                    r = sa.recommend(d, q)
                    roles = [p["role"] for p in r["phases"]]
                    self.assertEqual(roles[0], "fondamenta")
                    self.assertEqual(roles[-1], "integrazione")
                    self.assertEqual("revisione" in roles, q == 3)
                    self.assertLessEqual(r["phases"][1]["agents"], 5)
                    self.assertEqual(r["total_agents"], sum(p["agents"] for p in r["phases"]))
                    self.assertAlmostEqual(r["max_cost"], sum(p["agents"] * p["budget"] for p in r["phases"]), 2)
                    for p in r["phases"]:
                        self.assertIn(p["effort"], sa.EFFORTS[:4])  # mai "max" di default
                        self.assertLessEqual(p["budget"], r["budget"])
                        if r["model"] == "opus" and p["role"] not in sa.STRONG_ROLES:
                            self.assertEqual(p["model"], "sonnet")

    def test_planner_budget(self):
        self.assertEqual(sa.planner_budget("haiku", "medium"), 0.5)
        self.assertEqual(sa.planner_budget("sonnet", "medium"), 1.0)
        self.assertEqual(sa.planner_budget("sonnet", "high"), 1.5)
        self.assertEqual(sa.planner_budget("opus", "high"), 4.0)
        for d in (1, 2, 3):
            for q in (1, 2, 3):
                r = sa.recommend(d, q)
                self.assertEqual(r["planner_budget"], sa.planner_budget(r["model"], r["planner_effort"]))

    def test_valori_fuori_scala(self):
        self.assertEqual(sa.recommend(9, -1)["difficulty"], 3)
        self.assertEqual(sa.recommend("x", None)["quality"], 2)

    def test_modello(self):
        self.assertEqual(sa.recommend(1, 1)["model"], "haiku")
        self.assertEqual(sa.recommend(3, 3)["model"], "opus")
        self.assertEqual(sa.recommend(2, 2)["model"], "sonnet")


class PhaseRules(unittest.TestCase):
    def test_phase_model(self):
        self.assertEqual(sa.phase_model("sviluppo", "opus"), "sonnet")
        self.assertEqual(sa.phase_model("fondamenta", "opus"), "opus")
        self.assertEqual(sa.phase_model("sviluppo", "haiku"), "haiku")

    def test_phase_budget(self):
        self.assertEqual(sa.phase_budget(10, "sonnet", "opus"), 4)
        self.assertEqual(sa.phase_budget(10, "opus", "opus"), 10)
        self.assertEqual(sa.phase_budget(10, "opus", "sonnet"), 10)  # non sale mai
        self.assertEqual(sa.phase_budget(0.6, "haiku", "opus"), 0.5)  # minimo 0.5
        self.assertEqual(sa.phase_budget(0.3, "haiku", "opus"), 0.3)  # ma mai sopra quella scelta

    def test_phase_role(self):
        self.assertEqual(sa.phase_role("Fondamenta", 0, 3), "fondamenta")
        self.assertEqual(sa.phase_role("Revisione", 2, 4), "revisione")
        self.assertEqual(sa.phase_role("Code review", 1, 4), "revisione")
        self.assertEqual(sa.phase_role("Qualcosa", 3, 4), "integrazione")
        self.assertEqual(sa.phase_role("Unica", 0, 1), "sviluppo")

    def test_assign_efforts(self):
        rec = sa.recommend(3, 3)
        plan = {"phases": [{"name": "Fondamenta"}, {"name": "Sviluppo", "effort": "low"},
                           {"name": "Integrazione", "model": "haiku"}]}
        out = sa.assign_efforts(plan, rec, "opus")["phases"]
        self.assertEqual(out[0]["model"], "opus")
        self.assertEqual(out[1]["effort"], "low")  # scelto a mano: resta
        self.assertEqual(out[1]["model"], "sonnet")
        self.assertEqual(out[2]["model"], "haiku")
        self.assertEqual(out[0]["effort"], next(p["effort"] for p in rec["phases"] if p["role"] == "fondamenta"))


def js_function(name: str) -> str:
    m = re.search(r"function %s\([^)]*\) \{\n(.*?)\n\}" % name, PAGE.read_text(encoding="utf-8"), re.S)
    assert m, f"{name} non trovata in web/index.html"
    return m.group(1)


def js_obj(text: str) -> dict:
    """{ haiku: 0.5, sonnet: 2 } → dict."""
    return {k: float(v) for k, v in re.findall(r"(\w+):\s*([\d.]+)", text)}


class JsMirror(unittest.TestCase):
    """phaseModel/phaseBudget nella pagina devono dare gli stessi numeri di swarm_advice.py."""

    def test_phase_budget(self):
        body = js_function("phaseBudget")
        self.assertEqual(js_obj(re.search(r"\|\|\s*(\{[^}]*\})", body).group(1)), sa.MODEL_BUDGET)
        expr = re.search(r"return (Math\.min\(.*\));", body).group(1)
        py = expr.replace("Math.min", "min").replace("Math.max", "max").replace("Math.round", "_round")
        mb = sa.MODEL_BUDGET
        for budget in (0.3, 0.5, 1, 2, 2.5, 4, 7.5, 10, 33, 50):
            for model in sa.MODELS:
                for proj in sa.MODELS:
                    a, b = mb[model], mb[proj]
                    js = budget if a >= b else eval(py, {"_round": lambda x: int(x + 0.5), "min": min, "max": max},
                                                   {"budget": budget, "a": a, "b": b})
                    with self.subTest(budget=budget, model=model, proj=proj):
                        self.assertEqual(js, sa.phase_budget(budget, model, proj))

    def test_phase_model(self):
        body = js_function("phaseModel")
        strong = re.findall(r'"(\w+)"', re.search(r"\|\|\s*(\[[^\]]*\])", body).group(1))
        self.assertEqual(tuple(strong), sa.STRONG_ROLES)
        self.assertIn('return model === "opus" && !strong.includes(role) ? "sonnet" : model;', body)

    def test_tabella_della_pagina(self):
        t = sa.advice_table()
        self.assertEqual(t["model_budget"], sa.MODEL_BUDGET)
        self.assertEqual(t["strong_roles"], list(sa.STRONG_ROLES))


if __name__ == "__main__":
    unittest.main()
