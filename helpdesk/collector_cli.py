"""Run with python -m helpdesk.collector_cli; no account capability assumptions."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import time
import tomllib
from .collector_storage import CollectorStore
from .message_sync import SyncEngine
from .message_sources import SyncMode, business_zone


def load_source(config):
    factory = config.get("source", {}).get("factory")
    if not factory or ":" not in factory:
        raise ValueError("Configure a verified source factory module:function; no account adapter enabled by default")
    module, name = factory.split(":", 1)
    return getattr(importlib.import_module(module), name)(config)


def status(store, *, source_name, mode=SyncMode.LIVE, stale_after_seconds=300, business_timezone="Asia/Shanghai"):
    stats = store.monitoring(source_name, mode, stale_after_seconds=stale_after_seconds,
                             business_timezone=business_timezone)
    state = store.get_sync_state(source_name, mode)
    with store.connect() as db:
        pending_events = db.execute("SELECT COUNT(*) FROM events WHERE processed_at IS NULL AND source_name=? AND mode=?", (source_name, mode)).fetchone()[0]
    return stats | {"health": stats["sync_health"], "state": state,
        "seconds_since_success": stats["seconds_since_successful_sync"],
        "duplicate_messages_total": state.get("duplicate_count", 0),
        "duplicate_today_status": "TRACKED_COMMITTED_BATCHES", "events_pending": pending_events}


def run_live_iteration(store, source, settings):
    """One recoverable LIVE sync then an explicitly enabled ACK_ONLY drain.

    Sync commits precede business processing. Drain failure keeps durable events
    for the next iteration/restart and never rewinds the successful source cursor.
    """
    store.activate_live(source.source_name)
    sync = SyncEngine(store, source, settings.get("page_size", 1000)).sync(
        SyncMode.LIVE, settings.get("max_pages", 1000))
    result = {"sync": asdict(sync), "processing_mode": settings.get("processing_mode"),
              "events_processed": 0, "student_send_enabled": False}
    if settings.get("processing_mode") != "ACK_ONLY":
        result["drain_enabled"] = False
        return result
    from .collector_dispatch import CollectorDispatcher
    from .storage import Store
    path = Path(settings.get("business_database", "data/helpdesk.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    business = Store(path)
    try:
        dispatcher = CollectorDispatcher(store, business, processing_mode="ACK_ONLY",
            self_sender_ids=settings.get("self_sender_ids", ()),
            teacher_sender_ids=settings.get("teacher_sender_ids", ()))
        result["events_processed"] = dispatcher.drain(limit=settings.get("drain_limit", 1000))
        result["pending_ack_count"] = business.one("SELECT COUNT(*) FROM outbox o JOIN messages m ON m.id=o.message_id WHERE o.purpose='ACK' AND o.state='PENDING' AND m.source LIKE 'collector:%'")[0]
        result["drain_enabled"] = True
    finally:
        business.close()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Incremental source collector; no automatic student sending")
    parser.add_argument("command", choices=("sync", "watch", "run-once", "status", "drain", "media"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=("LIVE", "BACKFILL"), default="LIVE")
    args = parser.parse_args(argv)
    if args.command in {"watch", "run-once"} and args.mode != "LIVE":
        parser.error("watch/run-once are LIVE only; use bounded sync --mode BACKFILL")
    path = Path(args.config).resolve()
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = config.get("collector", {})
    db_path = Path(settings.get("database", "data/messages.db"))
    # Paths resolve relative to repository working directory, as documented.
    store = CollectorStore(db_path)
    mode = SyncMode(args.mode)
    if args.command == "status":
        result = status(store, source_name=config.get("source", {}).get("name", "wecom_archive"), mode=mode,
            stale_after_seconds=settings.get("stale_after_seconds", 300),
            business_timezone=settings.get("business_timezone", "Asia/Shanghai"))
    elif args.command == "drain":
        from .collector_dispatch import CollectorDispatcher
        from .storage import Store
        store.activate_live(config.get("source", {}).get("name", "wecom_archive"))
        business_path = Path(settings.get("business_database", "data/helpdesk.db"))
        business_path.parent.mkdir(parents=True, exist_ok=True)
        business = Store(business_path)
        try:
            dispatcher = CollectorDispatcher(store, business,
                processing_mode=settings.get("processing_mode", "ACK_ONLY"),
                self_sender_ids=settings.get("self_sender_ids", ()),
                teacher_sender_ids=settings.get("teacher_sender_ids", ()))
            result = {"events_processed": dispatcher.drain(), "tasks": dispatcher.pending()}
        finally:
            business.close()
    else:
        try:
            source = load_source(config)
        except Exception as error:
            from .wecom_archive import ArchiveNotConfigured
            authorization = config.get("archive", {})
            admin_ready = (all(authorization.get(key) is True for key in
                ("enabled", "admin_authorized", "member_scope_verified", "consent_verified"))
                and bool(authorization.get("evidence_reference")))
            health = "NEEDS_ADMIN_CONFIGURATION" if isinstance(error, ArchiveNotConfigured) and not admin_ready else "SOURCE_UNAVAILABLE"
            print(json.dumps({"health": health, "error_type": type(error).__name__,
                "watch_started": False, "cursor_preserved": True}, ensure_ascii=False), flush=True)
            return 2
        try:
            engine = SyncEngine(store, source, settings.get("page_size", 1000))
            if args.command == "media":
                from .message_media import ArchiveMediaWorker
                media = config.get("media", {})
                result = ArchiveMediaWorker(store, source.transport, source.authorization,
                    root=media.get("root", "data/media"), source_name=source.source_name).run_pending(limit=media.get("limit", 20))
            elif args.command == "sync":
                if mode == SyncMode.LIVE:
                    store.activate_live(source.source_name)
                result = asdict(engine.sync(mode, settings.get("max_pages", 1000)))
            elif args.command == "run-once":
                result = run_live_iteration(store, source, settings)
            else:
                interval = settings.get("poll_seconds", 10)
                if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval < 1:
                    raise ValueError("poll_seconds must be >= 1")
                try:
                    while True:
                        try:
                            print(json.dumps(run_live_iteration(store, source, settings), ensure_ascii=False), flush=True)
                        except Exception as error:
                            print(json.dumps({"health": "FAILED", "error_type": type(error).__name__}, ensure_ascii=False), flush=True)
                        time.sleep(interval)
                except KeyboardInterrupt:
                    result = {"stopped": True, "cursor_preserved": True}
        finally:
            close = getattr(source, "close", None)
            if not callable(close):
                close = getattr(getattr(source, "transport", None), "close", None)
            if callable(close):
                close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
