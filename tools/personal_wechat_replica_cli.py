"""Isolated collection and cached media only; no desktop transport or sending."""
import argparse
from contextlib import redirect_stdout,redirect_stderr,closing
import io
import json
from pathlib import Path
import sys
import time
import sqlite3
from uuid import uuid4
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from helpdesk.personal_wechat_replica import load_read_modules,strict_database,ReplicaSnapshotReader,UPSTREAM_COMMIT
from helpdesk.personal_wechat_source import PersonalWeChatSource
from helpdesk.collector_storage import CollectorStore
from helpdesk.message_sync import SyncEngine
from helpdesk.message_sources import SyncMode,utc_now


def run(config,operation,*,mode='BACKFILL',iterations=1,interval=5,message_id=None,image_tier='original'):
    if operation not in ('inventory','sync','media','upgrade-cursors'):
        raise ValueError('COLLECTION_OPERATION_NOT_SUPPORTED')
    import psutil
    private=Path(config['private_root']).resolve(strict=True)
    if not private.is_relative_to(ROOT/'data/private') or config.get('prototype_only') is not True:
        raise ValueError('ISOLATED_PRIVATE_PROTOTYPE_REQUIRED')
    m,media_module=load_read_modules(config['upstream'],include_media=operation=='media')
    process=psutil.Process(config['process_id']);created=process.create_time()
    if process.name().lower()!='weixin.exe' or created!=config['process_create_time']:
        raise ValueError('PINNED_WECHAT_PROCESS_CHANGED')
    cfg=m.extract_master_key_from_cfg(process.pid)
    if not cfg or cfg[2]!=config['account_id']:raise ValueError('LIVE_ACCOUNT_NOT_VERIFIED')
    work=private/'db-cache';work.mkdir(exist_ok=True)
    with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
        db=strict_database(m,db_root=config['db_root'],account=config['account_directory'],cache=work,pid=process.pid)
    if db.wxid!=config['account_id']:raise ValueError('ACCOUNT_DIRECTORY_MISMATCH')
    def guard():
        if not process.is_running() or process.create_time()!=created:raise ValueError('WECHAT_PROCESS_RESTARTED')
        current=db.extract_master_key()
        if not current or current[2]!=config['account_id']:raise ValueError('WECHAT_LOGIN_CHANGED')
    result={'operation':operation,'upstream_commit':UPSTREAM_COMMIT,'input_performed':False,
            'activation_performed':False,'production_enabled':False,'business_database_touched':False}
    if operation=='inventory':
        paths=[r for r,p,_ in db._db_files if Path(p).name=='contact.db']
        if len(paths)!=1:raise ValueError('CONTACT_DATABASE_NOT_UNIQUE')
        conn=db._open(paths[0])
        try:
            contacts=[dict(r) for r in conn.execute("SELECT username,nick_name FROM contact WHERE username LIKE '%@chatroom'")]
        finally:conn.close()
        wanted=config['authorized_group_names']
        compact=lambda value:''.join(value.split())
        result['group_candidates']=[{'requested_name':name,'matches':[r for r in contacts if compact(r['nick_name'] or '')==compact(name)]} for name in wanted]
        result['partial_name_candidates']=[r for r in contacts if any(compact(name) in compact(r['nick_name'] or '') for name in wanted)]
        result['status']='INVENTORY_READ'
    else:
        if config.get('groups_reviewed') is not True:raise ValueError('MANUALLY_CONFIRMED_GROUP_IDS_REQUIRED')
        rooms=config['rooms']
        reader=ReplicaSnapshotReader(db,account_id=config['account_id'],rooms=rooms,guard=guard)
        database=Path(config['collector_database']).resolve()
        if not database.is_relative_to(private):raise ValueError('PRODUCTION_DATABASE_FORBIDDEN')
        store=CollectorStore(database)
        sources=[PersonalWeChatSource(reader,account_id=config['account_id'],rooms=rooms,room_id=room,
                                     test_room_ids=config.get('test_room_ids',[])) for room in rooms]
        if operation=='upgrade-cursors':
            backup=private/('collector-before-cursor-v2-'+uuid4().hex+'.sqlite')
            with store.connect() as original,closing(sqlite3.connect(backup)) as target:
                original.backup(target)
            plans=[]
            for source in sources:
                for sync_mode in (SyncMode.BACKFILL,SyncMode.LIVE):
                    previous=store.get_sync_state(source.source_name,sync_mode)['cursor']
                    if previous and json.loads(previous).get('version')==1:
                        upgraded=source.upgrade_cursor(previous,sync_mode,store)
                        plans.append((source.source_name,str(sync_mode),previous,upgraded))
            guard()
            with store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                for name,sync_mode,previous,upgraded in plans:
                    row=connection.execute('SELECT cursor FROM sync_state WHERE source_name=? AND mode=?',
                                           (name,sync_mode)).fetchone()
                    if not row or row[0]!=previous:raise ValueError('CURSOR_CHANGED_DURING_UPGRADE')
                    connection.execute("UPDATE sync_state SET cursor=?,status='READY',last_error=NULL WHERE source_name=? AND mode=?",
                                       (upgraded,name,sync_mode))
            result.update(status='CURSORS_UPGRADED_WITH_ORIGINAL_EVIDENCE',upgraded=len(plans),backup=str(backup))
        elif operation=='sync':
            result['passes']=[]
            for index in range(iterations):
                if index:time.sleep(interval)
                cycle=[]
                for source in sources:
                    if mode=='LIVE':
                        source.live_seed_cursor=store.get_sync_state(source.source_name,SyncMode.BACKFILL).get('cursor')
                    row=SyncEngine(store,source,page_size=100).sync(SyncMode(mode),max_pages=100)
                    cycle.append({'source_name':source.source_name,'inserted':row.inserted_count,
                                  'duplicates':row.duplicate_count,'conflicts':row.conflict_count,'has_more':row.has_more})
                result['passes'].append(cycle)
            result['status']='RAW_COLLECTOR_SYNCED'
        else:
            row=store.get_message(message_id)
            if not row or row['source_type']!='personal_wechat_local' or row['message_type']!='image':
                raise ValueError('STORED_PERSONAL_IMAGE_MESSAGE_REQUIRED')
            class LocalOnlyMedia(media_module.MediaDownloader):
                def _scan_aes_key(self,monitor=False,**kwargs):return super()._scan_aes_key(monitor=False)
                def _wxgf_to_jpg(self,data):return None
                def download_image_original(self,*args,**kwargs):raise RuntimeError('GUI_MEDIA_FETCH_FORBIDDEN')
            approved=private/'attachments';approved.mkdir(exist_ok=True)
            media=LocalOnlyMedia(db,save_dir=str(approved))
            record=SimpleNamespace(message_id=message_id,raw_payload=json.loads(row['raw_payload']))
            result['media']=reader.read_image(record,media,approved,tier=image_tier)
            result['status']=result['media']['status']
            if result['status'] in ('LOCAL_ORIGINAL_READ','LOCAL_DERIVATIVE_READ'):
                info=result['media']
                with store.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    if info['original_attachment']:
                        connection.execute('UPDATE messages SET local_media_path=?,media_hash=? WHERE message_id=?',
                                           (info['path'],info['sha256'],message_id))
                    connection.execute('UPDATE message_media SET local_path=?,hash=?,size=?,downloaded_at=?,status=? WHERE message_id=?',
                                       (info['path'],info['sha256'],info['size'],utc_now(),
                                        'READY' if info['original_attachment'] else 'DERIVATIVE_ONLY',message_id))
    guard()
    result['desktop_access']=False
    (private/'last-result.local.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['inventory','sync','media','upgrade-cursors'])
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--mode',choices=['LIVE','BACKFILL'],default='BACKFILL')
    parser.add_argument('--iterations',type=int,default=1)
    parser.add_argument('--interval',type=int,default=5)
    parser.add_argument('--message-id')
    parser.add_argument('--image-tier',choices=['original','mid','thumb'],default='original')
    args=parser.parse_args(argv)
    if not 1<=args.iterations<=10 or not 1<=args.interval<=60:parser.error('Use 1..10 iterations and 1..60 second intervals')
    try:
        result=run(json.loads(args.config.read_text(encoding='utf-8-sig')),args.operation,
                   mode=args.mode,iterations=args.iterations,interval=args.interval,message_id=args.message_id,image_tier=args.image_tier)
        print(json.dumps({k:v for k,v in result.items() if k not in ('group_candidates','partial_name_candidates','media')},ensure_ascii=False))
        return 0
    except Exception as error:
        # Third-party errors can contain identity, paths or payloads. Keep output typed.
        failure={'operation':args.operation,'status':'UNAVAILABLE','error_type':type(error).__name__,
                 'automatic_retry':False,'production_enabled':False,'recorded_at':utc_now()}
        try:
            config=json.loads(args.config.read_text(encoding='utf-8-sig'))
            private=Path(config['private_root']).resolve(strict=True)
            if config.get('prototype_only') is True and private.is_relative_to(ROOT/'data/private'):
                (private/'last-result.local.json').write_text(json.dumps(failure,indent=2),encoding='utf-8')
        except Exception:
            pass  # Reporting must not expose credentials or mask the original failure.
        print(json.dumps(failure))
        return 2


if __name__=='__main__':raise SystemExit(main())
