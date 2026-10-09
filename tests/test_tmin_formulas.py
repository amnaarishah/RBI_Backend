"""t_min calculator tests (ASME Section VIII Div.1 formulas, client's T min calculation.xlsx).

Uses a custom stress so no database is needed.
Run from backend/:  python -m unittest tests.test_tmin_formulas -v
"""
import math
import os
import random
import sys
import unittest
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.crud.asme_calculator import calculate_tmin  # noqa: E402
from app.schemas.asme import TminCalculateRequest  # noqa: E402


def calc(ct, P=5.0, Dmm=1000.0, S_mpa=137.9, E=0.85, **kw):
    """P in bar, D in mm, S in MPa (custom stress)."""
    req = TminCalculateRequest(
        component_type=ct, design_pressure=D(str(P)), pressure_unit="bar",
        inside_diameter=D(str(Dmm)), dimension_unit="mm", design_temperature=D("150"),
        temperature_unit="celsius", joint_efficiency=D(str(E)), spec_number="X", grade="X",
        product_form="X", custom_stress=D(str(S_mpa)), custom_stress_unit="mpa", custom_stress_source="test sheet",
        **{k: (D(str(v)) if isinstance(v, (int, float)) else v) for k, v in kw.items()})
    return calculate_tmin(None, req)


def sheet(ct, P, Dm, S, E):
    """Client sheet rows 22-27 in consistent units (P and S in bar, lengths in mm)."""
    R = Dm / 2
    if ct == "cylindrical_shell":
        return P * R / (S * E - 0.6 * P)
    if ct == "spherical_shell":
        return P * R / (2 * S * E - 0.2 * P)
    if ct == "ellipsoidal_head":
        return P * Dm / (2 * S * E - 0.2 * P)
    if ct == "hemispherical_head":
        return P * R / (2 * S * E - 0.2 * P)
    if ct == "torispherical_head":                       # L = D
        return 0.885 * P * Dm / (S * E - 0.1 * P)
    if ct == "flat_head":                                # C = 0.33
        return Dm * math.sqrt(0.33 * P / (S * E))


class SheetEquivalence(unittest.TestCase):
    def test_random_inputs_match_sheet(self):
        rnd = random.Random(11)
        for ct in ("cylindrical_shell", "spherical_shell", "ellipsoidal_head", "hemispherical_head",
                   "torispherical_head", "flat_head"):
            for _ in range(100):
                Dm = round(rnd.uniform(300, 4000), 1)
                S = round(rnd.uniform(800, 1500), 1)           # bar
                E = rnd.choice([1, .85, .7, .65, .6])
                P = round(rnd.uniform(0.5, 0.3 * S * E), 3)    # stays in the thin-wall range
                r = calc(ct, P=P, Dmm=Dm, S_mpa=S / 10, E=E)
                exp = sheet(ct, P, Dm, S, E)
                self.assertAlmostEqual(float(r.t_min_mm), exp, delta=max(2e-4, exp * 5e-4), msg=f"{ct} P={P}")

    def test_sheet_example_1_8678_mm(self):
        # P 2.5 bar, D = 1000 inches, S = 20000 bar, E = 0.85  ->  1.8678 mm
        r = calculate_tmin(None, TminCalculateRequest(
            component_type="cylindrical_shell", design_pressure=D("2.5"), pressure_unit="bar",
            inside_diameter=D("1000"), dimension_unit="inches", design_temperature=D("150"),
            temperature_unit="celsius", joint_efficiency=D("0.85"), spec_number="X", grade="X",
            product_form="X", custom_stress=D("2000"), custom_stress_unit="mpa", custom_stress_source="test sheet"))
        self.assertAlmostEqual(float(r.t_min_mm), 1.8678, places=3)


class Torispherical(unittest.TestCase):
    def test_defaults_use_L_equals_D_and_r_6_percent(self):
        r = calc("torispherical_head", P=10, Dmm=1000)
        self.assertEqual(float(r.crown_radius_mm), 1000.0)
        self.assertEqual(float(r.knuckle_radius_mm), 60.0)
        self.assertIn("0.885", r.formula_used)

    def test_general_M_formula(self):
        L, rr, P, S, E = 1200.0, 100.0, 10.0, 1379.0, 0.85
        r = calc("torispherical_head", P=P, Dmm=1000, S_mpa=S / 10, E=E, crown_radius=L, knuckle_radius=rr)
        M = 0.25 * (3 + math.sqrt(L / rr))
        self.assertAlmostEqual(float(r.t_min_mm), P * L * M / (2 * S * E - 0.2 * P), places=3)
        self.assertAlmostEqual(float(r.m_factor), M, places=3)

    def test_validation_and_warning(self):
        with self.assertRaises(ValueError):
            calc("torispherical_head", crown_radius=100, knuckle_radius=200)
        r = calc("torispherical_head", crown_radius=1000, knuckle_radius=30)
        self.assertTrue(any("6 %" in w for w in r.warnings))


class FlatAndConical(unittest.TestCase):
    def test_flat_head_c_default_and_override(self):
        base = calc("flat_head", P=5, Dmm=500)
        self.assertTrue(base.c_is_default)
        self.assertEqual(float(base.c_factor), 0.33)
        c25 = calc("flat_head", P=5, Dmm=500, flat_head_c=0.25)
        self.assertFalse(c25.c_is_default)
        self.assertAlmostEqual(float(c25.t_min_mm) / float(base.t_min_mm), math.sqrt(0.25 / 0.33), places=3)

    def test_conical_head(self):
        P, Dm, S, E, a = 8.0, 1000.0, 1379.0, 1.0, 20.0
        r = calc("conical_head", P=P, Dmm=Dm, S_mpa=S / 10, E=E, cone_half_angle_deg=a)
        self.assertAlmostEqual(float(r.t_min_mm), P * Dm / (2 * math.cos(math.radians(a)) * (S * E - 0.6 * P)), places=3)
        with self.assertRaises(ValueError):
            calc("conical_head", cone_half_angle_deg=35)
        with self.assertRaises(ValueError):
            calc("conical_head")                               # angle is required


class ThickWall(unittest.TestCase):
    """Cylindrical shell, P > 0.385 S E: ASME Section VIII Div 1, Mandatory Appendix 1-2 (the book page of the client):
       (a) circumferential stress:  t = R [exp(P/SE) - 1]            when P > 0.385 SE
       (b) longitudinal stress:     t = R (sqrt(Z) - 1), Z = P/SE + 1  when P > 1.25 SE
       MAWP: (a)(2) P = SE ln((R+t)/R)."""
    S, E, Dm = 1379.0, 1.0, 1000.0

    def cyl(self, P, **kw):
        return calc("cylindrical_shell", P=P, Dmm=self.Dm, S_mpa=self.S / 10, E=self.E, **kw)

    def test_the_formula_switches_at_0_385_SE(self):
        lo = self.cyl(0.3849 * self.S * self.E)
        hi = self.cyl(0.3851 * self.S * self.E)
        self.assertFalse(lo.thick_wall_flag)
        self.assertIn("UG-27(c)(1)", lo.asme_clause)
        self.assertTrue(hi.thick_wall_flag)
        self.assertIn("Appendix 1-2(a)", hi.asme_clause)
        self.assertIn("exp(P / (S×E))", hi.formula_used)

    def test_circumferential_formula_of_the_book(self):
        P = 600.0                                              # > 0.385 S E = 530.9
        r = self.cyl(P)
        self.assertAlmostEqual(float(r.t_min_mm), (self.Dm / 2) * (math.exp(P / self.S) - 1), places=3)   # 272.57 mm
        self.assertTrue(r.thick_wall_flag)
        self.assertIn("Appendix 1-2(a)", r.thick_wall_warning)
        self.assertNotIn("1-2(b)", r.thick_wall_warning)      # P is below 1.25 S E: no longitudinal equation

    def test_pressure_above_SE_has_an_answer(self):
        r = self.cyl(1.5 * self.S)                             # the old equation had no solution here
        self.assertAlmostEqual(float(r.t_min_mm), 500 * (math.exp(1.5) - 1), places=2)

    def test_longitudinal_equation_is_added_above_1_25_SE_and_the_larger_governs(self):
        P = 2000.0                                             # > 1.25 S E = 1723.75
        r = self.cyl(P)
        t_a = 500 * (math.exp(P / self.S) - 1)
        t_b = 500 * (math.sqrt(P / self.S + 1) - 1)
        self.assertGreater(t_a, t_b)                           # the circumferential one is the larger
        self.assertAlmostEqual(float(r.t_min_mm), t_a, places=2)
        self.assertIn("1-2(b)", r.formula_used)
        self.assertIn("1-2(b)", r.thick_wall_warning)
        self.assertIn(f"{t_b:.4f}", r.thick_wall_warning)
        self.assertIn("Appendix 1-2(a)", r.asme_clause)

    def test_mawp_of_a_thick_shell_is_the_inverse_of_t_min(self):
        for P in (600.0, 1000.0, 2000.0):
            first = self.cyl(P)
            second = self.cyl(P, given_thickness=float(first.t_min_mm), given_thickness_unit="mm")
            self.assertAlmostEqual(float(second.mawp_bar), P, delta=P * 1e-4, msg=str(P))
            self.assertIn("ln((R + t) / R)", second.mawp_formula)

    def test_mawp_when_the_thickness_is_over_half_the_radius(self):
        r = self.cyl(10.0, given_thickness=300, given_thickness_unit="mm")          # R = 500: t = 300 > R / 2
        self.assertAlmostEqual(float(r.mawp_bar), self.S * math.log(800 / 500), places=2)
        self.assertIn("Appendix 1-2(a)", r.mawp_formula)

    def test_mawp_of_a_thin_shell_is_unchanged(self):
        r = self.cyl(6.0, given_thickness=12, given_thickness_unit="mm")
        self.assertAlmostEqual(float(r.mawp_bar), self.S * 12 / (500 + 0.6 * 12), places=3)
        self.assertTrue(r.mawp_formula.startswith("P = (S × E × t) / (R + 0.6 × t)"))


class CorrosionAllowanceAndMawp(unittest.TestCase):
    def test_ca_never_changes_t_min(self):
        a = calc("cylindrical_shell")
        b = calc("cylindrical_shell", corrosion_allowance=1.5, corrosion_allowance_unit="mm")
        self.assertEqual(a.t_min_mm, b.t_min_mm)
        self.assertEqual(float(b.design_thickness_mm), float(a.t_min_mm) + 1.5)

    def test_ca_in_inches(self):
        r = calc("cylindrical_shell", corrosion_allowance=0.1, corrosion_allowance_unit="inches")
        self.assertAlmostEqual(float(r.corrosion_allowance_mm), 2.54, places=4)

    def test_mawp_is_inverse_of_t_min_for_every_component(self):
        cases = [("cylindrical_shell", {}), ("spherical_shell", {}), ("ellipsoidal_head", {}),
                 ("hemispherical_head", {}), ("torispherical_head", {}),
                 ("torispherical_head", {"crown_radius": 1100, "knuckle_radius": 90}),
                 ("conical_head", {"cone_half_angle_deg": 25}), ("flat_head", {})]
        for ct, extra in cases:
            first = calc(ct, P=6.0, **extra)
            second = calc(ct, P=6.0, given_thickness=float(first.t_min_mm), given_thickness_unit="mm", **extra)
            self.assertAlmostEqual(float(second.mawp_bar), 6.0, delta=0.01, msg=f"{ct} {extra}")

    def test_mawp_uses_given_thickness_minus_ca(self):
        r = calc("cylindrical_shell", P=5, Dmm=1000, S_mpa=137.9, E=0.85,
                 given_thickness=12, corrosion_allowance=2, corrosion_allowance_unit="mm")
        S = 1379.0
        t = 10.0
        self.assertAlmostEqual(float(r.mawp_bar), S * 0.85 * t / (500 + 0.6 * t), places=3)
        with self.assertRaises(ValueError):
            calc("cylindrical_shell", given_thickness=2, corrosion_allowance=3)

    def test_no_given_thickness_no_mawp(self):
        self.assertIsNone(calc("cylindrical_shell", corrosion_allowance=1).mawp_bar)
        self.assertIsNone(calc("cylindrical_shell", corrosion_allowance=1).mawp_formula)

    def test_mawp_formula_is_shown_with_the_mawp(self):
        want = {"cylindrical_shell": "P = (S × E × t) / (R + 0.6 × t)", "spherical_shell": "P = (2 × S × E × t) / (R + 0.2 × t)",
                "hemispherical_head": "P = (2 × S × E × t) / (R + 0.2 × t)", "ellipsoidal_head": "P = (2 × S × E × t) / (D + 0.2 × t)",
                "torispherical_head": "P = (2 × S × E × t) / (L × M + 0.2 × t)",
                "conical_head": "P = (2 × S × E × t × cos α) / (D + 1.2 × t × cos α)", "flat_head": "P = (S × E × t²) / (C × d²)"}
        for ct, f in want.items():
            kw = {"cone_half_angle_deg": 25} if ct == "conical_head" else {}
            r = calc(ct, P=6.0, given_thickness=12, **kw)
            self.assertTrue(r.mawp_formula.startswith(f), ct)
            self.assertIn("given thickness − CA", r.mawp_formula)
            self.assertNotIn("given thickness", r.formula_used)       # the t_min formula stays as it was


class Validation(unittest.TestCase):
    def test_bad_inputs_rejected(self):
        with self.assertRaises(ValueError):
            calc("cylindrical_shell", E=0)
        with self.assertRaises(ValueError):
            calc("cylindrical_shell", P=0)
        with self.assertRaises(ValueError):
            calc("not_a_component")


class ThickWallLimits(unittest.TestCase):
    def test_limits_per_component(self):
        S = 1379.0
        sph = calc("spherical_shell", P=0.5 * S, Dmm=1000, S_mpa=S / 10, E=1.0)       # 0.5 SE: ok for a sphere
        self.assertFalse(sph.thick_wall_flag)
        sph2 = calc("spherical_shell", P=0.7 * S, Dmm=1000, S_mpa=S / 10, E=1.0)
        self.assertTrue(sph2.thick_wall_flag)
        self.assertIn("0.665", sph2.thick_wall_warning)
        ell = calc("ellipsoidal_head", P=0.5 * S, Dmm=1000, S_mpa=S / 10, E=1.0)
        self.assertFalse(ell.thick_wall_flag)                                         # no such limit for heads


class UserStressNeedsASource(unittest.TestCase):
    def req(self, **kw):
        base = dict(component_type="cylindrical_shell", design_pressure=D("10"), pressure_unit="bar",
                    inside_diameter=D("1000"), dimension_unit="mm", design_temperature=D("150"),
                    temperature_unit="celsius", joint_efficiency=D("1"), spec_number="X", grade="X",
                    product_form="X")
        base.update(kw)
        return TminCalculateRequest(**base)

    def test_source_is_required(self):
        for stress in (dict(custom_stress=D("100"), custom_stress_unit="mpa"), dict(custom_stress_mpa=D("100")),
                       dict(custom_stress_psi=D("14500"))):
            with self.assertRaises(ValueError):
                calculate_tmin(None, self.req(**stress))
            with self.assertRaises(ValueError):
                calculate_tmin(None, self.req(custom_stress_source="   ", **stress))

    def test_source_is_written_into_the_result(self):
        r = calculate_tmin(None, self.req(custom_stress=D("100"), custom_stress_unit="mpa",
                                          custom_stress_source="Client datasheet"))
        self.assertIn("NOT from the ASME table", r.stress_source)
        self.assertIn("Client datasheet", r.stress_source)
        self.assertIn("Client datasheet", r.calculation_trace)


class UnitsDoNotChangeTheAnswer(unittest.TestCase):
    """The same physical case typed in any unit must give the same t_min."""

    def t_min(self, p, p_unit, d, d_unit):
        req = TminCalculateRequest(
            component_type="cylindrical_shell", design_pressure=D(str(p)), pressure_unit=p_unit,
            inside_diameter=D(str(d)), dimension_unit=d_unit, design_temperature=D("330"),
            temperature_unit="celsius", joint_efficiency=D("0.85"), spec_number="X", grade="X", product_form="X",
            custom_stress=D("115"), custom_stress_unit="mpa", custom_stress_source="test")
        return float(calculate_tmin(None, req).t_min_mm)

    def test_pressure_units(self):
        base = self.t_min(21.77, "bar", 1200, "mm")
        for p, unit in ((21.77 / 0.0689476, "psi"), (2.177, "mpa"), (2177, "kpa"), (21.77 / 0.980665, "kg_cm2")):
            self.assertAlmostEqual(self.t_min(p, unit, 1200, "mm"), base, places=3, msg=unit)

    def test_diameter_units(self):
        base = self.t_min(21.77, "bar", 1200, "mm")
        self.assertAlmostEqual(self.t_min(21.77, "bar", 1200 / 25.4, "inches"), base, places=3)
        self.assertAlmostEqual(self.t_min(21.77, "bar", 1.2, "m"), base, places=3)

    def test_everything_in_other_units_at_once(self):
        base = self.t_min(21.77, "bar", 1200, "mm")
        self.assertAlmostEqual(self.t_min(21.77 / 0.0689476, "psi", 1200 / 25.4, "inches"), base, places=3)

    def test_temperature_unit_gives_the_same_table_row(self):
        celsius = calculate_tmin(None, TminCalculateRequest(
            component_type="cylindrical_shell", design_pressure=D("10"), pressure_unit="bar",
            inside_diameter=D("1000"), dimension_unit="mm", design_temperature=D("330"),
            temperature_unit="celsius", joint_efficiency=D("1"), spec_number="X", grade="X", product_form="X",
            custom_stress=D("100"), custom_stress_unit="mpa", custom_stress_source="test"))
        fahrenheit = calculate_tmin(None, TminCalculateRequest(
            component_type="cylindrical_shell", design_pressure=D("10"), pressure_unit="bar",
            inside_diameter=D("1000"), dimension_unit="mm", design_temperature=D("626"),
            temperature_unit="fahrenheit", joint_efficiency=D("1"), spec_number="X", grade="X", product_form="X",
            custom_stress=D("100"), custom_stress_unit="mpa", custom_stress_source="test"))
        self.assertEqual(celsius.temperature_c, fahrenheit.temperature_c)


if __name__ == "__main__":
    unittest.main()
