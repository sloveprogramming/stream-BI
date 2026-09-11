#!/usr/bin/env bash
# 安装 BIsql 数据库每日备份的 root 定时任务（幂等，重复执行不会重复添加）
#
#   sudo bash /home/ubuntu/bisql/ops/install_backup_cron.sh
#
# 会在保留原有 root crontab 条目的前提下追加一条，不会清空别人的任务。
set -euo pipefail

SCRIPT="/home/ubuntu/bisql/ops/backup_postgres.sh"
LOG="/home/ubuntu/backups/bisql/backup.log"
LINE="30 2 * * * ${SCRIPT} >> ${LOG} 2>&1"
MARK="backup_postgres.sh"

[ "$(id -u)" = "0" ] || { echo "需要 root 执行"; exit 1; }
[ -f "$SCRIPT" ] || { echo "找不到 $SCRIPT"; exit 1; }

chmod +x "$SCRIPT"
mkdir -p "$(dirname "$LOG")"

CURRENT="$(crontab -l 2>/dev/null || true)"
if printf '%s\n' "$CURRENT" | grep -q "$MARK"; then
  echo "已存在备份定时任务，跳过添加"
else
  { printf '%s\n' "$CURRENT"; printf '%s\n' "$LINE"; } | sed '/^[[:space:]]*$/d' | crontab -
  echo "已安装：$LINE"
fi

echo "--- 当前 root crontab ---"
crontab -l
