# Backups and restore

Backchannel uses SQLite. Backups are gzipped consistent snapshots taken
with SQLite's online `.backup` command (lock-free for readers).

## Daily backup (cron)

On the Hetzner host:

```bash
sudo install -m 0755 scripts/backup.sh   /opt/backchannel/scripts/
sudo install -m 0755 scripts/restore.sh  /opt/backchannel/scripts/

sudo crontab -e
# at 02:10 UTC every day:
10 2 * * * /opt/backchannel/scripts/backup.sh \
              --db /var/lib/docker/volumes/backchannel_backchannel_data/_data/backchannel.db \
              --out /var/backups/backchannel
```

The script:
- Snapshots via `sqlite3 ... .backup` (consistent under writes).
- Gzips with `-9`.
- Keeps the latest 30 backups (override with `BACKCHANNEL_BACKUP_KEEP`).

Sample output:
```
ok backup=/var/backups/backchannel/backchannel-20260513T021000Z.sqlite.gz bytes=412331
```

## Off-box copy

The on-box backup is only safe against application bugs, not against
hardware loss. Ship the file off-box on a schedule:

```bash
# rclone — works with S3, B2, GCS, etc.
10 3 * * * rclone copy /var/backups/backchannel/ remote:backchannel-prod/
```

Or simpler:

```bash
10 3 * * * rsync -a --delete /var/backups/backchannel/ user@offbox:backups/backchannel/
```

## Restore drill (do this once)

A backup that has never been restored is not a backup. Drill on a
non-production box:

```bash
sudo /opt/backchannel/scripts/restore.sh \
   --from /var/backups/backchannel/backchannel-20260513T021000Z.sqlite.gz \
   --to   /var/lib/docker/volumes/backchannel_backchannel_data/_data/backchannel.db \
   --compose-file /opt/backchannel/docker-compose.self-host.yml \
   --force
```

What it does:
1. Stops the app + worker containers.
2. Decompresses the snapshot to a temp file.
3. Runs `PRAGMA integrity_check;` against the snapshot. Aborts on corruption.
4. Moves the *current* db to `<path>.pre-restore` (kept as a safety net).
5. Moves the snapshot into place.
6. Restarts the containers.

Verify after:
```bash
curl -s http://localhost:8080/health
curl -s -X POST http://localhost:8080/v1/keys -H 'Content-Type: application/json' -d '{"agent_label":"post-restore-smoke"}'
```

## Caveats

- WAL/SHM sidecar files (`backchannel.db-wal`, `backchannel.db-shm`) are
  not part of the backup. `.backup` consolidates WAL contents into the
  snapshot, so this is correct.
- Backups capture *live* tables only. Audit tables grow over time; they
  are also captured.
- `.backup` is online but consumes I/O proportional to DB size. Run it
  during low-traffic hours.
- The cleanup worker continues to run during a backup; that's safe.
