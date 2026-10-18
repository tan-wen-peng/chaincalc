"""反向公差分配：给定封闭环目标公差，反算各组成环公差。

三种方法：
- equal         等公差法：所有可分配环取同一公差
- it (默认)      等精度法：按标准公差因子 i(D) 成比例分配，即各环取同一 IT 等级
- proportional  等比法：按现存公差（或指定权重）成比例缩放

两条工程约束（很多书上的公式没有，但必须遵守）：
1. 被 Ring.fixed 标记的环（标准件、采购件）公差锁定，先从预算中扣除。
2. 分配只收紧、不放宽：若某环算出的建议公差大于它现有公差，说明它本来就够紧，
   保持原值并把省下的预算让给其它环（水位填充）。给一个已经标 ±0.03 的面
   建议 ±0.08 毫无意义。

两种合成口径：
- wc  极值法：Σ Ti ≤ T0
- rss 概率法：sqrt(Σ Ti^2) ≤ T0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import List, Optional, Sequence

from .model import Chain, Ring
from .std_tol import grade_coef, grade_label, round_down_preferred, tol_unit


@dataclass
class AllocItem:
    name: str
    size: float
    sign: int
    fixed: bool
    current_tol: float
    suggested_tol: float
    grade: str
    i_factor: float
    weight: float
    kept: bool = False  # 现有公差已优于建议值，保持原样

    @property
    def kind(self) -> str:
        return "增环" if self.sign > 0 else "减环"

    @property
    def symmetric_label(self) -> str:
        return "±" + _trim(self.suggested_tol / 2.0)

    @property
    def remark(self) -> str:
        if self.fixed:
            return "锁定（标准件/采购件）"
        if self.kept:
            return "保持现有公差"
        if self.suggested_tol < self.current_tol - 1e-9:
            return f"收紧 {_trim(self.current_tol - self.suggested_tol)}"
        return ""


@dataclass
class Allocation:
    target_tol: float
    mode: str
    method: str
    items: List[AllocItem] = field(default_factory=list)
    achieved_tol: float = 0.0
    margin: float = 0.0
    warnings: List[str] = field(default_factory=list)

    @property
    def feasible(self) -> bool:
        return self.achieved_tol <= self.target_tol + 1e-9

    @property
    def free_items(self) -> List[AllocItem]:
        return [it for it in self.items if not it.fixed]


def _trim(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _split(budget: float, weights: Sequence[float], ks: Sequence[float],
           mode: str) -> List[float]:
    """按权重把预算拆成各环公差。

    概率法要与 solve() 的口径一致：封闭环 T0 = sqrt(Σ (kᵢTᵢ)²)，
    所以分配时有效权重是 kᵢ·wᵢ，而返回的仍是环自身的公差 Tᵢ。
    """
    if not weights:
        return []
    if mode == "rss":
        eff = [w * k for w, k in zip(weights, ks)]
        denom = sqrt(sum(x * x for x in eff))
        if denom <= 0:
            return [0.0] * len(weights)
        return [budget * w / denom for w in weights]
    denom = sum(weights)
    if denom <= 0:
        return [0.0] * len(weights)
    return [budget * w / denom for w in weights]


def _consume(tol: float, k: float, mode: str) -> float:
    """该环占用的预算量：极值法是公差本身，概率法是方差 (kᵢTᵢ)²。"""
    return (k * tol) ** 2 if mode == "rss" else tol


def _to_budget(value: float, mode: str) -> float:
    """把内部记帐量换算回可用的公差预算。"""
    return sqrt(value) if mode == "rss" else value


def _water_fill(budget: float, weights: Sequence[float], ks: Sequence[float],
                caps: Sequence[Optional[float]], mode: str) -> List[float]:
    """把预算分给各环，且任何一环都不超过它现有的公差（只收紧不放宽）。"""
    n = len(weights)
    tols: List[float] = [0.0] * n
    active = list(range(n))
    # 概率法内部按方差记帐，避免"公差 - 方差"的量纲混用
    remaining = budget ** 2 if mode == "rss" else budget
    floor = 1e-18 if mode == "rss" else 1e-12
    guard = 0

    while active and remaining > floor and guard <= n + 2:
        guard += 1
        share = _split(_to_budget(remaining, mode), [weights[i] for i in active],
                       [ks[i] for i in active], mode)
        clamped = [i for i, t in zip(active, share)
                   if caps[i] is not None and t > caps[i] - 1e-12]
        if not clamped:
            for i, t in zip(active, share):
                tols[i] = t
            remaining = 0.0
            active = []
            break
        for i in clamped:
            tols[i] = caps[i] if caps[i] is not None else 0.0
            remaining -= _consume(tols[i], ks[i], mode)
            active.remove(i)
        remaining = max(remaining, 0.0)

    for i in active:  # 只有预算被锁定环吃光时才会走到这里
        tols[i] = min(0.001, caps[i]) if caps[i] else 0.001
    return tols


def _size_for_it(ring: Ring) -> float:
    """等精度法取 i(D) 用的尺寸：GD&T 环用要素尺寸，尺寸环用自己的基本尺寸。"""
    size = ring.ref_size if (ring.ref_size or 0) > 0 else ring.nominal
    if not size or size <= 0:
        raise ValueError(
            f"环 {ring.name!r} 无法参与等精度法分配：请填写该环的要素实际尺寸"
            "（JSON 的 ref_size / Excel GD&T 表的'要素尺寸mm'列）")
    return size


def _combine(tols: Sequence[float], ks: Sequence[float], mode: str) -> float:
    """与 solve() 一致：极值法 Σ Tᵢ，概率法 sqrt(Σ (kᵢTᵢ)²)。"""
    if mode == "rss":
        return sqrt(sum((k * t) ** 2 for t, k in zip(tols, ks)))
    return sum(tols)


def allocate(chain: Chain, target_tol: Optional[float] = None,
             method: Optional[str] = None, mode: Optional[str] = None,
             weights: Optional[List[float]] = None,
             allow_widen: bool = False) -> Allocation:
    """对一条尺寸链做公差分配。"""
    target = float(target_tol if target_tol is not None else (chain.target_tol or 0.0))
    method = (method or chain.alloc_method or "it").lower()
    mode = (mode or chain.alloc_mode or "wc").lower()
    if target <= 0:
        raise ValueError("分配目标公差必须大于 0（--target 或 Chain.target_tol）")
    if method not in ("equal", "it", "proportional"):
        raise ValueError(f"未知分配方法 {method!r}，可选 equal / it / proportional")
    if mode not in ("wc", "rss"):
        raise ValueError(f"未知计算口径 {mode!r}，可选 wc / rss")

    rings: List[Ring] = chain.rings
    alloc = Allocation(target_tol=target, mode=mode, method=method)

    fixed_tols = [r.tol for r in rings if r.fixed]
    free = [r for r in rings if not r.fixed]
    if not free:
        alloc.warnings.append("所有环都被标记为锁定（fixed），无法再分配。")
        alloc.items = [AllocItem(r.name, r.nominal, r.sign, True, r.tol, r.tol,
                                 grade_label(grade_coef(_safe_size(r), r.tol)),
                                 tol_unit(_safe_size(r)), 0.0) for r in rings]
        alloc.achieved_tol = _combine([r.tol for r in rings],
                                      [r.k for r in rings], mode)
        alloc.margin = target - alloc.achieved_tol
        return alloc

    fixed_ks = [r.k for r in rings if r.fixed]
    if mode == "rss":
        budget = sqrt(max(target ** 2 - sum((k * t) ** 2
                                            for t, k in zip(fixed_tols, fixed_ks)), 0.0))
    else:
        budget = max(target - sum(fixed_tols), 0.0)
    if budget <= 0:
        alloc.warnings.append(
            f"锁定环已占用全部目标公差（{_combine(fixed_tols, fixed_ks, mode):.3f} ≥ "
            f"{target:.3f}），只能放宽目标或收紧标准件/采购件。")

    if method == "it":
        weights = [tol_unit(_size_for_it(r)) for r in free]
    elif method == "proportional":
        weights = list(weights) if weights else [r.tol if r.tol > 0 else 1.0 for r in free]
    else:
        weights = [1.0] * len(free)

    caps: List[Optional[float]] = [None if allow_widen or r.tol <= 0 else r.tol for r in free]
    ks = [r.k for r in free]
    raw = _water_fill(budget, weights, ks, caps, mode)
    suggested = [round_down_preferred(t) for t in raw]

    free_map = {}
    for r, w, t_raw, t_sug, cap in zip(free, weights, raw, suggested, caps):
        size = _size_for_it(r)
        free_map[id(r)] = AllocItem(
            name=r.name, size=size, sign=r.sign, fixed=False,
            current_tol=r.tol, suggested_tol=t_sug,
            grade=grade_label(grade_coef(size, t_sug)),
            i_factor=tol_unit(size), weight=w,
            kept=cap is not None and cap <= t_sug + 1e-12 and r.tol > 0)

    for r in rings:  # 按输入顺序输出，环名可重复
        if r.fixed:
            size = _safe_size(r)
            alloc.items.append(AllocItem(
                name=r.name, size=size, sign=r.sign, fixed=True,
                current_tol=r.tol, suggested_tol=r.tol,
                grade=grade_label(grade_coef(size, r.tol)),
                i_factor=tol_unit(size), weight=0.0))
        else:
            alloc.items.append(free_map[id(r)])

    all_tols = [it.suggested_tol for it in alloc.items]
    alloc.achieved_tol = _combine(all_tols, [r.k for r in rings], mode)
    alloc.margin = target - alloc.achieved_tol
    if alloc.achieved_tol > target + 1e-9:
        alloc.warnings.append(
            f"分配后合成公差 {alloc.achieved_tol:.3f} 超出目标 {target:.3f}"
            "：现有公差已无处可收，需放宽目标或改结构减少组成环。")
    return alloc


def _safe_size(ring: Ring) -> float:
    try:
        return _size_for_it(ring)
    except ValueError:
        return 0.0


def apply_allocation(chain: Chain, alloc: Allocation) -> Chain:
    """把分配建议写入一条新尺寸链，用于"出图后回验"。"""
    new_rings: List[Ring] = []
    for ring, item in zip(chain.rings, alloc.items):
        tol = item.suggested_tol
        mean = ring.mean_dev
        new_rings.append(Ring(
            name=ring.name, nominal=ring.nominal,
            es=mean + tol / 2.0, ei=mean - tol / 2.0,
            sign=ring.sign, k=ring.k, e=ring.e, source=ring.source,
            fixed=ring.fixed, note=ring.note, ref_size=ring.ref_size))
    return Chain(
        title=chain.title + "（按分配建议出图）", rings=new_rings,
        closed_name=chain.closed_name, limit_low=chain.limit_low,
        limit_high=chain.limit_high, closed_nominal=chain.closed_nominal,
        target_tol=chain.target_tol, alloc_method=chain.alloc_method,
        alloc_mode=chain.alloc_mode, notes=chain.notes, gdt_rows=chain.gdt_rows)
