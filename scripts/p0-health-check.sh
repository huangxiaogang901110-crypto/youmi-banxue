#!/bin/bash
# 悠米 P0 综合巡检脚本
# 每 30 分钟 cron 执行，异常推企业微信，正常静默

ALERT=0
MSG=""

check() {
    local label="$1" result="$2" detail="$3"
    if [ "$result" != "OK" ]; then
        ALERT=1
        MSG="${MSG}❌ ${label}: ${detail}\n"
    fi
}

# ── 1. yomi-backend 健康 ──
HB=$(curl -s --connect-timeout 5 --max-time 10 http://localhost:8000/health 2>/dev/null)
if echo "$HB" | grep -q '"ok":true'; then
    check "后端" "OK" ""
else
    check "后端" "FAIL" "$(echo $HB | head -c 100)"
fi

# ── 2. DeepSeek 余额 ──
source ~/.hermes/profiles/me/.env 2>/dev/null || true
BAL=$(curl -s --connect-timeout 5 --max-time 15 \
    -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
    https://api.deepseek.com/user/balance 2>/dev/null)
if echo "$BAL" | grep -q '"is_available":true'; then
    TOTAL=$(echo "$BAL" | python3 -c "import sys,json; d=json.load(sys.stdin); infos=d.get('balance_infos',[]); print(infos[0].get('total_balance','0') if infos else '0')" 2>/dev/null)
    if [ -n "$TOTAL" ]; then
        # 低于 10 元预警
        if [ "$(echo "$TOTAL < 10" | bc -l 2>/dev/null)" = "1" ] || [ -z "$(echo "$TOTAL" | tr -d '0.')" ]; then
            check "DS余额" "FAIL" "¥${TOTAL} 低于 ¥10 预警线"
        else
            check "DS余额" "OK" ""
        fi
    else
        check "DS余额" "FAIL" "解析失败"
    fi
else
    check "DS余额" "FAIL" "API不可达"
fi

# ── 3. OSS 连通性 ──
OSS_TEST=$(cd ~/yomi-dev/backend && ALIBABA_CLOUD_ACCESS_KEY_ID=LTAI5t613cJAgVPdhvwteaNE \
    ALIBABA_CLOUD_ACCESS_KEY_SECRET=FqL7X1iev33tUuzwSGjNFYeReXWwQ8 \
    python3 -c "
from oss_client import upload_image
png = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c4944415408d76360f8cf00000001010000b78f0bff0000000049454e44ae426082')
r = upload_image(png, 'healthcheck', 'png')
print('OK' if r else 'FAIL')
" 2>/dev/null)
check "OSS" "$OSS_TEST" ""

# ── 4. 磁盘 ──
DISK=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK" -gt 85 ] 2>/dev/null; then
    check "磁盘" "FAIL" "使用率 ${DISK}%"
else
    check "磁盘" "OK" ""
fi

# ── 5. SOCKS5 代理 ──
PROXY_TEST=$(curl -x socks5h://127.0.0.1:1080 -sS -o /dev/null -w "%{http_code}" \
    --connect-timeout 5 --max-time 10 https://github.com 2>/dev/null)
if [ "$PROXY_TEST" = "200" ] || [ "$PROXY_TEST" = "301" ] || [ "$PROXY_TEST" = "302" ]; then
    check "代理" "OK" ""
else
    check "代理" "FAIL" "SOCKS5 :1080 不通"
fi

# ── 6. 企业微信连通 ──
WECOM_TEST=$(env -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY \
    curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 10 \
    https://qyapi.weixin.qq.com 2>/dev/null)
if [ "$WECOM_TEST" != "000" ]; then
    check "企微" "OK" ""
else
    check "企微" "FAIL" "qyapi 不通"
fi

# ── 输出 ──
if [ "$ALERT" -eq 1 ]; then
    echo -e "⚠️ 悠米 P0 巡检异常\n${MSG}"
    exit 1
fi
# 正常静默
