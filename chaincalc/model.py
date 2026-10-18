"""尺寸链数据模型。

术语遵循 GB/T 5847《尺寸链 计算方法》：
- 组成环：直接参与尺寸链的环，分增环（+1）与减环（-1）。
- 封闭环：装配/加工最后自然形成的一环，其偏差由各组成环累积决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

EPS = 1e-12


@dataclass
class Ring:
    """一个组成环。

    nominal : 基本尺寸 (mm)
    es / ei : 上 / 下偏差 (mm)，输入时若颠倒会自动纠正
    sign    : +1 增环 / -1 减环
    k       : 相对分布系数 k = 6*sigma/T，正态=1.0，三角=1.22，均匀=1.73，仅供概率法
    e       : 相对不对称系数，分布偏斜时对中间偏差的修正（一般取 0）
    fixed   : True 表示该环公差不可分配（标准件、已有工艺能力），分配时锁定
    """

    name: str
    nominal: float
    es: float
    ei: float
    sign: int = 1
    k: float = 1.0
    e: float = 0.0
    source: str = "尺寸"
    fixed: bool = False
    note: str = ""
    ref_size: Optional[float] = None  # 要素实际尺寸，仅供等精度法取 i(D)；GD&T 环必填

    def __post_init__(self) -> None:
        self.nominal = float(self.nominal)
        self.es = float(self.es)
        self.ei = float(self.ei)
        if self.es < self.ei:
            self.es, self.ei = self.ei, self.es
        if self.sign not in (1, -1):
            raise ValueError(f"环 {self.name!r}: sign 只能取 +1(增环) 或 -1(减环)")

    @property
    def tol(self) -> float:
        return self.es - self.ei

    @property
    def mean_dev(self) -> float:
        return (self.es + self.ei) / 2.0

    @property
    def kind(self) -> str:
        return "增环" if self.sign > 0 else "减环"

    @property
    def upper(self) -> float:
        return self.nominal + self.es

    @property
    def lower(self) -> float:
        return self.nominal + self.ei

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "nominal": self.nominal, "es": self.es, "ei": self.ei,
            "sign": self.sign, "k": self.k, "e": self.e, "source": self.source,
            "fixed": self.fixed, "note": self.note,
            "ref_size": self.ref_size,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Ring":
        return Ring(
            name=str(d["name"]), nominal=float(d["nominal"]),
            es=float(d.get("es", 0.0)), ei=float(d.get("ei", 0.0)),
            sign=int(d.get("sign", 1)), k=float(d.get("k", 1.0)),
            e=float(d.get("e", 0.0)), source=str(d.get("source", "尺寸")),
            fixed=bool(d.get("fixed", False)), note=str(d.get("note", "")),
            ref_size=_opt_float(d.get("ref_size")),
        )


@dataclass
class Chain:
    """一条完整的尺寸链，含封闭环的验收要求与分配参数。"""

    title: str = "未命名尺寸链"
    rings: List[Ring] = field(default_factory=list)
    closed_name: str = "封闭环"
    limit_low: Optional[float] = None      # 封闭环允许下限（绝对值）
    limit_high: Optional[float] = None     # 封闭环允许上限（绝对值）
    closed_nominal: Optional[float] = None  # 若给定，作为设计目标值（默认由组成环算出）
    target_tol: Optional[float] = None     # 反向分配时的目标封闭环公差
    alloc_method: str = "it"               # it / equal / proportional
    alloc_mode: str = "wc"                 # wc(极值法) / rss(概率法)
    notes: str = ""
    gdt_rows: List[Dict[str, Any]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rings)

    def add(self, ring: Ring) -> "Ring":
        self.rings.append(ring)
        return ring

    @property
    def has_limits(self) -> bool:
        return self.limit_low is not None or self.limit_high is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "closed_name": self.closed_name,
            "limit_low": self.limit_low,
            "limit_high": self.limit_high,
            "closed_nominal": self.closed_nominal,
            "target_tol": self.target_tol,
            "alloc_method": self.alloc_method,
            "alloc_mode": self.alloc_mode,
            "notes": self.notes,
            "rings": [r.to_dict() for r in self.rings],
            "gdt": list(self.gdt_rows),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Chain":
        rings = [Ring.from_dict(x) for x in d.get("rings", [])]
        gdt_rows = [dict(x) for x in (d.get("gdt") or [])]
        if gdt_rows:
            rings.extend(gdt_rows_to_rings(gdt_rows))
        if not rings:
            raise ValueError("尺寸链为空：至少需要一个组成环")
        return Chain(
            title=str(d.get("title", "未命名尺寸链")),
            rings=rings,
            closed_name=str(d.get("closed_name", "封闭环")),
            limit_low=_opt_float(d.get("limit_low")),
            limit_high=_opt_float(d.get("limit_high")),
            closed_nominal=_opt_float(d.get("closed_nominal")),
            target_tol=_opt_float(d.get("target_tol")),
            alloc_method=str(d.get("alloc_method", "it")),
            alloc_mode=str(d.get("alloc_mode", "wc")),
            notes=str(d.get("notes", "")),
            gdt_rows=gdt_rows,
        )


def gdt_rows_to_rings(rows: List[Dict[str, Any]]) -> List[Ring]:
    """把 GD&T 描述行换算成可入链的等效环（延迟导入避免循环依赖）。"""
    from .gdttol import gdt_to_ring

    rings: List[Ring] = []
    for row in rows:
        kind = str(row.get("kind", "减环"))
        effect = str(row.get("effect", "收紧"))
        conv = gdt_to_ring(
            name=str(row.get("name") or f"{row.get('feature', '?')}换算环"),
            feature=str(row["feature"]),
            value=float(row.get("value", 0.0)),
            sign=-1 if "减" in kind else 1,
            mmc=bool(row.get("mmc", False)),
            size_tol=float(row.get("size_tol", 0.0) or 0.0),
            bonus_mode=str(row.get("bonus_mode", "mean")),
            effect="loosen" if "放宽" in effect else "tighten",
            ref_size=float(row["ref_size"]) if row.get("ref_size") not in (None, "") else None,
            note=str(row.get("note", "")),
        )
        rings.append(conv.ring)
    return rings


def _opt_float(v: Any) -> Optional[float]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return float(v)
