"""一致性自检：Excel 输入与 JSON 输入必须得到完全相同的结果。

    python tests/check_parity.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chaincalc.calc import solve
from chaincalc.excel_io import read_chain

CASES = ["case1_转轴轴向间隙链", "case2_花毂轴承配合链", "case3_塑胶五金配合链"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    bad = 0
    for case in CASES:
        d = os.path.join(ROOT, "cases", case)
        j, x = os.path.join(d, "input.json"), os.path.join(d, "input.xlsx")
        if not os.path.exists(j):
            print(f"{case}: 跳过（本地案例，未随仓库发布）")
            continue
        if not os.path.exists(x):
            print(f"{case}: 缺少 input.xlsx（先运行 python main.py template ... --from input.json）")
            bad += 1
            continue
        a, b = solve(read_chain(j)), solve(read_chain(x))
        ok = (len(a.chain.rings) == len(b.chain.rings)
              and abs(a.tol_wc - b.tol_wc) < 1e-9
              and abs(a.tol_rss - b.tol_rss) < 1e-9
              and abs(a.mean_dev - b.mean_dev) < 1e-9)
        print(f"{case}: 环数 {len(a.chain.rings)}/{len(b.chain.rings)}  "
              f"Twc {a.tol_wc:.4f}/{b.tol_wc:.4f}  "
              f"Trss {a.tol_rss:.4f}/{b.tol_rss:.4f}  "
              + ("OK" if ok else "MISMATCH"))
        bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
