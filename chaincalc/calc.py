"""封闭环求解：极值法（完全互换法）与概率法（RSS，大数互换法）。

极值法：T0 = Σ Ti
        ES0 = Σ_{增环} ESi − Σ_{减环} EIi
        EI0 = Σ_{增环} EIi − Σ_{减环} ESi
概率法：Δ0 = Σ ξi·(Δi + ei·Ti/2)          中间偏差
        T0 = sqrt(Σ (ki·Ti)^2)              等价于 6σ0（ki 为相对分布系数）
        σ0 = T0 / 6
封闭环超差概率由正态近似计算：P = Φ((LSL-μ)/σ) + 1 - Φ((USL-μ)/σ)。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt
from typing import List, Optional

from .model import Chain, Ring


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


@dataclass
class ChainResult:
    chain: Chain
    nominal: float
    # 极值法
    es_wc: float
    ei_wc: float
    tol_wc: float
    # 概率法
    mean_dev: float
    tol_rss: float
    es_rss: float
    ei_rss: float
    sigma: float
    # 验收判定
    limit_low: Optional[float] = None
    limit_high: Optional[float] = None
    p_low: float = 0.0
    p_high: float = 0.0
    cp: Optional[float] = None
    cpk: Optional[float] = None

    # --- 便捷属性 ---
    @property
    def upper_wc(self) -> float:
        return self.nominal + self.es_wc

    @property
    def lower_wc(self) -> float:
        return self.nominal + self.ei_wc

    @property
    def upper_rss(self) -> float:
        return self.nominal + self.es_rss

    @property
    def lower_rss(self) -> float:
        return self.nominal + self.ei_rss

    @property
    def mean_value(self) -> float:
        return self.nominal + self.mean_dev

    @property
    def p_out(self) -> float:
        return self.p_low + self.p_high

    @property
    def ppm(self) -> float:
        return self.p_out * 1e6

    @property
    def tol_spec(self) -> Optional[float]:
        if self.limit_low is None or self.limit_high is None:
            return None
        return self.limit_high - self.limit_low

    @property
    def wc_pass(self) -> Optional[bool]:
        if not self.chain.has_limits:
            return None
        ok = True
        if self.limit_low is not None:
            ok = ok and self.lower_wc >= self.limit_low - 1e-12
        if self.limit_high is not None:
            ok = ok and self.upper_wc <= self.limit_high + 1e-12
        return ok

    @property
    def rss_pass(self) -> Optional[bool]:
        if not self.chain.has_limits:
            return None
        ok = True
        if self.limit_low is not None:
            ok = ok and self.lower_rss >= self.limit_low - 1e-12
        if self.limit_high is not None:
            ok = ok and self.upper_rss <= self.limit_high + 1e-12
        return ok

    @property
    def center_offset(self) -> float:
        """封闭环分布中心相对要求中心的偏移量 (mm)，正值表示偏上限。"""
        if self.limit_low is None or self.limit_high is None:
            return 0.0
        return self.mean_value - (self.limit_low + self.limit_high) / 2.0

    @property
    def wc_margin(self) -> Optional[float]:
        """极值法相对要求上下限的最小裕度（负值即超差）。"""
        if not self.chain.has_limits:
            return None
        m = []
        if self.limit_low is not None:
            m.append(self.lower_wc - self.limit_low)
        if self.limit_high is not None:
            m.append(self.limit_high - self.upper_wc)
        return min(m) if m else None

    @property
    def shrink_ratio(self) -> float:
        """极值法 / 概率法 公差带之比（≈ 组成环数的平方根）。"""
        return self.tol_wc / self.tol_rss if self.tol_rss > 0 else 0.0


def solve(chain: Chain) -> ChainResult:
    """求解一条尺寸链。"""
    rings: List[Ring] = chain.rings
    if not rings:
        raise ValueError("尺寸链为空：至少需要一个组成环")

    nominal = sum(r.sign * r.nominal for r in rings)

    es_wc = (sum(r.es for r in rings if r.sign > 0)
             - sum(r.ei for r in rings if r.sign < 0))
    ei_wc = (sum(r.ei for r in rings if r.sign > 0)
             - sum(r.es for r in rings if r.sign < 0))
    tol_wc = sum(r.tol for r in rings)

    mean_dev = sum(r.sign * (r.mean_dev + r.e * r.tol / 2.0) for r in rings)
    tol_rss = sqrt(sum((r.k * r.tol) ** 2 for r in rings))
    sigma = tol_rss / 6.0 if tol_rss > 0 else 0.0

    res = ChainResult(
        chain=chain, nominal=nominal,
        es_wc=es_wc, ei_wc=ei_wc, tol_wc=tol_wc,
        mean_dev=mean_dev, tol_rss=tol_rss,
        es_rss=mean_dev + tol_rss / 2.0,
        ei_rss=mean_dev - tol_rss / 2.0,
        sigma=sigma,
        limit_low=chain.limit_low, limit_high=chain.limit_high,
    )

    mu = nominal + mean_dev
    if sigma > 0:
        if chain.limit_low is not None:
            res.p_low = max(0.0, min(1.0, norm_cdf((chain.limit_low - mu) / sigma)))
        if chain.limit_high is not None:
            res.p_high = max(0.0, min(1.0, 1.0 - norm_cdf((chain.limit_high - mu) / sigma)))
    if chain.limit_low is not None and chain.limit_high is not None and sigma > 0:
        t_spec = chain.limit_high - chain.limit_low
        res.cp = t_spec / (6.0 * sigma)
        res.cpk = min(chain.limit_high - mu, mu - chain.limit_low) / (3.0 * sigma)
    return res


def contribution(chain: Chain) -> List[dict]:
    """各组成环对封闭环公差的贡献分解。

    极值法按公差线性占比（合计 100%）；概率法按方差份额 (kT)^2/Σ(kT)^2
    （合计 100%），后者才反映"收紧这一环能带来多少收益"。
    """
    rings = chain.rings
    tot_wc = sum(r.tol for r in rings) or 1.0
    tot_var = sum((r.k * r.tol) ** 2 for r in rings) or 1.0
    out = []
    for r in rings:
        var = (r.k * r.tol) ** 2
        out.append({
            "name": r.name,
            "kind": r.kind,
            "nominal": r.nominal,
            "ref_size": r.ref_size,
            "source": r.source,
            "tol": r.tol,
            "share_wc": r.tol / tot_wc,
            "share_rss": var / tot_var,
            "mean_contrib": r.sign * (r.mean_dev + r.e * r.tol / 2.0),
        })
    return out
