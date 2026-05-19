#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "========================================="
echo "  悠米后端自动测试"
echo "========================================="

# 检查 8001
if ! curl -sf http://localhost:8001/health > /dev/null 2>&1; then
    echo ""
    echo "❌ 8001 测试后端未运行"
    echo ""
    echo "请先启动测试后端："
    echo "  uvicorn main:app --host 0.0.0.0 --port 8001"
    echo ""
    exit 1
fi
echo "✅ 8001 存活"

# 检查依赖
if ! python3 -c "import pytest" 2>/dev/null; then
    echo ""
    echo "📦 缺少 pytest，安装中..."
    pip install pytest pytest-asyncio httpx -q
fi

# 跑测试
echo ""
python3 -m pytest tests/ -v --tb=short

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ 全部通过"
else
    echo ""
    echo "❌ 测试失败"
    exit 1
fi
