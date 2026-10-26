#!/bin/sh
set -eu

top=$(git rev-parse --show-toplevel)
git_dir=$(git rev-parse --absolute-git-dir)
state_dir="$git_dir/benchbox-cloud-gateway"
status_file="$git_dir/benchbox-cloud-gateway.status"
workflow="$top/.github/workflows/oracle-verdict.yml"
proxy_port=${BENCHBOX_GATEWAY_PROXY_PORT:-1055}
install_dir=${BENCHBOX_OMP_INSTALL_DIR:-${HOME:-/tmp}/.local/bin}
pid_file="$state_dir/tailscaled.pid"

say() {
  printf 'cloud-gateway: %s\n' "$1"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

short_socket_dir() {
  tag=$(printf '%s' "$git_dir" | { sha256sum 2>/dev/null || shasum -a 256; } | cut -c1-16)
  base=${TMPDIR:-/tmp}
  base=${base%/}
  if [ "${#base}" -gt 70 ]; then
    base=/tmp
  fi
  printf '%s/bbcg-%s' "$base" "$tag"
}

socket_dir=$(short_socket_dir)
socket="$socket_dir/ts.sock"

proc_args() {
  if [ -r "/proc/$1/cmdline" ]; then
    tr '\0' ' ' <"/proc/$1/cmdline"
  else
    ps -p "$1" -o args= 2>/dev/null
  fi
}

daemon_alive() {
  [ -f "$pid_file" ] || return 1
  pid=$(cat "$pid_file")
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  args=$(proc_args "$pid") || return 1
  case "$args" in
    *"--socket=$socket"*) return 0 ;;
    *) return 1 ;;
  esac
}

stop_daemon() {
  if daemon_alive; then
    pid=$(cat "$pid_file")
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
      waited=0
      while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 50 ]; do
        sleep 0.1
        waited=$((waited + 1))
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -rf "$state_dir" "$socket_dir"
}

record() {
  mkdir -p "$git_dir"
  {
    printf 'gateway=%s\n' "$1"
    printf 'gateway_detail=%s\n' "$2"
    if [ "$1" = reachable ]; then
      printf 'gateway_proxy=http://127.0.0.1:%s\n' "$proxy_port"
    fi
    printf 'omp=%s\n' "${omp_state:-not-installed}"
  } >"$status_file"
}

http_status() {
  if [ "${2:-}" = auth ]; then
    printf 'header = "Authorization: Bearer %s"\n' "$ORACLE_CLOUD_GATEWAY_TOKEN" |
      curl -sS -K - --proxy "http://127.0.0.1:$proxy_port" --max-time 20 -o /dev/null -w '%{http_code}' \
        "${ORACLE_GATEWAY_URL%/}$1" 2>/dev/null || printf '000'
  else
    curl -sS --proxy "http://127.0.0.1:$proxy_port" --max-time 20 -o /dev/null -w '%{http_code}' \
      "${ORACLE_GATEWAY_URL%/}$1" 2>/dev/null || printf '000'
  fi
}

omp_pin() {
  [ -f "$workflow" ] || return 1
  omp_url=$(sed -n 's/^[[:space:]]*OMP_URL:[[:space:]]*//p' "$workflow" | sed -n '1p' | tr -d ' \r')
  omp_sha=$(sed -n 's/^[[:space:]]*OMP_SHA256:[[:space:]]*//p' "$workflow" | sed -n '1p' | tr -d ' \r')
  case "$omp_url" in https://*) ;; *) return 1 ;; esac
  printf '%s' "$omp_sha" | grep -Eq '^[0-9a-f]{64}$'
}

install_omp() {
  omp_state=not-installed
  if ! omp_pin; then
    say "OMP pin in .github/workflows/oracle-verdict.yml is unreadable; not installing OMP"
    return 1
  fi
  if [ "$(uname -s)" != Linux ] || { [ "$(uname -m)" != x86_64 ] && [ "$(uname -m)" != amd64 ]; }; then
    say "the pinned OMP build is linux-x64; skipping install on $(uname -s) $(uname -m)"
    return 0
  fi
  target="$install_dir/omp"
  if [ -x "$target" ] && [ "$(sha256_of "$target")" = "$omp_sha" ]; then
    omp_state=installed
    say "OMP already installed and verified"
    return 0
  fi
  if ! mkdir -p "$install_dir" "$state_dir"; then
    say "cannot create the OMP install directory"
    return 1
  fi
  candidate="$state_dir/omp.download"
  rm -f "$candidate"
  if ! curl -fsSL --proto '=https' --tlsv1.2 "$omp_url" -o "$candidate"; then
    rm -f "$candidate"
    say "OMP download failed; the environment network allowlist needs the OMP download host"
    return 1
  fi
  if [ "$(sha256_of "$candidate")" != "$omp_sha" ]; then
    rm -f "$candidate"
    say "OMP checksum mismatch; refusing to install"
    return 1
  fi
  if ! chmod +x "$candidate"; then
    rm -f "$candidate"
    say "cannot mark OMP executable; refusing to install"
    return 1
  fi
  if ! mv -f "$candidate" "$target"; then
    rm -f "$candidate"
    say "cannot move OMP into place; refusing to install"
    return 1
  fi
  omp_state=installed
  say "OMP installed and verified"
}

bring_up() {
  mkdir -p "$state_dir"
  chmod 700 "$state_dir"
  if daemon_alive && [ -e "$socket" ]; then
    return 0
  fi
  stop_daemon
  mkdir -p "$state_dir" "$socket_dir"
  chmod 700 "$state_dir" "$socket_dir"
  nohup tailscaled --tun=userspace-networking --state=mem: --socket="$socket" \
    --outbound-http-proxy-listen="127.0.0.1:$proxy_port" >"$state_dir/tailscaled.log" 2>&1 &
  echo "$!" >"$pid_file"
  waited=0
  while [ ! -e "$socket" ] && [ "$waited" -lt 100 ]; do
    kill -0 "$(cat "$pid_file")" 2>/dev/null || return 1
    sleep 0.2
    waited=$((waited + 1))
  done
  [ -e "$socket" ] && daemon_alive
}

join_tailnet() {
  if tailscale --socket="$socket" status --json 2>/dev/null | grep -q '"BackendState": *"Running"'; then
    return 0
  fi
  key_file="$state_dir/authkey"
  (umask 077 && printf '%s' "$TS_AUTHKEY" >"$key_file")
  joined=0
  tailscale --socket="$socket" up --auth-key="file:$key_file" --hostname="benchbox-cloud-$$" \
    --accept-dns=false --timeout=90s >"$state_dir/up.log" 2>&1 && joined=1
  rm -f "$key_file"
  [ "$joined" -eq 1 ]
}

verify_gateway() {
  attempt=0
  health=000
  while [ "$attempt" -lt 10 ]; do
    health=$(http_status /healthz)
    [ "$health" = 200 ] && break
    attempt=$((attempt + 1))
    sleep 1
  done
  models=$(http_status /v1/models auth)
  say "gateway healthz=$health models=$models"
  [ "$health" = 200 ] && [ "$models" = 200 ]
}

finish_failed() {
  stop_daemon
  record unavailable "$1"
  say "gateway unavailable: $1"
}

up() {
  omp_state=not-installed
  if [ -z "${TS_AUTHKEY:-}" ] || [ -z "${ORACLE_GATEWAY_URL:-}" ] || [ -z "${ORACLE_CLOUD_GATEWAY_TOKEN:-}" ]; then
    stop_daemon
    record unavailable "TS_AUTHKEY, ORACLE_GATEWAY_URL and ORACLE_CLOUD_GATEWAY_TOKEN are not all set"
    say "gateway unavailable: TS_AUTHKEY, ORACLE_GATEWAY_URL and ORACLE_CLOUD_GATEWAY_TOKEN are not all set"
    return 0
  fi

  omp_rc=0
  install_omp || omp_rc=$?

  if ! command -v tailscaled >/dev/null 2>&1 || ! command -v tailscale >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    finish_failed "tailscale, tailscaled or curl is not installed"
    return "$omp_rc"
  fi

  completed=0
  trap 'if [ "$completed" -ne 1 ]; then stop_daemon; fi' EXIT
  trap 'exit 1' INT TERM HUP

  if ! bring_up; then
    completed=1
    if [ -f "$pid_file" ]; then
      say "daemon process: $(proc_args "$(cat "$pid_file")" | cut -c1-300)"
    fi
    if [ -f "$state_dir/tailscaled.log" ]; then
      tail -n 20 "$state_dir/tailscaled.log" | sed 's/^/cloud-gateway: daemon log: /'
    fi
    finish_failed "tailscaled did not start"
    return 1
  fi
  if ! join_tailnet; then
    completed=1
    finish_failed "the userspace tailnet join failed"
    return 1
  fi
  if verify_gateway; then
    record reachable "healthz and authenticated models answered 200"
    completed=1
    say "gateway reachable through http://127.0.0.1:$proxy_port"
  else
    completed=1
    finish_failed "healthz or authenticated models did not answer 200"
  fi
  return "$omp_rc"
}

case "${1:-up}" in
  up) up ;;
  stop)
    stop_daemon
    rm -f "$status_file"
    say "stopped"
    ;;
  status)
    if [ -f "$status_file" ]; then cat "$status_file"; else say "no status recorded"; fi
    ;;
  *)
    printf 'usage: cloud_gateway.sh [up|stop|status]\n' >&2
    exit 2
    ;;
esac
