#!/bin/sh
# Off-box backup: copies what can't be rebuilt to Google Drive (snek3-backup/ in brensch's Drive), once at start
# and then daily at $BACKUP_AT. rclone's login is in ~/.config/rclone/rclone.conf (scope drive.file: it can only
# see files it made).
#
#   current/  a mirror of the sources as of the last run
#   old/DATE/ whatever that day's run changed or deleted in current/, kept $KEEP_DAYS days
#
# Each run writes status.json for Home Assistant (sensor.offsite_backup), which alerts if it goes stale.
set -u
DEST=gdrive:snek3-backup
STATUS=/status/status.json
R="rclone --config /config/rclone/rclone.conf --log-level NOTICE"

backup() {
	day=$(date +%F)
	stage=/tmp/stage
	rm -rf "$stage" && mkdir -p "$stage"
	failed=""

	# Home Assistant's own daily backups (config + database, encrypted with its backup key).
	$R sync /src/homeassistant-backups "$DEST/current/homeassistant-backups" \
		--backup-dir "$DEST/old/$day/homeassistant-backups" || failed="$failed homeassistant"

	# Plants: photos, the agent's run logs, and a consistent snapshot of its database. Not home/ (the agent's
	# Claude login), agent-cwd/ (empty scratch) or films/: those are rewritten every hour as they grow, so each
	# night's copy would push yesterday's whole film into old/, and `plants films` rebuilds them from the photos.
	if sqlite3 /src/plants/plants.db ".backup $stage/plants.db"; then
		$R sync /src/plants "$DEST/current/plants" --exclude "/home/**" --exclude "/agent-cwd/**" --exclude "/films/**" \
			--exclude "/plants.db*" --backup-dir "$DEST/old/$day/plants" || failed="$failed plants"
		$R copyto "$stage/plants.db" "$DEST/current/plants/plants.db" \
			--backup-dir "$DEST/old/$day/plants" || failed="$failed plants-db"
	else
		failed="$failed plants-db"
	fi

	# Frigate: the trained faces and the config (recordings are not backed up; the models re-download).
	$R sync /src/frigate-faces "$DEST/current/frigate/faces" --exclude "/train/**" \
		--backup-dir "$DEST/old/$day/frigate/faces" || failed="$failed frigate-faces"
	$R copyto /src/frigate-config/config.yml "$DEST/current/frigate/config.yml" \
		--backup-dir "$DEST/old/$day/frigate" || failed="$failed frigate-config"

	# Forget changed/deleted files after $KEEP_DAYS days (old/ only exists once something has changed).
	$R mkdir "$DEST/old" && $R delete "$DEST/old" --min-age "${KEEP_DAYS}d" && $R rmdirs "$DEST/old" --leave-root

	size=$($R size "$DEST/current" --json 2>/dev/null | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')
	now=$(date -Iseconds)
	last_ok=$(sed -n 's/.*"last_ok": *"\([^"]*\)".*/\1/p' "$STATUS" 2>/dev/null)
	if [ -z "$failed" ]; then
		last_ok=$now
		echo "backup ok, ${size:-?} bytes in current/"
	else
		echo "backup FAILED:$failed"
	fi
	printf '{"last_run": "%s", "last_ok": "%s", "failed": "%s", "bytes": %s}\n' \
		"$now" "${last_ok:-}" "${failed# }" "${size:-0}" > "$STATUS.tmp" && mv "$STATUS.tmp" "$STATUS"
}

while true; do
	backup
	# Sleep until the next $BACKUP_AT (after Home Assistant's own ~05:00 backup).
	now=$(date +%s)
	next=$(date -d "$(date +%F) $BACKUP_AT" +%s)
	[ "$next" -le "$now" ] && next=$((next + 86400))
	sleep $((next - now))
done
