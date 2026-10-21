"""One bounded ego-lite request or read-only recovery; captures a draft, never sends."""
import argparse
import json
from pathlib import Path

from helpdesk.ego_browser import EgoDeepSeek
from helpdesk.local_teaching_uploads import QUESTION_FOLDERS,local_teaching_files


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--space',type=int,required=True)
    parser.add_argument('--page',default='p1')
    parser.add_argument('--request-id',required=True)
    parser.add_argument('--session-url',required=True)
    question=parser.add_mutually_exclusive_group(required=True)
    question.add_argument('--prompt-file',type=Path)
    question.add_argument('--question',help='Student words or simply a question number, e.g. 第34题')
    parser.add_argument('--attachment',type=Path,action='append',default=[])
    parser.add_argument('--question-type',choices=tuple(QUESTION_FOLDERS),
                        help='Upload original files from the corresponding approved local skill folder')
    parser.add_argument('--response-token',help=argparse.SUPPRESS)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    parser.add_argument('--capture-only',action='store_true')
    args=parser.parse_args()
    files=[*args.attachment,*(local_teaching_files(args.question_type) if args.question_type else [])]
    adapter=EgoDeepSeek(args.space,args.evidence_dir,page=args.page,allowed_files=files)
    prompt=args.prompt_file.read_text(encoding='utf-8') if args.prompt_file else args.question
    result=adapter.submit(args.request_id,args.session_url,prompt,
        attachments=files,response_token=args.response_token,capture_only=args.capture_only)
    print(json.dumps({'adapter':result['adapter'],'status':'CAPTURED',
        'evidence':str((args.evidence_dir/(args.request_id+'.answer.json')).resolve()),
        'delivered':False,'formal_statistics_eligible':False},ensure_ascii=False))


if __name__=='__main__':
    main()
