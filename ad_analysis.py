#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ad_analysis.py —— 广告投放效率专项（ACOS 结构分析）
用法: python ad_analysis.py mock_ads.csv [--outdir out_ads]
"""
import argparse, os, sys
import numpy as np, pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def money(x):
    x = float(x)
    return f"{x/1e4:,.2f}万" if abs(x) >= 1e4 else f"{x:,.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--outdir", default="out_ads")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    df = pd.read_csv(a.path, encoding="utf-8-sig", low_memory=False)
    df["_spend"] = pd.to_numeric(df["spend"], errors="coerce")
    df["_sales"] = pd.to_numeric(df["sales"], errors="coerce")
    df["_orders"] = pd.to_numeric(df["orders"], errors="coerce")

    ts, tv = df["_spend"].sum(), df["_sales"].sum()
    print("=" * 64)
    print("广告投放效率总览")
    print("=" * 64)
    print(f"  总花费     {money(ts)}")
    print(f"  广告销售额 {money(tv)}")
    print(f"  整体 ACOS  {ts/tv:.2%}   （每卖出 100 元，花掉 {ts/tv*100:.1f} 元广告费）")
    print(f"  广告订单   {df['_orders'].sum():,.0f}")
    print(f"  平均 CPC   {ts/df['clicks'].sum():.2f} 元")

    # 零转化浪费
    waste = df[(df["_spend"] > 0) & (df["_orders"] == 0)]
    print(f"\n  ⚠️ 零转化花费：{money(waste['_spend'].sum())}"
          f"（占 {waste['_spend'].sum()/ts:.1%}，{len(waste):,} 行）")

    # 负花费异常
    neg = df[df["_spend"] < 0]
    if len(neg):
        print(f"  ⚠️ 负花费行 {len(neg):,} 行，合计 {money(neg['_spend'].sum())}（冲红/调整，需说明口径）")

    def by(col, name):
        g = df.groupby(col).agg(花费=("_spend", "sum"), 销售额=("_sales", "sum"),
                                订单=("_orders", "sum"), 点击=("clicks", "sum"),
                                曝光=("impressions", "sum"))
        g["ACOS"] = (g["花费"] / g["销售额"]).round(4)
        g["花费占比"] = (g["花费"] / ts).round(4)
        g["销售占比"] = (g["销售额"] / tv).round(4)
        g["CTR"] = (g["点击"] / g["曝光"]).round(4)
        g["转化率"] = (g["订单"] / g["点击"]).round(4)
        g = g.sort_values("花费", ascending=False)
        p = os.path.join(a.outdir, f"ads_by_{col}.csv")
        g.reset_index().to_csv(p, index=False, encoding="utf-8-sig")
        print("\n" + "=" * 64)
        print(f"{name}结构（按花费降序）")
        print("=" * 64)
        show = g.head(a.top).copy()
        show["花费"] = show["花费"].map(money)
        show["销售额"] = show["销售额"].map(money)
        show["ACOS"] = show["ACOS"].map(lambda v: f"{v:.1%}" if pd.notna(v) else "-")
        show["花费占比"] = show["花费占比"].map(lambda v: f"{v:.1%}")
        show["CTR"] = show["CTR"].map(lambda v: f"{v:.2%}")
        show["转化率"] = show["转化率"].map(lambda v: f"{v:.1%}")
        print(show[["花费", "花费占比", "销售额", "销售占比", "ACOS", "CTR", "转化率", "订单"]].to_string())
        print(f"  → {p}")
        return g

    g_camp = by("campaign", "广告活动 campaign")
    g_match = by("match_type", "匹配方式 match_type")
    g_kw = by("keyword", "关键词 keyword")

    # 差效率关键词（花费高但 ACOS 差）
    bad = g_kw[(g_kw["花费"] > g_kw["花费"].quantile(0.5)) & (g_kw["ACOS"] > ts / tv * 1.5)]
    if len(bad):
        print("\n" + "=" * 64)
        print(f"⚠️ 低效关键词：花费高于中位数，且 ACOS 高于整体 1.5 倍（共 {len(bad)} 个）")
        print("=" * 64)
        b = bad.head(10).copy()
        b["花费"] = b["花费"].map(money)
        b["ACOS"] = b["ACOS"].map(lambda v: f"{v:.1%}")
        print(b[["花费", "销售额", "ACOS", "订单"]].to_string())
        bad.reset_index().to_csv(os.path.join(a.outdir, "ads_low_efficiency.csv"),
                                 index=False, encoding="utf-8-sig")

    return g_camp, g_match, g_kw


if __name__ == "__main__":
    main()
