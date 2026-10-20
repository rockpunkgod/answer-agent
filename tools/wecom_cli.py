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
}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--session',type=Path)
    parser.add_argument('--enable-input',action='store_true')
    parser.add_argument('--enable-send',action='store_true')
    sub=parser.add_subparsers(dest='operation',required=True)
    for name in ('state','controls','chats'):
        sub.add_parser(name)
    for name,key in (('open','chat_id'),('messages','chat_id'),('image','message_id'),
                     ('attachment','message_id'),('prepare','outbox_id'),('send','outbox_id'),('receipt','outbox_id')):
        sub.add_parser(name).add_argument(key)
    sub.add_parser('poll').add_argument('chat_id')
    sub.add_parser('history').add_argument('chat_id')
    sub.add_parser('save-image').add_argument('message_id')
    sub.add_parser('search').add_argument('chat_id')
    watch=sub.add_parser('watch',help='Bounded reviewed-visible observations only; never sends')
    watch.add_argument('chat_id')
    watch.add_argument('--iterations',type=int,default=10)
    watch.add_argument('--interval',type=float,default=5)
    args=parser.parse_args(argv)
    if args.enable_send and not args.enable_input:
        parser.error('--enable-send requires --enable-input')
    if args.operation=='watch' and (not 1<=args.iterations<=1000 or not 1<=args.interval<=60):
        parser.error('watch requires 1..1000 iterations and 1..60 seconds interval')
    operation={'state':'get_chat_state','controls':'inspect_controls','chats':'list_chats',
        'open':'open_chat','messages':'get_messages','image':'open_image',
        'attachment':'download_attachment','prepare':'prepare_reply','send':'send_reply','receipt':'verify_reply',
        'poll':'poll_messages','watch':'poll_messages','history':'history_page','save-image':'save_open_image',
        'search':'search_chat'}[args.operation]
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
