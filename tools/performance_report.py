"""Local daily performance report commands; no network or message sending."""

import argparse
from datetime import datetime, time
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from helpdesk.storage import Store
from helpdesk.performance import PerformanceLedger
from helpdesk.performance_reports import PerformanceReports, scheduled_due_dates


def export_xlsx(payload: dict, output: Path, *, previews: Path | None = None):
    subprocess_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    runtime = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    if not (runtime / "@oai/artifact-tool").exists():
        raise RuntimeError("@oai/artifact-tool runtime unavailable; JSON snapshot remains available")
    builder = Path(__file__).with_name("performance_report_builder.mjs")
    with tempfile.TemporaryDirectory(prefix="performance-report-") as temp:
        base = Path(temp)
        (base / "report.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        shutil.copyfile(builder, base / "builder.mjs")
        try:
            os.symlink(runtime, base / "node_modules", target_is_directory=True)
        except OSError:
            junction = base / "junction.ps1"
            junction.write_text("param([string]$LinkPath,[string]$TargetPath)\n$ErrorActionPreference='Stop'\nNew-Item -ItemType Junction -Path $LinkPath -Target $TargetPath | Out-Null\n", encoding="utf-8")
            subprocess.run(["powershell", "-NoProfile", "-File", str(junction),
                            str(base / "node_modules"), str(runtime)], check=True, **subprocess_options)
        args = ["node", str(base / "builder.mjs"), str(base / "report.json"), str(output)]
        if previews:
            args.append(str(previews))
        subprocess.run(args, check=True, **subprocess_options)


def main(argv=None):
    p = argparse.ArgumentParser(description="本地每日答题统计草稿")
    p.add_argument("--db", required=True, help="本地 SQLite 台账路径")
    p.add_argument("--teacher", default="未指定")
    p.add_argument("--timezone", default="Asia/Shanghai")
    p.add_argument("--attribution", default="question_day_07",
                   choices=["question_day_07", "completion_date", "question_date", "approval_date"])
    p.add_argument("--activity-cutoff", "--cutoff", default="23:59:59", dest="activity_cutoff",
                   help="完成/核准活动自然日截止时刻；提问业务日固定07:00换日")
    p.add_argument("--output-dir", default="outputs/performance")
    sub = p.add_subparsers(dest="command", required=True)
    single = sub.add_parser("generate", help="生成或重算一个日期")
    single.add_argument("date")
    single.add_argument("--retroactive", action="store_true", help="保存已提交日报的修订版本")
    single.add_argument("--xlsx", action="store_true")
    catch = sub.add_parser("catch-up", help="补生成遗漏日期")
    catch.add_argument("start")
    catch.add_argument("end")
    catch.add_argument("--retroactive", action="store_true")
    catch.add_argument("--xlsx", action="store_true")
    due = sub.add_parser("due", help="列出达到生成时刻的缺失日期")
    due.add_argument("--start", required=True)
    due.add_argument("--time", default="08:00")
    revisions = sub.add_parser("versions", help="列出日报版本")
    revisions.add_argument("date")
    submitted = sub.add_parser("mark-submitted", help="记录已向机构提交的证据")
    submitted.add_argument("date")
    submitted.add_argument("version", type=int)
    submitted.add_argument("--actor", required=True)
    submitted.add_argument("--evidence", required=True)
    args = p.parse_args(argv)
    if not Path(args.db).exists():
        p.error(f"本地台账不存在：{args.db}")
    store = Store(args.db)
    try:
        reports = PerformanceReports(store, PerformanceLedger(store, timezone_name=args.timezone),
                                     teacher=args.teacher, timezone_name=args.timezone,
                                     attribution=args.attribution,
                                     cutoff_time=time.fromisoformat(args.activity_cutoff))
        output_dir = Path(args.output_dir)
        if args.command == "generate":
            result = reports.generate(args.date, retroactive=args.retroactive)
            output_dir.mkdir(parents=True, exist_ok=True)
            stem = f"每日答题统计-{args.date}-v{result['version']}"
            suffix = "-重算预览" if result.get("preview_only") else ""
            (output_dir / f"{stem}{suffix}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            if args.xlsx:
                export_xlsx(result["payload"], output_dir / f"{stem}{suffix}.xlsx")
            print(json.dumps({key: result.get(key) for key in ("version", "changed", "preview_only", "diff")}, ensure_ascii=False))
        elif args.command == "catch-up":
            results = reports.catch_up(args.start, args.end, retroactive=args.retroactive)
            output_dir.mkdir(parents=True, exist_ok=True)
            for result in results:
                day = result["payload"]["report_date"]
                stem = f"每日答题统计-{day}-v{result['version']}"
                if result.get("preview_only"):
                    stem += "-重算预览"
                (output_dir / f"{stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                if args.xlsx:
                    export_xlsx(result["payload"], output_dir / f"{stem}.xlsx")
            print(json.dumps([{"date": r["payload"]["report_date"], "version": r["version"],
                               "changed": r["changed"], "preview_only": r.get("preview_only", False)} for r in results], ensure_ascii=False))
        elif args.command == "due":
            print(json.dumps(scheduled_due_dates(reports, now=datetime.now().astimezone(),
                                                  generated_at_local=time.fromisoformat(args.time),
                                                  start_date=args.start), ensure_ascii=False))
        elif args.command == "versions":
            print(json.dumps(reports.list_versions(args.date), ensure_ascii=False, indent=2))
        elif args.command == "mark-submitted":
            reports.mark_submitted(args.date, args.version, actor=args.actor, evidence=args.evidence)
            print("提交记录已保存")
    finally:
        store.close()


if __name__ == "__main__":
    main()
