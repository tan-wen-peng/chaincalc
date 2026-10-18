"""标准公差换算（GB/T 1800.1-2020）。

标准公差因子：i = 0.45 * D^(1/3) + 0.001 * D  (µm, D 单位 mm, D ≤ 500)
其中 D 取尺寸分段的几何平均值。
标准公差值 ITn = a_n * i，a_n 为公差等级系数。
"""

from __future__ import annotations

import math

IT_COEF = {
    5: 7, 6: 10, 7: 16, 8: 25, 9: 40, 10: 64,
    11: 100, 12: 160, 13: 250, 14: 400, 15: 640,
    16: 1000, 17: 1600, 18: 2500,
}

# GB/T 1800.1 分段（mm）：(下界开, 上界闭]
SIZE_RANGES = [
    (1, 3), (3, 6), (6, 10), (10, 18), (18, 30), (30, 50),
    (50, 80), (80, 120), (120, 180), (180, 250), (250, 315),
    (315, 400), (400, 500),
]


def range_mean_size(d: float) -> float:
    """返回 d 所在尺寸分段的几何平均值 D (mm)。"""
    if d <= 0:
        raise ValueError("基本尺寸必须大于 0")
    if d <= 3:
        return math.sqrt(1.0 * 3.0)
    for lo, hi in SIZE_RANGES[1:]:
        if lo < d <= hi:
            return math.sqrt(lo * hi)
    return d  # > 500mm 时按实际尺寸近似


def tol_unit(d: float) -> float:
    """标准公差因子 i (µm)。"""
    if d <= 500:
        return 0.45 * range_mean_size(d) ** (1.0 / 3.0) + 0.001 * range_mean_size(d)
    return 0.004 * d + 2.1


def it_value(d: float, grade: int) -> float:
    """尺寸 d 在 IT{grade} 下的标准公差 (mm)。"""
    if grade not in IT_COEF:
        raise ValueError(f"支持的公差等级为 IT5~IT18，收到 IT{grade}")
    return IT_COEF[grade] * tol_unit(d) / 1000.0


def grade_coef(d: float, tol_mm: float) -> float:
    """把某尺寸上的公差换算为标准公差等级系数 a（连续值）。"""
    return abs(tol_mm) * 1000.0 / tol_unit(d)


def grade_for_a(a: float) -> int:
    """把连续系数 a 归到不超过它的最高标准 IT 等级。"""
    best = 5
    for g in sorted(IT_COEF):
        if IT_COEF[g] <= a + 1e-9:
            best = g
    return best


def grade_label(a: float) -> str:
    """形如 'IT7≈' 的近似等级标签。"""
    g = grade_for_a(a)
    coef = IT_COEF[g]
    ratio = a / coef
    if ratio <= 1.02:
        return f"IT{g}"
    nxt = min([x for x in IT_COEF if x > g], default=g)
    return f"IT{g}~IT{nxt}"


def preferred_step(t: float) -> float:
    """按公差大小选取可标注的最小步长 (mm)。"""
    if t < 0.02:
        return 0.001
    if t < 0.05:
        return 0.002
    if t < 0.2:
        return 0.005
    if t < 0.5:
        return 0.01
    if t < 2.0:
        return 0.02
    return 0.05


def round_down_preferred(t: float) -> float:
    """向下取整到可标注的公差值，保证不超出给定目标。"""
    if t <= 0:
        return 0.0
    step = preferred_step(t)
    out = math.floor(t / step + 1e-9) * step
    return max(round(out, 3), step)
