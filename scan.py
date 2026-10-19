#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
scan.py —— 30 秒摸清一份陌生 CSV：它是什么、有什么坑、能回答什么问题

用法:
    python scan.py 数据.csv
    python scan.py 数据.csv --out report.md
    python scan.py 数据.csv --engine duckdb   # 超大文件(>300MB)用 duckdb 更快

产出: 控制台摘要 + markdown 报告（可直接当 README 的事实底稿）
设计原则: 不假设业务、不猜列名、不崩；任何异常都降级成"未知"，绝不中断。
"""
import argparse
import os
import re
import sys
import time
from collections import OrderedDict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd

ENCODINGS = ["utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1"]
DELIMS = [",", "\t", ";", "|"]

# ---------- 列角色词典（中英双语，跨境电商常见口径）----------
ROLE_PATTERNS = OrderedDict([
    ("order_id",  [r"order[_ ]?id", r"order[_ ]?no", r"订单号", r"订单编号", r"单号", r"^订单$"]),
    ("date",      [r"date", r"time", r"日期", r"时间", r"月份", r"created", r"下单"]),
    ("sku",       [r"^sku$", r"asin", r"msku", r"product[_ ]?id", r"item[_ ]?id", r"商品", r"产品", r"编码"]),
    ("category",  [r"categor", r"类目", r"品类", r"分类", r"子类", r"系列"]),
    ("qty",       [r"qty", r"quantity", r"数量", r"件数", r"销量"]),
    ("price",     [r"unit[_ ]?price", r"price", r"单价", r"售价", r"价格"]),
    ("revenue",   [r"amount", r"total", r"revenue", r"sales", r"gmv", r"金额", r"销售额", r"成交", r"营收"]),
    ("cost",      [r"cost", r"成本", r"采购价", r"进价"]),
    ("profit",    [r"profit", r"margin", r"毛利", r"利润"]),
    ("customer",  [r"customer", r"buyer", r"member", r"客户", r"买家", r"会员", r"用户", r"user[_ ]?id"]),
    ("country",   [r"country", r"marketplace", r"region", r"站点", r"国家", r"地区", r"市场"]),
    ("status",    [r"status", r"state", r"状态"]),
    ("return",    [r"return", r"refund", r"退货", r"退款", r"售后"]),
    ("shipping",  [r"shipping", r"carrier", r"logistic", r"物流", r"快递", r"发货", r"配送", r"delivery"]),
    ("ad",        [r"ad[_ ]?spend", r"advert", r"acos", r"cpc", r"广告"]),
    ("stock",     [r"stock", r"inventory", r"库存"]),
    ("review",    [r"review", r"rating", r"star", r"评分", r"评论", r"评价"]),
])

ID_HINT = re.compile(r"(^id$|_id$|id$|code|编号|号$|sku|asin)", re.I)


def sniff_encoding(path, nbytes=262144):
    raw = open(path, "rb").read(nbytes)
    for enc in ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except Exception:
            continue
    return "latin-1"


def sniff_delim(path, enc):
    head = open(path, "r", encoding=enc, errors="replace").read(16384)
    lines = [ln for ln in head.splitlines()[:20] if ln.strip()]
    if not lines:
        return ","
    scores = {}
    for d in DELIMS:
        counts = [ln.count(d) for ln in lines]
        # 分隔符得分: 众数出现频率高且稳定
        if not counts:
            continue
        mode = max(set(counts), key=counts.count)
        scores[d] = (mode, counts.count(mode) / len(counts))
    best = max(scores, key=lambda d: (scores[d][0], scores[d][1]))
    return best if scores.get(best, (0, 0))[0] > 0 else ","


def role_of(col):
    c = str(col).strip().lower()
    for role, pats in ROLE_PATTERNS.items():
        for p in pats:
            if re.search(p, c, re.I):
                return role
    return "other"


ROLE_CN = {
    "order_id": "订单标识", "date": "时间", "sku": "商品/SKU", "category": "类目",
    "qty": "数量", "price": "单价", "revenue": "金额", "cost": "成本",
    "profit": "利润", "customer": "客户", "country": "国家/站点", "status": "状态",
    "return": "退货/退款", "shipping": "物流", "ad": "广告", "stock": "库存",
    "review": "评价", "other": "其他",
}


def try_numeric(s):
    """占比按【非空值】算 —— 空值多的列（如只对退货单有值的退款金额）不能因此被判成文本。"""
    v = pd.to_numeric(s, errors="coerce")
    nn = int(s.notna().sum())
    if nn == 0:
        return v, 0.0
    return v, float(v.notna().sum()) / nn


def try_datetime(s):
    nn = int(s.notna().sum())
    if nn == 0:
        return None, 0.0
    sample = s.dropna().head(3000)
    try:
        v = pd.to_datetime(sample, errors="coerce", format="mixed")
    except Exception:
        try:
            v = pd.to_datetime(sample, errors="coerce")
        except Exception:
            return None, 0.0
    return v, float(v.notna().sum()) / len(sample)


DATE_NAME = re.compile(r"date|_time|time$|日期|时间|月份|年月", re.I)
DATE_SHAPE = re.compile(
    r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"          # 2024-01-01
    r"|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"        # 01/02/2024
    r"|^\d{8}$"                                  # 20240101
    r"|^\d{4}年\d{1,2}月"                         # 2024年1月
    r"|\d{2}:\d{2}"                              # 带时间
)


def looks_like_date(s):
    """只有值本身长得像日期（或列名明确是日期）才允许判日期，避免把数字当纳秒时间戳。"""
    vals = [str(x) for x in s.dropna().head(60)]
    if not vals:
        return False
    hits = sum(1 for v in vals if DATE_SHAPE.search(v))
    return hits >= len(vals) * 0.8



def scan(path, sample_rows=200000, engine="pandas"):
    t0 = time.time()
    size_mb = os.path.getsize(path) / 1024 / 1024
    enc = sniff_encoding(path)
    delim = sniff_delim(path, enc)

    info = {
        "path": path, "size_mb": round(size_mb, 2), "encoding": enc,
        "delimiter": repr(delim), "rows": None, "cols": None, "columns": [],
        "engine": engine, "notes": [],
    }

    # 读取（超大文件先采样，保证速度）
    read_kw = dict(sep=delim, encoding=enc, low_memory=False, on_bad_lines="skip")
    if size_mb > 300 and engine == "pandas":
        info["notes"].append(f"文件 {size_mb:.0f}MB 偏大，仅扫描前 {sample_rows} 行用于体检（全量统计请用 analyze.py）")
        df = pd.read_csv(path, nrows=sample_rows, **read_kw)
    else:
        df = pd.read_csv(path, **read_kw)

    # 全量行数（快速计数，不解析）
    n_rows = len(df)
    if size_mb <= 300:
        try:
            with open(path, "r", encoding=enc, errors="ignore") as f:
                n_rows = max(0, sum(1 for _ in f) - 1)
        except Exception:
            pass
    info["rows"] = int(n_rows)
    info["cols"] = int(df.shape[1])

    cols_info = []
    for col in df.columns:
        s = df[col]
        nn = s.notna()
        nuniq = int(s[nn].nunique()) if nn.any() else 0
        null_rate = round(1 - float(nn.mean()), 4) if len(s) else 0.0

        num, num_ratio = try_numeric(s)
        dt, dt_ratio = try_datetime(s)

        # 类型判定顺序: 日期 -> 数值 -> 分类/文本
        # 关键：日期必须"值长得像日期"或"列名明确是日期"，否则纯数字会被误判成 1970 纳秒时间戳
        name_is_date = bool(DATE_NAME.search(str(col)))
        date_ok = (
            dt_ratio > 0.9
            and nuniq > 1
            and not ID_HINT.search(str(col))
            and (name_is_date or (s.dtype == object and looks_like_date(s)))
        )

        kind = "文本"
        extra = ""
        if date_ok:
            kind = "日期"
            try:
                extra = f"{dt.min():%Y-%m-%d} ~ {dt.max():%Y-%m-%d}"
            except Exception:
                extra = ""
        elif num_ratio > 0.9 and not ID_HINT.search(str(col)):
            kind = "数值"
            clean = num.dropna()
            if len(clean):
                extra = f"min={clean.min():.4g} mean={clean.mean():.4g} max={clean.max():.4g}"
        elif num_ratio > 0.9:
            kind = "编号/数值"
        elif nuniq <= 60 or (len(s) and nuniq / max(len(s), 1) < 0.2):
            kind = "分类"
            vc = s.value_counts().head(5)
            extra = " / ".join(f"{k}({v})" for k, v in vc.items())
        else:
            extra = str(s.dropna().head(1).iloc[0])[:40] if nn.any() else ""

        cols_info.append({
            "name": str(col), "kind": kind, "null_rate": null_rate, "nunique": nuniq,
            "role": role_of(col), "extra": extra,
        })

    info["columns"] = cols_info
    info["elapsed"] = round(time.time() - t0, 2)

    # ---------- 能回答什么问题 ----------
    roles = {c["role"] for c in cols_info}
    qs = []
    def add(cond, q): 
        if cond: qs.append(q)
    add({"revenue", "date"} <= roles, "月度/周度销售额趋势 + 环比增长（revenue × date）")
    add({"revenue", "category"} <= roles, "类目销售结构 + Top N 类目贡献（占比/集中度）")
    add({"revenue", "sku"} <= roles, "商品销售榜 + 头部商品依赖度（爆款集中风险）")
    add({"revenue", "country"} <= roles, "国家/站点市场分布 + 各市场客单差异")
    add({"revenue", "return"} <= roles, "退货率与退款金额损失（按类目/站点拆）")
    add({"revenue", "cost"} <= roles, "毛利率与亏损 SKU 识别")
    add({"revenue", "customer"} <= roles, "客单价分布 + 复购率 + RFM 分群")
    add({"qty", "sku"} <= roles, "销量榜 + 动销率（多少 SKU 零销）")
    add({"shipping", "date"} <= roles, "物流时效分析（发货/签收延迟）")
    add({"status", "order_id"} <= roles, "订单状态漏斗（取消/退款/完成占比）")
    add({"ad", "revenue"} <= roles, "广告投产比（ACOS/TACOS）与预算效率")
    add({"stock", "qty"} <= roles, "库存周转与断货风险")
    add({"review"} <= roles, "评分与差评归因")
    if not qs:
        qs.append("未识别出标准业务列 —— 先看列名语义，再手工定 2-3 个问题")
    info["questions"] = qs

    # ---------- 风险/坑 ----------
    risks = []
    for c in cols_info:
        if c["null_rate"] > 0.3 and c["role"] not in ("other",):
            risks.append(f"`{c['name']}`（{ROLE_CN[c['role']]}）空值 {c['null_rate']:.0%}，按此维度拆分时要说明口径")
    dup_key = next((c["name"] for c in cols_info if c["role"] == "order_id"), None)
    if dup_key:
        try:
            d = int(df[dup_key].duplicated().sum())
            if d > 0:
                risks.append(f"`{dup_key}` 有 {d:,} 行重复 —— 可能是订单内多行（明细粒度，不是订单粒度），汇总金额前必须先去重或确认口径")
        except Exception:
            pass
    if any(c["kind"] == "文本" and c["nunique"] < 5 for c in cols_info):
        risks.append("存在唯一值极少（<5）的文本列，注意是否为占位/测试数据")
    info["risks"] = risks
    return info, df


def render_md(info):
    L = []
    L.append(f"# 数据体检报告 · `{os.path.basename(info['path'])}`\n")
    L.append(f"- 规模：**{info['rows']:,} 行 × {info['cols']} 列**，{info['size_mb']} MB")
    L.append(f"- 编码 `{info['encoding']}`，分隔符 `{info['delimiter']}`，体检耗时 **{info['elapsed']}s**\n")
    for n in info["notes"]:
        L.append(f"> ⚠️ {n}\n")

    L.append("## 一、字段清单\n")
    L.append("| # | 字段 | 类型 | 业务角色 | 空值率 | 唯一值 | 概况/样例 |")
    L.append("|---|---|---|---|---|---|---|")
    for i, c in enumerate(info["columns"], 1):
        ex = str(c["extra"]).replace("|", "\\|")[:60]
        L.append(f"| {i} | `{c['name']}` | {c['kind']} | {ROLE_CN[c['role']]} | {c['null_rate']:.1%} | {c['nunique']:,} | {ex} |")

    L.append("\n## 二、这份数据能回答什么问题\n")
    for q in info["questions"]:
        L.append(f"- {q}")

    L.append("\n## 三、必须先确认的坑\n")
    if info["risks"]:
        for r in info["risks"]:
            L.append(f"- {r}")
    else:
        L.append("- 未发现明显结构性问题（仍要确认：金额是否含税/含运费、时间字段是下单还是发货）")
    L.append("\n## 四、下一步（可直接粘贴给 AI 或写进 README）\n")
    L.append("1. 选定 **1 个主问题**（业务方最关心的），其余作为佐证")
    L.append("2. 先出「总量 + 趋势」，再出「结构（Top N）」，最后出「异常/风险」")
    L.append("3. 每个结论都要带数字和口径，不要形容词")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--out", default=None)
    ap.add_argument("--sample", type=int, default=200000)
    a = ap.parse_args()

    if not os.path.exists(a.path):
        print(f"[X] 文件不存在: {a.path}")
        sys.exit(2)

    info, df = scan(a.path, sample_rows=a.sample)
    md = render_md(info)
    print(md)
    out = a.out or os.path.splitext(a.path)[0] + "_体检报告.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n[OK] 报告已写入: {out}")
    print(f"[OK] 总耗时 {info['elapsed']}s")


if __name__ == "__main__":
    main()
