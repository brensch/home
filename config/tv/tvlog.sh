#!/bin/bash
# Read the TV recorder's files (the tvlog container, config/tv/recorder.sh).
#
#   tvlog.sh [seconds] [what]   the last <seconds> (default 60) of:
#     state    playback state, video frames shown, memory, busiest threads (every 5 s)
#     video    logcat lines from the video/audio pipeline (YouTube's player, decoders, SurfaceFlinger)
#     logcat   all of logcat
#     all      state then logcat (default)
#   tvlog.sh frozen [hours]     when the picture stopped while the app said it was playing (default 1 h)
set -euo pipefail
DIR="$(cd "$(dirname "$0")/../.." && pwd)/data/tvlog"

# lines [kind] [since]: that kind's lines since an epoch, time shown as local clock time
lines() {
  ls "$DIR"/"$1"-*.log >/dev/null 2>&1 || { echo "no $1 files in $DIR"; return; }
  cat $(ls "$DIR"/"$1"-*.log | tail -2) | gawk -v since="$2" 'int($1) >= since {
    t = $1; printf "%s%s%s\n", strftime("%H:%M:%S", int(t)), (t ~ /\./ ? substr(t, index(t, ".")) : ""), substr($0, length(t) + 1) }'
}

VIDEO='starboard|MtkOmxVdec|VCodecDrv|MediaCodec|ACodec|CCodec|OMXNode|Codec2|BufferQueue|SurfaceView|hwcomposer|surfaceflinger|GRM|ExoPlayer|AudioTrack|AudioFlinger|am_crash|am_anr|lowmemorykiller|lmkd'

if [ "${1:-}" = frozen ]; then
  since=$(( $(date +%s) - ${2:-1} * 3600 ))
  lines state "$since" | gawk '
    /PlaybackState \{state=3/ { playing = 1 }
    / ----$/ { playing = 0 }
    /^.{8} video / && playing && /frames shown in last 5s: 0,/ { print }'
  exit
fi

secs=${1:-60}; what=${2:-all}
since=$(( $(date +%s) - secs ))
case "$what" in
  state)  lines state "$since" ;;
  logcat) lines logcat "$since" ;;
  video)  lines logcat "$since" | grep -E "$VIDEO" ;;
  all)    lines state "$since"; echo; lines logcat "$since" ;;
  *)      sed -n '2,10p' "$0"; exit 1 ;;
esac
