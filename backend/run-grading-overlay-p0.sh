#!/bin/bash
# 悠米伴学 Codex 测试环境 — 图上判题 MVP 启动脚本
# 用途: 8002 Codex 测试后端，启用 B++/math OCR-first 路径
# 不碰生产 8000，不碰公区 ~/yomi/
# 启动方式: cd ~/yomi-codex-r1 && bash backend/run-grading-overlay-p0.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Codex Overlay P0 测试后端启动 ==="
echo "端口: 8002"
echo "数据库: $(pwd)/yomi.db"

export YOMICALL_SOURCE="${YOMICALL_SOURCE:-grading_overlay_p0}"
export YOMI_DISABLE_DEDUP_FOR_CALL_SOURCE="${YOMI_DISABLE_DEDUP_FOR_CALL_SOURCE:-$YOMICALL_SOURCE}"
export YOMI_RECOGNITION_10S_SLA=false
export YOMI_SYNC_JOB_TIMEOUT_SECONDS=60
export YOMI_MATH_OCR_FIRST=true
export YOMI_BPLUSPLUS_RECOGNITION=true

echo "环境变量:"
echo "  YOMICALL_SOURCE=$YOMICALL_SOURCE"
echo "  YOMI_MATH_OCR_FIRST=$YOMI_MATH_OCR_FIRST"
echo "  YOMI_BPLUSPLUS_RECOGNITION=$YOMI_BPLUSPLUS_RECOGNITION"
echo "  YOMI_RECOGNITION_10S_SLA=$YOMI_RECOGNITION_10S_SLA"
echo "  YOMI_SYNC_JOB_TIMEOUT_SECONDS=$YOMI_SYNC_JOB_TIMEOUT_SECONDS"

exec python3 -m uvicorn main:app --host 0.0.0.0 --port 8002
