#!/bin/bash
# 悠米公共区 Git 开门脚本
# 由 Mac 以 root 身份 SSH 到 ECS 执行
# 用法：ssh root@ECS '/home/hermes_me/yomi-dev/scripts/open-gate.sh'

GIT_DIR="/home/hermes_me/yomi/.git"
UNLOCK_FILE="/tmp/yomi_git_unlocked"
TIMEOUT_MIN=5

log() { echo "[$(date '+%H:%M:%S')] $1"; }

# ── 1. 解锁全链路 other write ──
log "🔓 解锁 .git 写权限..."
chmod o+w "$GIT_DIR/" 2>/dev/null || true
chmod o+w "$GIT_DIR/HEAD" 2>/dev/null || true
chmod o+w "$GIT_DIR/index" 2>/dev/null || true
chmod o+w "$GIT_DIR/FETCH_HEAD" 2>/dev/null || true
chmod -R o+w "$GIT_DIR/refs/" 2>/dev/null || true
chmod -R o+w "$GIT_DIR/logs/" 2>/dev/null || true
find "$GIT_DIR/objects/" -type d -exec chmod o+w {} + 2>/dev/null || true
chmod o+x "$GIT_DIR/hooks/" 2>/dev/null || true
chmod o+x "$GIT_DIR/hooks/pre-receive" "$GIT_DIR/hooks/post-receive" 2>/dev/null || true
log "✅ 权限已解锁"

# ── 1.5. hermes_me 所有权（commit 用）──
chown -R hermes_me:hermes_me "$GIT_DIR/"
log "✅ .git 所有权已移交 hermes_me"

# ── 2. 创建令牌 ──
touch "$UNLOCK_FILE" && chmod 666 "$UNLOCK_FILE"
log "✅ 令牌已创建"

# ── 3. 超时保护（N 分钟后自动关门）──
echo "/home/hermes_me/yomi-dev/scripts/close-gate.sh" | at now + ${TIMEOUT_MIN} minutes 2>/dev/null
log "⏰ ${TIMEOUT_MIN} 分钟超时自动关门"

log "🚪 门已开，可以 push 了"
