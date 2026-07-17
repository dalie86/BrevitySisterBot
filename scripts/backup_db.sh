#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE=${ENV_FILE:-"$PROJECT_ROOT/.env"}

if [ ! -f "$ENV_FILE" ]; then
    echo "Environment file not found: $ENV_FILE" >&2
    exit 2
fi

set -a
. "$ENV_FILE"
set +a

: "${DB_HOST:?DB_HOST is required}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"

BACKUP_DIR=${BACKUP_DIR:-"$PROJECT_ROOT/backups"}
RETENTION_DAYS=${ARCHIVE_RETENTION_DAYS:-30}
TIMESTAMP=$(date -u "+%Y%m%dT%H%M%SZ")
FINAL_PATH="$BACKUP_DIR/brevity-$TIMESTAMP.sql.gz"
TEMP_PATH="$FINAL_PATH.tmp"

umask 077
mkdir -p "$BACKUP_DIR"
trap 'rm -f "$TEMP_PATH"' EXIT HUP INT TERM

MYSQL_PWD=$DB_PASSWORD mysqldump \
    --host="$DB_HOST" \
    --user="$DB_USER" \
    --single-transaction \
    --quick \
    --skip-lock-tables \
    "$DB_NAME" | gzip -9 > "$TEMP_PATH"

gzip -t "$TEMP_PATH"
mv "$TEMP_PATH" "$FINAL_PATH"
trap - EXIT HUP INT TERM

find "$BACKUP_DIR" -type f -name 'brevity-*.sql.gz' -mtime "+$RETENTION_DAYS" -delete
echo "$FINAL_PATH"
