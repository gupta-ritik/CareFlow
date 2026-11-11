#!/bin/sh
# Nightly PostgreSQL backup (runs in the `backup` container of docker-compose.prod.yml).
# Writes /backups/careflow-YYYYmmdd-HHMM.sql.gz and deletes backups older than KEEP_DAYS.
# Restore: see docs/deployment.md ("Restore a backup").
set -eu
KEEP_DAYS="${KEEP_DAYS:-14}"
BACKUP_HOUR="${BACKUP_HOUR:-2}"

backup() {
  f="/backups/careflow-$(date +%Y%m%d-%H%M).sql.gz"
  echo "$(date -Iseconds) backup -> $f"
  if pg_dump --no-owner --format=plain | gzip -9 > "$f.tmp"; then
    mv "$f.tmp" "$f"
    find /backups -name 'careflow-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
    echo "$(date -Iseconds) backup ok ($(du -h "$f" | cut -f1))"
  else
    rm -f "$f.tmp"
    echo "$(date -Iseconds) BACKUP FAILED" >&2
  fi
}

backup   # one at start-up, so a fresh install has a backup straight away
while true; do
  now_h=$(date +%H | sed 's/^0//')
  [ -z "$now_h" ] && now_h=0
  # sleep until the next BACKUP_HOUR:00
  hours=$(( (BACKUP_HOUR - now_h + 24) % 24 ))
  [ "$hours" -eq 0 ] && hours=24
  secs=$(( hours * 3600 - $(date +%M | sed 's/^0//;s/^$/0/') * 60 ))
  sleep "$secs"
  backup
done
