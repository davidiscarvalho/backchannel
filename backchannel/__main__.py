from __future__ import annotations

import argparse
import os
import signal
import threading
import time
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from backchannel.http import create_app
from backchannel.store import BackchannelStore


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """Handle each request on its own thread.

    Stock ``wsgiref`` is single-threaded with a listen backlog of 5, so a
    burst of concurrent connections gets refused and everything else
    serializes. A self-host instance is exposed directly (no proxy in the
    default compose), so we thread the server here. SQLite WAL + the atomic
    claim (``UPDATE … WHERE claimed_by_actor_id IS NULL``) keep correctness
    under the resulting concurrency; this only removes the connection-refusal
    sharp edge. For serious QPS, a production WSGI server is still the path.
    """

    daemon_threads = True
    request_queue_size = 128


# Module-level flag toggled by signal handlers. Subcommand loops poll it
# at safe yield points (between sleeps + work batches) so SIGTERM produces
# a clean shutdown rather than an interrupted DB write.
_shutdown = threading.Event()


def _install_signal_handlers() -> None:
    def _handle(signum, frame):
        if not _shutdown.is_set():
            print(f"received signal {signum}, draining…", flush=True)
        _shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle)
        except (OSError, ValueError):
            # Some environments (Windows, restricted sandboxes) block this — ignore.
            pass


def _env_int(name: str, default: int) -> int:
    """Read an integer env var, falling back to default on missing/invalid."""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _base_url_advisory(base_url: str, host: str) -> str | None:
    """Warn when the advertised base URL is still a localhost default while the
    server is publicly bound.

    ``base_url`` is what agents are told to connect to (OpenAPI ``servers`` +
    ai-manifest.json). If it points at localhost but the socket is bound to all
    interfaces (the Docker default, ``--host 0.0.0.0``), an agent that fetches
    the manifest from outside will be handed an unreachable address. That fails
    silently — discovery "works", the returned URL just doesn't. Surface it.
    Returns the warning text, or ``None`` when nothing looks wrong.
    """
    publicly_bound = host in ("0.0.0.0", "::")
    looks_local = (not base_url) or "localhost" in base_url or "127.0.0.1" in base_url
    if publicly_bound and looks_local:
        shown = base_url or "(unset)"
        return (
            f"WARNING: advertising base_url={shown} while bound to {host}. Agents "
            "that fetch /openapi.json or ai-manifest.json will be told to connect "
            "to that address. For anything but local testing, set "
            "BACKCHANNEL_BASE_URL to your public URL "
            "(e.g. BACKCHANNEL_BASE_URL=https://bus.example.com)."
        )
    return None


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
    worker_parser.add_argument("--lease-interval", default=60, type=int, help="Seconds between expired-lease reclaim sweeps (default: 60)")

    report_parser = subparsers.add_parser("audit-report", help="Inspect recent cleanup runs and archived messages")
    report_parser.add_argument("--db", default="backchannel.db", help="SQLite database path")
    report_parser.add_argument("--limit", default=10, type=int, help="How many rows to show")

    args = parser.parse_args()

    if args.command == "worker":
        _install_signal_handlers()
        store = BackchannelStore(Path(args.db))
        print(f"Backchannel cleanup worker started (interval={args.interval}s)", flush=True)
        # Provision the public sandbox channel + its heartbeat bot. The bot
        # key owns the channel so there are no synthetic identities. The
        # sandbox ships aggressive abuse-control limits (operator-tunable).
        sandbox_ttl = _clamp(_env_int("BACKCHANNEL_SANDBOX_TTL_SECONDS", 600), 300, 2592000)
        sandbox_max_messages = _clamp(_env_int("BACKCHANNEL_SANDBOX_MAX_MESSAGES", 200), 1, 1_000_000)
        sandbox_max_writes = _clamp(_env_int("BACKCHANNEL_SANDBOX_MAX_WRITES_PER_MINUTE", 60), 1, 1_000_000)
        bot_key_id = store.ensure_heartbeat_bot_key()
        sandbox_channel_id = store.ensure_sandbox_channel(
            owner_key_id=bot_key_id,
            ttl_seconds=sandbox_ttl,
            max_messages=sandbox_max_messages,
            max_writes_per_minute=sandbox_max_writes,
        )
        print(
            f"sandbox channel ready channel_id={sandbox_channel_id} bot={bot_key_id} "
            f"ttl={sandbox_ttl}s max_messages={sandbox_max_messages} max_writes_per_minute={sandbox_max_writes}",
            flush=True,
        )
        # Auto-trip: if the DB file outgrows this many bytes, pause the
        # sandbox channel so an overnight flood cannot fill the disk
        # unattended. 0 disables. Only ever pauses the sandbox channel.
        db_size_limit = _env_int("BACKCHANNEL_DB_SIZE_LIMIT_BYTES", 1_073_741_824)
        db_files = [args.db, f"{args.db}-wal", f"{args.db}-shm"]
        auto_tripped = False
        # Heartbeat cadence is independent of the cleanup interval: the bot
        # must keep the sandbox channel fresh even when --interval is large.
        heartbeat_check_interval = 30.0
        seconds_since_heartbeat = heartbeat_check_interval  # check on first slice
        # Reclaim expired leases on their own cadence so a crashed claimer's
        # work returns to the unclaimed pool promptly, independent of cleanup.
        lease_check_interval = float(max(1, args.lease_interval))
        seconds_since_lease_sweep = lease_check_interval  # sweep on first slice
        while not _shutdown.is_set():
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
                        f"purged_audit_messages={summary['purged_audit_messages']}",
                    ]),
                    flush=True,
                )
            except Exception as exc:
                print(f"cleanup error: {exc}", flush=True)
            try:
                delivered = store.deliver_pending_webhooks()
                if delivered:
                    print(f"webhooks delivered={delivered}", flush=True)
            except Exception as exc:
                print(f"webhook delivery error: {exc}", flush=True)
            # Sleep in small slices so SIGTERM is observed quickly, and run
            # the sandbox heartbeat on its own cadence within the slices.
            slept = 0.0
            while slept < args.interval and not _shutdown.is_set():
                step = min(1.0, args.interval - slept)
                time.sleep(step)
                slept += step
                seconds_since_lease_sweep += step
                if seconds_since_lease_sweep >= lease_check_interval:
                    seconds_since_lease_sweep = 0.0
                    try:
                        reclaimed = store.reclaim_expired_leases()
                        if reclaimed:
                            print(f"leases reclaimed={reclaimed}", flush=True)
                    except Exception as exc:
                        print(f"lease reclaim error: {exc}", flush=True)
                seconds_since_heartbeat += step
                if seconds_since_heartbeat >= heartbeat_check_interval:
                    seconds_since_heartbeat = 0.0
                    try:
                        if store.post_sandbox_heartbeat_if_quiet(sandbox_channel_id, bot_key_id):
                            print("sandbox heartbeat posted", flush=True)
                    except Exception as exc:
                        print(f"sandbox heartbeat error: {exc}", flush=True)
                    if db_size_limit > 0 and not auto_tripped:
                        try:
                            db_bytes = sum(
                                os.path.getsize(p) for p in db_files if os.path.exists(p)
                            )
                            if db_bytes > db_size_limit:
                                store.set_channel_paused(sandbox_channel_id, True)
                                auto_tripped = True
                                print(
                                    f"AUTO-TRIP db_bytes={db_bytes} exceeds limit={db_size_limit} "
                                    "— sandbox channel paused; resume via the admin API after investigating",
                                    flush=True,
                                )
                        except Exception as exc:
                            print(f"auto-trip check error: {exc}", flush=True)
        print("worker drained, exiting", flush=True)
        return 0

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
                    f"purged_audit_messages={summary['purged_audit_messages']}",
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

    _install_signal_handlers()
    app = create_app(db_path=Path(args.db))
    advertised = os.environ.get("BACKCHANNEL_BASE_URL", "")
    with make_server(args.host, args.port, app, server_class=ThreadingWSGIServer) as server:
        print(f"Backchannel listening on http://{args.host}:{args.port}", flush=True)
        print(
            f"advertising base_url={advertised or '(unset → agent docs fall back to the public instance)'}",
            flush=True,
        )
        advisory = _base_url_advisory(advertised, args.host)
        if advisory:
            print(advisory, flush=True)
        # Run serve_forever in a thread so the main thread can wait on
        # the shutdown event. On SIGTERM we ask the server to stop
        # accepting new connections; in-flight requests finish on their
        # own worker threads before the process exits.
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            while not _shutdown.is_set():
                _shutdown.wait(timeout=1.0)
        finally:
            print("draining HTTP server…", flush=True)
            server.shutdown()
            server_thread.join(timeout=10)
            print("server stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
