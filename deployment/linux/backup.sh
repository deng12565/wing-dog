#!/usr/bin/env bash
set -euo pipefail

backup_root="${BACKUP_ROOT:-/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-30}"
backup_uid="${BACKUP_UID:-1000}"
backup_gid="${BACKUP_GID:-1000}"

backup_once() {
    local day target temporary checksum
    day="$(date +%F)"
    target="$backup_root/goutoujunshi-$day.sql.gz"
    checksum="$target.sha256"
    if [[ -s "$target" && -s "$checksum" ]]; then
        return 0
    fi
    temporary="$target.tmp"
    umask 077
    MYSQL_PWD="$(< /run/secrets/mysql_root_password)" \
        mysqldump -h mysql -uroot \
        --single-transaction --quick --hex-blob --no-tablespaces \
        --routines=false --events=false --triggers \
        goutoujunshi | gzip -9 > "$temporary"
    gzip -t "$temporary"
    mv -f "$temporary" "$target"
    (cd "$backup_root" && sha256sum "$(basename "$target")") > "$checksum.tmp"
    mv -f "$checksum.tmp" "$checksum"
    chmod 600 "$target" "$checksum"
    chown "$backup_uid:$backup_gid" "$target" "$checksum"
    find "$backup_root" -maxdepth 1 -type f \
        \( -name 'goutoujunshi-*.sql.gz' -o -name 'goutoujunshi-*.sql.gz.sha256' \) \
        -mtime "+$retention_days" -delete
    printf '{"component":"backup","code":"done","file":"%s"}\n' "$(basename "$target")"
}

mkdir -p "$backup_root"
if [[ "${1:-}" == "--once" ]]; then
    backup_once
    exit 0
fi
while true; do
    backup_once
    sleep 3600
done
