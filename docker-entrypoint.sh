#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
    data_dir="${DATA_DIR:-/app/bot/data}"
    if [ "$data_dir" = "/" ] || [ -z "$data_dir" ]; then
        echo "Refusing unsafe DATA_DIR" >&2
        exit 1
    fi
    mkdir -p "$data_dir"
    chown -R appuser:appuser "$data_dir"
    exec gosu appuser "$@"
fi

exec "$@"
