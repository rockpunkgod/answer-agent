"""Restricted WeCom CLI sharing the business MCP implementation."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from helpdesk.wecom_tools import WeComTools


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
    args=parser.parse_args(argv)
    if args.enable_send and not args.enable_input:
        parser.error('--enable-send requires --enable-input')
    operation={'state':'get_chat_state','controls':'inspect_controls','chats':'list_chats',
        'open':'open_chat','messages':'get_messages','image':'open_image',
        'attachment':'download_attachment','prepare':'prepare_reply','send':'send_reply','receipt':'verify_reply'}[args.operation]
    try:
        session=json.loads(args.session.read_text(encoding='utf-8-sig')) if args.session else None
        service=WeComTools(args.database,session=session,allow_input=args.enable_input,allow_send=args.enable_send)
        arguments={key:getattr(args,key) for key in ('chat_id','message_id','outbox_id') if hasattr(args,key)}
        result=service.call(operation,**arguments)
    except Exception:
        result={'status':'UNAVAILABLE','reason':'CLI_CONFIGURATION_UNAVAILABLE','automatic_retry':False}
    # stdout is the explicit private command result, not an ordinary log.
    print(json.dumps(result,ensure_ascii=False))
    return 2 if result.get('status') in ('UNAVAILABLE','HISTORICAL_TEXT_UNCONFIRMED') else 0


if __name__=='__main__':
    raise SystemExit(main())
