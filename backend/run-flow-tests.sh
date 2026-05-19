#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "========================================="
echo "  悠米 v5 作业识别主链路测试"
echo "========================================="

if ! curl -sf http://localhost:8001/health > /dev/null 2>&1; then
    echo "❌ 8001 未运行，请先启动"
    exit 1
fi
echo "✅ 8001 存活"

echo ""
python3 -m pytest tests/test_homework_flow.py -v --tb=short -s

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ 作业识别主链路测试通过"
else
    echo ""
    echo "❌ 作业识别主链路测试失败"
    exit 1
fi
