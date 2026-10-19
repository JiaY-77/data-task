#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyze.py —— 现场深挖用的万能刀：分组、占比、趋势、交叉、比率，一条命令一张表

用法示例（把列名换成你数据里的真实列名）:
    # 1) 分组汇总 + 占比 + 累计占比（最常用）
    python analyze.py 数据.csv --group 品类 --metric 成交金额 --top 15

    # 2) 分组 × 月份 交叉趋势（找增长/下滑的类目）
    python analyze.py 数据.csv --group 品类 --metric 成交金额 --date 下单时间 --freq M

    # 3) 比率分析：退货率 by 类目（--rate 填 0/1 列或"是/否"列）
    python analyze.py 数据.csv --rate 是否退货 --group 品类

    # 4) 交叉透视表
    python analyze.py 数据.csv --pivot 品类 国家 成交金额

    # 5) 只数个数（订单数 by 国家）
    python analyze.py 数据.csv --group 国家 --agg count

通用参数:
    --top N        只显示前 N 行（默认 20）
    --out 文件名    另存 CSV（默认自动命名）
    --sort-by      排序依据列
"""
import argparse
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd

ENCODINGS = ["utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1"]


def load(path):
    raw = open(path, "rb").read(262144)
    enc = "utf-8-sig"
    for e in ENCODINGS:
        try:
            raw.decode(e); enc = e; break
        except Exception:
            continue
    head = open(path, "r", encoding=enc, errors="replace").read(8192)
    first = head.splitlines()[0] if head.splitlines() else ""
    delim = max([",", "\t", ";", "|"], key=lambda d: first.count(d))
    return pd.read_csv(path, sep=delim, encoding=enc, low_memory=False,
                       on_bad_lines="skip"), enc


def tonum(s):
    return pd.to_numeric(s, errors="coerce")


def pct(x, d=2):
    try:
        return f"{float(x)*100:.{d}f}%"
    except Exception:
        return "-"


def money(x):
    try:
        x = float(x)
    except Exception:
        return "-"
    if abs(x) >= 1e8:
        return f"{x/1e8:.2f}亿"
    if abs(x) >= 1e4:
        return f"{x/1e4:.2f}万"
    return f"{x:,.2f}"


def print_table(df, max_rows=None):
    if max_rows:
        df = df.head(max_rows)
    with pd.option_context("display.width", 200, "display.max_columns", 50,
                           "display.float_format", lambda v: f"{v:,.2f}"):
        print(df.to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--group", nargs="+", help="分组列（可多列）")
    ap.add_argument("--metric", help="指标列（金额/数量等）")
    ap.add_argument("--agg", default="sum", choices=["sum", "mean", "count", "nunique", "median", "max", "min"])
    ap.add_argument("--date", help="时间列（与 --group 同用出趋势）")
    ap.add_argument("--freq", default="M", choices=["D", "W", "M", "Q", "Y"])
    ap.add_argument("--rate", help="比率列（0/1 或 是/否），输出比率而不是求和")
    ap.add_argument("--pivot", nargs=3, metavar=("行", "列", "值"))
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default=None)
    ap.add_argument("--sort-by", default=None)
    a = ap.parse_args()

    df, enc = load(a.path)
    print(f"# {os.path.basename(a.path)} · {len(df):,} 行 · 编码 {enc}\n")

    # ========== 模式 1: 比率 ==========
    if a.rate:
        if a.rate not in df.columns:
            print(f"[X] 找不到列 `{a.rate}`。现有列：{list(df.columns)}"); return
        col = df[a.rate]
        # 归一化成 0/1
        if col.dtype == object:
            m = {"是": 1, "否": 0, "true": 1, "false": 0, "yes": 1, "no": 0,
                 "y": 1, "n": 0, "1": 1, "0": 0, "退货": 1, "正常": 0}
            v = col.astype(str).str.strip().str.lower().map(m)
            v = v.where(v.notna(), tonum(col))
        else:
            v = tonum(col)
        v = (v > 0).astype(float).where(v.notna())
        if a.group:
            g = df.assign(_r=v).groupby(a.group)["_r"].agg(["mean", "count"])
            g.columns = ["比率", "样本数"]
            g["比率"] = (g["比率"] * 100).round(2)
            g = g.sort_values("比率", ascending=False)
            print(f"## `{a.rate}` 比率 by {a.group}（单位 %）\n")
            print_table(g, a.top)
            print(f"\n总体比率：{pct(v.mean())}（有效样本 {int(v.notna().sum()):,}）")
            out = a.out or f"rate_{a.rate}.csv"
        else:
            print(f"总体比率：{pct(v.mean())}（有效样本 {int(v.notna().sum()):,}）")
            print(f"分组明细："); print_table(v.value_counts(dropna=False).rename("计数").to_frame())
            out = a.out or f"rate_{a.rate}.csv"
            pd.DataFrame({"指标": [a.rate], "比率": [round(float(v.mean())*100, 2) if v.notna().any() else None]}).to_csv(out, index=False, encoding="utf-8-sig")
            print(f"\n[OK] → {out}"); return
        g.reset_index().to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n[OK] → {out}")
        return

    # ========== 模式 2: 交叉透视 ==========
    if a.pivot:
        r, c, v = a.pivot
        for x in (r, c, v):
            if x not in df.columns:
                print(f"[X] 找不到列 `{x}`。现有列：{list(df.columns)}"); return
        pt = pd.pivot_table(df, index=r, columns=c, values=v, aggfunc="sum", fill_value=0)
        print(f"## 透视：行={r} 列={c} 值={v}（sum）\n")
        print_table(pt)
        out = a.out or f"pivot_{r}_{c}_{v}.csv"
        pt.to_csv(out, encoding="utf-8-sig")
        print(f"\n[OK] → {out}")
        return

    # ========== 模式 3: 分组 × 月份趋势 ==========
    if a.group and a.date and a.metric:
        if a.date not in df.columns or a.metric not in df.columns:
            print(f"[X] 列名不对。现有列：{list(df.columns)}"); return
        d = df.copy()
        d["_m"] = pd.to_datetime(d[a.date], errors="coerce", format="mixed").dt.to_period(a.freq)
        d[a.metric] = tonum(d[a.metric])
        d = d.dropna(subset=["_m"])
        pt = d.pivot_table(index="_m", columns=a.group, values=a.metric,
                           aggfunc=a.agg, fill_value=0)
        print(f"## {a.metric} by {a.group} × {a.freq}（{a.agg}）\n")
        print_table(pt)
        out = a.out or f"trend_{'-'.join(a.group)}_{a.metric}.csv"
        pt.to_csv(out, encoding="utf-8-sig")
        # 首末对比：剔掉首末期（常见不完整），避免算出"全部暴跌"的假结论
        if len(pt) >= 4:
            core = pt.iloc[1:-1]
            note = "（已剔除首/末期，防不完整期造成假涨跌）"
        elif len(pt) >= 2:
            core = pt
            note = "（期数不足 4，首末期可能不完整，结论需谨慎）"
        else:
            core = None
            note = ""
        if core is not None and len(core) >= 2:
            first, last = core.iloc[0], core.iloc[-1]
            chg = ((last - first) / first.replace(0, np.nan)).dropna().sort_values(ascending=False)
            if len(chg):
                print(f"\n### {core.index[0]} → {core.index[-1]} 变化 {note}")
                print_table(chg.head(5).rename("涨幅").to_frame(), 5)
                if len(chg) > 5:
                    print_table(chg.tail(3).rename("跌幅").to_frame(), 3)
        print(f"\n[OK] → {out}")
        return

    # ========== 模式 4: 普通分组汇总 ==========
    if not a.group:
        print("[X] 至少给一个 --group，或用 --rate / --pivot")
        print(f"现有列：{list(df.columns)}")
        return

    for x in a.group:
        if x not in df.columns:
            print(f"[X] 找不到列 `{x}`。现有列：{list(df.columns)}"); return

    d = df.copy()
    if a.metric:
        if a.metric not in d.columns:
            print(f"[X] 找不到指标列 `{a.metric}`。现有列：{list(df.columns)}"); return
        d[a.metric] = tonum(d[a.metric])

    if a.agg == "nunique" and not a.metric:
        print("[X] --agg nunique 需要 --metric"); return

    if a.metric:
        g = d.groupby(a.group)[a.metric].agg([a.agg, "count"])
        g.columns = [a.metric, "记录数"]
    else:
        g = d.groupby(a.group).size().to_frame("记录数")

    # 先排序，再算占比与累计占比（顺序错了会让累计占比错位）
    if a.metric:
        total = float(g[a.metric].sum())
        g = g.sort_values(a.metric, ascending=False)
        if a.agg in ("sum", "count", "max", "min"):
            g["占比"] = (g[a.metric] / total).round(4) if total else np.nan
            g["累计占比"] = g["占比"].cumsum().round(4)
    else:
        g = g.sort_values("记录数", ascending=False)
        g["占比"] = (g["记录数"] / g["记录数"].sum()).round(4)
        g["累计占比"] = g["占比"].cumsum().round(4)

    print(f"## {a.metric or '记录数'} by {a.group}（{a.agg}，Top {a.top}）\n")
    print_table(g, a.top)

    # 自动洞察
    key = a.metric or "记录数"
    try:
        top1 = g.index[0]
        cr3 = float(g["占比"].head(3).sum())
        print(f"\n### 一句话\n- 最大项：**{top1}**，占 {pct(g['占比'].iloc[0])}")
        print(f"- 前三集中度 CR3 = **{pct(cr3)}**" + ("（高度集中，头部风险）" if cr3 > 0.8 else "（相对分散）" if cr3 < 0.5 else ""))
        if a.metric and a.agg == "sum":
            print(f"- 合计：**{money(total)}**，共 {len(g):,} 个 {a.group[0]} 取值")
    except Exception:
        pass

    out = a.out or f"group_{'-'.join(a.group)}.csv"
    g.reset_index().to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n[OK] → {out}")


if __name__ == "__main__":
    main()
