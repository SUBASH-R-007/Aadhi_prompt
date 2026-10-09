#!/bin/sh
# Container entrypoint for the API, job workers and render workers.
#   RUN_MIGRATIONS=1   apply database migrations (python -m aadhi.cli migrate) before starting;
#                      set it on exactly one service (the API) so workers never race on DDL.
#   MIGRATION_RETRIES  attempts while the database is still starting (default 10, 3 s apart).
# Then exec the command (default: uvicorn), so signals reach the process directly (tini is PID 1).
set -eu

umask 027
mkdir -p "${DATA_DIR:-/data/app}" "${STORAGE_LOCAL_DIR:-/data/storage}" 2>/dev/null || true

# A TMPDIR that is not writable makes Python silently fall back to /tmp. For the docker-sandbox
# worker that breaks Manim renders (the sandbox bind-mounts the host path), so fail loudly instead.
if [ -n "${TMPDIR:-}" ]; then
    mkdir -p "$TMPDIR" 2>/dev/null || true
    if [ ! -d "$TMPDIR" ] || [ ! -w "$TMPDIR" ]; then
        echo "entrypoint: TMPDIR=$TMPDIR is not a writable directory for uid $(id -u);" \
             "create it on the host with: install -d -o 1000 -g 1000 $TMPDIR" >&2
        exit 1
    fi
fi

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    attempts="${MIGRATION_RETRIES:-10}"
    n=1
    until python -m aadhi.cli migrate; do
        if [ "$n" -ge "$attempts" ]; then
            echo "entrypoint: migrations failed after $n attempts" >&2
            exit 1
        fi
        echo "entrypoint: migration attempt $n failed; retrying in 3 s" >&2
        n=$((n + 1))
        sleep 3
    done
    echo "entrypoint: database schema is up to date"
fi

exec "$@"
