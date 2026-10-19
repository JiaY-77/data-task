#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""plot_ads.py —— 广告投放图（花费/销售结构 + 零转化损耗）"""
import os, sys
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian"]
plt.rcParams["axes.unicode_minus"] = False

SRC = sys.argv[1] if len(sys.argv) > 1 else "mock_ads.csv"
OUT = sys.argv[2] if len(sys.argv) > 2 else "out_ads"
os.makedirs(OUT, exist_ok=True)

df = pd.read_csv(SRC, encoding="utf-8-sig", low_memory=False)
df["_spend"] = pd.to_numeric(df["spend"], errors="coerce")
df["_sales"] = pd.to_numeric(df["sales"], errors="coerce")
df["_orders"] = pd.to_numeric(df["orders"], errors="coerce")

# 图1：campaign 的花费与销售额 + ACOS
g = df.groupby("campaign").agg(spend=("_spend", "sum"), sales=("_sales", "sum"))
g["acos"] = g["spend"] / g["sales"]
g = g.sort_values("spend", ascending=False)

fig, ax = plt.subplots(figsize=(10, 4.6))
x = np.arange(len(g))
w = 0.38
b1 = ax.bar(x - w/2, g["spend"]/1e4, w, label="花费", color="#5B8FF9")
b2 = ax.bar(x + w/2, g["sales"]/1e4, w, label="广告销售额", color="#61DDAA")
ax.set_xticks(x); ax.set_xticklabels(g.index, rotation=20, ha="right")
ax.set_ylabel("金额（万元）")
ax.set_ylim(0, max(g["spend"].max(), g["sales"].max()) / 1e4 * 1.2)
ax.set_title("各广告活动的花费与产出（万元）", fontsize=13, fontweight="bold")
ax.legend(loc="upper left", framealpha=0.9)
ax2 = ax.twinx()
ax2.plot(x, g["acos"]*100, "o--", color="#F6685E", linewidth=2, label="ACOS")
for i, v in enumerate(g["acos"]*100):
    ax2.text(i, v + 0.3, f"{v:.1f}%", ha="center", fontsize=8, color="#F6685E")
ax2.set_ylabel("ACOS (%)"); ax2.set_ylim(0, max(g["acos"]*100)*1.35)
ax2.legend(loc="upper right")
plt.tight_layout(); plt.savefig(os.path.join(OUT, "fig_ads_campaign.png"), dpi=150, bbox_inches="tight"); plt.close()
print("[OK] fig_ads_campaign.png")

# 图2：整体花费去向（零转化 vs 有转化）
zero = df[(df["_spend"] > 0) & (df["_orders"] == 0)]["_spend"].sum()
okc = df["_spend"].sum() - zero
fig, ax = plt.subplots(figsize=(6.4, 4.4))
ax.pie([okc, zero], labels=["有转化花费", "零转化花费"], autopct="%1.1f%%",
       colors=["#5B8FF9", "#F6BD16"], startangle=90,
       textprops={"fontsize": 11})
ax.set_title(f"广告花费去向（零转化 {zero/1e4:.2f} 万）", fontsize=13, fontweight="bold")
plt.tight_layout(); plt.savefig(os.path.join(OUT, "fig_ads_waste.png"), dpi=150, bbox_inches="tight"); plt.close()
print("[OK] fig_ads_waste.png")
