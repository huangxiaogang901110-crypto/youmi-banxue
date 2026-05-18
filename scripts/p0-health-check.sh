#!/bin/bash
# P0 悠米伴学综合巡检脚本
# 正常：exit 0 静默 | 异常：输出报警文本 → exit 1
# crontab: */30 * * * * bash /home/hermes_me/yomi-dev/scripts/p0-health-check.sh

set -o pipefail
ALERTS=""
WEBHOOK_KEY="71366a96-eea7-400c-b919-3e8e46b87bd8"
WEBHOOK_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${WEBHOOK_KEY}"

# ============================================================
# 辅助函数
# ============================================================
alert() { ALERTS="${ALERTS}${1}\n"; }

send_wecom() {
    local msg="$1"
    python3 -c "
import json, sys
msg = sys.argv[1]
payload = json.dumps({'msgtype': 'markdown', 'markdown': {'content': msg}}, ensure_ascii=False)
print(payload)
" "$msg" | \
    env -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY -u all_proxy -u https_proxy -u http_proxy \
    curl -sS --max-time 10 -H "Content-Type: application/json" -d @- "$WEBHOOK_URL" > /dev/null 2>&1
}

# ============================================================
# 1. yomi-backend 健康
# ============================================================
check_backend() {
    local resp
    resp=$(env -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY \
        curl -sS --connect-timeout 5 --max-time 10 http://127.0.0.1:8000/health 2>&1) || true

    if echo "$resp" | grep -q '"ok":\s*true'; then
        return 0
    else
        alert "❌ **yomi-backend** 异常 | 响应: \`$(echo "$resp" | head -c 120)\`"
        return 1
    fi
}

# ============================================================
# 2. DeepSeek 余额
# ============================================================
check_deepseek_balance() {
    local key_file="${HOME}/yomi/backend/.env"
    local api_key balance is_avail currency

    if [ ! -f "$key_file" ]; then
        alert "⚠️ **DeepSeek 余额** 无法检查 | 密钥文件不存在: \`$key_file\`"
        return 1
    fi

    api_key=$(grep -oP 'DEEPSEEK_API_KEY\s*=\s*\K.+' "$key_file" | tr -d '"'"'" | xargs)
    if [ -z "$api_key" ]; then
        alert "⚠️ **DeepSeek 余额** 无法检查 | 未找到 API Key"
        return 1
    fi

    local resp
    resp=$(env -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY \
        curl -sS --connect-timeout 8 --max-time 15 \
        -H "Authorization: Bearer $api_key" \
        https://api.deepseek.com/user/balance 2>&1) || true

    is_avail=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('is_available',False))" 2>/dev/null) || true
    balance=$(echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for b in d.get('balance_infos',[]):
    print(b.get('total_balance','?'))
" 2>/dev/null) || true
    currency=$(echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for b in d.get('balance_infos',[]):
    print(b.get('currency',''))
" 2>/dev/null) || true

    if [ "$is_avail" != "True" ]; then
        alert "❌ **DeepSeek 余额** 不可用 | 响应: \`$(echo "$resp" | head -c 200)\`"
        return 1
    fi

    # 提取数值部分
    local balance_num
    balance_num=$(echo "$balance" | grep -oP '[\d.]+' | head -1)

    if [ -n "$balance_num" ] && python3 -c "exit(0 if float('$balance_num') < 10 else 1)" 2>/dev/null; then
        alert "⚠️ **DeepSeek 余额偏低** | ${currency} ${balance}"
        return 1
    fi

    return 0
}

# ============================================================
# 3. OSS 连通性
# ============================================================
check_oss() {
    local key_file="${HOME}/yomi/backend/.env"
    local endpoint bucket key secret

    [ -f "$key_file" ] || { alert "⚠️ **OSS** 无法检查 | 密钥文件不存在"; return 1; }

    endpoint=$(grep -oP 'OSS_ENDPOINT\s*=\s*\K.+' "$key_file" | tr -d '"'"'" | xargs)
    bucket=$(grep -oP 'OSS_BUCKET_NAME\s*=\s*\K.+' "$key_file" | tr -d '"'"'" | xargs)
    key=$(grep -oP 'OSS_ACCESS_KEY_ID\s*=\s*\K.+' "$key_file" | tr -d '"'"'" | xargs)
    secret=$(grep -oP 'OSS_ACCESS_KEY_SECRET\s*=\s*\K.+' "$key_file" | tr -d '"'"'" | xargs)

    if [ -z "$endpoint" ] || [ -z "$bucket" ] || [ -z "$key" ] || [ -z "$secret" ]; then
        alert "⚠️ **OSS** 无法检查 | 配置不完整"
        return 1
    fi

    local result
    result=$(python3 -c "
import oss2, sys, struct, zlib
try:
    auth = oss2.Auth('$key', '$secret')
    bucket = oss2.Bucket(auth, '$endpoint', '$bucket', connect_timeout=8)
    # 动态生成 1x1 透明 PNG
    def make_1x1_png():
        def chunk(ctype, data):
            c = ctype + data
            return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
        raw = b'\\x00' + b'\\x00\\x00\\x00\\xff'
        return b'\\x89PNG\\r\\n\\x1a\\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
    bucket.put_object('health-check/test.png', make_1x1_png())
    bucket.delete_object('health-check/test.png')
    print('OK')
except Exception as e:
    print(f'FAIL:{e}')
" 2>&1)
    if [ "$result" != "OK" ]; then
        alert "❌ **OSS 连通性** 异常 | \`$result\`"
        return 1
    fi
    return 0
}

# ============================================================
# 4. 磁盘使用率
# ============================================================
check_disk() {
    local usage pct
    pct=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')
    usage=$(df -h / | tail -1 | awk '{print $3 "/" $2}')
    if [ "$pct" -gt 85 ] 2>/dev/null; then
        alert "⚠️ **磁盘使用率** ${usage} (${pct}%) | >85%"
        return 1
    fi
    return 0
}

# ============================================================
# 5. SOCKS5 代理 (:1080)
# ============================================================
check_socks5() {
    local code
    code=$(curl -x socks5h://127.0.0.1:1080 \
        -sS -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 --max-time 15 \
        https://github.com 2>&1) || true
    case "$code" in
        200|301|302) return 0 ;;
        *)
            alert "❌ **SOCKS5 :1080** 异常 | HTTP ${code}"
            return 1
            ;;
    esac
}

# ============================================================
# 6. 企业微信 API 直连
# ============================================================
check_wecom_api() {
    local code
    code=$(env -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY -u all_proxy -u https_proxy -u http_proxy \
        curl -sS -o /dev/null -w "%{http_code}" \
        --connect-timeout 8 --max-time 12 \
        https://qyapi.weixin.qq.com 2>&1) || true
    if [ "$code" = "000" ]; then
        alert "❌ **企业微信 API** 不可达 | HTTP 000"
        return 1
    fi
    return 0
}

# ============================================================
# 主流程
# ============================================================
main() {
    local exit_code=0

    check_backend         || exit_code=1
    check_deepseek_balance || exit_code=1
    check_oss             || exit_code=1
    check_disk            || exit_code=1
    check_socks5          || exit_code=1
    check_wecom_api       || exit_code=1

    if [ "$exit_code" -eq 1 ]; then
        local ts
        ts=$(TZ='Asia/Shanghai' date '+%Y-%m-%d %H:%M')
        local msg="## 🚨 悠米 P0 巡检告警\n> ${ts}\n\n${ALERTS}"

        # 发送企业微信
        send_wecom "$msg"

        # 输出告警文本（供 cronjob 读取）
        echo -e "$msg"
        exit 1
    fi

    exit 0
}

main "$@"
