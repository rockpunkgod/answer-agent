"""Anonymous snapshots and existing collector transactions; no real account."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch

from helpdesk.personal_wechat_source import PersonalWeChatSource,digest,canonical
from helpdesk.personal_wechat_replica import ReplicaSnapshotReader,load_read_modules
from helpdesk.message_sources import SyncMode,CursorExpired,NormalizedMessage
from helpdesk.message_sync import SyncEngine
from helpdesk.collector_storage import CollectorStore
from helpdesk.collector_dispatch import CollectorDispatcher
from helpdesk.storage import Store
from helpdesk.service import Helpdesk

ROOMS={'100@chatroom':'English A','200@chatroom':'English B','300@chatroom':'English C'}


def row(local_id=1,*,shard='message_0.db',seq=10,kind=1,text='老师这题为什么选B？',sender='wxid_student',timestamp=1792509000):
    raw={'local_id':local_id,'local_type':kind,'sort_seq':seq,'server_id':seq+100,'message_content':text,
         'real_sender_id':2,'create_time':timestamp,'packed_info_data':b'\x00\xff'}
    return dict(local_id=local_id,local_type=kind,sort_seq=seq,server_id=seq+100,shard=shard,
                content=text,sender_username=sender,sender_display_name='匿名学生',create_time=timestamp,raw_row=raw)


class Reader:
    def __init__(self):
        self.value={'account_id':'wxid_self','room_id':'100@chatroom','complete':True,
                    'shards':{'message_0.db':'g0','message_1.db':'g1'},'rows':[row()]}
    def snapshot(self,*args):return deepcopy(self.value)


class PersonalSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=CollectorStore(self.root/'collector.sqlite')
        self.reader=Reader();self.source=self.make();self.engine=SyncEngine(self.store,self.source,page_size=1)
    def make(self,**kw):
        return PersonalWeChatSource(self.reader,**(dict(account_id='wxid_self',rooms=ROOMS,room_id='100@chatroom')|kw))
    def test_explicit_new_source_requires_stable_id_and_three_rooms(self):
        item=self.source.fetch_page(None,SyncMode.BACKFILL,10).messages[0]
        self.assertEqual(item.source_type,'personal_wechat_local')
        with self.assertRaises(ValueError):replace(item,source_message_id=None)
        for changes in ({'rooms':{}},{'room_id':'outsider@chatroom'},{'rooms':{'../a@chatroom':'A','b@chatroom':'B','c@chatroom':'C'}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.make(**changes)
    def test_local_id_type_seq_shard_are_not_global_identity(self):
        self.reader.value['rows']=[row(),row(shard='message_1.db'),row(kind=3),row(seq=11)]
        result=self.engine.sync(SyncMode.BACKFILL)
        self.assertEqual(result.inserted_count,4)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT message_id) FROM messages').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT SUM(auto_reply_allowed) FROM events').fetchone()[0],0)
    def test_repeated_restart_and_replay_use_existing_deduplication(self):
        batch=self.source.fetch_page(None,SyncMode.BACKFILL,10)
        self.engine.sync(SyncMode.BACKFILL)
        self.assertEqual(SyncEngine(self.store,self.make()).sync(SyncMode.BACKFILL).inserted_count,0)
        replay=self.store.persist_batch('explicit-anonymous-replay',SyncMode.BACKFILL,batch,None)
        self.assertEqual(replay.duplicate_count,1)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)

    def test_downloading_original_changes_status_without_changing_message_facts(self):
        self.reader.value['rows'][0]['raw_row']['download_status']=0
        self.engine.sync(SyncMode.BACKFILL)
        before=self.store.get_message(self.source.fetch_page(None,SyncMode.BACKFILL,10).messages[0].message_id)
        self.reader.value['rows'][0]['raw_row']['download_status']=2
        self.assertEqual(self.engine.sync(SyncMode.BACKFILL).inserted_count,0)
        after=self.store.get_message(before['message_id'])
        self.assertEqual(after['raw_payload'],before['raw_payload'])
        self.reader.value['rows'][0]['raw_row']['message_content']='changed question'
        with self.assertRaises(CursorExpired):self.engine.sync(SyncMode.BACKFILL)

    def test_legacy_cursor_upgrade_requires_original_proof_and_keeps_unseen_rows_unseen(self):
        self.reader.value['rows'][0]['raw_row']['download_status']=0
        batch=self.source.fetch_page(None,SyncMode.BACKFILL,10)
        self.engine.sync(SyncMode.BACKFILL)
        cursor=json.loads(batch.next_cursor);cursor['version']=1
        key=next(iter(cursor['seen']))
        cursor['seen'][key]=digest(batch.messages[0].raw_payload['raw_row'])
        legacy=canonical(cursor)
        with self.assertRaisesRegex(CursorExpired,'VERIFIED_UPGRADE'):
            self.source.fetch_page(legacy,SyncMode.BACKFILL,10)
        self.reader.value['rows'][0]['raw_row']['download_status']=2
        self.reader.value['rows'].append(row(2,seq=9))
        upgraded=self.source.upgrade_cursor(legacy,SyncMode.BACKFILL,self.store)
        self.assertEqual(json.loads(upgraded)['version'],2)
        self.assertEqual(set(json.loads(upgraded)['seen']),{key})
        self.assertEqual(len(self.source.fetch_page(upgraded,SyncMode.BACKFILL,10).messages),1)
        self.reader.value['rows'][0]['raw_row']['message_content']='substantive correction'
        with self.assertRaisesRegex(CursorExpired,'MESSAGE_CHANGED'):
            self.source.upgrade_cursor(legacy,SyncMode.BACKFILL,self.store)

    def test_legacy_cursor_upgrade_rejects_tampered_original_evidence(self):
        batch=self.source.fetch_page(None,SyncMode.BACKFILL,10)
        self.engine.sync(SyncMode.BACKFILL)
        cursor=json.loads(batch.next_cursor);cursor['version']=1
        cursor['seen']={key:digest(batch.messages[0].raw_payload['raw_row']) for key in cursor['seen']}
        with self.store.connect() as db:
            data=dict(batch.messages[0].raw_payload);data['raw_row']={'fake':True}
            db.execute('UPDATE messages SET raw_payload=?',(json.dumps(data),))
        with self.assertRaisesRegex(CursorExpired,'EVIDENCE_CHANGED'):
            self.source.upgrade_cursor(canonical(cursor),SyncMode.BACKFILL,self.store)
    def test_initial_live_view_is_history_and_late_equal_seq_arrivals_survive(self):
        self.engine.sync(SyncMode.BACKFILL)
        self.source.live_seed_cursor=self.store.get_sync_state(self.source.source_name,SyncMode.BACKFILL)['cursor']
        self.assertEqual(self.engine.sync(SyncMode.LIVE).inserted_count,0)
        self.reader.value['rows'] += [row(2,seq=10),row(3,seq=1)]
        result=self.engine.sync(SyncMode.LIVE)
        self.assertEqual(result.inserted_count,2)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT SUM(auto_reply_allowed) FROM events').fetchone()[0],0)
    def test_first_live_cannot_silently_discard_rows_arriving_after_backfill(self):
        self.engine.sync(SyncMode.BACKFILL)
        seed=self.store.get_sync_state(self.source.source_name,SyncMode.BACKFILL)['cursor']
        self.reader.value['rows'].append(row(2,seq=11))
        source=self.make(live_seed_cursor=seed)
        self.assertEqual(SyncEngine(self.store,source).sync(SyncMode.LIVE).inserted_count,1)
    def test_live_without_committed_complete_backfill_stops(self):
        with self.assertRaises(CursorExpired):self.source.fetch_page(None,SyncMode.LIVE,10)
        self.reader.value['rows'].append(row(2))
        partial=self.source.fetch_page(None,SyncMode.BACKFILL,1).next_cursor
        with self.assertRaises(CursorExpired):self.make(live_seed_cursor=partial).fetch_page(None,SyncMode.LIVE,10)
    def test_fourth_test_room_is_explicit_and_marked(self):
        rooms=dict(ROOMS,**{'400@chatroom':'测试群'})
        with self.assertRaises(ValueError):self.make(rooms=rooms,room_id='400@chatroom')
        self.reader.value['room_id']='400@chatroom'
        message=self.make(rooms=rooms,room_id='400@chatroom',test_room_ids=['400@chatroom']).fetch_page(None,SyncMode.BACKFILL,10).messages[0]
        self.assertTrue(message.raw_payload['is_test_data'])
    def test_source_timestamp_original_bytes_and_ingestion_are_separate(self):
        message=self.source.fetch_page(None,SyncMode.BACKFILL,10).messages[0]
        self.assertEqual(message.sent_at_raw,1792509000)
        self.assertNotEqual(message.ingested_at,message.sent_at_utc)
        self.assertEqual(message.raw_payload['raw_row']['packed_info_data'],{'base64':'AP8='})
        self.assertEqual(message.raw_payload['sender_username'],'wxid_student')
    def test_missing_sender_time_stays_pending_without_guessing(self):
        self.reader.value['rows']=[row(sender='',timestamp=0)]
        message=self.source.fetch_page(None,SyncMode.BACKFILL,10).messages[0]
        self.assertEqual(message.source_confidence,'low');self.assertEqual(message.time_confidence,'low')
        self.assertIsNone(message.sent_at_utc);self.assertTrue(message.sender_id.startswith('unverified:'))
    def test_storage_failure_rolls_back_messages_and_cursor_then_recovers(self):
        original=self.store.persist_batch
        def fail(name,mode,batch,cursor):
            return original(name,mode,batch,cursor,before_cursor=lambda db:(_ for _ in ()).throw(sqlite3.OperationalError('synthetic disk error')))
        with patch.object(self.store,'persist_batch',side_effect=fail),self.assertRaises(sqlite3.OperationalError):
            self.engine.sync(SyncMode.BACKFILL)
        self.assertIsNone(self.store.get_sync_state(self.source.source_name,SyncMode.BACKFILL)['cursor'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        self.assertEqual(SyncEngine(self.store,self.make()).sync(SyncMode.BACKFILL).inserted_count,1)
    def test_missing_changed_or_replaced_anchors_suspend_instead_of_skipping(self):
        for change in ('removed_row','changed_row','shard_generation','removed_shard'):
            with self.subTest(change=change):
                self.setUp();self.engine.sync(SyncMode.BACKFILL)
                cursor=self.store.get_sync_state(self.source.source_name,SyncMode.BACKFILL)['cursor']
                if change=='removed_row':self.reader.value['rows']=[]
                if change=='changed_row':self.reader.value['rows'][0]['raw_row']['message_content']='changed'
                if change=='shard_generation':self.reader.value['shards']['message_0.db']='changed'
                if change=='removed_shard':del self.reader.value['shards']['message_0.db']
                with self.assertRaises(CursorExpired):self.engine.sync(SyncMode.BACKFILL)
                state=self.store.get_sync_state(self.source.source_name,SyncMode.BACKFILL)
                self.assertEqual(state['cursor'],cursor);self.assertEqual(state['status'],'NEEDS_REVIEW')
                with self.assertRaises(CursorExpired):self.engine.sync(SyncMode.BACKFILL)
    def test_new_shard_is_included_without_resetting_cursor(self):
        self.engine.sync(SyncMode.BACKFILL)
        self.reader.value['shards']['message_2.db']='g2'
        self.reader.value['rows'].append(row(shard='message_2.db',seq=10))
        self.assertEqual(self.engine.sync(SyncMode.BACKFILL).inserted_count,1)
    def test_room_account_modes_and_cursors_do_not_mix(self):
        cursor=self.source.fetch_page(None,SyncMode.BACKFILL,10).next_cursor
        for source,mode in ((self.make(account_id='another'),SyncMode.BACKFILL),
                            (self.make(room_id='200@chatroom'),SyncMode.BACKFILL),(self.source,SyncMode.LIVE)):
            with self.subTest(mode=mode),self.assertRaises(CursorExpired):source.fetch_page(cursor,mode,10)
    def test_incomplete_snapshot_or_oversize_is_never_empty_success(self):
        for change in ('incomplete','wrong_account','missing_seq','too_many'):
            with self.subTest(change=change):
                self.setUp()
                if change=='incomplete':self.reader.value['complete']=False
                if change=='wrong_account':self.reader.value['account_id']='other'
                if change=='missing_seq':del self.reader.value['rows'][0]['sort_seq']
                if change=='too_many':self.source.max_rows=1;self.reader.value['rows'].append(row(2))
                with self.assertRaises(CursorExpired):self.engine.sync(SyncMode.BACKFILL)
    def test_dispatcher_cannot_promote_personal_messages_even_if_binding_is_verified(self):
        message=self.source.fetch_page(None,SyncMode.BACKFILL,10).messages[0]
        self.engine.sync(SyncMode.BACKFILL)
        with closing(Store(self.root/'synthetic-business.sqlite')) as business:
            Helpdesk(business).bind(message.room_id,message.sender_id,'匿名',verified=True)
            for mode in ('ACK_ONLY','CASE_RESOLUTION'):
                dispatcher=CollectorDispatcher(self.store,business,processing_mode=mode)
                dispatcher.enqueue(message,'LIVE',auto_reply_allowed=True)
            self.assertEqual(business.one('SELECT COUNT(*) FROM outbox')[0],0)
            self.assertEqual(business.one('SELECT COUNT(*) FROM messages')[0],0)
            self.assertEqual(business.one('SELECT COUNT(*) FROM performance_units')[0],0)


class ReplicaSafetyTests(unittest.TestCase):
    def test_sqlite_reader_resolves_same_sender_number_in_each_own_shard(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);db=Mock();db._message_dbs.return_value=['message_0.db','message_1.db']
            table='Msg_'+hashlib.md5(b'100@chatroom').hexdigest()
            for index in range(2):
                with closing(sqlite3.connect(root/('message_'+str(index)+'.db'))) as conn:
                    conn.execute('CREATE TABLE Name2Id(user_name TEXT)')
                    conn.execute('INSERT INTO Name2Id(rowid,user_name) VALUES(2,?)',('student_'+str(index),))
                    conn.execute('CREATE TABLE "'+table+'"(local_id,local_type,sort_seq,server_id,real_sender_id,create_time,message_content)')
                    conn.execute('INSERT INTO "'+table+'" VALUES(1,1,10,110,2,1792509000,?)',('匿名题目',))
                    conn.commit()
            db._db_path.side_effect=lambda rel:str(root/rel)
            def open_db(rel):
                conn=sqlite3.connect((root/rel).as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
                return conn
            db._open.side_effect=open_db
            db._name2id_map.side_effect=lambda conn:dict(conn.execute('SELECT rowid,user_name FROM Name2Id'))
            db.get_nickname.side_effect=lambda sender:'匿名'
            reader=ReplicaSnapshotReader(db,account_id='wxid_self',rooms=ROOMS)
            batch=PersonalWeChatSource(reader,account_id='wxid_self',rooms=ROOMS,room_id='100@chatroom').fetch_page(None,SyncMode.BACKFILL,10)
            self.assertEqual(len(batch.messages),2)
            self.assertEqual({m.raw_payload['sender_username'] for m in batch.messages},{'student_0','student_1'})
            self.assertEqual(len({m.sender_id for m in batch.messages}),2)

    def test_changed_upstream_content_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'wechatauto').mkdir();(root/'wechatauto/db.py').write_text('raise RuntimeError("must not execute")')
            with self.assertRaisesRegex(ValueError,'UPSTREAM_CONTENT_CHANGED'):load_read_modules(root)
    def test_media_keeps_exact_shard_and_source_binding_and_allowed_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);db=Mock();db.get_message_row.return_value={'sort_seq':10,'server_id':110}
            db._message_dbs.return_value=['message_0.db'];db._shard_label.return_value='message__message_0.db'
            reader=ReplicaSnapshotReader(db,account_id='wxid_self',rooms=ROOMS)
            value=Reader();value.value['rows']=[row(kind=3)]
            message=PersonalWeChatSource(value,account_id='wxid_self',rooms=ROOMS,room_id='100@chatroom').fetch_page(None,SyncMode.BACKFILL,10).messages[0]
            media=Mock();media.download_image.return_value=None
            media.image_status.return_value={'tiers':{},'available':False,'reason':'no_local_copy'}
            self.assertEqual(reader.original_image(message,media,root)['status'],'ORIGINAL_NOT_LOCAL')
            db.get_message_row.assert_called_with('100@chatroom',1,local_type=3,shard='message__message_0.db')
            media.download_image.assert_not_called()
            from hashlib import sha256
            def local_image(*args,**kwargs):
                file=Path(kwargs['save_dir'])/'anonymous-fixture.jpg'
                file.write_bytes(b'anonymous simulated media bytes')
                return str(file)
            media.download_image.side_effect=local_image
            media.image_status.return_value={'tiers':{'original':3000,'mid':1000},'available':True,'reason':'ok'}
            saved=reader.original_image(message,media,root)
            self.assertEqual(media.download_image.call_args.kwargs['tier'],'original')
            self.assertEqual(media.download_image.call_args.kwargs['shard'],'message__message_0.db')
            self.assertEqual(saved['message_id'],message.message_id)
            self.assertEqual(saved['sha256'],sha256(Path(saved['path']).read_bytes()).hexdigest())
            derivative=reader.read_image(message,media,root,tier='mid')
            self.assertFalse(derivative['original_attachment']);self.assertEqual(derivative['status'],'LOCAL_DERIVATIVE_READ')
            with tempfile.NamedTemporaryFile(delete=False) as stream:
                stream.write(b'anonymous');outside=Path(stream.name)
            self.addCleanup(outside.unlink)
            media.download_image.side_effect=lambda *a,**k:str(outside)
            with self.assertRaises(ValueError):reader.original_image(message,media,root)
            db.get_message_row.return_value['sort_seq']=99
            with self.assertRaises(ValueError):reader.original_image(message,media,root)
