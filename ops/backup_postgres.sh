#!/usr/bin/env bash
# BIsql 中台 PostgreSQL 每日备份
#
# 由服务器 root crontab 调用（root 才能直连 docker socket）：
#   30 2 * * * /home/ubuntu/bisql/ops/backup_postgres.sh >> /home/ubuntu/backups/bisql/backup.log 2>&1
#
# 只操作 bisql-db 这一个容器，不碰同机其他项目的服务与数据。
# 所有参数可用环境变量覆盖。
set -euo pipefail

CONTAINER="${BISQL_DB_CONTAINER:-bisql-db}"
DB_USER="${BISQL_DB_USER:-bisql}"
DB_NAME="${BISQL_DB_NAME:-bisql}"
BACKUP_DIR="${BISQL_BACKUP_DIR:-/home/ubuntu/backups/bisql}"
KEEP_DAYS="${BISQL_BACKUP_KEEP_DAYS:-7}"

log() { echo "[$(date '+%F %T')] $*"; }

mkdir -p "$BACKUP_DIR"

# 1) 数据库就绪检查：不就绪直接退出（避免产生半截备份）
if ! docker exec "$CONTAINER" pg_isready -U "$DB_USER" >/dev/null 2>&1; then
  log "ERROR 容器 $CONTAINER 未就绪，跳过本次备份"
  exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/bisql_${STAMP}.sql.gz"
TMP="${OUT}.part"

# 2) 导出：先写 .part，成功后改名，保证线上目录里只有完整备份
#    --clean --if-exists 让 dump 可以直接覆盖恢复到已有库
log "开始备份 -> $OUT"
if docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" \
     --no-owner --clean --if-exists 2>>"$BACKUP_DIR/backup.log" | gzip -9 > "$TMP"; then
  if [ -s "$TMP" ]; then
    mv "$TMP" "$OUT"
    log "备份完成 $OUT ($(du -h "$OUT" | cut -f1))"
  else
    rm -f "$TMP"
    log "ERROR 备份文件为空，已丢弃"
    exit 1
  fi
else
  rm -f "$TMP"
  log "ERROR pg_dump 失败"
  exit 1
fi

# 3) 清理超期备份（只匹配本目录下 bisql_*.sql.gz）
find "$BACKUP_DIR" -maxdepth 1 -name 'bisql_*.sql.gz' -type f -mtime "+${KEEP_DAYS}" -print -delete \
  | while read -r old; do log "清理超期备份 $old"; done

log "保留策略：${KEEP_DAYS} 天；当前备份数 $(find "$BACKUP_DIR" -maxdepth 1 -name 'bisql_*.sql.gz' -type f | wc -l)"
