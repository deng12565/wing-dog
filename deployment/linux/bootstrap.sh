#!/usr/bin/env bash
set -euo pipefail

project_root="${WING_DOG_PROJECT_ROOT:-/opt/wing-dog}"
hermes_home="${HERMES_HOME:-/opt/data}"
profile_home="$hermes_home/profiles/goutoujunshi"
python="/opt/hermes/.venv/bin/python"
run_env=("$python" "$project_root/deployment/linux/run_with_env.py")

test -s "$hermes_home/.env"
test -s "$hermes_home/config.yaml"

if [[ ! -d "$profile_home" ]]; then
    "${run_env[@]}" hermes profile create goutoujunshi --no-skills --no-alias \
        --description 'Private Feishu relationship adviser backed only by the goutoujunshi MySQL database.' \
        >/dev/null
fi

"${run_env[@]}" "$python" "$project_root/runtime/bootstrap.py" install-plugin \
    --plugin-source "$project_root/runtime/goutoujunshi" \
    --target-home "$hermes_home"
"${run_env[@]}" "$python" "$project_root/runtime/bootstrap.py" install-skill \
    --project-root "$project_root" --target-home "$hermes_home"
"${run_env[@]}" "$python" "$project_root/runtime/bootstrap.py" install-skill \
    --project-root "$project_root" --target-home "$profile_home"
"${run_env[@]}" "$python" "$project_root/runtime/bootstrap.py" configure-profile \
    --profile-home "$profile_home" --global-env "$hermes_home/.env"
"${run_env[@]}" "$python" "$project_root/runtime/bootstrap.py" configure-global \
    --config "$hermes_home/config.yaml" --source-env "$hermes_home/.env"
"${run_env[@]}" "$python" "$project_root/runtime/goutoujunshi_cli.py" reconcile-config \
    --config "$hermes_home/config.yaml"
"${run_env[@]}" "$python" "$project_root/runtime/bootstrap.py" verify \
    --config "$hermes_home/config.yaml" \
    --profile-config "$profile_home/config.yaml" \
    --profile-env "$profile_home/.env" \
    --env "$hermes_home/.env"
