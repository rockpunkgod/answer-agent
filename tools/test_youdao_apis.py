"""One explicitly approved anonymous image. No send, queue daemon or new database."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from helpdesk.youdao_api import ROOT, ApiError, Settings, checked_path
from helpdesk.youdao_handwriting_erase import HandwritingErase
from helpdesk.youdao_photo_search import PhotoSearch
from helpdesk.youdao_reference_pipeline import YoudaoReferencePipeline


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=ROOT/'config/youdao.example.toml')
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--erase-only',action='store_true')
    modes.add_argument('--search-only',action='store_true')
    modes.add_argument('--pipeline',action='store_true')
    parser.add_argument('--needs-cleanup',action='store_true')
    parser.add_argument('--cleanup-review',type=Path)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'data/private/youdao/results')
    parser.add_argument('--db',type=Path,help='Existing task database, pipeline only')
    parser.add_argument('--run-id',help='Existing frozen run, pipeline only')
    args=parser.parse_args(argv)
    try:
        settings=Settings.load(args.config)
        _,cache=settings.paths()
        if not args.dry_run:
            PhotoSearch(settings).purge_expired()
        output=checked_path(args.output_dir,ROOT/'data/private',exists=False)
        if output.is_relative_to(cache):
            raise ApiError('OUTPUT_DIRECTORY_IS_CALL_CACHE')
        if args.dry_run or not args.pipeline:
            client=HandwritingErase(settings) if args.erase_only or args.pipeline and args.needs_cleanup else PhotoSearch(settings)
            result=client.execute(args.image,context={'question_version':'ANONYMOUS_SINGLE_IMAGE',
                'context_revision':1,'synthetic_test':True},dry_run=args.dry_run)
        else:
            if not args.db or not args.db.is_file() or not args.run_id:
                raise ApiError('EXISTING_TASK_REQUIRED_FOR_PIPELINE')
            review=None
            if args.cleanup_review:
                path=checked_path(args.cleanup_review,ROOT/'data/private')
                if path.stat().st_size>1000000:raise ApiError('REVIEW_TOO_LARGE')
                review=json.loads(path.read_text(encoding='utf-8'))
            from helpdesk.storage import Store,ensure_local_database
            database=args.db.resolve(strict=True)
            ensure_local_database(database)
            store=Store.__new__(Store)
            store.path=str(database)
            store.connection=sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,isolation_level=None,timeout=10)
            store.connection.row_factory=sqlite3.Row
            store.connection.execute('PRAGMA foreign_keys=ON')
            with closing(store):
                if store.one('SELECT MAX(version) FROM schema_migrations')[0]!=6:
                    raise ApiError('BACKUP_AND_MIGRATION_REQUIRED')
                result=YoudaoReferencePipeline(settings).run(store,args.run_id,args.image,
                    needs_cleanup=args.needs_cleanup,cleanup_review=review)
        allowed=('status','cleanup_status','search_status','original_sha256','input_sha256',
                 'processed_sha256','dimensions','processed_dimensions','actual_call','actual_requests',
                 'cache_hit','latency_ms','charge_status','amount_cny','error_code','raw_response_sha256','budget_warning','injected')
        summary={k:result[k] for k in allowed if k in result}
        if 'candidates' in result:summary['candidate_count']=len(result['candidates'])
        if 'search' in result:summary.update(charge_status='UNVERIFIED',amount_cny=None,
                                             cache_hit=result['search']['cache_hit'])
        output.mkdir(parents=True,exist_ok=True)
        (output/('summary-'+result['original_sha256']+'.json')).write_text(json.dumps(summary,ensure_ascii=False),encoding='utf-8')
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[A-Z0-9_]{1,80}',str(exc)) else 'LOCAL_VALIDATION_FAILED'
        summary={'status':'UNAVAILABLE','error_code':code,'actual_call':None}
    print(json.dumps(summary,ensure_ascii=False))
    return 0 if summary['status'] in ('DRY_RUN','CLEANUP_SUCCEEDED','SEARCH_SUCCEEDED','SEARCH_NO_RESULTS','MATCH_CANDIDATE') else 2


if __name__=='__main__':
    raise SystemExit(main())
