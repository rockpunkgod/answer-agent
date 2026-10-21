"""Collection only: history import, one incremental poll, or persistent polling.

No desktop transport, upstream Listener, media driver or business dispatch.
Each poll runs in a fresh isolated process; durable cursors live in CollectorStore.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))


def report_failure(config_path,payload):
    """Replace stale success in the existing private result file, when allowed."""
    try:
        config=json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
        private=Path(config['private_root']).resolve(strict=True)
        if config.get('prototype_only') is True and private.is_relative_to(ROOT/'data/private'):
            (private/'last-result.local.json').write_text(json.dumps(payload),encoding='utf-8')
    except Exception:
        pass
    print(json.dumps(payload),flush=True)


def collect_once(config_path,mode):
    # Importing the shared CLI does not initialize any desktop or upstream module.
    from tools.personal_wechat_replica_cli import run
    config=json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    result=run(config,'sync',mode=mode,observe_foreground=False)
    if any(row['has_more'] or row['conflicts'] for cycle in result['passes'] for row in cycle):
        raise RuntimeError('COLLECTION_INCOMPLETE_OR_CONFLICTED')
    return {'status':result['status'],'mode':mode,
            'inserted':sum(row['inserted'] for cycle in result['passes'] for row in cycle),
            'duplicates':sum(row['duplicates'] for cycle in result['passes'] for row in cycle),
            'desktop_access':False,'sending_enabled':False}


def watch(config_path,*,interval=5,cycles=None,timeout=120):
    """Fail closed; never retries a failed poll or resets a durable cursor."""
    if not 1<=interval<=60 or (cycles is not None and cycles<1) or not 1<=timeout<=600:
        raise ValueError('INVALID_WATCH_BOUNDS')
    count=0
    while cycles is None or count<cycles:
        try:
            completed=subprocess.run([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),
                'poll','--config',str(Path(config_path).resolve())],
                capture_output=True,text=True,encoding='utf-8',timeout=timeout)
        except subprocess.TimeoutExpired:
            report_failure(config_path,{'status':'COLLECTION_TIMEOUT','automatic_retry':False})
            return 2
        # The child emits sanitized summaries only; raw errors never reach stdout.
        if completed.returncode:
            report_failure(config_path,{'status':'COLLECTION_STOPPED','automatic_retry':False,
                                        'reason':'POLL_FAILED'})
            return 2
        try:
            summary=json.loads(completed.stdout)
            if summary.get('status')!='RAW_COLLECTOR_SYNCED':raise ValueError('Invalid child result')
        except (ValueError,AttributeError):
            report_failure(config_path,{'status':'COLLECTION_STOPPED','reason':'INVALID_POLL_RESULT',
                                        'automatic_retry':False})
            return 2
        print(json.dumps(summary),flush=True)
        count+=1
        if cycles is None or count<cycles:time.sleep(interval)
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('history','poll','watch'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--interval',type=int,default=5)
    parser.add_argument('--cycles',type=int,help='Watch cycles; omitted means until stopped')
    parser.add_argument('--timeout',type=int,default=120)
    args=parser.parse_args(argv)
    try:
        if args.operation=='watch':
            return watch(args.config,interval=args.interval,cycles=args.cycles,timeout=args.timeout)
        print(json.dumps(collect_once(args.config,'BACKFILL' if args.operation=='history' else 'LIVE')),
              flush=True)
        return 0
    except KeyboardInterrupt:
        report_failure(args.config,{'status':'COLLECTION_PAUSED','automatic_retry':False})
        return 130
    except Exception as error:
        # No payloads, secrets, decrypted paths or third-party exception strings.
        report_failure(args.config,{'status':'COLLECTION_FAILED','error_type':type(error).__name__,
                                    'automatic_retry':False})
        return 2


if __name__=='__main__':raise SystemExit(main())
