"""单元测试：用可手算的已知答案校验算法。

运行：python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chaincalc.allocate import allocate
from chaincalc.calc import solve
from chaincalc.gdttol import gdt_to_ring, mmc_bonus, angular_to_linear
from chaincalc.model import Chain, Ring
from chaincalc.report import render_html, render_markdown
from chaincalc.std_tol import IT_COEF, grade_label, round_down_preferred, tol_unit


def bearing_case():
    """转轴组件轴向间隙链：X = A1 - (A2 + A3 + A4)。"""
    return Chain(
        title="转轴组件轴向间隙链",
        rings=[
            Ring("轴承座内腔深度 A1", 40.00, 0.10, 0.00, +1),
            Ring("轴承宽度 A2", 12.00, 0.00, -0.12, -1, fixed=True, source="标准件"),
            Ring("隔套长度 A3", 22.00, 0.05, -0.05, -1),
            Ring("端盖凸台高度 A4", 5.85, 0.00, -0.05, -1),
        ],
        limit_low=0.05,
        limit_high=0.30,
    )


class TestWorstCase(unittest.TestCase):
    def test_nominal_and_deviations(self):
        r = solve(bearing_case())
        self.assertAlmostEqual(r.nominal, 0.15, places=9)
        # ES0 = Σ增环ES - Σ减环EI = 0.10 - (-0.12-0.05-0.05)
        self.assertAlmostEqual(r.es_wc, 0.32, places=9)
        # EI0 = Σ增环EI - Σ减环ES = 0 - (0+0.05+0)
        self.assertAlmostEqual(r.ei_wc, -0.05, places=9)
        self.assertAlmostEqual(r.tol_wc, 0.37, places=9)
        self.assertAlmostEqual(r.lower_wc, 0.10, places=9)
        self.assertAlmostEqual(r.upper_wc, 0.47, places=9)

    def test_tolerance_is_sum_of_rings(self):
        ch = bearing_case()
        self.assertAlmostEqual(solve(ch).tol_wc, sum(r.tol for r in ch.rings), places=12)

    def test_unilateral_ring_is_sorted(self):
        ring = Ring("x", 10.0, -0.1, 0.2)  # 故意颠倒
        self.assertAlmostEqual(ring.es, 0.2)
        self.assertAlmostEqual(ring.ei, -0.1)
        self.assertAlmostEqual(ring.tol, 0.3)


class TestRss(unittest.TestCase):
    def test_mean_deviation_and_rss_tolerance(self):
        r = solve(bearing_case())
        # Δ0 = +0.05 +0.06 +0 +0.025
        self.assertAlmostEqual(r.mean_dev, 0.135, places=9)
        self.assertAlmostEqual(r.tol_rss, 0.1920937, places=6)
        self.assertAlmostEqual(r.sigma, r.tol_rss / 6, places=12)
        self.assertAlmostEqual(r.es_rss, 0.135 + r.tol_rss / 2, places=9)

    def test_out_of_spec_probability(self):
        r = solve(bearing_case())
        self.assertFalse(r.wc_pass)
        self.assertFalse(r.rss_pass)
        self.assertAlmostEqual(r.mean_value, 0.285, places=9)
        self.assertGreater(r.p_high, 0.30)
        self.assertLess(r.p_low, 1e-6)

    def test_rss_equal_tolerances_gives_sqrt_n(self):
        ch = Chain("等公差链", [Ring(f"A{i}", 10.0, 0.05, -0.05, 1) for i in range(4)])
        r = solve(ch)
        self.assertAlmostEqual(r.tol_wc, 0.4, places=12)
        self.assertAlmostEqual(r.tol_rss, 0.2, places=12)  # sqrt(4*0.01)=0.2
        self.assertAlmostEqual(r.shrink_ratio, 2.0, places=9)  # = sqrt(4)

    def test_k_factor_scales_rss(self):
        ch = Chain("均匀分布", [Ring("A", 10.0, 0.05, -0.05, 1, k=1.73)])
        self.assertAlmostEqual(solve(ch).tol_rss, 1.73 * 0.1, places=9)

    def test_cpk_detects_center_offset(self):
        r = solve(bearing_case())
        self.assertAlmostEqual(r.cp, 0.25 / (6 * r.sigma), places=9)
        self.assertLess(r.cpk, 0.2)


class TestContribution(unittest.TestCase):
    def test_shares_sum_to_one(self):
        from chaincalc.calc import contribution
        ch = bearing_case()
        ch.rings.append(gdt_to_ring("圆跳动环", "圆跳动", 0.02, -1, ref_size=26.0).ring)
        c = contribution(ch)
        self.assertAlmostEqual(sum(x["share_wc"] for x in c), 1.0, places=9)
        self.assertAlmostEqual(sum(x["share_rss"] for x in c), 1.0, places=9)

    def test_rss_share_uses_variance(self):
        from chaincalc.calc import contribution
        ch = Chain("t", [Ring("A", 10.0, 0.1, -0.1, 1), Ring("B", 10.0, 0.1, -0.1, 1)])
        c = contribution(ch)
        self.assertAlmostEqual(c[0]["share_rss"], 0.5, places=12)


class TestAllocation(unittest.TestCase):
    def test_equal_wc(self):
        ch = Chain("t", [Ring(n, 10.0, 0.25, -0.25, 1) for n in "ABC"])
        al = allocate(ch, target_tol=0.30, method="equal", mode="wc")
        self.assertTrue(all(abs(it.suggested_tol - 0.10) < 1e-9 for it in al.items))
        self.assertAlmostEqual(al.achieved_tol, 0.30, places=9)
        self.assertTrue(al.feasible)

    def test_equal_rss_uses_sqrt_n(self):
        ch = Chain("t", [Ring(n, 10.0, 0.25, -0.25, 1) for n in "ABC"])
        al = allocate(ch, target_tol=0.30, method="equal", mode="rss")
        self.assertAlmostEqual(al.items[0].suggested_tol,
                               round_down_preferred(0.30 / 3 ** 0.5), places=12)

    def test_fixed_ring_is_deducted(self):
        ch = bearing_case()
        al = allocate(ch, target_tol=0.25, method="it", mode="wc")
        fixed = [it for it in al.items if it.fixed]
        self.assertEqual(len(fixed), 1)
        self.assertAlmostEqual(fixed[0].suggested_tol, 0.12)
        self.assertLessEqual(al.achieved_tol, 0.25 + 1e-9)
        self.assertTrue(al.feasible)

    def test_equal_precision_shares_grade(self):
        ch = Chain("t", [
            Ring("小尺寸", 8.0, 0.25, -0.25, 1),
            Ring("大尺寸", 200.0, 0.25, -0.25, 1),
        ])
        al = allocate(ch, target_tol=0.40, method="it", mode="wc")
        self.assertGreater(al.items[1].suggested_tol, al.items[0].suggested_tol,
                           "等精度法下大尺寸应获得更大公差")

    def test_impossible_budget_warns(self):
        ch = bearing_case()
        ch.rings[1].es, ch.rings[1].ei = 0.0, -0.30
        ch.rings[1].fixed = True
        al = allocate(ch, target_tol=0.20)
        self.assertTrue(al.warnings)
        self.assertFalse(al.feasible)

    def test_invalid_target_raises(self):
        with self.assertRaises(ValueError):
            allocate(bearing_case(), target_tol=0)

    def test_apply_allocation_keeps_unchanged_rings(self):
        from chaincalc.allocate import apply_allocation
        ch = bearing_case()
        al = allocate(ch, target_tol=0.37, method="equal", mode="wc")
        back = apply_allocation(ch, al)
        for old, new in zip(ch.rings, back.rings):
            if old.fixed:
                self.assertAlmostEqual(old.es, new.es, places=12)
                self.assertAlmostEqual(old.ei, new.ei, places=12)

    def test_allocation_verifies_under_worst_case(self):
        from chaincalc.allocate import apply_allocation
        ch = bearing_case()
        al = allocate(ch, target_tol=0.24, method="it", mode="wc")
        self.assertTrue(al.feasible)
        vt = solve(apply_allocation(ch, al))
        self.assertLessEqual(vt.tol_wc, 0.24 + 1e-9)

    def test_allocation_never_widens(self):
        from chaincalc.allocate import apply_allocation
        ch = bearing_case()
        ch.rings[2].es, ch.rings[2].ei = 0.010, -0.010  # 隔套已经磨得很紧
        al = allocate(ch, target_tol=0.30, method="it", mode="wc")
        tight = [it for it in al.items if it.name.startswith("隔套")]
        self.assertAlmostEqual(tight[0].suggested_tol, 0.020, places=12)
        self.assertTrue(tight[0].kept)
        vt = solve(apply_allocation(ch, al))
        self.assertLessEqual(vt.tol_wc, 0.30 + 1e-9)

    def test_rss_mode_never_worsens_worst_case(self):
        from chaincalc.allocate import apply_allocation
        ch = bearing_case()
        base = solve(ch).tol_wc
        al = allocate(ch, target_tol=0.24, method="it", mode="rss")
        vt = solve(apply_allocation(ch, al))
        self.assertLessEqual(vt.tol_wc, base + 1e-9,
                             "概率法分配不应让极值法结果变差")

    def test_rss_allocation_matches_solver_convention(self):
        from chaincalc.allocate import apply_allocation
        ch = Chain("k 加权链", [
            Ring("塑胶", 8.0, 0.12, -0.12, 1, k=1.17),
            Ring("五金", 1.5, 0.03, -0.03, -1),
            Ring("平面度", 0.0, 0.0, -0.08, 1, k=1.73, source="GD&T", ref_size=8.0),
        ])
        al = allocate(ch, target_tol=0.30, method="it", mode="rss")
        vt = solve(apply_allocation(ch, al))
        self.assertLessEqual(vt.tol_rss, 0.30 + 1e-6,
                             "分配后的实际 RSS（含 k）必须不超目标")

    def test_gdt_ref_size_required_for_it_method(self):
        g = gdt_to_ring("平面度环", "平面度", 0.05, -1)
        ch = Chain("t", [g.ring])
        with self.assertRaises(ValueError):
            allocate(ch, target_tol=0.1, method="it")
        g2 = gdt_to_ring("平面度环", "平面度", 0.05, -1, ref_size=20.0)
        al = allocate(Chain("t", [g2.ring]), target_tol=0.1, method="it")
        self.assertTrue(al.items)


class TestGdt(unittest.TestCase):
    def test_position_two_way(self):
        c = gdt_to_ring("位置度环", "位置度", 0.20, -1)
        self.assertAlmostEqual(c.t_eq, 0.20)
        self.assertTrue(c.symmetric)
        self.assertAlmostEqual(c.ring.es, 0.10)
        self.assertAlmostEqual(c.ring.ei, -0.10)
        self.assertAlmostEqual(c.ring.k, 1.0)

    def test_flatness_one_way_eats_clearance(self):
        # 减环上的形状误差使其"变厚"，从而减小间隙 -> es=+t, ei=0
        c = gdt_to_ring("平面度环", "平面度", 0.05, -1)
        self.assertFalse(c.symmetric)
        self.assertAlmostEqual(c.ring.es, 0.05)
        self.assertAlmostEqual(c.ring.ei, 0.0)
        self.assertAlmostEqual(c.ring.k, 1.73)
        # 增环上同理应向减小封闭环的方向偏 -> es=0, ei=-t
        c2 = gdt_to_ring("平面度环", "平面度", 0.05, +1)
        self.assertAlmostEqual(c2.ring.es, 0.0)
        self.assertAlmostEqual(c2.ring.ei, -0.05)

    def test_mmc_bonus(self):
        self.assertAlmostEqual(mmc_bonus(0.20, "none"), 0.0)
        self.assertAlmostEqual(mmc_bonus(0.20, "mean"), 0.10)
        self.assertAlmostEqual(mmc_bonus(0.20, "full"), 0.20)
        c = gdt_to_ring("位置度M", "位置度", 0.10, -1, mmc=True, size_tol=0.20, bonus_mode="mean")
        self.assertAlmostEqual(c.t_eq, 0.20)

    def test_unknown_feature_raises(self):
        with self.assertRaises(ValueError):
            gdt_to_ring("x", "火星度", 0.1)

    def test_angular_to_linear(self):
        import math
        self.assertAlmostEqual(angular_to_linear(0.1, 50.0),
                               50.0 * math.tan(math.radians(0.1)), places=12)


class TestStdTol(unittest.TestCase):
    def test_tol_unit_monotonic(self):
        self.assertLess(tol_unit(5), tol_unit(50))
        self.assertLess(tol_unit(50), tol_unit(400))

    def test_it_grades_increase(self):
        vals = [IT_COEF[g] * tol_unit(25) for g in sorted(IT_COEF)]
        self.assertEqual(vals, sorted(vals))
        self.assertAlmostEqual(IT_COEF[7] * tol_unit(25) / 1000, 0.021, places=3)

    def test_grade_label(self):
        self.assertEqual(grade_label(16), "IT7")
        self.assertTrue(grade_label(20).startswith("IT7"))

    def test_round_down_never_exceeds(self):
        for t in (0.0213, 0.0471, 0.123, 0.456, 1.234):
            self.assertLessEqual(round_down_preferred(t), t + 1e-12)


class TestIoAndReport(unittest.TestCase):
    def test_json_roundtrip(self):
        ch = bearing_case()
        again = Chain.from_dict(json.loads(json.dumps(ch.to_dict())))
        a, b = solve(ch), solve(again)
        self.assertAlmostEqual(a.tol_wc, b.tol_wc)
        self.assertAlmostEqual(a.tol_rss, b.tol_rss)

    def test_excel_template_roundtrip(self):
        from chaincalc.excel_io import read_excel, write_template
        ch = bearing_case()
        ch.target_tol = 0.25
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.xlsx")
            write_template(path, ch)
            back = read_excel(path)
        self.assertEqual(len(back.rings), 4)
        self.assertEqual(back.closed_name, ch.closed_name)
        self.assertAlmostEqual(back.limit_high, 0.30)
        self.assertAlmostEqual(solve(back).tol_wc, solve(ch).tol_wc)

    def test_excel_reads_gdt_sheet(self):
        from chaincalc.excel_io import read_excel, write_template
        ch = bearing_case()
        gdt_rows = [{"name": "端面平行度", "feature": "平行度", "value": 0.03,
                     "kind": "减环", "mmc": "否", "size_tol": 0.0,
                     "bonus_mode": "mean", "effect": "收紧", "note": "端盖端面"}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.xlsx")
            write_template(path, ch, gdt_rows)
            back = read_excel(path)
        self.assertEqual(len(back.rings), 5)
        g = back.rings[-1]
        self.assertEqual(g.source, "GD&T")
        self.assertAlmostEqual(g.tol, 0.03)

    def test_gdt_rings_not_duplicated_on_roundtrip(self):
        from chaincalc.excel_io import read_excel, write_template
        ch = Chain.from_dict({
            "title": "含 GD&T 的链",
            "limit_low": 0.1, "limit_high": 0.35, "target_tol": 0.2,
            "alloc_mode": "rss",
            "rings": [
                {"name": "A1", "nominal": 26.0, "es": 0.05, "ei": 0.0, "sign": 1},
                {"name": "A2", "nominal": 8.0, "es": 0.0, "ei": -0.12, "sign": -1, "fixed": True},
            ],
            "gdt": [{"name": "端面圆跳动", "feature": "圆跳动", "value": 0.02,
                     "kind": "减环", "ref_size": 26.0}],
        })
        self.assertEqual(len(ch.rings), 3)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.xlsx")
            write_template(path, ch, ch.gdt_rows)
            back = read_excel(path)
        self.assertEqual(len(back.rings), 3, "GD&T 环不应被重复换算")
        self.assertAlmostEqual(solve(back).tol_wc, solve(ch).tol_wc, places=12)
        allocate(back, target_tol=0.2, method="it", mode="rss")  # 不应因缺 ref_size 报错

    def test_render_markdown_and_html(self):
        ch = bearing_case()
        res = solve(ch)
        al = allocate(ch, target_tol=0.25)
        md = render_markdown(res, al, ch)
        self.assertIn("极值法", md)
        self.assertIn("尺寸链计算书", md)
        self.assertIn("0.37", md)
        html = render_html(md, ch.title)
        self.assertIn("<table>", html)
        self.assertIn("</html>", html)
        self.assertNotIn("| ---", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
