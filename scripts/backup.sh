#!/usr/bin/env bash
# SQLite's online backup API produces a coherent snapshot even with WAL active.
set -euo pipefail
umask 077
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_root="${ERREX_BACKUP_ROOT:-/mnt/backup/errex-sentry}"
backup_image="${ERREX_BACKUP_IMAGE:-errex-sentry:local}"
install -d -m 700 "$backup_root"
backup_dir="$(mktemp -d "$backup_root/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
sqlite3 "$root/data/store/errex.db" ".timeout 10000" ".backup '$backup_dir/errex.db'"
test "$(sqlite3 -readonly "$backup_dir/errex.db" 'PRAGMA integrity_check;')" = ok
tar -C "$root" --exclude='./.git' --exclude='./data' --exclude='./target' \
    --exclude='./.venv-*' --exclude='./web/node_modules' --exclude='./web/.svelte-kit' \
    --exclude='./web/build' --exclude='__pycache__' -czf "$backup_dir/source-and-config.tar.gz" .
docker image inspect "$backup_image" --format '{{.Id}}' > "$backup_dir/image-id"
chmod -R go-rwx "$backup_dir"
printf '%s\n' "$backup_dir"
