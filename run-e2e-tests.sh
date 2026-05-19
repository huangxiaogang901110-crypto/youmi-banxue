#!/bin/bash
set -euo pipefail

cd ~/yomi-dev

echo "=== 检查 3001 前端 ==="
if ! curl -sf -o /dev/null http://localhost:3001; then
  echo "❌ 3001 前端不可访问，请先启动开发服务器"
  exit 1
fi
echo "✅ 3001 可达"

echo "=== 检查 8001 后端 ==="
if ! curl -sf -o /dev/null http://localhost:8001/health; then
  echo "❌ 8001 后端不可访问，请先启动测试后端"
  exit 1
fi
echo "✅ 8001 可达"

echo "=== 运行 v6 前端 E2E 测试 ==="
npx playwright test e2e/yomi-front-flow.spec.ts --project=chromium
echo ""
echo "✅ v6 前端 E2E 主链路测试通过"
