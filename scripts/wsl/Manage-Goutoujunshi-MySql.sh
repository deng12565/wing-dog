#!/usr/bin/env bash
set -euo pipefail

container="mysql_container"
compose_dir="/home/dengdeng/mysql"

mysql_root() {
    docker exec -i "$container" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot --default-character-set=utf8mb4 "$@"' sh "$@"
}

start_mysql() {
    if ! docker start "$container" >/dev/null 2>&1; then
        (cd "$compose_dir" && docker compose up -d mysql >/dev/null)
    fi
    for _ in $(seq 1 30); do
        if docker exec "$container" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqladmin ping -uroot --silent' >/dev/null 2>&1; then
            printf '{"ok":true,"mysql":"ready"}\n'
            return 0
        fi
        sleep 2
    done
    printf '{"ok":false,"mysql":"timeout"}\n' >&2
    return 1
}

stop_mysql() {
    if ! docker inspect "$container" >/dev/null 2>&1; then
        printf '{"ok":true,"mysql":"already_stopped"}\n'
        return 0
    fi
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container")" == "true" ]]; then
        docker stop --time 30 "$container" >/dev/null
    fi
    printf '{"ok":true,"mysql":"stopped"}\n'
}

function_calls_fingerprint() {
    mysql_root -Nse "SELECT CONCAT(TABLE_NAME,CHAR(9),ENGINE,CHAR(9),TABLE_COLLATION) FROM information_schema.TABLES WHERE TABLE_SCHEMA='function_calls' ORDER BY TABLE_NAME" \
        | sha256sum | awk '{print $1}'
}

setup_database() {
    local schema_path="$1"
    local app_password
    IFS= read -r app_password
    app_password="${app_password#$'\xEF\xBB\xBF'}"
    app_password="${app_password%$'\r'}"
    if [[ ! "$app_password" =~ ^[A-Za-z0-9_-]{32,}$ ]]; then
        printf '{"ok":false,"error":"invalid_app_password_format","length":%d}\n' "${#app_password}" >&2
        return 2
    fi
    if [[ ! -f "$schema_path" ]]; then
        printf '{"ok":false,"error":"schema_not_found"}\n' >&2
        return 2
    fi
    start_mysql >/dev/null
    local before after
    before="$(function_calls_fingerprint)"
    mysql_root <<SQL
CREATE DATABASE IF NOT EXISTS goutoujunshi CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'goutoujunshi_app'@'%' IDENTIFIED BY '$app_password';
ALTER USER 'goutoujunshi_app'@'%' IDENTIFIED BY '$app_password';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'goutoujunshi_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON goutoujunshi.* TO 'goutoujunshi_app'@'%';
FLUSH PRIVILEGES;
SQL
    { printf 'USE goutoujunshi;\n'; cat "$schema_path"; } | mysql_root
    after="$(function_calls_fingerprint)"
    if [[ "$before" != "$after" ]]; then
        printf '{"ok":false,"error":"function_calls_changed"}\n' >&2
        return 3
    fi
    printf '{"ok":true,"schema_version":3,"function_calls_unchanged":true}\n'
}

backup_database() {
    local destination="$1"
    if [[ "$destination" != /mnt/*/goutoujunshi-????-??-??.sql ]]; then
        printf '{"ok":false,"error":"unsafe_backup_path"}\n' >&2
        return 2
    fi
    start_mysql >/dev/null
    mkdir -p "$(dirname "$destination")"
    local temporary="${destination}.tmp"
    docker exec "$container" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --no-tablespaces --routines=false --events=false goutoujunshi' > "$temporary"
    chmod 600 "$temporary"
    mv -f "$temporary" "$destination"
    printf '{"ok":true,"backup":"%s"}\n' "$(basename "$destination")"
}

case "${1:-}" in
    start)
        start_mysql
        ;;
    stop)
        stop_mysql
        ;;
    setup)
        [[ $# -eq 2 ]] || { printf 'usage: %s setup SCHEMA\n' "$0" >&2; exit 2; }
        setup_database "$2"
        ;;
    backup)
        [[ $# -eq 2 ]] || { printf 'usage: %s backup DESTINATION\n' "$0" >&2; exit 2; }
        backup_database "$2"
        ;;
    *)
        printf 'usage: %s {start|stop|setup|backup}\n' "$0" >&2
        exit 2
        ;;
esac
