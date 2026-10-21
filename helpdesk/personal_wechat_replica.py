"""Opt-in replica DB/media access, only inside an isolated read-only worker.

Avoid package __init__: it imports GUI, sending and interactive message models.
Upstream remains unmodified; these are explicit answer-agent restrictions.
"""
from hashlib import sha256
import importlib.util
import importlib.abc
from pathlib import Path
import sys
import types

UPSTREAM_COMMIT='d322c4a9b1124228820bab363e5676016f188fc6'
HASHES={'db.py':'9d4eba69e868dea8000d8acdbaed723149cf8732c52e097f23fb3b07fa5ddc55',
        'media.py':'ef2c2943e0b9f3a707900ee7eb08b58839a1d1e1d8eddf87a7942869bc4da221'}


def verify_local_image(file):
    """Decode the saved copy before treating cached media as usable evidence."""
    import warnings
    from PIL import Image
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(file) as image:
                image.verify()
            # JPEG verify() alone does not decode scan data or detect truncation.
            with Image.open(file) as image:
                image.load()
                return {'format':image.format,'width':image.width,'height':image.height}
    except (OSError,ValueError,SyntaxError,Image.DecompressionBombWarning,Image.DecompressionBombError):
        return None


class StrictReadLogger:
    def debug(self,*args,**kwargs):pass
    def info(self,*args,**kwargs):pass
    def warning(self,*args,**kwargs):
        # Upstream can fall back to stale main DB or omit a broken shard.
        raise RuntimeError('UPSTREAM_READ_WARNING_REQUIRES_REVIEW')
    error=warning


def load_read_modules(upstream):
    root=Path(upstream).resolve(strict=True)
    if any(name=='wechatauto' or name.startswith('wechatauto.') for name in sys.modules):
        raise RuntimeError('FRESH_ISOLATED_REPLICA_PROCESS_REQUIRED')
    for filename,digest in HASHES.items():
        if sha256((root/'wechatauto'/filename).read_bytes()).hexdigest()!=digest:
            raise ValueError('UPSTREAM_CONTENT_CHANGED')
    package=types.ModuleType('wechatauto');package.__path__=[str(root/'wechatauto')]
    sys.modules['wechatauto']=package
    logger=types.ModuleType('wechatauto.logger');logger.wxlog=StrictReadLogger()
    sys.modules['wechatauto.logger']=logger
    class ReadOnlyImports(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if fullname.startswith('wechatauto.') and fullname not in ('wechatauto.db','wechatauto.media','wechatauto.logger'):
                raise ImportError('INTERACTIVE_REPLICA_MODULE_FORBIDDEN')
            return None
    sys.meta_path.insert(0,ReadOnlyImports())
    modules=[]
    for name in ('db','media'):
        spec=importlib.util.spec_from_file_location('wechatauto.'+name,root/'wechatauto'/(name+'.py'))
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
        spec.loader.exec_module(module);setattr(package,name,module);modules.append(module)
    return tuple(modules)


def strict_database(module, *, db_root, account, cache, pid):
    """Pin one account/PID; do not allow upstream account self-healing."""
    root=Path(db_root).resolve(strict=True);work=Path(cache).resolve(strict=True)
    if Path(account).name!=account or not (root/account/'db_storage').is_dir():
        raise ValueError('EXPLICIT_ACCOUNT_DIRECTORY_REQUIRED')
    class PinnedDB(module.WeChatDB):
        def _find_weixin_pids(self):return [pid]
        def _try_other_accounts(self):return False
        def _select_account_by_keys(self,*args):return False
        def _all_key_candidates(self):return set()
        def _stable_key_dirs(self):return [str(work/'keys')]
    module._find_account_dirs=lambda directory:[str(root/account)]
    db=PinnedDB(db_dir=str(root),account=account,workdir=str(work),keys_file=str(work/'keys.json'))
    if db.account!=account:
        raise RuntimeError('ACCOUNT_CHANGED')
    return db


class ReplicaSnapshotReader:
    """Strict SELECTs over every shard; never use upstream skip-on-error merges."""
    def __init__(self,db,*,account_id,rooms,guard=lambda:None):
        self.db,self.account,self.rooms,self.guard=db,account_id,dict(rooms),guard

    def snapshot(self,room_id,max_rows):
        import hashlib
        import re
        from .personal_wechat_source import CursorExpired
        if room_id not in self.rooms:raise ValueError('ROOM_OUTSIDE_READ_ALLOWLIST')
        self.guard()
        shards={};rows=[]
        table='Msg_'+hashlib.md5(room_id.encode()).hexdigest()
        available=self.db._message_dbs()
        if not available:raise CursorExpired('MESSAGE_SHARDS_UNAVAILABLE')
        for rel in available:
            shard=Path(rel).name
            if not re.fullmatch(r'message_\d+\.db',shard):raise CursorExpired('UNKNOWN_MESSAGE_SHARD')
            original=Path(self.db._db_path(rel))
            with original.open('rb') as stream:generation=sha256(stream.read(16)).hexdigest()
            shards[shard]=generation
            connection=self.db._open(rel)
            try:
                connection.execute('PRAGMA query_only=ON')
                exists=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
                if not exists:continue
                mapping=self.db._name2id_map(connection)
                raw_rows=connection.execute('SELECT * FROM "'+table+'"').fetchmany(max_rows-len(rows)+1)
                if len(raw_rows)+len(rows)>max_rows:raise CursorExpired('PERSONAL_WECHAT_SNAPSHOT_LIMIT')
                for raw_row in raw_rows:
                    raw=dict(raw_row)
                    sender=mapping.get(raw.get('real_sender_id'),'') or ''
                    kind=self.db._msg_type_name(raw.get('local_type'))
                    content=raw.get('message_content')
                    if isinstance(content,bytes):content=self.db._friendly_content(content,kind)
                    if not content and isinstance(raw.get('compress_content'),bytes):
                        content=self.db._friendly_content(raw['compress_content'],kind)
                    rows.append(dict(local_id=raw.get('local_id'),local_type=raw.get('local_type'),
                        sort_seq=raw.get('sort_seq'),server_id=raw.get('server_id'),shard=shard,
                        sender_username=sender,sender_display_name=self.db.get_nickname(sender) if sender else '',
                        create_time=raw.get('create_time'),content=content or '',raw_row=raw))
            finally:connection.close()
            with original.open('rb') as stream:
                if sha256(stream.read(16)).hexdigest()!=generation:raise CursorExpired('SHARD_CHANGED_DURING_READ')
        self.guard()
        return {'account_id':self.account,'room_id':room_id,'complete':True,'shards':shards,'rows':rows}

    def original_image(self,message,media,approved_directory):
        return self.read_image(message,media,approved_directory,tier='original')

    def read_image(self,message,media,approved_directory,*,tier='original'):
        """Explicit local tier only; derivatives never become original evidence."""
        from .personal_wechat_source import digest
        if tier not in ('original','mid','thumb'):raise ValueError('EXPLICIT_LOCAL_IMAGE_TIER_REQUIRED')
        self.guard()
        payload=message.raw_payload;locator=payload['locator']
        if (payload['account_id']!=self.account or payload['room_username'] not in self.rooms
                or locator[:2]!=[self.account,payload['room_username']]
                or message.message_id!='personal-wechat-local:'+digest(locator)):
            raise ValueError('MEDIA_OUTSIDE_READ_ALLOWLIST')
        room=payload['room_username'];shard,local_id,kind,seq,server=locator[2:]
        # Source IDs use the physical shard name. Upstream media APIs instead
        # expect the decrypted cache basename (message__message_N.db).
        matches=[rel for rel in self.db._message_dbs() if Path(rel).name==shard]
        if len(matches)!=1:raise ValueError('MEDIA_SHARD_NOT_UNIQUE')
        connection=self.db._open(matches[0])
        try:api_shard=self.db._shard_label(connection)
        finally:connection.close()
        if not isinstance(api_shard,str) or not api_shard:raise ValueError('MEDIA_SHARD_ALIAS_UNVERIFIED')
        row=self.db.get_message_row(room,local_id,local_type=3,shard=api_shard)
        if (kind!=3 or not row or row['sort_seq']!=seq or str(row.get('server_id') or '')!=server):
            raise ValueError('MEDIA_MESSAGE_LOCATOR_CHANGED')
        state=media.image_status(room,local_id,verify=False,shard=api_shard)
        if not isinstance(state,dict) or not isinstance(state.get('tiers'),dict):
            raise ValueError('MEDIA_STATUS_NOT_VERIFIED')
        reason=state.get('reason')
        if reason in ('no_message_row','not_image','no_md5'):
            return {'status':'MEDIA_REFERENCE_REQUIRES_REVIEW','message_id':message.message_id,'reason':reason,'tier':tier}
        if tier not in state['tiers'] or (tier=='original' and state.get('available') is not True):
            return {'status':'ORIGINAL_NOT_LOCAL' if tier=='original' else 'REQUESTED_IMAGE_TIER_NOT_LOCAL',
                    'message_id':message.message_id,'tier':tier,'reason':reason}
        root=Path(approved_directory).resolve(strict=True)
        output=root/(digest(locator)+'-'+tier)
        if not output.resolve().is_relative_to(root):raise ValueError('MEDIA_PATH_NOT_ALLOWED')
        output.mkdir(exist_ok=True)
        path=media.download_image(room,local_id,save_dir=str(output),tier=tier,shard=api_shard)
        if path is None:return {'status':'MEDIA_READ_UNVERIFIED','message_id':message.message_id,'tier':tier}
        file=Path(path).resolve(strict=True)
        if not file.is_relative_to(root) or file.stat().st_size>25_000_000:
            raise ValueError('MEDIA_PATH_OR_SIZE_NOT_ALLOWED')
        image=verify_local_image(file)
        if image is None:
            return {'status':'MEDIA_READ_UNVERIFIED','message_id':message.message_id,'tier':tier,
                    'reason':'LOCAL_IMAGE_DECODE_FAILED','path':str(file),
                    'sha256':sha256(file.read_bytes()).hexdigest()}
        self.guard()
        return {'status':'LOCAL_ORIGINAL_READ' if tier=='original' else 'LOCAL_DERIVATIVE_READ',
                'message_id':message.message_id,'source_locator':locator,'tier':tier,'original_attachment':tier=='original',
                'path':str(file),'sha256':sha256(file.read_bytes()).hexdigest(),'size':file.stat().st_size,
                'image':image}
