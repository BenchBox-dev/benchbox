#!/bin/bash
set -euo pipefail
umask 077

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
REPO_SLUG=${REPO_SLUG:-BenchBox-dev/benchbox}
OMP=
PINNED_VERSION=
PINNED_SHA=
BROKER_PORT=${BROKER_PORT:-8765}
GATEWAY_PORT=${GATEWAY_PORT:-4000}
GATEWAY_DIR=${GATEWAY_DIR:-$HOME/.omp/oracle-gateway}
BROKER_TOKEN_FILE=$HOME/.omp/auth-broker.token
CLIENT_TOKEN_FILE=$GATEWAY_DIR/client.token
CLIENTS_FILE=$GATEWAY_DIR/clients.json
ACCOUNTS_FILE=$GATEWAY_DIR/.accounts.json
RUN_FILE=$GATEWAY_DIR/run.sh
LOG_DIR=$HOME/.omp/logs
AGENT_DIR=$HOME/Library/LaunchAgents
DOMAIN=gui/$(id -u)
BROKER_LABEL=dev.omp.auth-broker
GATEWAY_LABEL=dev.omp.auth-gateway
GATEWAY_PROVIDERS=(anthropic openai-codex muse-code google-antigravity)

usage() {
  echo "usage: $(basename "$0") broker|gateway|secrets|check|revoke <client>" >&2
  exit 2
}

fail() {
  echo "error: $*" >&2
  exit 1
}

file_sha256() {
  shasum -a 256 "$1" | cut -d' ' -f1
}

binary_version() {
  "$1" --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+([-+][A-Za-z0-9._-]+)?' | head -n 1
}

require_omp() {
  local develop_dir binary
  develop_dir=$(mktemp -d)
  export_develop "$develop_dir" || { rm -rf "$develop_dir"; exit 1; }
  binary=$(cd "$develop_dir" && uv run --quiet --isolated --no-project --python 3.12 --with pyyaml==6.0.3 -- \
    python -m _project.scripts.oracle_reviewers.cli install-omp --policy .github/oracle-reviewers.yml) \
    || { rm -rf "$develop_dir"; fail "cannot install the omp build pinned in origin/develop:.github/oracle-reviewers.yml"; }
  rm -rf "$develop_dir"
  [ -n "$binary" ] && [ -x "$binary" ] || fail "the pinned omp install gave no executable"
  OMP=$binary
  PINNED_SHA=$(file_sha256 "$OMP")
  PINNED_VERSION=$(binary_version "$OMP")
  [ -n "$PINNED_VERSION" ] || fail "the pinned omp at $OMP reports no version"
  "$OMP" auth-gateway serve --help 2>&1 | grep -q -e '--clients' \
    || fail "the pinned omp $PINNED_VERSION has no auth-gateway serve --clients, which scopes each reviewer client"
  echo "omp pin: $PINNED_VERSION sha256=$PINNED_SHA"
}

running_pid() {
  local pid
  pid=$(launchctl print "$DOMAIN/$1" 2>/dev/null | sed -n 's/^[[:space:]]*pid = \([0-9][0-9]*\)[[:space:]]*$/\1/p' | head -n 1)
  [ -n "$pid" ] || fail "$1 is not running"
  echo "$pid"
}

etime_seconds() {
  local value=$1 days=0 first second third hours=0 minutes seconds
  case $value in
    *-*) days=${value%%-*}; value=${value#*-} ;;
  esac
  IFS=: read -r first second third <<< "$value"
  if [ -n "$third" ]; then
    hours=$first minutes=$second seconds=$third
  else
    minutes=$first seconds=$second
  fi
  echo $(( (10#$days * 24 + 10#$hours) * 3600 + 10#$minutes * 60 + 10#$seconds ))
}

file_mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"
}

process_predates_file() {
  local pid=$1 file=$2 etime elapsed mtime
  etime=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d '[:space:]') || return 0
  [[ $etime =~ ^([0-9]+-)?([0-9]+:)?[0-9]+:[0-9]+$ ]] || return 0
  elapsed=$(etime_seconds "$etime") || return 0
  mtime=$(file_mtime "$file") || return 0
  [[ $mtime =~ ^[0-9]+$ ]] || return 0
  [ $(( $(date +%s) - elapsed + 1 )) -lt "$mtime" ]
}

agent_predates_binary() {
  local pid
  pid=$(launchctl print "$DOMAIN/$1" 2>/dev/null | sed -n 's/^[[:space:]]*pid = \([0-9][0-9]*\)[[:space:]]*$/\1/p' | head -n 1) || pid=""
  [ -n "$pid" ] && process_predates_file "$pid" "$OMP"
}

check_running_build() {
  local label=$1 port=$2 pid listener binary sha version
  pid=$(running_pid "$label") || exit 1
  listener=$(lsof -nP "-iTCP:$port" -sTCP:LISTEN -t 2>/dev/null | sort -u | paste -sd ' ' -)
  [ "$listener" = "$pid" ] \
    || fail "$label is process $pid but port $port is held by '${listener:-nothing}'; see its log in $LOG_DIR"
  binary=$(ps -o comm= -p "$pid" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  [ -n "$binary" ] && [ -x "$binary" ] || fail "$label runs '$binary', which is not an executable file"
  ! process_predates_file "$pid" "$binary" \
    || fail "$label started before $binary was last written, so it may run an older build; run '$(basename "$0") broker' and '$(basename "$0") gateway'"
  sha=$(file_sha256 "$binary")
  version=$(binary_version "$binary")
  echo "$label: omp $version sha256=$sha"
  [ "$sha" = "$PINNED_SHA" ] && [ "$version" = "$PINNED_VERSION" ] \
    || fail "$label runs omp $version sha256=$sha, not the pinned omp $PINNED_VERSION sha256=$PINNED_SHA; run '$(basename "$0") broker' and '$(basename "$0") gateway'"
}

check_run_file_build() {
  grep -qxF "$(run_file_exec_line)" "$RUN_FILE" 2>/dev/null \
    || fail "$RUN_FILE does not exec the pinned build at $OMP with the clients file and bind address this script writes; run '$(basename "$0") gateway'"
}

run_file_exec_line() {
  echo "exec \"$OMP\" auth-gateway serve --bind=127.0.0.1:$GATEWAY_PORT --clients=\"$CLIENTS_FILE\""
}

check_builds() {
  check_running_build "$BROKER_LABEL" "$BROKER_PORT"
  check_run_file_build
  check_running_build "$GATEWAY_LABEL" "$GATEWAY_PORT"
}

xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

render_plist() {
  local label=$1 log=$2 argument
  shift 2
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
EOF
  for argument in "$@"; do
    echo "    <string>$(xml_escape "$argument")</string>"
  done
  cat <<EOF
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>StandardOutPath</key>
  <string>$(xml_escape "$log")</string>
  <key>StandardErrorPath</key>
  <string>$(xml_escape "$log")</string>
</dict>
</plist>
EOF
}

agent_loaded() {
  launchctl print "$DOMAIN/$1" >/dev/null 2>&1
}

agent_unloaded() {
  ! agent_loaded "$1"
}

wait_for() {
  local _
  for _ in $(seq 1 20); do
    "$@" && return 0
    sleep 1
  done
  return 1
}

install_agent() {
  local label=$1 log=$2 changed=$3 plist staged
  shift 3
  plist=$AGENT_DIR/$label.plist
  mkdir -p "$AGENT_DIR" "$LOG_DIR"
  staged=$(mktemp)
  render_plist "$label" "$log" "$@" > "$staged"
  plutil -lint "$staged" >/dev/null
  if [ "$changed" = no ] && agent_loaded "$label" && cmp -s "$staged" "$plist" && ! agent_predates_binary "$label"; then
    rm -f "$staged"
    echo "$label: unchanged, left running"
    return 0
  fi
  chmod 644 "$staged"
  mv "$staged" "$plist"
  if agent_loaded "$label"; then
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    wait_for agent_unloaded "$label" || fail "launchd did not unload $label"
  fi
  launchctl bootstrap "$DOMAIN" "$plist"
  echo "$label: loaded"
}

broker_omp() {
  OMP_AUTH_BROKER_URL="http://127.0.0.1:$BROKER_PORT" OMP_AUTH_BROKER_TOKEN=$(cat "$BROKER_TOKEN_FILE") "$OMP" "$@"
}

broker_ok() {
  broker_omp auth-broker status --json 2>/dev/null | jq -e '.ok == true' >/dev/null
}

missing_logins() {
  local present provider
  present=$(broker_omp usage accounts --json 2>/dev/null | jq -r '.accounts[].provider' | sort -u) || present=""
  for provider in "${GATEWAY_PROVIDERS[@]}"; do
    printf '%s\n' "$present" | grep -x "$provider" >/dev/null || echo "$provider"
  done
}

gateway_curl() {
  local token_file=$1 url=$2
  shift 2
  printf 'Authorization: Bearer %s\n' "$(cat "$token_file")" | curl -s --max-time 10 -H @- "$@" "$url"
}

gateway_code() {
  gateway_curl "$CLIENT_TOKEN_FILE" "$1" -o /dev/null -w '%{http_code}' || true
}

valid_client_name() {
  local LC_ALL=C
  [[ $1 =~ ^oracle-[a-z0-9]+(-[a-z0-9]+)*$ ]]
}

client_token_files() {
  local file
  echo "oracle-ci $CLIENT_TOKEN_FILE"
  for file in "$GATEWAY_DIR"/*-client.token; do
    [ -e "$file" ] || continue
    echo "oracle-$(basename "$file" -client.token) $file"
  done
}

require_valid_token_files() {
  local name file
  [ ! -e "$GATEWAY_DIR/ci-client.token" ] \
    || fail "$GATEWAY_DIR/ci-client.token would be a second oracle-ci; oracle-ci's token is $CLIENT_TOKEN_FILE, so remove or rename it"
  while read -r name file; do
    valid_client_name "$name" \
      || fail "$file names the client '$name'; rename it to <name>-client.token with <name> lowercase letters, digits and single hyphens"
  done < <(client_token_files)
}

gateway_healthy() {
  [ "$(gateway_code "http://127.0.0.1:$GATEWAY_PORT/healthz")" = 200 ]
}

tailnet_url() {
  local host
  host=$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // empty' | sed 's/\.$//') || host=""
  [ -n "$host" ] || fail "tailscale is not running or not logged in on this Mac"
  echo "https://$host:$GATEWAY_PORT"
}

joined() {
  paste -sd ' ' -
}

model_signature() {
  jq -c '[.data[]?.id] | sort'
}

check_clients_file() {
  local names expected differing
  names=$(jq -r 'keys[]' "$CLIENTS_FILE" | sort) || fail "$CLIENTS_FILE is not readable JSON"
  expected=$(client_token_files | cut -d' ' -f1 | sort)
  [ "$names" = "$expected" ] || fail "$CLIENTS_FILE has clients [$(echo "$names" | joined)] but the token files are for [$(echo "$expected" | joined)]; run '$(basename "$0") gateway'"
  differing=$(jq -r '."oracle-ci".models as $ci | to_entries[] | select(.value.models != $ci) | .key' "$CLIENTS_FILE")
  [ -z "$differing" ] || fail "$CLIENTS_FILE gives [$(echo "$differing" | joined)] models that differ from oracle-ci's"
}

check_gateway_scope() {
  local base=$1 expected health models usage name token_file signature ci_signature
  check_clients_file
  expected=$(jq -r '."oracle-ci".models | length' "$CLIENTS_FILE")
  health=$(gateway_code "$base/healthz")
  [ "$health" = 200 ] || fail "$base/healthz answered '$health'; is the gateway running and published?"
  ci_signature=$(gateway_curl "$CLIENT_TOKEN_FILE" "$base/v1/models" | model_signature) \
    || fail "$base/v1/models did not answer"
  models=$(echo "$ci_signature" | jq 'length')
  usage=$(gateway_code "$base/v1/usage")
  echo "$base: healthz=$health models=$models/$expected usage=$usage"
  [ "$models" = "$expected" ] || fail "$base lists $models models; the clients file allows $expected"
  [ "$usage" = 403 ] || fail "$base answered /v1/usage with $usage; expected 403"
  while read -r name token_file; do
    [ "$name" != oracle-ci ] || continue
    signature=$(gateway_curl "$token_file" "$base/v1/models" | model_signature) \
      || fail "$base/v1/models did not answer for $name"
    models=$(echo "$signature" | jq 'length')
    usage=$(gateway_curl "$token_file" "$base/v1/usage" -o /dev/null -w '%{http_code}' || true)
    echo "$base: $name models=$models/$expected usage=$usage"
    [ "$models" = "$expected" ] || fail "$base lists $models models for $name; oracle-ci has $expected"
    [ "$signature" = "$ci_signature" ] || fail "$base lists different models for $name than for oracle-ci"
    [ "$usage" = 403 ] || fail "$base answered /v1/usage for $name with $usage; expected 403"
  done < <(client_token_files)
}

export_develop() {
  local destination=$1
  git -C "$REPO_ROOT" fetch --quiet origin +refs/heads/develop:refs/remotes/origin/develop \
    || fail "cannot fetch origin/develop in $REPO_ROOT"
  git -C "$REPO_ROOT" archive origin/develop _project/scripts .github \
    | tar -x -C "$destination" || fail "cannot export origin/develop from $REPO_ROOT"
}

cmd_broker() {
  require_omp
  "$OMP" auth-broker token >/dev/null
  [ -s "$BROKER_TOKEN_FILE" ] || fail "omp auth-broker token did not create $BROKER_TOKEN_FILE"
  chmod 600 "$BROKER_TOKEN_FILE"
  install_agent "$BROKER_LABEL" "$LOG_DIR/auth-broker.log" no \
    "$OMP" auth-broker serve "--bind=127.0.0.1:$BROKER_PORT" --trust-proxy-headers
  wait_for broker_ok || fail "the broker did not report ok; see $LOG_DIR/auth-broker.log"
  echo "broker: running on 127.0.0.1:$BROKER_PORT"
  local missing provider
  missing=$(missing_logins)
  if [ -n "$missing" ]; then
    echo "log in to each missing provider, then run '$(basename "$0") gateway':"
    for provider in $missing; do
      echo "  $OMP auth-broker login $provider"
    done
  fi
}

cmd_gateway() {
  require_omp
  if ! { [ -s "$BROKER_TOKEN_FILE" ] && broker_ok; }; then
    fail "the broker is not running; run '$(basename "$0") broker' first"
  fi
  local missing
  missing=$(missing_logins)
  [ -z "$missing" ] || fail "no broker login for: $(printf '%s\n' "$missing" | paste -sd ' ' -)"
  require_valid_token_files
  mkdir -p "$GATEWAY_DIR/home"
  chmod 700 "$GATEWAY_DIR" "$GATEWAY_DIR/home"
  if [ ! -s "$CLIENT_TOKEN_FILE" ]; then
    openssl rand -base64 36 | tr -d '\n' > "$CLIENT_TOKEN_FILE"
  fi
  chmod 600 "$CLIENT_TOKEN_FILE"
  local named_clients=() name token_file
  while read -r name token_file; do
    [ "$name" != oracle-ci ] || continue
    chmod 600 "$token_file"
    named_clients+=(--client "$name=$token_file")
  done < <(client_token_files)
  local develop_dir
  develop_dir=$(mktemp -d)
  trap "rm -rf '$ACCOUNTS_FILE' '$develop_dir'" EXIT
  export_develop "$develop_dir"
  broker_omp usage accounts --json > "$ACCOUNTS_FILE"
  (cd "$develop_dir" && uv run --quiet --isolated --no-project --python 3.12 --with pyyaml==6.0.3 -- \
    python -m _project.scripts.oracle_reviewers.cli gateway-clients \
    --token-file "$CLIENT_TOKEN_FILE" ${named_clients[@]+"${named_clients[@]}"} \
    --accounts "$ACCOUNTS_FILE" --out "$CLIENTS_FILE")
  rm -f "$ACCOUNTS_FILE"
  local staged changed=no
  staged=$(mktemp)
  cat > "$staged" <<EOF
#!/bin/sh
set -eu
export HOME="$GATEWAY_DIR/home"
export OMP_AUTH_BROKER_URL="http://127.0.0.1:$BROKER_PORT"
OMP_AUTH_BROKER_TOKEN=\$(cat "$BROKER_TOKEN_FILE")
export OMP_AUTH_BROKER_TOKEN
$(run_file_exec_line)
EOF
  if cmp -s "$staged" "$RUN_FILE"; then
    rm -f "$staged"
  else
    chmod 700 "$staged"
    mv "$staged" "$RUN_FILE"
    changed=yes
  fi
  install_agent "$GATEWAY_LABEL" "$LOG_DIR/auth-gateway.log" "$changed" /bin/sh "$RUN_FILE"
  wait_for gateway_healthy || fail "the gateway did not answer /healthz; see $LOG_DIR/auth-gateway.log"
  check_gateway_scope "http://127.0.0.1:$GATEWAY_PORT"
  tailscale serve --bg "--https=$GATEWAY_PORT" "http://127.0.0.1:$GATEWAY_PORT"
  local url
  url=$(tailnet_url)
  echo "gateway: published at $url"
}

cmd_revoke() {
  local name=${1:-} token_file staged
  [ "$name" != oracle-ci ] \
    || fail "oracle-ci is the review client; rotate its token with '$(basename "$0") gateway' instead"
  valid_client_name "$name" \
    || fail "name a client like oracle-local; usage: $(basename "$0") revoke <client>"
  token_file=$GATEWAY_DIR/${name#oracle-}-client.token
  jq -e --arg name "$name" 'has($name)' "$CLIENTS_FILE" >/dev/null 2>&1 || [ -e "$token_file" ] \
    || fail "$name is neither in $CLIENTS_FILE nor has a token file"
  staged=$(mktemp "$GATEWAY_DIR/.clients.XXXXXX")
  if ! jq --indent 2 --arg name "$name" 'del(.[$name])' "$CLIENTS_FILE" > "$staged"; then
    rm -f "$staged"
    fail "cannot read $CLIENTS_FILE"
  fi
  chmod 600 "$staged"
  mv "$staged" "$CLIENTS_FILE"
  rm -f "$token_file"
  echo "revoked: $name leaves $CLIENTS_FILE and its token file is removed; the gateway stops accepting it within 10 seconds"
}

cmd_secrets() {
  [ -s "$CLIENT_TOKEN_FILE" ] || fail "no client token; run '$(basename "$0") gateway' first"
  local url
  url=$(tailnet_url)
  gh secret set ORACLE_GATEWAY_TOKEN --env oracle --repo "$REPO_SLUG" < "$CLIENT_TOKEN_FILE"
  gh secret set ORACLE_GATEWAY_URL --env oracle --repo "$REPO_SLUG" --body "$url"
  echo "secrets: ORACLE_GATEWAY_TOKEN and ORACLE_GATEWAY_URL=$url set on the oracle environment"
}

cmd_check() {
  require_omp
  broker_ok || fail "the broker is not running"
  echo "broker: ok on 127.0.0.1:$BROKER_PORT"
  check_builds
  local missing url secrets name
  missing=$(missing_logins)
  [ -z "$missing" ] || fail "no broker login for: $(printf '%s\n' "$missing" | paste -sd ' ' -)"
  echo "logins: ${GATEWAY_PROVIDERS[*]}"
  require_valid_token_files
  check_gateway_scope "http://127.0.0.1:$GATEWAY_PORT"
  url=$(tailnet_url)
  check_gateway_scope "$url"
  secrets=$(gh secret list --env oracle --repo "$REPO_SLUG" --json name --jq '.[].name') \
    || fail "gh could not list the oracle environment secrets; run 'gh auth login'"
  for name in ORACLE_GATEWAY_TOKEN ORACLE_GATEWAY_URL; do
    printf '%s\n' "$secrets" | grep -x "$name" >/dev/null || fail "the oracle environment has no $name secret"
  done
  echo "secrets: ORACLE_GATEWAY_TOKEN and ORACLE_GATEWAY_URL are set"
}

main() {
  [ $# -ge 1 ] || usage
  case $1 in
    broker | gateway | secrets | check) [ $# -eq 1 ] || usage ;;
    revoke) [ $# -eq 2 ] || usage ;;
  esac
  case $1 in
    broker) cmd_broker ;;
    gateway) cmd_gateway ;;
    secrets) cmd_secrets ;;
    check) cmd_check ;;
    revoke) cmd_revoke "$2" ;;
    *) usage ;;
  esac
}

(return 0 2>/dev/null) || main "$@"
