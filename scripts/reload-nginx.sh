#!/bin/sh
# Certbot deploy hook on the Linux VM. Resolve the installed symlink back to
# this checkout, so renewal uses its .env (including COMPOSE_FILE/project name).
set -eu
script_path=$(readlink -f "$0")
project_dir=$(dirname "$(dirname "$script_path")")
cd "$project_dir"
docker compose exec -T frontend nginx -t
exec docker compose exec -T frontend nginx -s reload
