"""命令行入口（无需安装，直接 python main.py 运行）。

    python main.py calc     输入.xlsx
    python main.py alloc    输入.xlsx --target 0.25
    python main.py gdt      --feature 位置度 --value 0.2 --mmc --size-tol 0.2
    python main.py template 模板.xlsx
"""

import sys

from chaincalc.cli import main

if __name__ == "__main__":
    sys.exit(main())
