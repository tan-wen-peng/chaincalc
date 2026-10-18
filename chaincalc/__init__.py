"""尺寸链与公差堆叠计算工具（Dimension Chain / Tolerance Stack-up）。"""

__version__ = "0.1.0"

from .model import Chain, Ring
from .calc import ChainResult, solve
from .allocate import Allocation, allocate
from .gdttol import gdt_to_ring

__all__ = [
    "Chain", "Ring", "ChainResult", "solve",
    "Allocation", "allocate", "gdt_to_ring", "__version__",
]
