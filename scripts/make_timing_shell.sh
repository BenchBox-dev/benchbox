#!/bin/sh
case "${BENCHBOX_MAKE_TIMINGS:-}" in
	0) exec /bin/sh "$@" ;;
	off) exec /bin/sh "$@" ;;
	false) exec /bin/sh "$@" ;;
	no) exec /bin/sh "$@" ;;
esac
flags_head=${MAKEFLAGS%% *}
case "$flags_head" in
	-*|*[!a-zA-Z]*|"") ;;
	*[nqt]*) exec /bin/sh "$@" ;;
esac
if command -v python3 >/dev/null 2>&1; then
	exec python3 "$(dirname "$0")/make_timing.py" run -- /bin/sh "$@"
fi
exec /bin/sh "$@"
