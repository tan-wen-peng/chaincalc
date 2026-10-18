"""计算书输出：Markdown / HTML。"""

from __future__ import annotations

import re
from typing import List, Optional

from .allocate import Allocation, apply_allocation
from .calc import ChainResult, contribution, solve
from .model import Chain

BT = chr(96)          # 反引号，避免源码里出现难读的转义
FENCE = BT * 3


def f(x: float, n: int = 3) -> str:
    """定点小数，保留末尾 0，符合工程图纸习惯。"""
    if x is None:
        return ""
    s = f"{x:.{n}f}"
    return "0" if s.strip("-0.") == "" else s


def sg(x: float, n: int = 3) -> str:
    """带正负号的偏差；恰好为 0 时只写 0。"""
    if abs(x) < 10 ** (-(n + 2)):
        return f(0.0, n)
    return ("+" if x > 0 else "-") + f(abs(x), n)


def pct(p: float) -> str:
    if p <= 0:
        return "0"
    if p < 1e-4:
        return f"{p:.2e}"
    return f"{p * 100:.3f}%"


def _size_label(c: dict) -> str:
    """GD&T 环没有独立的尺寸贡献，显示其要素尺寸。"""
    nominal = c.get("nominal") or 0.0
    ref = c.get("ref_size")
    if abs(nominal) < 1e-12:
        return f"要素 {f(ref)}" if ref else "—"
    return f(nominal)


def _table(headers: List[str], rows: List[List[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_markdown(res: ChainResult, alloc: Optional[Allocation] = None,
                    chain: Optional[Chain] = None) -> str:
    chain = chain or res.chain
    L: List[str] = []

    L.append(f"# 尺寸链计算书：{chain.title}")
    L.append("")
    L.append("> 由「尺寸链与公差堆叠计算工具」（chaincalc）自动生成。")
    L.append("> 方法依据 GB/T 5847《尺寸链 计算方法》、GB/T 1800.1 标准公差、ASME Y14.5 几何公差。")
    L.append("")

    L.append("## 1 输入条件")
    L.append("")
    if chain.notes:
        L.append(chain.notes)
        L.append("")
    L.append(_table(
        ["项目", "取值"],
        [["封闭环", chain.closed_name],
         ["组成环数", str(len(chain.rings))],
         ["封闭环要求下限", "—" if chain.limit_low is None else f"{f(chain.limit_low)} mm"],
         ["封闭环要求上限", "—" if chain.limit_high is None else f"{f(chain.limit_high)} mm"],
         ["要求公差带", "—" if res.tol_spec is None else f"{f(res.tol_spec)} mm"],
         ["分配目标公差", "—" if chain.target_tol is None else f"{f(chain.target_tol)} mm"]]))

    L.append("")
    L.append("### 1.1 组成环")
    L.append("")
    rows = []
    for i, r in enumerate(chain.rings, start=1):
        rows.append([str(i), r.name, r.kind, _size_label(r.to_dict()), sg(r.es), sg(r.ei),
                     f(r.tol), f(r.k, 2), "是" if r.fixed else "", r.source])
    L.append(_table(["#", "环名称", "类型", "基本尺寸", "上偏差", "下偏差",
                     "公差", "k", "锁定", "来源"], rows))

    L.append("")
    L.append("## 2 极值法（完全互换法）")
    L.append("")
    L.append("封闭环基本尺寸 A0 = Σ(ξi·Ai)，ξi = +1 为增环、-1 为减环。")
    L.append("")
    L.append(_table(["量", "计算式", "结果 (mm)"], [
        ["封闭环基本尺寸 A0", "Σ ξi·Ai", f(res.nominal)],
        ["上偏差 ES0", "Σ增环 ES − Σ减环 EI", sg(res.es_wc)],
        ["下偏差 EI0", "Σ增环 EI − Σ减环 ES", sg(res.ei_wc)],
        ["公差 T0", "Σ Ti", f(res.tol_wc)],
        ["极限范围", "A0+EI0 ~ A0+ES0", f"{f(res.lower_wc)} ~ {f(res.upper_wc)}"],
    ]))

    L.append("")
    L.append("## 3 概率法（大数互换法 / RSS）")
    L.append("")
    L.append("中间偏差 Δ0 = Σ(ξi·Δi)，封闭环公差 T0 = √Σ(ki·Ti)²，标准差 σ0 = T0/6。")
    L.append("")
    L.append(_table(["量", "计算式", "结果"], [
        ["中间偏差 Δ0", "Σ ξi·Δi", f"{sg(res.mean_dev)} mm"],
        ["封闭环公差 T0 (RSS)", "√Σ(ki·Ti)²", f"{f(res.tol_rss, 4)} mm"],
        ["标准差 σ0", "T0 / 6", f"{f(res.sigma, 4)} mm"],
        ["分布中心 μ", "A0 + Δ0", f"{f(res.mean_value)} mm"],
        ["±3σ 范围", "μ ± T0/2", f"{f(res.lower_rss)} ~ {f(res.upper_rss)} mm"],
        ["极值法/概率法", "T_wc / T_rss", f"{f(res.shrink_ratio, 2)} 倍"],
    ]))

    L.append("")
    L.append("## 4 合格性判定")
    L.append("")
    judge = []
    if res.tol_spec is not None:
        judge.append(["极值法是否满足", "是" if res.wc_pass else "否",
                      "—" if res.wc_margin is None else f"最小裕度 {sg(res.wc_margin)} mm"])
        judge.append(["概率法 ±3σ 是否满足", "是" if res.rss_pass else "否",
                      f"±3σ = {f(res.lower_rss)} ~ {f(res.upper_rss)} mm"])
        judge.append(["超差概率", pct(res.p_out),
                      f"下超差 {pct(res.p_low)}，上超差 {pct(res.p_high)}"])
        judge.append(["等效 PPM", f"{res.ppm:,.0f}", "百万件不合格数"])
        judge.append(["Cp", f(res.cp, 3) if res.cp else "—", "公差带 / 6σ"])
        judge.append(["Cpk", f(res.cpk, 3) if res.cpk else "—", "考虑中心偏移后的能力指数"])
        judge.append(["分布中心偏移", sg(res.center_offset), "μ 相对要求中心的偏移（正 = 偏上限）"])
    else:
        judge.append(["验收要求", "未给定", "仅输出公差累积结果"])
    L.append(_table(["判定项", "结果", "说明"], judge))

    L.append("")
    L.append("## 5 公差贡献分解")
    L.append("")
    contrib = contribution(chain)
    rows = []
    for c in sorted(contrib, key=lambda x: -x["share_rss"]):
        rows.append([c["name"], c["kind"], _size_label(c), f(c["tol"]),
                     pct(c["share_wc"]), pct(c["share_rss"]), sg(c["mean_contrib"])])
    L.append(_table(["环", "类型", "基本尺寸", "公差", "极值法占比", "概率法占比", "中间偏差贡献"], rows))
    if contrib:
        top = max(contrib, key=lambda c: c["share_rss"])
        L.append("")
        L.append(f"概率法口径下最大贡献环：**{top['name']}**（{pct(top['share_rss'])}）。"
                 "优化公差优先从这里入手，收紧小贡献环几乎没有收益。")

    if alloc:
        L.append("")
        L.append("## 6 反向公差分配建议")
        L.append("")
        L.append(f"目标：封闭环公差 {f(alloc.target_tol)} mm，口径 {alloc.mode.upper()}，"
                 f"方法 {alloc.method}。锁定环公差先扣除，剩余预算再分配。")
        L.append("")
        rows = []
        for it in alloc.items:
            rows.append([it.name, it.kind, f(it.size), f(it.current_tol),
                         f(it.suggested_tol), it.grade, it.symmetric_label, it.remark])
        L.append(_table(["环", "类型", "基本尺寸", "现公差", "建议公差",
                         "对应等级", "对称标注", "备注"], rows))
        L.append("")
        L.append(f"- 分配后合成公差：**{f(alloc.achieved_tol, 4)} mm**（目标 {f(alloc.target_tol)} mm）")
        L.append(f"- 余量：**{f(alloc.margin, 4)} mm**，"
                 + ("可行" if alloc.feasible else "不可行，需放宽目标或改结构"))
        for wmsg in alloc.warnings:
            L.append(f"- 警告：{wmsg}")
        vt = solve(apply_allocation(chain, alloc))
        L.append("")
        L.append("### 6.1 回验（按建议公差出图后重新求解）")
        L.append("")
        L.append(_table(["口径", "封闭环范围 (mm)", "是否满足要求", "备注"], [
            ["极值法", f"{f(vt.lower_wc, 4)} ~ {f(vt.upper_wc, 4)}",
             "是" if vt.wc_pass else "否", f"T0 = {f(vt.tol_wc, 4)} mm"],
            ["概率法", f"{f(vt.lower_rss, 4)} ~ {f(vt.upper_rss, 4)}",
             "是" if vt.rss_pass else "否",
             f"T0 = {f(vt.tol_rss, 4)} mm，超差率 {pct(vt.p_out)}"],
        ]))
        if vt.wc_pass:
            L.append("")
            L.append("按建议公差出图后，极值法即可满足，零件完全互换。")
        L.append("")
        L.append("> 建议公差已向下取整到可标注数值；实际出图时按对称标注 ±T/2 或按配合制度标注"
                 "（孔 H、轴 h 等），并复核是否落在本厂工艺能力内。")

    L.append("")
    L.append("## 7 结论与建议")
    L.append("")
    for line in conclusion_lines(res, alloc):
        L.append(f"- {line}")
    L.append("")

    L.append("## 附录 计算公式")
    L.append("")
    L.append(FENCE)
    L.append("增环/减环判定：使封闭环增大的环为增环(ξ=+1)，反之为减环(ξ=-1)")
    L.append("极值法 : A0=ΣξiAi  ES0=Σ_增环ESi-Σ_减环EIi  EI0=Σ_增环EIi-Σ_减环ESi  T0=ΣTi")
    L.append("概率法 : Δi=(ESi+EIi)/2  Δ0=ΣξiΔi  T0=√(Σ(kiTi)^2)  σ0=T0/6")
    L.append("超差率 : P=P[X<LSL]+P[X>USL]=Φ((LSL-μ)/σ)+1-Φ((USL-μ)/σ), μ=A0+Δ0")
    L.append("等精度 : Ti = a·i(Ai), i=0.45·D^(1/3)+0.001·D (µm), a 为公差等级系数")
    L.append(FENCE)
    L.append("")
    return "\n".join(L)


def conclusion_lines(res: ChainResult, alloc: Optional[Allocation]) -> List[str]:
    out: List[str] = []
    chain = res.chain
    if res.tol_spec is None:
        out.append(f"封闭环公差累积：极值法 T0={f(res.tol_wc)} mm，概率法 T0={f(res.tol_rss)} mm。")
        return out

    if res.wc_pass:
        out.append(f"极值法即满足要求（T0={f(res.tol_wc)} ≤ 要求 {f(res.tol_spec)} mm），"
                   "零件完全互换，无需选配或概率论证。")
    elif res.rss_pass:
        out.append("极值法超差但概率法可行：统计意义上装配超差率 " + pct(res.p_out) +
                   "。需要来料管控 + 关键环抽检，不能完全依赖互换性。")
    else:
        out.append("极值法与概率法均不满足要求，必须修改设计或收紧公差。")

    if (res.cpk is not None and res.cpk < 1.0
            and abs(res.center_offset) > 0.10 * (res.tol_spec or 1)):
        out.append(f"**首要问题不是公差太大，而是分布中心偏移 {sg(res.center_offset)} mm**"
                   f"（Cpk={f(res.cpk, 2)}，Cp={f(res.cp, 2)}）。把名义尺寸朝"
                   f"{'减小' if res.center_offset > 0 else '增大'}方向调整 "
                   f"{f(abs(res.center_offset))} mm，Cpk 可显著改善且几乎不增加成本。")

    if not res.wc_pass and res.tol_spec is not None:
        gap = res.tol_wc - res.tol_spec
        out.append(f"若要求极值法（100% 互换）通过，当前累积公差 {f(res.tol_wc)} mm "
                   f"需压缩 {f(gap)} mm，压缩比 {pct(gap / res.tol_wc)}。")

    if alloc:
        if alloc.feasible:
            out.append(f"按 {alloc.method} 法分配，合成公差 {f(alloc.achieved_tol, 4)} mm "
                       f"满足目标 {f(alloc.target_tol)} mm，可直接用于出图。")
        else:
            out.append("当前目标公差下分配不可行，建议放宽目标、改用概率法验收，或减少组成环数量。")

    if res.p_high > res.p_low * 5 and res.p_high > 0.0001:
        out.append("超差集中在上限侧：优先检查各环偏差是否系统性偏正（如轴类均按上偏差加工）。")
    elif res.p_low > res.p_high * 5 and res.p_low > 0.0001:
        out.append("超差集中在下限侧：存在干涉风险，优先核查轴承、垫片等标准件的实际偏差分布。")
    return out


def render_html(md: str, title: str = "尺寸链计算书") -> str:
    body: List[str] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(FENCE):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith(FENCE):
                buf.append(_esc(lines[i]))
                i += 1
            body.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            body.append(_html_table(rows))
            continue
        if line.startswith("#"):
            lvl = len(line) - len(line.lstrip("#"))
            lvl = min(lvl, 6)
            body.append(f"<h{lvl}>{_inline(line[len(line) - len(line.lstrip('#')):].strip())}</h{lvl}>")
        elif line.startswith("> "):
            body.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            body.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif line.strip() == "":
            body.append("")
        else:
            body.append(f"<p>{_inline(line)}</p>")
        i += 1

    css = """
body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;max-width:1040px;
margin:32px auto;padding:0 20px;line-height:1.7;color:#1f2328;background:#fff}
h1{border-bottom:2px solid #d0d7de;padding-bottom:.3em}
h2{border-bottom:1px solid #e5e7eb;padding-bottom:.2em;margin-top:1.6em}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}
th,td{border:1px solid #d0d7de;padding:6px 10px;text-align:left}
th{background:#f6f8fa}tr:nth-child(even){background:#fbfcfd}
code{background:#f6f8fa;padding:1px 4px;border-radius:4px;font-size:13px}
pre{background:#f6f8fa;padding:12px;border-radius:6px;overflow-x:auto}
pre code{background:none;padding:0}
blockquote{color:#57606a;border-left:4px solid #d0d7de;margin:0;padding:0 12px}
"""
    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{_esc(title)}</title>\n<style>{css}</style>\n</head>\n<body>\n"
            + "\n".join(body) + "\n</body>\n</html>\n")


def _html_table(rows: List[str]) -> str:
    out = ["<table>"]
    for k, r in enumerate(rows):
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        tag = "th" if k == 0 else "td"
        out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


def _inline(s: str) -> str:
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(BT + r"([^" + BT + r"]+)" + BT, r"<code>\1</code>", s)
    return s


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def console_summary(res: ChainResult) -> str:
    c = res.chain
    out = [f"尺寸链: {c.title}   组成环 {len(c.rings)} 个",
           f"  封闭环基本尺寸 A0 = {f(res.nominal, 4)} mm",
           f"  极值法 : {sg(res.es_wc)} / {sg(res.ei_wc)}   T0 = {f(res.tol_wc, 4)} mm"
           f"   范围 {f(res.lower_wc, 4)} ~ {f(res.upper_wc, 4)}",
           f"  概率法 : {sg(res.es_rss)} / {sg(res.ei_rss)}   T0 = {f(res.tol_rss, 4)} mm"
           f"   范围 {f(res.lower_rss, 4)} ~ {f(res.upper_rss, 4)}"]
    if res.tol_spec is not None:
        out.append(f"  要求   : {f(res.limit_low, 4)} ~ {f(res.limit_high, 4)} mm"
                   f"  (T = {f(res.tol_spec, 4)})")
        out.append(f"  判定   : 极值法 {'PASS' if res.wc_pass else 'FAIL'} | "
                   f"概率法 {'PASS' if res.rss_pass else 'FAIL'} | 超差率 {pct(res.p_out)}"
                   + (f" | Cp={f(res.cp, 2)} Cpk={f(res.cpk, 2)}" if res.cp else ""))
    return "\n".join(out)
