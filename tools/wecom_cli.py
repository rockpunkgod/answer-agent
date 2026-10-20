"""Restricted WeCom CLI sharing the business MCP implementation."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from helpdesk.wecom_tools import WeComTools


SUCCESS_STATUSES={
    'state':{'VERIFIED_CURRENT_CHAT'},'controls':{'OK'},'chats':{'OK'},
    'open':{'ALREADY_OPEN'},'messages':{'OK'},'image':{'INVOKED_UNVERIFIED'},
    'attachment':{'EXISTING_ORIGINAL_VERIFIED'},'prepare':{'PREPARED_VERIFIED'},
    'send':{'SENT_UI_CONFIRMED'},'receipt':{'HISTORICAL_TEXT_VERIFIED'},
    'poll':{'OBSERVATIONS_SAVED','NO_NEW_OBSERVATIONS'},
    'watch':{'OBSERVATIONS_SAVED','NO_NEW_OBSERVATIONS'},
    'history':{'HISTORY_VIEW_CHANGED','HISTORY_VIEW_UNCHANGED'},
    'save-image':{'ORIGINAL_SAVED'},
    'search':{'OPENED_VERIFIED'},
    'scan':{'OK','NO_VISIBLE_TEXT_CANDIDATES'},
    'ack-status':{'OK'},
    'ack-tick':{'SENT_UI_CONFIRMED','IDLE'},
}
for _operation in ('poll','watch'):
    SUCCESS_STATUSES[_operation].add('NO_VISIBLE_TEXT_CANDIDATES')


def group_ack(args):
    """Existing runtime only: no recipient input, new source, migration or teaching."""
    from contextlib import closing
    import sqlite3
    import tomllib
    from helpdesk.storage import Store
    from helpdesk.automatic_delivery_runtime import AutomaticDeliveryRuntime
    if not args.delivery_config or args.session:
        raise ValueError("Explicit native ACK configuration required; test session is not a group recipe")
    if args.operation == 'ack-tick' and not (args.enable_input and args.enable_send):
        return {'status':'UNAVAILABLE', 'reason':'SEND_DISABLED', 'automatic_retry':False}
    database = args.database.resolve(strict=True)
    source = tomllib.loads(args.collector_config.read_text(encoding='utf-8')) if args.collector_config else None
    if source and Path(source.get('collector', {}).get('business_database', '')).resolve() != database:
        raise ValueError("Collector and sender business database must match")
    if args.operation == 'ack-tick' and (not source or source.get('collector', {}).get('processing_mode') != 'ACK_ONLY'):
        return {'status':'UNAVAILABLE', 'reason':'ACK_ONLY_COLLECTOR_CONFIGURATION_REQUIRED', 'automatic_retry':False}
    runtime = AutomaticDeliveryRuntime(database, args.delivery_config, collector_config=source)
    if (runtime.desktop_config or {}).get('backend') != 'NATIVE_ACK':
        raise ValueError("This command requires the ACK-only native group backend")
    # Status is read-only; tick refuses old schemas instead of silently migrating
    # an operator's business database without its required backup.
    store = Store.__new__(Store)
    store.path = str(database)
    store.connection = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, isolation_level=None)
    store.connection.row_factory = sqlite3.Row
    with closing(store):
        version = store.one('SELECT MAX(version) FROM schema_migrations')[0]
        if version != 6:
            return {'status':'UNAVAILABLE', 'reason':'BACKUP_AND_MIGRATION_REQUIRED', 'automatic_retry':False}
        if args.operation == 'ack-status':
            state = runtime.snapshot(store)
            result = {k: state[k] for k in ('enabled','paused','desktop_backend','allowed_purposes',
                      'live_validation','real_confirmed_tasks','error_type')}
            pending = "purpose='ACK' AND state IN ('PENDING','SENDING','SEND_UNKNOWN','FAILED')"
            count = store.one('SELECT COUNT(*) FROM outbox WHERE '+pending)[0]
            rows = store.all('''SELECT o.id AS task_id,o.state,o.last_error,b.group_key,
                        b.student_key,b.display_name AS student_name
                        FROM outbox o LEFT JOIN bindings b ON b.id=o.binding_id
                        WHERE o.'''+pending+''' ORDER BY CASE state WHEN 'SEND_UNKNOWN' THEN 0
                        WHEN 'SENDING' THEN 1 ELSE 2 END,o.created_at,o.rowid LIMIT 20''')
            groups = runtime.desktop_config.get('groups', [])
            tasks = [dict(row, within_configured_targets=any(g['group_key']==row['group_key']
                          and row['student_key'] in g['student_keys'] for g in groups)) for row in rows]
            return {'status':'OK', 'scope':'EXISTING_OUTBOX_ONLY', 'runtime':result,
                    'ack_tasks':tasks, 'ack_task_count':count, 'truncated':count>len(tasks),
                    'continuous_collection_verified':False}
    result = runtime.tick()
    return {'status':result['state'], 'scope':'EXISTING_OUTBOX_ONLY', 'result':result,
            'automatic_retry':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--session',type=Path)
    parser.add_argument('--delivery-config',type=Path)
    parser.add_argument('--collector-config',type=Path)
    parser.add_argument('--enable-input',action='store_true')
    parser.add_argument('--enable-send',action='store_true')
    sub=parser.add_subparsers(dest='operation',required=True)
    for name in ('state','controls','chats','ack-status','ack-tick'):
        sub.add_parser(name)
    for name,key in (('open','chat_id'),('messages','chat_id'),('image','message_id'),
                     ('attachment','message_id'),('prepare','outbox_id'),('send','outbox_id'),('receipt','outbox_id')):
        sub.add_parser(name).add_argument(key)
    sub.add_parser('poll').add_argument('chat_id')
    sub.add_parser('history').add_argument('chat_id')
    sub.add_parser('save-image').add_argument('message_id')
    sub.add_parser('search').add_argument('chat_id')
    sub.add_parser('scan').add_argument('chat_id')
    watch=sub.add_parser('watch',help='Bounded reviewed-visible observations only; never sends')
    watch.add_argument('chat_id')
    watch.add_argument('--iterations',type=int,default=10)
    watch.add_argument('--interval',type=float,default=5)
    args=parser.parse_args(argv)
    if args.enable_send and not args.enable_input:
        parser.error('--enable-send requires --enable-input')
    if args.operation=='watch' and (not 1<=args.iterations<=1000 or not 1<=args.interval<=60):
        parser.error('watch requires 1..1000 iterations and 1..60 seconds interval')
    if args.operation in ('ack-status','ack-tick'):
        try:
            result=group_ack(args)
        except KeyboardInterrupt:
            result={'status':'STOPPED','automatic_retry':False}
        except Exception:
            result={'status':'UNAVAILABLE','reason':'GROUP_ACK_CONFIGURATION_OR_DATABASE_UNAVAILABLE','automatic_retry':False}
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result.get('status') in SUCCESS_STATUSES[args.operation] else 2
    operation={'state':'get_chat_state','controls':'inspect_controls','chats':'list_chats',
        'open':'open_chat','messages':'get_messages','image':'open_image',
        'attachment':'download_attachment','prepare':'prepare_reply','send':'send_reply','receipt':'verify_reply',
        'poll':'poll_messages','watch':'poll_messages','history':'history_page','save-image':'save_open_image',
        'search':'search_chat','scan':'scan_messages'}[args.operation]
    try:
        session=json.loads(args.session.read_text(encoding='utf-8-sig')) if args.session else None
        service=WeComTools(args.database,session=session,allow_input=args.enable_input,allow_send=args.enable_send)
        arguments={key:getattr(args,key) for key in ('chat_id','message_id','outbox_id') if hasattr(args,key)}
        count=args.iterations if args.operation=='watch' else 1
        for index in range(count):
            result=service.call(operation,**arguments)
            if args.operation=='watch':
                print(json.dumps(result,ensure_ascii=False),flush=True)
                if result.get('status') not in SUCCESS_STATUSES['watch']:return 2
                if index+1<count:time.sleep(args.interval)
        if args.operation=='watch':return 0
    except KeyboardInterrupt:
        print(json.dumps({'status':'STOPPED','automatic_retry':False}),flush=True)
        return 130
    except Exception:
        result={'status':'UNAVAILABLE','reason':'CLI_CONFIGURATION_UNAVAILABLE','automatic_retry':False}
    # stdout is the explicit private command result, not an ordinary log.
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result.get('status') in SUCCESS_STATUSES[args.operation] else 2


if __name__=='__main__':
    raise SystemExit(main())
