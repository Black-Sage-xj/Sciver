#!/usr/bin/env bash
# Codex startup wrapper: verify/repair network before every launch.
set -euo pipefail

CODEX_BIN="${CODEX_BIN:-$HOME/bin/codex.real}"
NET_CLI="${CODEX_NET_CLI:-$HOME/bin/codex-net}"
DAILY_CODEX_HOME="${DAILY_CODEX_HOME:-$HOME/.codex}"

# Account selection is auth-only. Keep the daily Codex runtime fixed so
# config/skills/plugins/workspace state never change when quota is switched.
if [[ -z "${CODEX_HOME:-}" ]]; then
  export CODEX_HOME="$DAILY_CODEX_HOME"
fi

if [[ ! -x "$CODEX_BIN" ]]; then
  echo "codex: missing executable: $CODEX_BIN" >&2
  exit 127
fi

# Remote SSH sends an independent `app-server --listen unix://` bootstrap.
# Redirect it into the durable daemon; never create an orphan listener that
# can survive CLI upgrades and block account switching.
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[i]}" == "app-server" && "${args[i+1]:-}" == "--listen" && "${args[i+2]:-}" == "unix://" ]]; then
    exec "$CODEX_BIN" -c features.code_mode_host=true app-server daemon start
  fi
done

if [[ "${CODEX_NO_PROXY:-}" == "1" ]]; then
  exec env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy -u CODEX_PROXY \
    "$CODEX_BIN" "$@"
fi

if [[ "${CODEX_NET_SKIP:-}" != "1" ]]; then
  export CODEX_NET_PREFERRED_PROXY="${CODEX_NET_PREFERRED_PROXY:-${CODEX_PROXY:-http://127.0.0.1:7890}}"
  if ! net_env="$($NET_CLI ensure --shell --reason codex-startup)"; then
    echo "codex: network preflight failed; run 'codex-net ensure' for details" >&2
    exit 69
  fi
  eval "$net_env"
fi

exec "$CODEX_BIN" "$@"
