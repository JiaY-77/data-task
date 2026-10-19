#!/usr/bin/env bash
# push.sh —— 一条命令把结果推到 GitHub 公开仓库（含秒表和敏感信息预检）
# 用法:
#   bash push.sh "提交说明"                  # 仓库名自动用时间戳
#   bash push.sh "提交说明" my-repo-name     # 指定仓库名
#   bash push.sh "提交说明" my-repo-name private
set -euo pipefail

MSG="${1:-数据分析任务提交}"
# 默认名短、中性、专业（面试官会看到）；要按规则命名就传第二个参数
REPO="${2:-data-task}"
VIS="${3:-public}"

echo "=============================================="
echo " 提交 → $REPO ($VIS)"
echo "=============================================="

T0=$(date +%s)

# ---------- 0) 敏感信息预检（防把 key/密码推上公开仓库）----------
echo "[1/6] 敏感信息预检..."
HITS=$(grep -rIl -E '(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|password\s*=\s*["'"'"'][^"'"'"']{6,}|BEGIN (RSA|OPENSSH) PRIVATE KEY)' \
        --exclude-dir=.git --exclude-dir=.venv --exclude-dir=out --exclude='*.png' . 2>/dev/null || true)
if [ -n "$HITS" ]; then
  echo "  [!] 发现疑似敏感内容，已中止："
  echo "$HITS" | sed 's/^/      /'
  echo "      处理掉再跑一次（不要推到公开仓库！）"
  exit 3
fi
echo "  [OK] 未发现密钥/密码"

# ---------- 1) git 身份（缺就补，避免现场卡住）----------
git config user.name  >/dev/null 2>&1 || git config user.name  "candidate"
git config user.email >/dev/null 2>&1 || git config user.email "candidate@example.com"

# ---------- 2) 初始化 ----------
if [ ! -d .git ]; then
  git init -q
  git branch -M main 2>/dev/null || true
  echo "[2/6] git init 完成"
else
  echo "[2/6] 已有 git 仓库"
fi

# ---------- 3) 提交 ----------
git add -A
if git diff --cached --quiet; then
  echo "[3/6] 没有新改动（跳过 commit）"
else
  git commit -q -m "$MSG"
  echo "[3/6] commit 完成：$MSG"
fi

# ---------- 4) 建远端仓库并推送 ----------
if git remote get-url origin >/dev/null 2>&1; then
  echo "[4/6] 已有 origin，直接推送"
  git push -u origin main 2>&1 | tail -3
else
  echo "[4/6] 创建 GitHub 仓库并推送..."
  gh repo create "$REPO" "--$VIS" --source=. --remote=origin --push 2>&1 | tail -5
fi

# ---------- 5) 确认 ----------
URL=$(gh repo view --json url -q .url 2>/dev/null || echo "（取 URL 失败，请到 GitHub 确认）")
echo "[5/6] 仓库地址：$URL"

# ---------- 6) 秒表 ----------
T1=$(date +%s)
echo "[6/6] 总耗时：$((T1-T0)) 秒"
echo "=============================================="
