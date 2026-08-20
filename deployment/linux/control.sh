#!/usr/bin/env bash
set -euo pipefail

deployment_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="$deployment_dir/server.env"
compose=(docker compose --env-file "$env_file" -f "$deployment_dir/compose.yaml")

test -s "$env_file"

case "${1:-}" in
    build)
        "${compose[@]}" build --pull gateway
        ;;
    seed-home)
        "${compose[@]}" run --rm --no-deps gateway true
        ;;
    bootstrap)
        "${compose[@]}" run --rm --no-deps gateway bash /opt/wing-dog/deployment/linux/bootstrap.sh
        ;;
    start)
        "${compose[@]}" up -d mysql gateway backup
        ;;
    stop)
        "${compose[@]}" stop gateway backup mysql
        ;;
    status)
        "${compose[@]}" ps
        ;;
    logs)
        "${compose[@]}" logs --tail "${2:-100}" gateway backup mysql
        ;;
    fingerprint)
        "${compose[@]}" exec -T gateway \
            python /opt/wing-dog/deployment/linux/run_with_env.py \
            python /opt/wing-dog/runtime/goutoujunshi_cli.py migration-fingerprint
        ;;
    backup)
        "${compose[@]}" run --rm --no-deps backup bash -lc \
            '/opt/wing-dog/deployment/linux/backup.sh --once'
        ;;
    *)
        printf 'usage: %s {build|seed-home|bootstrap|start|stop|status|logs|fingerprint|backup}\n' "$0" >&2
        exit 2
        ;;
esac
