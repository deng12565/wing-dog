#!/usr/bin/env bash
set -euo pipefail

deployment_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="$deployment_dir/server.env"
compose=(docker compose --env-file "$env_file" -f "$deployment_dir/compose.yaml")

if [[ $# -ne 2 || "$1" != "--confirm-replace-goutoujunshi" ]]; then
    printf 'usage: %s --confirm-replace-goutoujunshi DUMP.sql[.gz]\n' "$0" >&2
    exit 2
fi

test -s "$env_file"
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

dump_path="$(realpath "$2")"
migration_root="$(realpath "$WING_DOG_DATA_ROOT/migration")"
case "$dump_path" in
    "$migration_root"/*) ;;
    *) printf 'refusing dump outside %s\n' "$migration_root" >&2; exit 2 ;;
esac
test -s "$dump_path"

"${compose[@]}" stop gateway backup >/dev/null 2>&1 || true
"${compose[@]}" up -d mysql
healthy=false
for _ in $(seq 1 60); do
    mysql_container="$("${compose[@]}" ps -q mysql)"
    if [[ -n "$mysql_container" ]] && \
            [[ "$(docker inspect -f '{{.State.Health.Status}}' "$mysql_container")" == "healthy" ]]; then
        healthy=true
        break
    fi
    sleep 2
done
if [[ "$healthy" != true ]]; then
    printf 'MySQL did not become healthy\n' >&2
    exit 1
fi

mysql_root() {
    "${compose[@]}" exec -T mysql sh -lc \
        'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" exec mysql -uroot --default-character-set=utf8mb4 "$@"' \
        sh "$@"
}
printf 'DROP DATABASE IF EXISTS goutoujunshi; CREATE DATABASE goutoujunshi CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;\n' \
    | mysql_root
if [[ "$dump_path" == *.gz ]]; then
    gzip -dc "$dump_path" | mysql_root goutoujunshi
else
    mysql_root goutoujunshi < "$dump_path"
fi
{
    printf 'USE goutoujunshi;\n'
    cat "$WING_DOG_CODE_ROOT/runtime/goutoujunshi/schema.sql"
} | mysql_root
cat <<'SQL' | mysql_root
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'goutoujunshi_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON goutoujunshi.* TO 'goutoujunshi_app'@'%';
FLUSH PRIVILEGES;
SQL

"${compose[@]}" run --rm --no-deps gateway \
    python /opt/wing-dog/deployment/linux/run_with_env.py \
    python /opt/wing-dog/runtime/goutoujunshi_cli.py migration-fingerprint \
    | tee "$WING_DOG_DATA_ROOT/migration/remote-fingerprint.json"
chmod 600 "$WING_DOG_DATA_ROOT/migration/remote-fingerprint.json"
