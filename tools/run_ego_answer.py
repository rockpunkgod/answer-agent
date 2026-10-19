"""Resume an already frozen ego teaching run into the original draft/Outbox; never sends."""
import argparse
from contextlib import closing
import json
from pathlib import Path

from helpdesk.ego_generation import run_existing
from helpdesk.storage import Store


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--space',type=int,required=True)
    parser.add_argument('--page',default='p1')
    parser.add_argument('--evidence-dir',type=Path,required=True)
    parser.add_argument('--capture-only',action='store_true')
    args=parser.parse_args()
    with closing(Store(args.database.resolve(strict=True))) as store:
        result=run_existing(store,args.run,args.space,args.evidence_dir,page=args.page,capture_only=args.capture_only)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
