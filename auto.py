#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
auto.py —— 一条命令跑完 80% 的活：体检 → 核心KPI → 趋势 → 结构 → 异常 → 出图 → README 草稿

用法:
    python auto.py 数据.csv
    python auto.py 数据.csv --outdir out --top 15

现场用法（30 分钟考试）:
    第 0-2 分钟   python auto.py 数据.csv
    第 2-8 分钟   读 out/README.md + out/kpi.md，定主问题
    第 8-20 分钟  补 1-2 个深挖分析（analyze.py）
    第 20-26 分钟 改 README 的"结论/建议"两段
    第 26-30 分钟 git push

设计原则: 每块都独立 try/except，任何一块失败不影响其他块；绝不编造数字。
"""
import argparse
import os
import sys
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian"]
plt.rcParams["axes.unicode_minus"] = False

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from scan import scan, ROLE_CN  # noqa: E402

PALETTE = ["#2E5EAA", "#5B8FF9", "#61DDAA", "#65789B", "#F6BD16", "#7262FD", "#78D3F8", "#9661BC"]


def fmt_money(x):
    try:
        x = float(x)
    except Exception:
        return "-"
    if abs(x) >= 1e8:
        return f"{x/1e8:.2f} 亿"
    if abs(x) >= 1e4:
        return f"{x/1e4:.2f} 万"
    return f"{x:,.2f}"


def pct(x, d=1):
    try:
        return f"{float(x)*100:.{d}f}%"
    except Exception:
        return "-"


def pick(cols, role, kinds=None):
    """按业务角色挑第一个可用列。"""
    for c in cols:
        if c["role"] == role:
            if kinds is None or c["kind"] in kinds:
                return c["name"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    log = []

    def ok(msg):
        print(f"  [OK] {msg}")
        log.append(f"- [OK] {msg}")

    def bad(msg, e=None):
        print(f"  [!] {msg}" + (f" — {type(e).__name__}: {e}" if e else ""))
        log.append(f"- [!] {msg}")

    print("=" * 62)
    print("① 体检")
    print("=" * 62)
    info, df = scan(a.path)
    print(f"  {info['rows']:,} 行 × {info['cols']} 列 · {info['size_mb']}MB · {info['elapsed']}s")
    ok(f"体检完成：{info['rows']:,} 行 × {info['cols']} 列")

    cols = info["columns"]
    C = {
        "date": pick(cols, "date"),
        "rev": pick(cols, "revenue"),
        "price": pick(cols, "price"),
        "qty": pick(cols, "qty"),
        "cost": pick(cols, "cost"),
        "profit": pick(cols, "profit"),
        "cat": pick(cols, "category"),
        "sku": pick(cols, "sku"),
        "country": pick(cols, "country"),
        "status": pick(cols, "status"),
        "ret": pick(cols, "return"),
        "order": pick(cols, "order_id"),
        "cust": pick(cols, "customer"),
        "ship": pick(cols, "shipping"),
    }

    # 如果没有 revenue，用 price*qty 兜底
    if not C["rev"] and C["price"] and C["qty"]:
        try:
            df["_rev"] = pd.to_numeric(df[C["price"]], errors="coerce") * pd.to_numeric(df[C["qty"]], errors="coerce")
            C["rev"] = "_rev"
            ok("无金额列 → 用 单价×数量 生成 _rev")
        except Exception as e:
            bad("生成金额失败", e)

    # 数值化
    for k in ("rev", "price", "qty", "cost", "profit", "ret"):
        c = C.get(k)
        if c and c != "_rev" and c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # 日期化
    C["date_raw"] = C.get("date")
    if C["date"] and C["date"] in df.columns:
        try:
            df["_dt"] = pd.to_datetime(df[C["date"]], errors="coerce", format="mixed")
            C["date"] = "_dt"
        except Exception as e:
            bad("日期解析失败", e)

    # ---------- ② KPI ----------
    print("\n" + "=" * 62)
    print("② 核心 KPI")
    print("=" * 62)
    kpi = {}
    try:
        n = len(df)
        orders = int(df[C["order"]].nunique()) if C["order"] else n
        kpi["行数(记录粒度)"] = f"{n:,}"
        kpi["订单数(去重)"] = f"{orders:,}"
        if C["rev"]:
            total = float(df[C["rev"]].sum())
            kpi["总营收"] = fmt_money(total)
            kpi["客单价"] = fmt_money(total / orders) if orders else "-"
        if C["qty"]:
            kpi["总销量"] = f"{float(df[C['qty']].sum()):,.0f}"
        if C["sku"]:
            kpi["SKU 数"] = f"{int(df[C['sku']].nunique()):,}"
        if C["cust"]:
            kpi["客户数"] = f"{int(df[C['cust']].nunique()):,}"
            kpi["人均订单"] = f"{orders / max(df[C['cust']].nunique(),1):.2f}"
        if C["ret"]:
            r = df[C["ret"]]
            rate = float((pd.to_numeric(r, errors="coerce") > 0).mean())
            kpi["退货/退款占比(标记>0)"] = pct(rate, 2)
        if C["status"]:
            vc = df[C["status"]].value_counts()
            for key in list(vc.index)[:6]:
                kpi[f"状态·{key}"] = f"{int(vc[key]):,} ({pct(vc[key]/n)})"
        if C["rev"] and C["cost"]:
            rev_s, cost_s = float(df[C["rev"]].sum()), float(df[C["cost"]].sum())
            if rev_s:
                kpi["毛利率(含退货口径)"] = pct((rev_s - cost_s) / rev_s)
    except Exception as e:
        bad("KPI 计算部分失败", e)
    for k, v in kpi.items():
        print(f"  {k:24s} {v}")
        log.append(f"- {k}：{v}")

    # ---------- ③ 趋势 ----------
    print("\n" + "=" * 62)
    print("③ 时间趋势")
    print("=" * 62)
    trend = None
    if C["date"] and C["rev"]:
        try:
            d = df.dropna(subset=["_dt"])
            g = d.groupby(d["_dt"].dt.to_period("M")).agg(
                营收=(C["rev"], "sum"),
                订单=(C["order"], "nunique") if C["order"] else (C["rev"], "size"),
            ).reset_index().rename(columns={"_dt": "月份"})
            g["月份"] = g["月份"].astype(str)
            g["环比"] = g["营收"].pct_change().round(4)
            trend = g
            trend.to_csv(os.path.join(a.outdir, "trend_monthly.csv"), index=False, encoding="utf-8-sig")
            ok(f"月度趋势 {len(g)} 个月，已存 trend_monthly.csv")
            top = g.nlargest(3, "营收")
            print("  营收最高的 3 个月：" + "、".join(f"{r['月份']}（{fmt_money(r['营收'])}）" for _, r in top.iterrows()))
            log.append("- 营收最高月份：" + "、".join(f"{r['月份']}({fmt_money(r['营收'])})" for _, r in top.iterrows()))
        except Exception as e:
            bad("趋势分析失败", e)
    else:
        print("  （缺 日期 或 金额 列，跳过）")

    # ---------- ④ 结构 ----------
    print("\n" + "=" * 62)
    print("④ 结构 / Top N")
    print("=" * 62)
    dims = []
    for label, key in [("类目", "cat"), ("国家/站点", "country"), ("商品/SKU", "sku")]:
        col = C.get(key)
        if not col or col not in df.columns or not C["rev"]:
            continue
        try:
            g = (df.groupby(col)[C["rev"]].agg(["sum", "count"])
                   .sort_values("sum", ascending=False).head(a.top))
            g.columns = ["营收", "记录数"]
            g["占比"] = (g["营收"] / float(df[C["rev"]].sum())).round(4)
            g["累计占比"] = g["占比"].cumsum().round(4)
            fn = f"by_{key}.csv"
            g.reset_index().to_csv(os.path.join(a.outdir, fn), index=False, encoding="utf-8-sig")
            dims.append((label, key, g, fn))
            head = g.head(5)
            print(f"  {label} Top5：" + " | ".join(f"{i}({fmt_money(r['营收'])},{pct(r['占比'])})" for i, r in head.iterrows()))
            log.append(f"- {label} Top5：" + "、".join(f"{i} {fmt_money(r['营收'])}（{pct(r['占比'])}）" for i, r in head.iterrows()))
            if len(g) >= 3:
                cr3 = float(g["占比"].head(3).sum())
                log.append(f"  · {label} CR3（前三集中度）= {pct(cr3)}")
                print(f"    {label} CR3 = {pct(cr3)}")
        except Exception as e:
            bad(f"{label} 聚合失败", e)

    # ---------- ⑤ 异常 ----------
    print("\n" + "=" * 62)
    print("⑤ 异常与口径风险")
    print("=" * 62)
    anomalies = []
    def add_anom(s):
        anomalies.append(s); print(f"  · {s}"); log.append(f"- ⚠️ {s}")

    try:
        if C["rev"]:
            r = df[C["rev"]]
            neg = int((r < 0).sum())
            if neg:
                add_anom(f"金额为负 {neg:,} 行（可能是退款/冲红，汇总时要说明）")
            zero = int((r == 0).sum())
            if zero:
                add_anom(f"金额为 0 的 {zero:,} 行（赠品/补发/无效单？）")
            na = int(r.isna().sum())
            if na:
                add_anom(f"金额空值 {na:,} 行（{pct(na/len(df))}）")
        if C["order"]:
            dup = int(df[C["order"]].duplicated().sum())
            if dup:
                add_anom(f"订单号重复 {dup:,} 行 → 这是**明细粒度**不是订单粒度，按订单数统计必须先 nunique")
        if C["date"]:
            na = int(df["_dt"].isna().sum())
            if na:
                add_anom(f"日期无法解析 {na:,} 行")
            try:
                span = (df["_dt"].max() - df["_dt"].min()).days
                if span > 365 * 6:
                    add_anom(f"时间跨度 {span} 天（>6年），可能存在测试数据或异常日期")
            except Exception:
                pass
        # 首末月不完整 —— 最常见的"假下跌/假增长"陷阱
        if C["date"] and "_dt" in df.columns:
            try:
                per = df["_dt"].dt.to_period("M")
                valid = per.dropna()
                if len(valid):
                    for tag, lp in (("末月", valid.max()), ("首月", valid.min())):
                        mx = df.loc[per == lp, "_dt"].dt.day.max()
                        if pd.notna(mx) and int(mx) < lp.days_in_month * 0.85:
                            add_anom(f"{tag} {lp} 只有 {int(mx)}/{lp.days_in_month} 天数据 —— 趋势两端的涨跌是「月份不完整」造成的，不是业务变化，图表和结论必须标注")
            except Exception as e:
                bad("首末月完整性检查失败", e)
        if C["qty"]:
            bad_qty = int((df[C["qty"]] <= 0).sum())
            if bad_qty:
                add_anom(f"数量 ≤0 的 {bad_qty:,} 行")
        # 高缺失列
        for c in cols:
            if c["null_rate"] > 0.5 and c["role"] != "other":
                add_anom(f"`{c['name']}`（{ROLE_CN[c['role']]}）缺失 {c['null_rate']:.0%}，相关结论必须标注口径")
    except Exception as e:
        bad("异常检查失败", e)
    if not anomalies:
        print("  （未发现明显异常）")

    # ---------- ⑥ 出图 ----------
    print("\n" + "=" * 62)
    print("⑥ 图表")
    print("=" * 62)
    figs = []
    def savefig(name, title):
        p = os.path.join(a.outdir, name)
        plt.tight_layout(); plt.savefig(p, dpi=150, bbox_inches="tight"); plt.close()
        figs.append((name, title)); ok(f"{name} · {title}")

    if trend is not None and len(trend) > 1:
        try:
            fig, ax = plt.subplots(figsize=(10, 4.2))
            y = trend["营收"] / 1e4
            ax.plot(trend["月份"], y, marker="o", color=PALETTE[0], linewidth=2)
            ax.set_title("月度营收趋势（万元）", fontsize=13, fontweight="bold")
            ax.set_ylabel("营收（万元）")
            ax.tick_params(axis="x", rotation=45)
            ax.grid(axis="y", alpha=.25)
            for x, y in zip(trend["月份"], trend["营收"]):
                pass
            savefig("fig1_trend.png", "月度营收趋势")
        except Exception as e:
            bad("趋势图失败", e)

    for label, key, g, fn in dims[:2]:
        try:
            fig, ax = plt.subplots(figsize=(9, 4.2))
            gg = g.head(a.top).iloc[::-1]
            vals = gg["营收"] / 1e4
            ax.barh([str(i) for i in gg.index], vals, color=PALETTE[1])
            ax.set_title(f"{label}营收 Top{a.top}（万元）", fontsize=13, fontweight="bold")
            ax.set_xlabel("营收（万元）")
            span = float(vals.max()) if len(vals) else 1.0
            for i, v in enumerate(vals):
                ax.text(v, i, f" {v:,.1f}", va="center", fontsize=8)
            ax.set_xlim(0, span * 1.18)
            savefig(f"fig2_{key}.png" if key != "country" else "fig3_country.png", f"{label}营收 Top{a.top}")
        except Exception as e:
            bad(f"{label} 图失败", e)

    # ---------- ⑦ README（详细版骨架：数字自动填，思考部分留空给主人）----------
    print("\n" + "=" * 62)
    print("⑦ README 详细骨架")
    print("=" * 62)
    name = os.path.basename(a.path)
    L = []
    def add(s=""):
        L.append(s)

    main_dim = dims[0][0] if dims else "主维度"
    span_txt = "-"
    if C["date"] and "_dt" in df.columns:
        try:
            span_txt = f"{df['_dt'].min():%Y-%m-%d} ~ {df['_dt'].max():%Y-%m-%d}"
        except Exception:
            pass
    dup_txt = "订单号有重复 → 明细粒度（按订单统计需去重）" if any("重复" in x for x in anomalies) else "未发现重复键，一行即一条记录"
    n_rows_txt = f"{info['rows']:,}"

    add(f"# {name} · 数据分析交付\n")
    add("> 业务目标：【把任务书里那句话原样抄进来】\n")
    add("## 0. 一句话结论\n")
    add("> 【必填 · 30 字内：发现了什么 + 建议做什么，不要形容词】\n")
    add("## 1. 我理解的业务问题\n")
    add("- 任务要我回答的是：【一句话】")
    add("- 我拆成两个可验证的子问题：")
    add("  1. 【可量化的问题 A】")
    add("  2. 【可量化的问题 B】")
    add(f"- 我选 **{main_dim}结构** 当主线，其余列入「如果还有时间」。\n")
    add("## 2. 数据长什么样（先看再算）\n")
    add("| 项 | 实际情况 |")
    add("|---|---|")
    add(f"| 规模 | {n_rows_txt} 行 × {info['cols']} 列（{info['size_mb']} MB） |")
    add(f"| 记录粒度 | {dup_txt} |")
    add(f"| 时间跨度 | {span_txt} |")
    add("")
    add("### 我发现的数据问题（以及我的处理）\n")
    if anomalies:
        for x in anomalies:
            add(f"- {x} → 【我的处理：____】")
    else:
        add("- 【未发现明显异常，但仍需确认：金额是否含税/含运费、时间字段是下单还是发货】")
    add("\n## 3. 我是怎么分析的（决策路径）\n")
    add("1. **先体检**：看类型 / 空值 / 取值范围，不急着算 —— 避免在错误口径上做漂亮分析。")
    add(f"2. **再算总量与结构**：总营收 {kpi.get('总营收', '-')}，按主维度拆 Top N 看钱从哪来。")
    add(f"3. **定主线**：选「{main_dim}」—— 因为【____，一句话说清它离业务动作最近】。")
    add("4. **排除的方向**：【____ 我没做】——【因为 ____】。\n")
    add("## 4. 关键发现（每条带数字 + 口径）\n")
    idx = 1
    for k, v in kpi.items():
        if k == "行数(记录粒度)":
            continue
        add(f"{idx}. **{k}**：{v}")
        idx += 1
        if idx > 5:
            break
    for d in [x for x in log if x.startswith("- 类目 Top5") or x.startswith("- 国家/站点 Top5") or x.startswith("- 商品/SKU Top5")]:
        add(f"- {d[2:]}")
    add("")
    add("> 口径说明：【含/不含退货单；是否含税、运费；按什么粒度汇总】\n")
    add("## 5. 图表（每张图都要说「它证明了什么」）\n")
    if figs:
        for n_, t_ in figs:
            add(f"### {t_}\n\n![{t_}]({n_})\n\n**它证明了**：【一句话，不能只写「展示了趋势」】\n")
    else:
        add("- 【无可用图表】\n")
    add("## 6. 所以，建议做什么\n")
    add("| 建议 | 依据 | 预期影响 | 风险 |")
    add("|---|---|---|---|")
    add("| 【动作 1】 | 【发现几】 | 【量化预期】 | 【可能的反作用】 |")
    add("| 【动作 2】 | 【发现几】 | 【量化预期】 | 【风险】 |\n")
    add("## 7. 我的判断的边界（诚实）\n")
    add("- **数据局限**：【缺什么 / 缺失多少 / 时间是否完整】")
    add("- **方法局限**：【用了什么假设，例如「金额列不含税」未经验证】")
    add("- **结论强度**：【哪条强（样本大、口径清），哪条弱（样本小/噪声内）】\n")
    add("## 8. AI 协作记录（加分项，不要藏）\n")
    add("- **我让 AI 做的**：数据扫描与绘图脚本、口径一致性检查、README 结构起草。")
    add("- **我自己做的**：确定业务问题、选择主线、判断结论是否成立、核对每一个数字。")
    add("- **我怎么验证 AI 的输出**：总量与明细交叉核对 / 重复运行确认稳定 / 抽查前几行确认类型判断。")
    add("- **一个 AI 出错被我抓到的例子**：【____】\n")
    add("## 9. 复现方式\n")
    add("```bash")
    add("# 环境：Python 3.12+ / pandas / matplotlib")
    add(f"python scan.py {name}          # 数据体检")
    add(f"python auto.py {name}          # KPI / 趋势 / 结构 / 图")
    add("python analyze.py ...          # 按需深挖")
    add("```")

    rp = os.path.join(a.outdir, "README.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    ok(f"README 详细骨架 → {rp}")
    ok(f"填空提示：共 12 处【】，其中「一口气结论」「主问题」「建议」三处最关键")

    # 体检报告也落盘
    from scan import render_md
    with open(os.path.join(a.outdir, "data_profile.md"), "w", encoding="utf-8") as f:
        f.write(render_md(info))

    # ---------- ⑧ 下一步：推荐主问题 + 现成命令 ----------
    name2 = os.path.basename(a.path)
    sugg = []
    def add_s(cond, title, cmd):
        if cond:
            sugg.append((title, cmd))
    add_s(C["cat"] and C["rev"], "类目结构：谁在养公司，集中度是否过高",
          f"python analyze.py {name2} --group {C['cat']} --metric {C['rev']} --top 15")
    add_s(C["country"] and C["rev"], "市场结构：站点差异 + 是否过度依赖单一市场",
          f"python analyze.py {name2} --group {C['country']} --metric {C['rev']}")
    add_s(C["ret"] and C["cat"], "退货/退款归因：哪个类目在吃掉利润",
          f"python analyze.py {name2} --rate {C['ret']} --group {C['cat']}")
    add_s(C["date"] and C["cat"] and C["rev"], "增长来自哪：类目级趋势（找涨的、找跌的）",
          f"python analyze.py {name2} --group {C['cat']} --metric {C['rev']} --date {C.get('date_raw') or C['date']} --freq M")
    add_s(C["sku"] and C["rev"], "爆款依赖风险：头部 SKU 贡献多少",
          f"python analyze.py {name2} --group {C['sku']} --metric {C['rev']} --top 20")
    add_s(C["cust"] and C["rev"], "客户价值：复购与大客户集中度",
          f"python analyze.py {name2} --group {C['cust']} --metric {C['rev']} --top 20")
    add_s(C["cost"] and C["rev"], "毛利结构：哪些类目在亏钱",
          f"python analyze.py {name2} --group {C['cat'] or C['sku']} --metric {C.get('profit') or C['cost']}")
    add_s(C["status"] and C["order"], "订单状态漏斗：取消/退款吃掉多少",
          f"python analyze.py {name2} --group {C['status']}")

    print("\n" + "=" * 62)
    print("⑧ 建议深挖（选 1 个主问题就够，别贪）")
    print("=" * 62)
    lines = ["# 下一步：推荐主问题（选 1 个当主线，其余当佐证）\n"]
    for i, (t, cmd) in enumerate(sugg[:7], 1):
        print(f"  {i}. {t}")
        print(f"     {cmd}")
        lines.append(f"## {i}. {t}\n\n```bash\n{cmd}\n```\n")
    with open(os.path.join(a.outdir, "next_steps.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    ok("建议清单 → next_steps.md")

    print("\n" + "=" * 62)
    print(f"全部完成 · 产物在 {os.path.abspath(a.outdir)}")
    print("=" * 62)
    print("下一步：打开 out/README.md，重点写「一句话结论」和「建议」两段。")
    print()
    print("★ 把小七喊过来：把数据文件路径（或本目录 out/ 的内容）发给它，")
    print("  它会直接给判断包 —— 3 个发现 + 主线推荐 + 一句话结论草稿 + 建议草稿 + 它拿不准的地方。")


if __name__ == "__main__":
    main()
