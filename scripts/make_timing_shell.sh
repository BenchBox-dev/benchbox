#!/bin/sh
case "${BENCHBOX_MAKE_TIMINGS:-}" in
	0) exec /bin/sh "$@" ;;
	off) exec /bin/sh "$@" ;;
	false) exec /bin/sh "$@" ;;
	no) exec /bin/sh "$@" ;;
esac
dry_run=
word_index=0
set -f
for flag_word in $MAKEFLAGS; do
	case "$flag_word" in --) break ;; -*) ;; *=*) break ;; esac
	word_index=$((word_index + 1))
	case "$flag_word" in
		--dry-run|--just-print|--recon) dry_run=1 ;;
		--*) ;;
		-*[!a-zA-Z]*) ;;
		-*[nqt]*) dry_run=1 ;;
		*[!a-zA-Z]*|"") ;;
		*[nqt]*) if [ "$word_index" -eq 1 ]; then dry_run=1; fi ;;
	esac
done
set +f
if [ -n "$dry_run" ]; then exec /bin/sh "$@"; fi
if command -v python3 >/dev/null 2>&1; then
	exec python3 "$(dirname "$0")/make_timing.py" run -- /bin/sh "$@"
fi
exec /bin/sh "$@"
