"""GD&T（几何公差）标注 → 等效尺寸环换算。

换算原则（本工具的建模约定，均可在报告中复核）：

1. 一条几何公差 t 界定的"公差带宽度"就是它给尺寸链带来的变动量全宽，即 T_eq = t。
2. 位置类（位置度 / 同轴度 / 同心度 / 对称度）界定的是中心要素的可偏范围，
   按对称双向 ±t/2 处理，分布接近正态 → k = 1.0。
3. 形状 / 方向类（平面度 / 直线度 / 圆度 / 圆柱度 / 平行度 / 垂直度 / 倾斜度）
   是要素本身对理想形状的偏离，只会单向"吃掉"间隙 → 按 [0, t] 单向处理，
   分布按均匀分布 → k = 1.73。
4. 最大实体要求 Ⓜ 的补偿量 bonus = |实际尺寸 − MMC|。最坏情况 bonus = 0；
   概率法取 bonus 均值 = 尺寸公差 / 2。注意：若尺寸公差本身已作为一环入链，
   再叠加全额 bonus 会重复计算。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import tan, radians
from typing import Dict, Optional

from .model import Ring

# 特征 -> (类别, 是否对称双向, 默认 k, 说明)
FEATURES: Dict[str, dict] = {
    "位置度": dict(cls="位置", symmetric=True, k=1.0,
                note="φt 圆柱公差带，轴线/中心可在 ±t/2 内偏移，沿任一方向的投影全宽为 t"),
    "同轴度": dict(cls="位置", symmetric=True, k=1.0, note="轴线位于 φt 圆柱带内，偏心 ±t/2"),
    "同心度": dict(cls="位置", symmetric=True, k=1.0, note="中心偏移 ±t/2"),
    "对称度": dict(cls="位置", symmetric=True, k=1.0, note="中心面偏移 ±t/2"),
    "圆跳动": dict(cls="位置", symmetric=True, k=1.0,
                note="回转一周的最大读数差为 t，等效中心摆动 ±t/2"),
    "全跳动": dict(cls="位置", symmetric=True, k=1.0, note="整个表面的读数差为 t"),
    "平面度": dict(cls="形状", symmetric=False, k=1.73, note="实际表面凸凹 t，单向侵占间隙"),
    "直线度": dict(cls="形状", symmetric=False, k=1.73, note="轴线弯曲 t，单向侵占间隙"),
    "圆度": dict(cls="形状", symmetric=False, k=1.73, note="实际圆轮廓落在 t 环带内，单向侵占配合间隙"),
    "圆柱度": dict(cls="形状", symmetric=False, k=1.73, note="圆柱面落在 t 环带内，单向侵占配合间隙"),
    "平行度": dict(cls="方向", symmetric=False, k=1.73, note="两要素间距离在 t 带内变化，单向侵占间隙"),
    "垂直度": dict(cls="方向", symmetric=False, k=1.73, note="端面对轴线倾斜，造成轴向长度增加 t"),
    "倾斜度": dict(cls="方向", symmetric=False, k=1.73, note="角度偏差引起的线性量 t"),
    "面轮廓度": dict(cls="形状", symmetric=False, k=1.73, note="实际表面对理论轮廓偏离 t"),
    "线轮廓度": dict(cls="形状", symmetric=False, k=1.73, note="实际轮廓对理论轮廓偏离 t"),
}


@dataclass
class GdtConversion:
    feature: str
    value: float
    bonus: float
    t_eq: float
    symmetric: bool
    direction: str
    k: float
    cls: str
    note: str
    ring: Ring


def angular_to_linear(angle_deg: float, lever_mm: float, factor: float = 1.0) -> float:
    """角度公差（度）→ 指定杠杆长度上的线性公差 (mm)。"""
    return factor * lever_mm * tan(radians(angle_deg))


def mmc_bonus(size_tol: float, mode: str = "mean") -> float:
    """Ⓜ（最大实体要求）带来的补偿公差。

    mode='none' 最坏情况（不叠加补偿）；'mean' 取尺寸公差的一半；'full' 取全额。
    """
    m = (mode or "mean").lower()
    if m in ("none", "0", "worst", "worst-case"):
        return 0.0
    if m == "full":
        return abs(size_tol)
    return abs(size_tol) / 2.0


def gdt_to_ring(name: str, feature: str, value: float, sign: int = 1, *,
                symmetric: Optional[bool] = None, k: Optional[float] = None,
                mmc: bool = False, size_tol: float = 0.0, bonus_mode: str = "mean",
                effect: str = "tighten", nominal: float = 0.0,
                ref_size: Optional[float] = None,
                note: str = "") -> GdtConversion:
    """把一条 GD&T 标注换算成一个可入链的等效 Ring。

    effect='tighten' 表示该几何误差使封闭环（间隙）减小（最常见）；
    effect='loosen' 表示使其增大。
    """
    key = feature.strip()
    if key not in FEATURES:
        raise ValueError(f"未支持的几何公差特征 {feature!r}，可选：{'、'.join(FEATURES)}")
    spec = FEATURES[key]
    sym = spec["symmetric"] if symmetric is None else symmetric
    kk = spec["k"] if k is None else k
    bonus = mmc_bonus(size_tol, bonus_mode) if mmc else 0.0
    t_eq = abs(value) + bonus

    if sym:
        es, ei = t_eq / 2.0, -t_eq / 2.0
        direction = "双向 ±t/2"
    else:
        # 单向：等效为"让封闭环变小的方向"上变动 t
        reduce_sign = 1 if effect == "tighten" else -1
        if sign * reduce_sign > 0:
            es, ei = 0.0, -t_eq
        else:
            es, ei = t_eq, 0.0
        direction = "单向 [0, t]"

    ring = Ring(name=name, nominal=nominal, es=es, ei=ei, sign=sign, k=kk,
                source="GD&T", ref_size=ref_size,
                note=note or f"{key} {value}mm")
    return GdtConversion(
        feature=key, value=abs(value), bonus=bonus, t_eq=t_eq, symmetric=sym,
        direction=direction, k=kk, cls=spec["cls"], note=spec["note"], ring=ring)
