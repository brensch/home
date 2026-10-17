#!/bin/sh
# tvlog container: a rolling record of the TV, so "what happened in the last minute" can be read after
# the fact instead of chased live. Writes hourly files to /out, keeps KEEP_HOURS of them:
#   logcat-YYYYmmdd-HH.log  the TV's whole logcat (main, system, crash, events), stamped with epoch seconds
#   state-YYYYmmdd-HH.log   every INTERVAL seconds: memory, playback state, video frames shown, busiest threads
# Read with config/tv/tvlog.sh. Needs USB debugging on the TV; the key in ~/.config/adb is approved there.
# The always-open connection also keeps that approval from lapsing (Android drops it after 7 days unused).
TV=${TV:-192.168.1.14:5555}
INTERVAL=${INTERVAL:-5}
KEEP_HOURS=${KEEP_HOURS:-6}

connect() {
  until adb connect "$TV" 2>&1 | grep -q connected && adb -s "$TV" shell true 2>/dev/null; do sleep 5; done
}

# epoch-stamped lines on stdin -> the hourly file for each line's own time
hourly() {
  gawk -v dir=/out -v kind="$1" '{
    sub(/^ +/, ""); if ($1 !~ /^[0-9]+(\.[0-9]+)?$/) next
    f = dir "/" kind "-" strftime("%Y%m%d-%H", int($1)) ".log"
    if (f != cur) { if (cur) close(cur); cur = f }
    print >> f; fflush(f)
  }'
}

logcat_loop() {
  while true; do
    connect
    # Carry on from the last line recorded (after a TV restart that's the whole boot); first run: from now.
    last=$(cat "$(ls /out/logcat-*.log 2>/dev/null | tail -1)" 2>/dev/null | tail -1 | cut -d' ' -f1)
    adb -s "$TV" logcat -v threadtime -v epoch -b main,system,crash,events -T "${last:-1}" 2>&1 | hourly logcat
    sleep 5
  done
}

# One sample. Video frames: the YouTube (or any app's) video layer's last presented frames from
# SurfaceFlinger, against the TV's monotonic clock; "last frame Xs ago" growing while playing = frozen picture.
SAMPLE='
echo "uptime $(cut -d" " -f1 /proc/uptime)  $(grep -E "MemAvailable|SwapFree" /proc/meminfo | tr -s " " | tr "\n" " ")"
dumpsys activity activities | grep mResumedActivity | sed "s/^ */foreground: /"
dumpsys media_session | grep -E "^ +package=|state=PlaybackState" | sed "s/^ */  /"
up=$(cut -d" " -f1 /proc/uptime)
dumpsys SurfaceFlinger --list | grep "^SurfaceView.*(BLAST)" | while read -r L; do
  dumpsys SurfaceFlinger --latency "$L" | awk -v up="$up" -v name="$L" "NR>1 && \$2>0 && \$2<1e17 { n++; last=\$2; if (\$2/1e9 > up-5) recent++ }
    END { printf \"video %s: frames shown in last 5s: %d, last frame %.1fs ago\n\", name, recent, (n ? up-last/1e9 : -1) }"
done
top -b -n1 -H -m 15 | sed -n "5,21p"
'

state_loop() {
  while true; do
    connect
    while out=$(timeout 20 adb -s "$TV" shell "$SAMPLE" 2>&1); do
      now=$(date +%s)
      echo "$out" | sed "s/^/$now /" | hourly state
      echo "$now ----" | hourly state
      sleep "$INTERVAL"
    done
    echo "$(date +%s) (no connection to the TV)" | hourly state
    sleep 5
  done
}

prune() {
  while true; do
    find /out -name '*.log' -mmin +$((KEEP_HOURS * 60)) -delete
    sleep 600
  done
}

adb start-server >/dev/null 2>&1
prune &
state_loop &
logcat_loop
