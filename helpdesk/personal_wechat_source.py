"""Account/room-scoped local source, no GUI, models, sending or business writes.

Use complete bounded snapshots, not upstream's lossy seq-only Listener. Late rows
and equal sort_seq remain visible. The existing SyncEngine commits each cursor
with its messages. Missing/changed anchors stop rather than skipping forward.
"""
import base64
from hashlib import sha256
import json
import re

from .message_sources import MessageBatch,NormalizedMessage,SyncMode,CursorExpired,normalize_sent_time


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):return sha256(canonical(value).encode()).hexdigest()


def source_fact_digest(raw):
    # Real client verification: opening the original updates download_status.
    # Keep its initial raw value as evidence, but it is not message content.
    return digest({k:v for k,v in raw.items() if k!='download_status'})


def encode_raw(value):
    if isinstance(value,bytes):return {'base64':base64.b64encode(value).decode('ascii')}
    if isinstance(value,dict):return {k:encode_raw(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [encode_raw(v) for v in value]
    return value


class PersonalWeChatSource:
    def __init__(self, reader, *, account_id, rooms, room_id, max_rows=10000, business_timezone='Asia/Shanghai',test_room_ids=(),live_seed_cursor=None):
        if (not isinstance(account_id,str) or not account_id or not isinstance(rooms,dict) or len(rooms) not in (3,4)
                or room_id not in rooms or any(not isinstance(k,str) or not re.fullmatch(r'[A-Za-z0-9_-]+@chatroom',k)
                    or not isinstance(v,str) or not v.strip() for k,v in rooms.items())
                or type(max_rows) is not int or not 1<=max_rows<=100000):
            raise ValueError('THREE_CONFIRMED_ROOMS_AND_OPTIONAL_AUTHORIZED_TEST_ROOM_REQUIRED')
        if (not isinstance(test_room_ids,(list,tuple,set,frozenset)) or len(test_room_ids)>1
                or not set(test_room_ids).issubset(rooms) or (len(rooms)==4 and len(test_room_ids)!=1)):
            raise ValueError('EXPLICIT_TEST_ROOM_REQUIRED_FOR_FOURTH_ROOM')
        self.reader,self.account,self.rooms,self.room=reader,account_id,dict(rooms),room_id
        self.max_rows,self.timezone=max_rows,business_timezone
        self.test_rooms=frozenset(test_room_ids)
        self.live_seed_cursor=live_seed_cursor
        self.scope=digest([account_id,room_id])
        self.source_name='personal_wechat_local:'+self.scope

    def _cursor(self,cursor,mode):
        if cursor is None:return {'seen':{},'shards':{}}
        try:
            if not isinstance(cursor,str) or len(cursor)>30_000_000:raise ValueError()
            value=json.loads(cursor)
            if (type(value.get('version')) is not int or value.get('version') not in (1,2) or value.get('scope')!=self.scope or value.get('mode')!=str(mode)
                    or not isinstance(value.get('seen'),dict) or len(value['seen'])>self.max_rows
                    or not isinstance(value.get('shards'),dict)
                    or any(not re.fullmatch('[0-9a-f]{64}',k) or not isinstance(v,str)
                           or not re.fullmatch('[0-9a-f]{64}',v) for k,v in value['seen'].items())):
                raise ValueError()
            return value
        except (ValueError,TypeError,AttributeError):raise CursorExpired('PERSONAL_WECHAT_CURSOR_REQUIRES_REVIEW') from None

    def _record(self,row):
        for key in ('local_id','local_type','sort_seq'):
            if type(row.get(key)) is not int or row[key]<0:
                raise CursorExpired('PERSONAL_WECHAT_LOCATOR_INCOMPLETE')
        if not isinstance(row.get('shard'),str) or not re.fullmatch(r'message_\d+\.db',row['shard']):
            raise CursorExpired('PERSONAL_WECHAT_SHARD_UNVERIFIED')
        locator=[self.account,self.room,row['shard'],row['local_id'],row['local_type'],row['sort_seq'],str(row.get('server_id') or '')]
        identity='personal-wechat-local:'+digest(locator)
        sender=row.get('sender_username') or ''
        sender_verified=isinstance(sender,str) and bool(sender.strip())
        sender_id='personal-wechat-sender:'+digest([self.account,sender]) if sender_verified else 'unverified:'+digest(locator)
        utc=local=None
        raw_time=row.get('create_time')
        if type(raw_time) is int and raw_time>0:
            try:utc,local=normalize_sent_time(raw_time,unit='seconds',business_timezone=self.timezone)
            except (ValueError,OverflowError,OSError):pass
        kind={1:'text',3:'image',34:'voice',43:'video',49:'file'}.get(row['local_type']&0xffffffff,'other')
        raw=encode_raw(row['raw_row'])
        body=row.get('content','')
        if not isinstance(body,str):raise CursorExpired('PERSONAL_WECHAT_TEXT_DECODE_UNVERIFIED')
        payload={'account_id':self.account,'room_username':self.room,'sender_username':sender,
            'identity_verified':sender_verified,'is_self':sender==self.account,'raw_row':raw,
            'locator':locator,'provenance':'local_sqlcipher_read_only',
            'production_use_allowed':False,'automatic_reply_allowed':False}
        if self.room in self.test_rooms:payload['is_test_data']=True
        message=NormalizedMessage(source_type='personal_wechat_local',message_id=identity,source_message_id=identity,
            source_seq=canonical(locator[2:]),room_id='personal-wechat-room:'+self.scope,room_name=self.rooms[self.room],
            sender_id=sender_id,sender_display_name=row.get('sender_display_name',''),message_type=kind,
            raw_content=body,normalized_text=body,sent_at_raw=raw_time,sent_at_utc=utc,sent_at_local=local,
            media_id=identity if kind!='text' else None,raw_payload=payload,
            source_confidence='high' if sender_verified else 'low',time_confidence='high' if utc else 'low',
            parse_status='parsed' if sender_verified and utc else 'pending_identity_verification',
            fingerprint=digest([locator,raw]),business_timezone=self.timezone)
        return digest(locator),source_fact_digest(raw),message

    def upgrade_cursor(self,cursor,mode,store):
        """Verify old raw anchors against stored evidence before v1 -> v2.

        Caller must back up the store and commit with an old-cursor comparison.
        Never remove an anchor or mark an unseen row as already collected.
        """
        mode=SyncMode(mode);previous=self._cursor(cursor,mode)
        if previous.get('version')!=1 or previous.get('baseline_complete') is not True or self.max_rows>10000:
            raise CursorExpired('LEGACY_CURSOR_UPGRADE_NOT_APPLICABLE')
        batch=self.fetch_page(None,SyncMode.BACKFILL,self.max_rows)
        current=json.loads(batch.next_cursor)
        if batch.has_more:raise CursorExpired('LEGACY_CURSOR_SNAPSHOT_INCOMPLETE')
        if any(current['shards'].get(k)!=v for k,v in previous['shards'].items()):
            raise CursorExpired('LEGACY_CURSOR_SHARD_CHANGED')
        seen={}
        for key,checksum in previous['seen'].items():
            saved=store.get_message('personal-wechat-local:'+key)
            if not saved or saved['source_type']!='personal_wechat_local':
                raise CursorExpired('LEGACY_CURSOR_ORIGINAL_EVIDENCE_MISSING')
            payload=json.loads(saved['raw_payload'])
            if (payload.get('account_id')!=self.account or payload.get('room_username')!=self.room
                    or digest(payload.get('locator'))!=key or digest(payload.get('raw_row'))!=checksum):
                raise CursorExpired('LEGACY_CURSOR_ORIGINAL_EVIDENCE_CHANGED')
            expected=source_fact_digest(payload['raw_row'])
            if current['seen'].get(key)!=expected:
                raise CursorExpired('LEGACY_CURSOR_MESSAGE_CHANGED_OR_MISSING')
            seen[key]=expected
        return canonical({**current,'seen':seen,'mode':str(mode)})

    def fetch_page(self,cursor,mode,page_size):
        mode=SyncMode(mode)
        if type(page_size) is not int or not 1<=page_size<=10000:raise ValueError('INVALID_PAGE_SIZE')
        previous=self._cursor(cursor,mode)
        if cursor is None and mode==SyncMode.LIVE:
            if self.live_seed_cursor is None:raise CursorExpired('COMMITTED_BACKFILL_REQUIRED_BEFORE_LIVE')
            previous=self._cursor(self.live_seed_cursor,SyncMode.BACKFILL)
            if previous.get('baseline_complete') is not True:
                raise CursorExpired('BACKFILL_BASELINE_NOT_COMPLETE')
        if previous.get('version')==1:
            raise CursorExpired('LEGACY_CURSOR_REQUIRES_VERIFIED_UPGRADE')
        snapshot=self.reader.snapshot(self.room,self.max_rows)
        if (snapshot.get('account_id')!=self.account or snapshot.get('room_id')!=self.room
                or snapshot.get('complete') is not True or not isinstance(snapshot.get('shards'),dict)):
            raise CursorExpired('PERSONAL_WECHAT_SNAPSHOT_NOT_COMPLETE_OR_WRONG_ACCOUNT')
        rows=snapshot.get('rows')
        if not isinstance(rows,list) or len(rows)>self.max_rows:raise CursorExpired('PERSONAL_WECHAT_SNAPSHOT_LIMIT')
        for shard,generation in previous['shards'].items():
            if snapshot['shards'].get(shard)!=generation:raise CursorExpired('PERSONAL_WECHAT_SHARD_CHANGED_OR_REMOVED')
        current={}
        try:prepared=[(row,*self._record(row)) for row in rows]
        except (KeyError,TypeError,AttributeError,ValueError):raise CursorExpired('PERSONAL_WECHAT_ROW_REQUIRES_REVIEW') from None
        for row,key,checksum,message in sorted(prepared,key=lambda item:(item[0]['sort_seq'],item[0]['shard'],item[0]['local_id'],item[0]['local_type'])):
            if row['shard'] not in snapshot['shards'] or key in current:raise CursorExpired('PERSONAL_WECHAT_DUPLICATE_LOCATOR')
            current[key]=(checksum,message)
        for key,checksum in previous['seen'].items():
            if key not in current or current[key][0]!=checksum:raise CursorExpired('PERSONAL_WECHAT_ANCHOR_CHANGED_OR_MISSING')
        # Seed LIVE only from committed complete BACKFILL, so a message arriving
        # between history import and listening cannot disappear into a baseline.
        unseen=[(k,v) for k,v in current.items() if k not in previous['seen']]
        selected=unseen[:page_size];more=len(unseen)>page_size
        seen=dict(previous['seen']);seen.update({k:v[0] for k,v in selected})
        next_cursor=canonical({'version':2,'scope':self.scope,'mode':str(mode),'seen':seen,
                               'shards':snapshot['shards'],'baseline_complete':not more})
        return MessageBatch(tuple(v[1] for _,v in selected),next_cursor,more)
