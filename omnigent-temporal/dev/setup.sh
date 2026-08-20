#!/usr/bin/env bash
# One-time setup: build the venv this package and the dev scripts run in.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

command -v uv >/dev/null || die "uv is not installed. See https://docs.astral.sh/uv/"
command -v temporal >/dev/null || log "temporal CLI not found; install it before running temporal.sh (brew install temporal)"

log "creating the venv at $VENV"
# UV_NO_CONFIG: the repo's uv.toml uses a duration syntax older uv builds cannot
# parse, and it applies to anything run from inside the tree.
( cd "$PKG_DIR" && UV_NO_CONFIG=1 uv venv --python 3.12 )

log "installing omnigent, its python client, and this package"
( cd "$PKG_DIR" && UV_NO_CONFIG=1 uv pip install -e "$REPO_DIR" -e "$REPO_DIR/sdks/python-client" -e . )

mkdir -p "$OMNIGENT_WORKSPACE"
log "done. Next: dev/temporal.sh, dev/omnigent.sh, dev/worker.sh (three shells)"
