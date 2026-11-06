#!/bin/sh
set -eu

# Runtime-only setting: never bake the admin path or password into React assets.
route=/etc/nginx/snippets/admin-route.conf
if [ -z "${ADMIN_PATH:-}" ]; then
    printf '# Admin route disabled\n' > "$route"
else
    if ! printf '%s\n' "$ADMIN_PATH" | grep -Eq '^/[A-Za-z0-9][A-Za-z0-9_-]{7,63}$'; then
        echo 'Invalid ADMIN_PATH' >&2
        exit 1
    fi
    printf 'location = %s { proxy_pass http://backend_app; }\nlocation ^~ %s/ { proxy_pass http://backend_app; }\n' "$ADMIN_PATH" "$ADMIN_PATH" > "$route"
fi
