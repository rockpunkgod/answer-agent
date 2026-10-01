"""Dispatch one approved TEST_ANSWER through an unexpired, verified WeCom pin.

Does not search for contacts or switch applications. The selected WeCom chat
must still match the reviewed pin. A repeat invocation cannot re-send a receipt.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpdesk.mcp_transport import MCPProcess
from helpdesk.mcp_test_delivery import MCPTestAnswerDesktop
from helpdesk.storage import Store
from helpdesk.workflow import Workflow


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--outbox',required=True)
    parser.add_argument('--pin',type=Path,required=True)
    parser.add_argument('--inspect',action='store_true',help='Only inspect a prior uncertain send; never submit')
    args=parser.parse_args()
    pin=json.loads(args.pin.read_text(encoding='utf-8'))
    if pin.get('outbox_id')!=args.outbox:
        parser.error('Pin and outbox do not match')
    with closing(Store(args.database)) as store:
        row=store.one('SELECT * FROM outbox WHERE id=?',(args.outbox,))
        if not row or row['purpose']!='TEST_ANSWER':
            parser.error('Only an existing TEST_ANSWER is eligible')
        if not args.inspect and row['state']!='PENDING':
            print(json.dumps({'outbox_id':args.outbox,'state':row['state'],'resubmitted':False}))
            return
        with MCPProcess() as mcp:
            flow=Workflow(store,desktop=MCPTestAnswerDesktop(mcp,pin,ROOT))
            result=flow.inspect_unknown(args.outbox) if args.inspect else flow.dispatch(args.outbox)
            print(json.dumps({'outbox_id':args.outbox,'state':result,'source_student_delivered':False}))


if __name__=='__main__': main()
