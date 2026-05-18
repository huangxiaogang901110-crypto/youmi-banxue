#!/bin/bash
# 悠米公共区 Git 关门脚本
# 由 Mac 以 root 身份 SSH 到 ECS 执行，或由 at 超时自动触发
# 用法：ssh root@ECS '/home/hermes_me/yomi-dev/scripts/close-gate.sh'

GIT_DIR="/home/hermes_me/yomi/.git"
UNLOCK_FILE="/tmp/yomi_git_unlocked"

log() { echo "[$(date '+%H:%M:%S')] $1"; }

# ── 1. 恢复权限 ──
chmod -R o-w "$GIT_DIR/" 2>/dev/null
chmod o-x "$GIT_DIR/hooks/" 2>/dev/null || true
chmod o-x "$GIT_DIR/hooks/pre-receive" "$GIT_DIR/hooks/post-receive" 2>/dev/null || true
log "🔒 .git 写权限已恢复"

# ── 1.5. 恢复 root 所有权 + 全局只读锁 ──
chown -R root:root "$GIT_DIR/"
chmod -R a-w /home/hermes_me/yomi/
log "🔒 公共区已恢复 root 所有权 + a-w 只读"

# ── 2. 销毁令牌 ──
rm -f "$UNLOCK_FILE"
log "🔒 令牌已销毁"

log "🚪 门已关"
