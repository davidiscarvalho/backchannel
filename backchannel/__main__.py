from __future__ import annotations

import argparse
import time
from pathlib import Path
from wsgiref.simple_server import make_server

from backchannel.http import create_app
from backchannel.store import BackchannelStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Backchannel service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the Backchannel HTTP server")
    serve_parser.add_argument("--db", default="backchannel.db", help="SQLite database path")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    serve_parser.add_argument("--port", default=8080, type=int, help="Bind port")

    cleanup_parser = subparsers.add_parser("cleanup", help="Archive expired live data, then purge it from the runtime store")
    cleanup_parser.add_argument("--db", default="backchannel.db", help="SQLite database path")

    worker_parser = subparsers.add_parser("worker", help="Run the cleanup worker loop (for container deployments)")
    worker_parser.add_argument("--db", default="backchannel.db", help="SQLite database path")
    worker_parser.add_argument("--interval", default=86400, type=int, help="Seconds between cleanup runs (default: 86400 = 24h)")

    report_parser = subparsers.add_parser("audit-report", help="Inspect recent cleanup runs and archived messages")
    report_parser.add_argument("--db", default="backchannel.db", help="SQLite database path")
    report_parser.add_argument("--limit", default=10, type=int, help="How many rows to show")

    args = parser.parse_args()

    if args.command == "worker":
        store = BackchannelStore(Path(args.db))
        print(f"Backchannel cleanup worker started (interval={args.interval}s)", flush=True)
        while True:
            try:
                summary = store.archive_and_cleanup_expired_records()
                print(
                    " ".join([
                        "cleanup",
                        f"run_id={summary['run_id']}",
                        f"archived_messages={summary['archived_messages']}",
                        f"purged_messages={summary['purged_messages']}",
                        f"archived_invitations={summary['archived_invitations']}",
                        f"purged_invitations={summary['purged_invitations']}",
                    ]),
                    flush=True,
                )
            except Exception as exc:
                print(f"cleanup error: {exc}", flush=True)
            time.sleep(args.interval)

    if args.command == "cleanup":
        store = BackchannelStore(Path(args.db))
        summary = store.archive_and_cleanup_expired_records()
        print(
            " ".join(
                [
                    f"run_id={summary['run_id']}",
                    f"archived_messages={summary['archived_messages']}",
                    f"purged_messages={summary['purged_messages']}",
                    f"archived_invitations={summary['archived_invitations']}",
                    f"purged_invitations={summary['purged_invitations']}",
                ]
            )
        )
        return 0

    if args.command == "audit-report":
        store = BackchannelStore(Path(args.db))
        print("recent_runs:")
        for row in store.list_audit_runs(limit=args.limit):
            print(
                " ".join(
                    [
                        f"id={row['id']}",
                        f"status={row['status']}",
                        f"archived_messages={row['archived_messages']}",
                        f"purged_messages={row['purged_messages']}",
                        f"archived_invitations={row['archived_invitations']}",
                        f"purged_invitations={row['purged_invitations']}",
                    ]
                )
            )
        print("recent_archived_messages:")
        for row in store.list_audit_messages(limit=args.limit):
            print(
                " ".join(
                    [
                        f"message_id={row['live_message_id']}",
                        f"channel_id={row['live_channel_id']}",
                        f"actor={row['actor_name'] or '-'}",
                        f"archived_at={row['archived_at']}",
                    ]
                )
            )
        return 0

    app = create_app(db_path=Path(args.db))
    with make_server(args.host, args.port, app) as server:
        print(f"Backchannel listening on http://{args.host}:{args.port}")
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
