"""命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from .allocate import allocate
from .calc import solve
from .model import Chain
from .report import console_summary, render_html, render_markdown


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chaincalc",
        description="尺寸链与公差堆叠计算工具：极值法 / 概率法(RSS) / 反向公差分配 / GD&T 换算")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("calc", help="求解尺寸链并输出计算书")
    c.add_argument("input", help="输入文件 .xlsx 或 .json")
    c.add_argument("--out", help="计算书输出路径（.md）")
    c.add_argument("--html", help="同时输出 HTML 计算书路径")
    c.add_argument("--target", type=float, help="覆盖分配目标公差 mm")
    c.add_argument("--method", choices=["it", "equal", "proportional"], help="分配方法")
    c.add_argument("--mode", choices=["wc", "rss"], help="分配口径")
    c.add_argument("--no-alloc", action="store_true", help="不做公差分配")
    c.add_argument("--quiet", action="store_true", help="只写文件，不打印计算书")

    a = sub.add_parser("alloc", help="只做反向公差分配")
    a.add_argument("input", help="输入文件 .xlsx 或 .json")
    a.add_argument("--target", type=float, required=True, help="封闭环目标公差 mm")
    a.add_argument("--method", choices=["it", "equal", "proportional"], default=None)
    a.add_argument("--mode", choices=["wc", "rss"], default=None)
    a.add_argument("--out", help="输出 Markdown 路径")

    g = sub.add_parser("gdt", help="GD&T 标注换算为等效尺寸环")
    g.add_argument("--feature", required=True, help="几何特征，如 位置度/同轴度/平面度")
    g.add_argument("--value", type=float, required=True, help="几何公差值 mm")
    g.add_argument("--sign", choices=["增环", "减环"], default="减环")
    g.add_argument("--mmc", action="store_true", help="带最大实体要求 M 圈")
    g.add_argument("--size-tol", type=float, default=0.0, help="该要素的尺寸公差 mm（算补偿用）")
    g.add_argument("--bonus-mode", choices=["none", "mean", "full"], default="mean")
    g.add_argument("--effect", choices=["tighten", "loosen"], default="tighten")
    g.add_argument("--one-way", action="store_true", help="强制单向公差带")
    g.add_argument("--two-way", action="store_true", help="强制双向公差带")

    t = sub.add_parser("template", help="生成 Excel 输入模板")
    t.add_argument("out", help="输出 .xlsx 路径")
    t.add_argument("--from", dest="src", help="用该 .json/.xlsx 的数据填充模板")

    sub.add_parser("features", help="列出支持的几何公差特征")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    _utf8()
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "calc":
            return _cmd_calc(args)
        if args.cmd == "alloc":
            return _cmd_alloc(args)
        if args.cmd == "gdt":
            return _cmd_gdt(args)
        if args.cmd == "template":
            return _cmd_template(args)
        if args.cmd == "features":
            return _cmd_features()
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 1


def _load(path: str) -> Chain:
    from .excel_io import read_chain
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到输入文件：{path}")
    return read_chain(path)


def _cmd_calc(args) -> int:
    chain = _load(args.input)
    res = solve(chain)
    alloc = None
    target = args.target if args.target is not None else chain.target_tol
    if not args.no_alloc and target:
        alloc = allocate(chain, target_tol=target, method=args.method, mode=args.mode)
    md = render_markdown(res, alloc, chain)

    print(console_summary(res))
    if alloc:
        print(f"  分配   : 目标 {alloc.target_tol} mm -> 合成 {alloc.achieved_tol:.4f} mm "
              f"({'可行' if alloc.feasible else '不可行'})")
    if not args.quiet:
        print()
        print(md)

    out = args.out or _default_out(args.input, ".md")
    _write(out, md)
    print(f"\n[已保存] {os.path.abspath(out)}")
    if args.html:
        _write(args.html, render_html(md, chain.title))
        print(f"[已保存] {os.path.abspath(args.html)}")
    return 0


def _cmd_alloc(args) -> int:
    chain = _load(args.input)
    alloc = allocate(chain, target_tol=args.target, method=args.method, mode=args.mode)
    res = solve(chain)
    md = render_markdown(res, alloc, chain)
    print(console_summary(res))
    print()
    print(f"{'环':<26}{'类型':<6}{'现公差':>10}{'建议公差':>12}{'等级':>16}")
    for it in alloc.items:
        flag = "  [锁定]" if it.fixed else ""
        print(f"{it.name:<26}{it.kind:<6}{it.current_tol:>10.3f}"
              f"{it.suggested_tol:>12.3f}{it.grade:>16}{flag}")
    print(f"\n合成公差 {alloc.achieved_tol:.4f} / 目标 {alloc.target_tol:.4f} mm "
          f"-> {'可行' if alloc.feasible else '不可行'}")
    for w in alloc.warnings:
        print(f"  ! {w}")
    if args.out:
        _write(args.out, md)
        print(f"[已保存] {os.path.abspath(args.out)}")
    return 0


def _cmd_gdt(args) -> int:
    from .gdttol import gdt_to_ring
    sym = None
    if args.one_way:
        sym = False
    if args.two_way:
        sym = True
    conv = gdt_to_ring(
        name=f"{args.feature}换算环", feature=args.feature, value=args.value,
        sign=1 if args.sign == "增环" else -1, symmetric=sym, mmc=args.mmc,
        size_tol=args.size_tol, bonus_mode=args.bonus_mode, effect=args.effect)
    print(f"几何特征   : {conv.feature}（{conv.cls}类）")
    print(f"标注值 t   : {conv.value:.4f} mm")
    if args.mmc:
        print(f"MMC 补偿量 : {conv.bonus:.4f} mm（方式 {args.bonus_mode}）")
    print(f"等效公差带 : {conv.t_eq:.4f} mm，{conv.direction}，k = {conv.k}")
    print(f"折算偏差   : ES={conv.ring.es:+.4f}  EI={conv.ring.ei:+.4f}  T={conv.ring.tol:.4f}")
    print(f"入链类型   : {conv.ring.kind}")
    print(f"建模说明   : {conv.note}")
    print(f"JSON 片段  : {conv.ring.to_dict()}")
    return 0


def _cmd_template(args) -> int:
    from .excel_io import write_template
    chain = None
    gdt_rows = None
    if args.src:
        chain = _load(args.src)
        gdt_rows = getattr(chain, "gdt_rows", None)
    path = write_template(args.out, chain, gdt_rows)
    print(f"[已生成] {os.path.abspath(path)}")
    return 0


def _cmd_features() -> int:
    from .gdttol import FEATURES
    print(f"{'特征':<10}{'类别':<6}{'公差带':<8}{'k':<6}说明")
    for name, spec in FEATURES.items():
        zone = "双向" if spec["symmetric"] else "单向"
        print(f"{name:<10}{spec['cls']:<6}{zone:<8}{spec['k']:<6}{spec['note']}")
    return 0


def _default_out(src: str, ext: str) -> str:
    base = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(os.path.dirname(os.path.abspath(src)), base + "_计算书" + ext)


def _write(path: str, text: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
