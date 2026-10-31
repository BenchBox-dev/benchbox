#!/usr/bin/env bash
set -euo pipefail

machine=${CI_LINUX_MACHINE:-benchbox-agent}

if [ "${BENCHBOX_LINUX_INNER:-}" = "1" ]; then
  cd "$1"
  shift
  export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.cache/benchbox-linux-venv}"
  uv sync --frozen -q
  if [ ! -e /usr/share/zoneinfo/UTC ]; then
    echo "WARNING: this machine has no system tzdata; tests using zoneinfo fail to collect. Rebuild the agent image (docker/benchbox-agent) and recreate the machine." >&2
  fi
  echo "linux pytest in $(pwd) at $(git rev-parse --short HEAD)"
  status=0
  uv run --frozen -- python -m pytest -q -n 0 --tb=short "$@" || status=$?
  if [ "$status" -eq 5 ]; then
    echo "Mapped tests are all deselected by the default markers."
    status=0
  fi
  exit "$status"
fi

unverified() {
  echo "WARNING: Linux not verified -- $1" >&2
  echo "  The changed tests touch platform-branching code or platform skips; CI runs them on Linux." >&2
  if [ -n "${2:-}" ]; then
    printf '%s\n' "$2" >&2
  fi
  exit 0
}

setup_steps="  One-time setup (Apple silicon, macOS 26+):
    brew install container
    container system start
    container build --arch arm64 --tag local/benchbox-agent docker/benchbox-agent
    container machine create local/benchbox-agent --name $machine --home-mount rw --cpus 4 --memory 6G"

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "usage: linux_preflight_pytest.sh <test-list-file>" >&2
  exit 2
fi
test_list=$1

if [ "$(uname -s)" = "Linux" ]; then
  echo "Host is Linux; the changed-file tests above already ran on Linux."
  exit 0
fi
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  unverified "host is $(uname -s)/$(uname -m), not Apple silicon macOS; the Linux container run is unavailable."
fi
if ! command -v container >/dev/null 2>&1; then
  unverified "Apple 'container' is not installed." "$setup_steps"
fi
if ! container machine list 2>/dev/null | awk 'NR>1{print $1}' | grep -Fqx "$machine"; then
  unverified "container machine '$machine' not found." "$setup_steps"
fi

top=$(git rev-parse --show-toplevel)
tests=()
while IFS= read -r line; do
  [ -n "$line" ] && tests+=("$line")
done <"$test_list"

echo "==> Linux container run of ${#tests[@]} changed-file test target(s) in machine '$machine'"
cd "$top"
exec uv run -- python -m benchbox.utils.host_lock run --phase linux-preflight --gate linux-preflight \
  --admit --need-gib "${CI_LINUX_NEED_GIB:-2}" --guard -- \
  bash "$top/scripts/linux_machine_run.sh" "$machine" \
  -e BENCHBOX_LINUX_INNER=1 -e BENCHBOX_TEST_LOCK_DIR=/tmp/benchbox-lock \
  -e BENCHBOX_HEAVY_ADMIT_MIN_GIB=1.5 -e BENCHBOX_HEAVY_MIN_FREE_DISK_GIB=2 -- \
  bash "$top/scripts/linux_preflight_pytest.sh" "$top" "${tests[@]}"
