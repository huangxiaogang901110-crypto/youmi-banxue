#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "========================================="
echo "  悠米后端 — Watch 模式"
echo "  监听 *.py → 3s 无变动 → 自动跑测试"
echo "  Ctrl+C 退出"
echo "========================================="

if ! curl -sf http://localhost:8001/health > /dev/null 2>&1; then
    echo "❌ 8001 未运行，请先启动"
    echo "   uvicorn main:app --host 0.0.0.0 --port 8001 --reload"
    exit 1
fi

echo "✅ 8001 存活  监听中..."
echo ""

# 初始 mtime
last_ts=$(find . -name '*.py' -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
[[ -z "$last_ts" ]] && last_ts=0

while true; do
    sleep 1

    cur_ts=$(find . -name '*.py' -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
    [[ -z "$cur_ts" ]] && cur_ts=0

    if [ "$cur_ts" != "$last_ts" ]; then
        # 等到文件停止变动 3 秒
        stable=0
        for i in $(seq 1 3); do
            sleep 1
            next_ts=$(find . -name '*.py' -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
            if [ "$next_ts" != "$cur_ts" ]; then
                cur_ts="$next_ts"
                stable=0
                break
            fi
            stable=1
        done

        if [ "$stable" -eq 1 ]; then
            echo "───────────────────────────────────────"
            echo "  📋 $(date '+%H:%M:%S') 检测到变更，自动测试..."
            echo "───────────────────────────────────────"
            ./run-tests.sh
            echo ""
        fi

        last_ts="$cur_ts"
    fi
done
