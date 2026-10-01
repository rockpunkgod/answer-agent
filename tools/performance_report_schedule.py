"""One-shot local scheduler entry. Run daily through the operating system scheduler."""
import argparse
from datetime import datetime, time
import json
from pathlib import Path
import tempfile
import tomllib
import os
import sys
from uuid import uuid4

from helpdesk.performance import PerformanceLedger
from helpdesk.performance_reports import PerformanceReports, scheduled_due_dates
from helpdesk.storage import Store
from tools.performance_report import export_xlsx
from helpdesk.native_export import export_native_records


def _export_sources(config, native_exporter):
    """Verify and export sources before any report snapshot is generated.

    The existing exporter verifies all archive bytes. A content-addressed output
    keeps changed archives separate and lets identical reruns reuse one package.
    No acquisition is promoted into a performance unit by this operation.
    """
    root = config.get("native_archive_root")
    if not root:
        if config.get("require_native_export", False):
            raise ValueError("Native export is required but native_archive_root is missing")
        return None
    export_base = Path(config.get("native_export_dir", "outputs/performance-native-sources")).resolve()
    archive_root = Path(root).resolve(strict=True)
    if export_base == archive_root or archive_root in export_base.parents or export_base in archive_root.parents:
        raise ValueError("Native export directory must be separate from archive root")
    export_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="source-export-", dir=export_base) as staging:
        staged = native_exporter(archive_root, Path(staging) / "export",
                                 scope_start_date=config.get("scope_start_date", "2026-09-17"))
        digest = staged["files"]["原文与采集凭据.zip"]["sha256"]
        destination = export_base / ("原始记录-" + digest)
        # Reuse the existing exporter's byte comparison and no-overwrite policy.
        return native_exporter(archive_root, destination,
                               scope_start_date=config.get("scope_start_date", "2026-09-17"))


def run(config_path: str, *, now: datetime | None = None, exporter=export_xlsx,
        native_exporter=export_native_records):
    config = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    db_path = Path(config["database"])
    if not db_path.exists():
        raise FileNotFoundError(f"Configured database is missing: {db_path}")
    zone = config.get("business_timezone", "Asia/Shanghai")
    store = Store(db_path)
    try:
        reports = PerformanceReports(store, PerformanceLedger(store, timezone_name=zone),
                                     teacher=config.get("teacher", "未指定"), timezone_name=zone,
                                     attribution=config.get("attribution", "question_day_07"),
                                     scope_start_date=config.get("scope_start_date", "2026-09-17"),
                                     cutoff_time=time.fromisoformat(config.get("activity_day_cutoff", "23:59:59")))
        dates = scheduled_due_dates(reports, now=now or datetime.now().astimezone(),
                                    generated_at_local=time.fromisoformat(config.get("generate_at", "08:00")),
                                    start_date=config.get("catch_up_start", "2026-09-17"), include_saved=True)
        output = Path(config.get("output_dir", "outputs/performance"))
        output.mkdir(parents=True, exist_ok=True)
        native_export = _export_sources(config, native_exporter) if dates else None
        results = []
        for day in dates:
            # Existing files are not evidence that the ledger is unchanged.
            # The report engine compares the current deterministic fingerprint.
            result = reports.generate(day, retroactive=bool(config.get("retroactive_rule_application", False)))
            stem = f"每日答题统计-{day}-v{result['version']}"
            if result.get("preview_only"):
                stem += "-重算预览"
            json_path, xlsx_path = output / f"{stem}.json", output / f"{stem}.xlsx"
            if native_export:
                result["native_export"] = native_export
            if json_path.exists() and (not config.get("export_xlsx", True) or xlsx_path.exists()):
                # Preview changes do not persist versions; compare the saved
                # content so repeated previews also avoid duplicate exports.
                saved = json.loads(json_path.read_text(encoding="utf-8"))
                current_payload, saved_payload = dict(result["payload"]), dict(saved.get("payload", {}))
                current_payload.pop("generated_at", None)
                saved_payload.pop("generated_at", None)
                current_source = (native_export or {}).get("files", {}).get("原文与采集凭据.zip", {}).get("sha256")
                saved_source = (saved.get("native_export") or {}).get("files", {}).get("原文与采集凭据.zip", {}).get("sha256")
                if current_payload == saved_payload and (current_source == saved_source or result.get("submitted")):
                    continue
            json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            if config.get("export_xlsx", True):
                exporter(result["payload"], output / f"{stem}.xlsx")
            results.append({"date": day, "version": result["version"], "preview_only": result.get("preview_only", False)})
        return results
    finally:
        store.close()


def run_logged(config_path: str, **run_arguments):
    """Persist one background invocation's outcome, including failed exports.

    The log contains paths/statuses, never configuration contents or credentials.
    ``run`` remains the existing report engine entry; this adds observability.
    """
    config_file = Path(config_path).resolve(strict=True)
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    log_root = Path(config.get("run_log_dir", str(Path(config.get("output_dir", "outputs/performance")) / "run-logs")))
    log_root.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex
    record = {"run_id": run_id, "started_at": datetime.now().astimezone().isoformat(),
              "config_path": str(config_file), "database": str(Path(config["database"]).resolve()),
              "business_timezone": config.get("business_timezone", "Asia/Shanghai"),
              "status": "RUNNING", "exit_code": None}

    def save():
        temp_path = log_root / ("last-run-" + run_id + ".tmp")
        temp_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, log_root / "last-run.json")
        with (log_root / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    save()
    try:
        results = run(str(config_file), **run_arguments)
    except Exception as exc:
        record.update(status="FAILED", exit_code=1, finished_at=datetime.now().astimezone().isoformat(),
                      error_type=type(exc).__name__, error=str(exc))
        save()
        raise
    record.update(status="SUCCESS", exit_code=0, finished_at=datetime.now().astimezone().isoformat(),
                  generated_or_repaired_reports=results, generated_or_repaired_count=len(results),
                  output_directory=str(Path(config.get("output_dir", "outputs/performance")).resolve()),
                  outcome="草稿生成/补齐完成" if results else "同源已保存且无变化，或尚无到期业务日")
    save()
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="每日绩效草稿本地补生成")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        results = run_logged(args.config)
    except Exception as exc:
        if sys.stderr is not None:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if sys.stdout is not None:
        print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
