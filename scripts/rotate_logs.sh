#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE=${ENV_FILE:-"$PROJECT_ROOT/.env"}
RETENTION_DAYS=30

if [ -f "$ENV_FILE" ]; then
    set -a
    . "$ENV_FILE"
    set +a
    RETENTION_DAYS=${ARCHIVE_RETENTION_DAYS:-30}
fi

mkdir -p "$PROJECT_ROOT/logs"
find "$PROJECT_ROOT/logs" -type f -name '*.log' -mtime "+$RETENTION_DAYS" -delete
