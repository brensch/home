#!/bin/bash
# Manage the Sony Bravia over ADB from the home server. No host install: adb runs
# in a throwaway container, using the key in ~/.config/adb (already approved on
# the TV). Needs Developer options -> USB debugging switched on on the TV.
#
#   tv.sh status            packages from packages.txt that are enabled again
#   tv.sh apply             disable everything in packages.txt (after a firmware
#                           update or factory reset; safe to re-run)
#   tv.sh enable-updates    turn the [updates] group back on (see README.md)
#   tv.sh disable-updates   turn it off again
#   tv.sh adb <args...>     any adb command, e.g.  tv.sh adb shell pm enable <pkg>
set -euo pipefail
TV=192.168.1.14:5555
DIR="$(cd "$(dirname "$0")" && pwd)"

adb() {
  docker run --rm --network host -v "$HOME/.config/adb:/root/.android" alpine sh -c \
    "apk add -q android-tools >/dev/null && adb connect $TV >/dev/null 2>&1; sleep 1; adb -s $TV $*" 2>&1 | grep -v daemon
}

# packages [group]: package names from packages.txt, optionally one group only
packages() {
  awk -v want="${1:-}" '
    /^\[/ { group = substr($1, 2, length($1) - 2); next }
    /^[a-z]/ { if (want == "" || want == group) print $1 }' "$DIR/packages.txt"
}

# set_state on|off pkgs...: switch packages on or off, in one adb session
set_state() {
  local cmd; [ "$1" = on ] && cmd="pm enable" || cmd="pm disable-user --user 0"; shift
  local script=""
  for p in "$@"; do script+="$cmd $p; "; done
  adb shell "'$script'"
}

case "${1:-}" in
  status)
    enabled=$(adb shell pm list packages -e | sed 's/package://' | tr -d '\r')
    for p in $(packages); do grep -qx "$p" <<<"$enabled" && echo "enabled again: $p"; done
    echo "checked $(packages | wc -l) packages" ;;
  apply)           set_state off $(packages) | grep -v 'new state: disabled-user' || true
                   echo "applied: $(packages | wc -l) packages disabled" ;;
  enable-updates)  set_state on $(packages updates) ;;
  disable-updates) set_state off $(packages updates) ;;
  adb)             shift; adb "$@" ;;
  *)               sed -n '2,13p' "$0"; exit 1 ;;
esac
