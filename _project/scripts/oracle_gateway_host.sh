#!/bin/bash
set -euo pipefail
umask 077

REPO_ROOT=${GATEWAY_HOST_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
REPO_SLUG=${REPO_SLUG:-BenchBox-dev/benchbox}
OMP=
PINNED_VERSION=
PINNED_SHA=
BROKER_PORT=${BROKER_PORT:-8765}
GATEWAY_PORT=${GATEWAY_PORT:-4000}
GATEWAY_DIR=${GATEWAY_DIR:-$HOME/.omp/review-gateway}
BROKER_TOKEN_FILE=$HOME/.omp/auth-broker.token
CLIENT_TOKEN_FILE=$GATEWAY_DIR/client.token
CLIENTS_FILE=$GATEWAY_DIR/clients.json
DENYLIST_FILE=$GATEWAY_DIR/disabled-providers
GATEWAY_CONFIG=$GATEWAY_DIR/home/.omp/agent/config.yml
STATE_FILE=$GATEWAY_DIR/monitor.state
SCRATCH=
DEVELOP_DIR=
ACCOUNTS_FILE=
EXCLUDE_ARGS=()
DEGRADED_COUNT=0
RUN_FILE=$GATEWAY_DIR/run.sh
LOG_DIR=$HOME/.omp/logs
AGENT_DIR=$HOME/Library/LaunchAgents
DOMAIN=gui/$(id -u)
BROKER_LABEL=dev.omp.auth-broker
GATEWAY_LABEL=dev.omp.auth-gateway
MONITOR_LABEL=dev.omp.review-gateway-monitor
MONITOR_INTERVAL=900
HEALTH_LABEL=review-gateway-health
PROVIDER_REASON_NO_LOGIN="no logged-in broker account"

usage() {
  echo "usage: $(basename "$0") broker|gateway|secrets|check [--json]|monitor|monitor-install|revoke <client>" >&2
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
  local binary
  SCRATCH=$(mktemp -d)
  DEVELOP_DIR=$SCRATCH/develop
  ACCOUNTS_FILE=$SCRATCH/accounts.json
  mkdir "$DEVELOP_DIR"
  export_develop "$DEVELOP_DIR" || exit 1
  binary=$("$THESEUS" install-omp --policy "$DEVELOP_DIR/.github/oracle-reviewers.yml") \
    || fail "cannot install the omp build pinned in origin/develop:.github/oracle-reviewers.yml"
  [ -n "$binary" ] && [ -x "$binary" ] || fail "the pinned omp install gave no executable"
  OMP=$binary
  PINNED_SHA=$(file_sha256 "$OMP")
  PINNED_VERSION=$(binary_version "$OMP")
  [ -n "$PINNED_VERSION" ] || fail "the pinned omp at $OMP reports no version"
  "$OMP" auth-gateway serve --help 2>&1 | grep -q -e '--clients' \
    || fail "the pinned omp $PINNED_VERSION has no auth-gateway serve --clients, which scopes each reviewer client"
  echo "omp pin: $PINNED_VERSION sha256=$PINNED_SHA"
}

cleanup() {
  [ -z "$SCRATCH" ] || rm -rf "$SCRATCH"
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
EOF
  if [ -n "${PLIST_INTERVAL:-}" ]; then
    cat <<EOF
  <key>StartInterval</key>
  <integer>$PLIST_INTERVAL</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$(xml_escape "$PATH")</string>
  </dict>
EOF
  else
    cat <<EOF
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
EOF
  fi
  cat <<EOF
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

denied_providers() {
  [ -e "$DENYLIST_FILE" ] || return 0
  [ -f "$DENYLIST_FILE" ] && [ ! -L "$DENYLIST_FILE" ] && [ -O "$DENYLIST_FILE" ] \
    || fail "$DENYLIST_FILE must be a regular file you own"
  local mode
  mode=$(stat -c %a "$DENYLIST_FILE" 2>/dev/null || stat -f %Lp "$DENYLIST_FILE")
  [ $(( 8#$mode & 077 )) -eq 0 ] || fail "$DENYLIST_FILE is mode $mode; run: chmod 600 $DENYLIST_FILE"
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    line=$(printf '%s' "${line%%#*}" | tr -d '[:space:]')
    [ -n "$line" ] || continue
    [[ $line =~ ^[a-z0-9][a-z0-9-]*$ ]] || fail "$DENYLIST_FILE names '$line'; list one lowercase provider id per line"
    echo "$line"
  done < "$DENYLIST_FILE"
}

load_denylist() {
  local list provider
  list=$(denied_providers) || exit 1
  EXCLUDE_ARGS=()
  while read -r provider; do
    [ -n "$provider" ] || continue
    EXCLUDE_ARGS+=(--exclude-provider "$provider")
  done <<< "$list"
  return 0
}

redact() {
  sed -e 's|https\{0,1\}://[^[:space:]]*|<gateway-url>|g' -e "s|$HOME|~|g"
}

fetch_accounts() {
  broker_omp usage accounts --json > "$ACCOUNTS_FILE" 2>/dev/null \
    || fail "the broker did not list its accounts; see $LOG_DIR/auth-broker.log"
  jq -e '.accounts | type == "array"' "$ACCOUNTS_FILE" >/dev/null 2>&1 \
    || fail "the broker did not list its accounts; see $LOG_DIR/auth-broker.log"
}

provider_report() {
  "$THESEUS" gateway-providers --omp-config "$DEVELOP_DIR/.github/oracle-omp-config.yml" \
    --accounts "$ACCOUNTS_FILE" ${EXCLUDE_ARGS[@]+"${EXCLUDE_ARGS[@]}"} --json "$@"
}

note_degraded() {
  echo "degraded: $*"
  DEGRADED_COUNT=$((DEGRADED_COUNT + 1))
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
  [[ $1 =~ ^review-[a-z0-9]+(-[a-z0-9]+)*$ ]]
}

client_token_files() {
  local file
  echo "review-ci $CLIENT_TOKEN_FILE"
  for file in "$GATEWAY_DIR"/*-client.token; do
    [ -e "$file" ] || continue
    echo "review-$(basename "$file" -client.token) $file"
  done
}

require_valid_token_files() {
  local name file
  [ ! -e "$GATEWAY_DIR/ci-client.token" ] \
    || fail "$GATEWAY_DIR/ci-client.token would be a second review-ci; review-ci's token is $CLIENT_TOKEN_FILE, so remove or rename it"
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
  local names expected differing name token_file recorded
  names=$(jq -r 'keys[]' "$CLIENTS_FILE" | sort) || fail "$CLIENTS_FILE is not readable JSON"
  expected=$(client_token_files | cut -d' ' -f1 | sort)
  [ "$names" = "$expected" ] || fail "$CLIENTS_FILE has clients [$(echo "$names" | joined)] but the token files are for [$(echo "$expected" | joined)]; run '$(basename "$0") gateway'"
  differing=$(jq -r '."review-ci".models as $ci | to_entries[] | select(.value.models != $ci) | .key' "$CLIENTS_FILE")
  [ -z "$differing" ] || fail "$CLIENTS_FILE gives [$(echo "$differing" | joined)] models that differ from review-ci's"
  while read -r name token_file; do
    recorded=$(jq -r --arg name "$name" '.[$name].tokenSha256 // empty' "$CLIENTS_FILE")
    [ "$recorded" = "$(file_sha256 "$token_file")" ] \
      || fail "$CLIENTS_FILE holds a token hash for $name that does not match $token_file; run '$(basename "$0") gateway'"
  done < <(client_token_files)
}

check_gateway_scope() {
  local base=$1 expected health models usage name token_file signature ci_signature outside
  check_clients_file
  expected=$(jq -r '."review-ci".models | length' "$CLIENTS_FILE")
  health=$(gateway_code "$base/healthz")
  [ "$health" = 200 ] || fail "$base/healthz answered '$health'; is the gateway running and published?"
  ci_signature=$(gateway_curl "$CLIENT_TOKEN_FILE" "$base/v1/models" | model_signature) \
    || fail "$base/v1/models did not answer"
  models=$(echo "$ci_signature" | jq 'length')
  usage=$(gateway_code "$base/v1/usage")
  echo "$base: healthz=$health models=$models/$expected usage=$usage"
  outside=$(jq -r --argjson served "$ci_signature" '."review-ci".models as $granted | $served - $granted | join(" ")' "$CLIENTS_FILE")
  [ -z "$outside" ] || fail "$base lists models the clients file does not grant: $outside"
  [ "$models" -ge "$expected" ] || note_degraded "$base lists $models of the $expected granted models"
  [ "$usage" = 403 ] || fail "$base answered /v1/usage with $usage; expected 403"
  while read -r name token_file; do
    [ "$name" != review-ci ] || continue
    signature=$(gateway_curl "$token_file" "$base/v1/models" | model_signature) \
      || fail "$base/v1/models did not answer for $name"
    models=$(echo "$signature" | jq 'length')
    usage=$(gateway_curl "$token_file" "$base/v1/usage" -o /dev/null -w '%{http_code}' || true)
    echo "$base: $name models=$models/$expected usage=$usage"
    [ "$signature" = "$ci_signature" ] || fail "$base lists different models for $name than for review-ci"
    [ "$usage" = 403 ] || fail "$base answered /v1/usage for $name with $usage; expected 403"
  done < <(client_token_files)
}

export_develop() {
  local destination=$1 commit scratch
  git -C "$REPO_ROOT" fetch --quiet origin +refs/heads/develop:refs/remotes/origin/develop \
    || fail "cannot fetch origin/develop in $REPO_ROOT"
  commit=$(git -C "$REPO_ROOT" rev-parse --verify "origin/develop^{commit}") \
    || fail "cannot resolve origin/develop in $REPO_ROOT"
  git -C "$REPO_ROOT" archive "$commit" .github | tar -x -C "$destination" \
    || fail "cannot export origin/develop from $REPO_ROOT"
  scratch=$(mktemp -d)
  git -C "$REPO_ROOT" show "$commit:_project/scripts/theseus_bootstrap.py" > "$scratch/bootstrap.py" \
    || { rm -rf "$scratch"; fail "origin/develop has no Theseus first stage"; }
  THESEUS=$(THESEUS_OMP_CACHE=${THESEUS_OMP_CACHE:-$HOME/.cache/benchbox/omp} \
    python3 -I -S "$scratch/bootstrap.py" install --repo "$REPO_ROOT" --commit "$commit") \
    || { rm -rf "$scratch"; fail "the verified Theseus install of origin/develop was refused"; }
  rm -rf "$scratch"
  [ -x "$THESEUS" ] || fail "the verified Theseus install gave no executable"
  export THESEUS_OMP_CACHE=${THESEUS_OMP_CACHE:-$HOME/.cache/benchbox/omp}
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
  load_denylist
  fetch_accounts
  local report missing provider
  report=$(provider_report) || true
  missing=$(printf '%s' "$report" | jq -r --arg reason "$PROVIDER_REASON_NO_LOGIN" \
    '.providers // {} | to_entries[] | select(.value.reason == $reason) | .key') || missing=""
  if [ -n "$missing" ]; then
    echo "the gateway starts with the providers that are logged in; to add a missing one, log in, then run '$(basename "$0") gateway':"
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
  load_denylist
  fetch_accounts
  require_valid_token_files
  mkdir -p "$GATEWAY_DIR/home"
  chmod 700 "$GATEWAY_DIR" "$GATEWAY_DIR/home"
  if [ ! -s "$CLIENT_TOKEN_FILE" ]; then
    openssl rand -base64 36 | tr -d '\n' > "$CLIENT_TOKEN_FILE"
  fi
  chmod 600 "$CLIENT_TOKEN_FILE"
  local named_clients=() name token_file
  while read -r name token_file; do
    [ "$name" != review-ci ] || continue
    chmod 600 "$token_file"
    named_clients+=(--client "$name=$token_file")
  done < <(client_token_files)
  "$THESEUS" gateway-clients \
    --token-file "$CLIENT_TOKEN_FILE" ${named_clients[@]+"${named_clients[@]}"} \
    --accounts "$ACCOUNTS_FILE" --omp-config "$DEVELOP_DIR/.github/oracle-omp-config.yml" --out "$CLIENTS_FILE" \
    ${EXCLUDE_ARGS[@]+"${EXCLUDE_ARGS[@]}"} --report "$SCRATCH/clients-report.json"
  echo "gateway: granted $(jq -r '.granted | join(" ")' "$SCRATCH/clients-report.json")"
  local staged changed=no config
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
  config=$(mktemp)
  render_gateway_config > "$config"
  mkdir -p "$(dirname "$GATEWAY_CONFIG")"
  if cmp -s "$config" "$GATEWAY_CONFIG"; then
    rm -f "$config"
  else
    chmod 600 "$config"
    mv "$config" "$GATEWAY_CONFIG"
    changed=yes
  fi
  install_agent "$GATEWAY_LABEL" "$LOG_DIR/auth-gateway.log" "$changed" /bin/sh "$RUN_FILE"
  wait_for gateway_healthy || fail "the gateway did not answer /healthz; see $LOG_DIR/auth-gateway.log"
  check_gateway_scope "http://127.0.0.1:$GATEWAY_PORT"
  [ "$DEGRADED_COUNT" -eq 0 ] || echo "gateway: degraded; '$(basename "$0") check' names each provider"
  tailscale serve --bg "--https=$GATEWAY_PORT" "http://127.0.0.1:$GATEWAY_PORT"
  local url
  url=$(tailnet_url)
  echo "gateway: published at $url"
}

cmd_revoke() {
  local name=${1:-} token_file staged
  [ "$name" != review-ci ] \
    || fail "review-ci is the review client; rotate its token with '$(basename "$0") gateway' instead"
  valid_client_name "$name" \
    || fail "name a client like review-local; usage: $(basename "$0") revoke <client>"
  token_file=$GATEWAY_DIR/${name#review-}-client.token
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

health_collect() {
  trap cleanup EXIT
  DEGRADED_COUNT=0
  require_omp
  broker_ok || fail "the broker is not running"
  echo "broker: ok on 127.0.0.1:$BROKER_PORT"
  check_builds
  load_denylist
  fetch_accounts
  require_valid_token_files
  local report rc=0 problem name url
  report=$(provider_report --clients "$CLIENTS_FILE" --served-url "http://127.0.0.1:$GATEWAY_PORT" \
    --token-file "$CLIENT_TOKEN_FILE") || rc=$?
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 3 ]; then
    problem=$(printf '%s' "$report" | jq -r '.served_error // empty' 2>/dev/null) || problem=""
    fail "no provider is granted and served${problem:+: $problem}"
  fi
  printf '%s' "$report" | jq -r '.providers | to_entries[] | "provider: \(.key) granted=\(.value.granted) served=\(.value.served)"'
  while read -r name problem; do
    note_degraded "$name: $problem"
  done < <(printf '%s' "$report" | jq -r \
    '.providers | to_entries[] | select(.value.reason != "" and .value.excluded_by_owner == false) | "\(.key) \(.value.reason)"')
  while read -r name; do
    echo "notice: $name removed by owner; develop still names $name"
  done < <(printf '%s' "$report" | jq -r '.providers | to_entries[] | select(.value.excluded_by_owner) | .key')
  check_gateway_scope "http://127.0.0.1:$GATEWAY_PORT"
  url=$(tailnet_url)
  check_gateway_scope "$url"
  check_usage_probes
  [ "$rc" -ne 3 ] || [ "$DEGRADED_COUNT" -gt 0 ] || note_degraded "gateway-providers reports a degraded state with no provider reason"
  [ "$DEGRADED_COUNT" -eq 0 ] || return 3
}

check_usage_probes() {
  local output
  mkdir -p "$GATEWAY_DIR/home"
  if output=$(HOME="$GATEWAY_DIR/home" OMP_AUTH_BROKER_URL="http://127.0.0.1:$BROKER_PORT" \
    OMP_AUTH_BROKER_TOKEN=$(cat "$BROKER_TOKEN_FILE") "$OMP" auth-gateway check 2>&1); then
    echo "usage probes: ok"
  else
    note_degraded "usage probe: omp auth-gateway check failed; run it on the host for the provider and cause"
  fi
}

cmd_check() {
  local json=no rc=0 output name secrets
  case ${1:-} in
    --json) json=yes ;;
    "") ;;
    *) usage ;;
  esac
  if [ "$json" = yes ]; then
    output=$(health_collect 2>&1) || rc=$?
    health_json "$output" "$rc"
    exit "$rc"
  fi
  health_collect || rc=$?
  [ "$rc" -eq 0 ] || [ "$rc" -eq 3 ] || exit "$rc"
  secrets=$(gh secret list --env oracle --repo "$REPO_SLUG" --json name --jq '.[].name') \
    || fail "gh could not list the oracle environment secrets; run 'gh auth login'"
  for name in ORACLE_GATEWAY_TOKEN ORACLE_GATEWAY_URL; do
    printf '%s\n' "$secrets" | grep -x "$name" >/dev/null || fail "the oracle environment has no $name secret"
  done
  echo "secrets: ORACLE_GATEWAY_TOKEN and ORACLE_GATEWAY_URL are set"
  exit "$rc"
}

health_state() {
  case $1 in
    0) echo healthy ;;
    3) echo degraded ;;
    *) echo broken ;;
  esac
}

health_lines() {
  printf '%s\n' "$1" | grep -E "^($2):" || true
}

health_json() {
  local output=$1 rc=$2
  jq -n --arg state "$(health_state "$rc")" \
    --arg degraded "$(health_lines "$output" 'degraded|error')" \
    --arg notices "$(health_lines "$output" notice)" \
    '{state: $state, degraded: ($degraded | split("\n") | map(select(. != ""))), notices: ($notices | split("\n") | map(select(. != "")))}'
}

notify() {
  local message
  message=$(printf '%s' "$1" | tr '\n' ' ')
  osascript -e 'on run argv' -e 'display notification (item 1 of argv) with title "Review gateway"' -e 'end run' \
    "$message" >/dev/null 2>&1 || true
}

open_health_issue() {
  gh issue list --repo "$REPO_SLUG" --label "$HEALTH_LABEL" --state open --json number --jq '.[0].number // empty'
}

publish_health() {
  local state=$1 body=$2 number title
  number=$(open_health_issue) || fail "gh could not list the $HEALTH_LABEL issues; run 'gh auth login'"
  if [ "$state" = healthy ]; then
    [ -z "$number" ] || gh issue close "$number" --repo "$REPO_SLUG" --comment "The review gateway is healthy again."
    return 0
  fi
  title="Review gateway $state"
  if [ -n "$number" ]; then
    gh issue edit "$number" --repo "$REPO_SLUG" --title "$title" --body "$body"
  else
    gh label create "$HEALTH_LABEL" --repo "$REPO_SLUG" --color B60205 \
      --description "Review gateway or provider health" --force >/dev/null
    gh issue create --repo "$REPO_SLUG" --label "$HEALTH_LABEL" --title "$title" --body "$body" >/dev/null
  fi
}

cmd_monitor() {
  local output rc=0 state signature previous summary
  output=$(health_collect 2>&1) || rc=$?
  state=$(health_state "$rc")
  summary=$(health_lines "$output" 'degraded|error')
  signature=$(printf '%s\n%s\n' "$state" "$summary" | grep -v '^$' | sort)
  previous=$(cat "$STATE_FILE" 2>/dev/null || echo healthy)
  if [ "$signature" = "$previous" ]; then
    echo "monitor: $state, unchanged"
    return 0
  fi
  if [ "$state" = healthy ]; then
    notify "recovered"
  else
    notify "$state: $(printf '%s' "$summary" | paste -sd ';' -)"
  fi
  publish_health "$state" "$(printf 'State: %s\n\n%s\n\nRun "check" on the gateway host for the full report.' \
    "$state" "$(printf '%s\n' "$output" | grep -E '^(degraded|error|notice):' | redact | sed 's/^/- /')")"
  printf '%s\n' "$signature" > "$STATE_FILE"
  echo "monitor: $state"
}

cmd_monitor_install() {
  local snapshot=$GATEWAY_DIR/monitor-host.sh staged
  mkdir -p "$GATEWAY_DIR"
  git -C "$REPO_ROOT" fetch --quiet origin +refs/heads/develop:refs/remotes/origin/develop \
    || fail "cannot fetch origin/develop in $REPO_ROOT"
  staged=$(mktemp "$GATEWAY_DIR/.monitor-host.XXXXXX")
  git -C "$REPO_ROOT" show "origin/develop:_project/scripts/oracle_gateway_host.sh" > "$staged" \
    || { rm -f "$staged"; fail "origin/develop has no gateway host script"; }
  chmod 700 "$staged"
  mv "$staged" "$snapshot"
  PLIST_INTERVAL=$MONITOR_INTERVAL install_agent "$MONITOR_LABEL" "$LOG_DIR/review-gateway-monitor.log" yes \
    /usr/bin/env "GATEWAY_HOST_REPO=$REPO_ROOT" /bin/bash "$snapshot" monitor
}

render_gateway_config() {
  local list provider
  list=$(denied_providers)
  if [ -z "$list" ]; then
    echo "disabledProviders: []"
    return 0
  fi
  echo "disabledProviders:"
  while read -r provider; do
    echo "  - $provider"
  done <<< "$list"
}

main() {
  [ $# -ge 1 ] || usage
  trap cleanup EXIT
  case $1 in
    broker | gateway | secrets | monitor | monitor-install) [ $# -eq 1 ] || usage ;;
    check) [ $# -le 2 ] || usage ;;
    revoke) [ $# -eq 2 ] || usage ;;
  esac
  case $1 in
    broker) cmd_broker ;;
    gateway) cmd_gateway ;;
    secrets) cmd_secrets ;;
    check) cmd_check "${2:-}" ;;
    monitor) cmd_monitor ;;
    monitor-install) cmd_monitor_install ;;
    revoke) cmd_revoke "$2" ;;
    *) usage ;;
  esac
}

(return 0 2>/dev/null) || main "$@"
