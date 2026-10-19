"""Narrow WeCom business operations over the existing guarded desktop and ledger.

Selectors/points are local reviewed recipes, never MCP arguments. This module
does not infer a recipient, approve an answer, or enable an Outbox policy.
"""
from contextlib import closing, contextmanager
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import time

from .locking import resource_lock
from .mcp_test_delivery import MCPTestAnswerDesktop, verify_frame, pixel_hash, same_clipboard_body
from .mcp_window_probe import FOREGROUND_COMMAND, parse_foreground
from .storage import Store, now
from .workflow import Workflow
from .delivery import PreflightFailure

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = {
    'chat_search': ('open_chat', 'Edit', 'ValuePattern'),
    'chat_result': ('open_chat', 'ListItem', 'SelectionItemPattern'),
    'chat_header': ('get_chat_state', 'Text', 'TextPattern'),
    'message_list': ('get_messages', 'List', 'ItemContainerPattern'),
    'incoming_image': ('open_image', 'Image', 'InvokePattern'),
    'image_save': ('download_attachment', 'Button', 'InvokePattern'),
    'file_name': ('download_attachment', 'Edit', 'ValuePattern'),
    'file_save': ('download_attachment', 'Button', 'InvokePattern'),
    'reply_editor': ('prepare_reply', 'Edit', 'ValuePattern'),
    'send_button': ('send_reply', 'Button', 'InvokePattern'),
    'outgoing_bubble': ('send_reply', 'Text', 'TextPattern'),
    'copy_menu': ('get_messages/send_reply', 'MenuItem', 'InvokePattern'),
}


def probe_controls(root=ROOT):
    result = subprocess.run(['powershell.exe','-NoProfile','-File',str(root/'tools/probe_wecom_controls.ps1')],
        capture_output=True, timeout=20, shell=False,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('UIA_PROBE_FAILED')
    value = json.loads(result.stdout.decode('utf-8-sig'))
    if value.get('status') not in ('OK','UNAVAILABLE'):
        raise ValueError('UIA_PROBE_INVALID')
    return value


def control_inventory(probe):
    nodes = probe.get('controls', [])
    # A type alone never becomes a usable selector. Report evidence, not a
    # guessed AutomationId or automatic permission to invoke a discovered node.
    return {'status':probe['status'], 'window':probe.get('window'),
        'truncated':probe.get('truncated',False), 'uia_node_count':len(nodes),
        'controls':[{'control':key,'operation':spec[0],'expected_type':spec[1],
                     'required_pattern':spec[2], 'status':'NOT_REVIEWED' if any(
                         n.get('control_type')==spec[1] for n in nodes) else 'NOT_EXPOSED',
                     'selector':None} for key,spec in CONTROLS.items()]}


class PinnedDesktop(MCPTestAnswerDesktop):
    def _call(self, tool, **arguments):
        if tool == 'Screenshot':
            arguments={'display':[self.pin['display_index']],**arguments}
        return super()._call(tool,**arguments)


class WeComTools:
    def __init__(self, database, *, session=None, root=ROOT, transport_factory=None,
                 probe=probe_controls, allow_input=False, allow_send=False):
        self.root=Path(root).resolve()
        self.database=Path(database).resolve(strict=True)
        self.session=session or {}
        self.probe=probe
        self.allow_input=allow_input
        self.allow_send=allow_send
        self.backend=self.session.get('backend','NATIVE')
        if self.backend not in ('NATIVE','WINDOWS_MCP'):
            raise ValueError('UNKNOWN_WECOM_BACKEND')
        if session and (session.get('platform')!='wecom' or session.get('display_name')!='苇中鹤'
                        or not session.get('target_key') or not isinstance(session.get('pin'),dict)):
            raise ValueError('REVIEWED_TEST_CONTACT_SESSION_REQUIRED')
        if transport_factory is None:
            if self.backend=='NATIVE':
                from .wecom_native import NativeWeComTransport
                transport_factory=lambda: NativeWeComTransport(self._pin(),self.root,guard=self._require_input)
            else:
                from .mcp_transport import MCPProcess
                transport_factory=lambda: MCPProcess(root=self.root,bound_input_process='WXWork')
        self.transport_factory=transport_factory
        self.lock=self.root/'data/windows-interactive-desktop.lock'

    def _audit(self, action, status, *, controls=(), outbox=None, evidence=None):
        with closing(Store(self.database)) as db, db.transaction():
            Workflow(db)._event('WECOM_CONTROL_OPERATION',outbox=outbox,
                details={'action':action,'status':status,'controls':list(controls),
                         'backend':self.backend,'evidence':evidence or {},
                         'target_key':self.session.get('target_key'),'at':now()})

    def _pin(self):
        if not self.session:
            raise ValueError('SESSION_NOT_CONFIGURED')
        pin=dict(self.session['pin'])
        if (pin.get('target_key')!=self.session['target_key']
                or (pin.get('platform'),pin.get('display_name'))!=('wecom','苇中鹤')
                or type(pin.get('display_index')) is not int
                or time.time()>pin.get('expires_at',0)):
            raise ValueError('SESSION_PIN_EXPIRED_OR_CHANGED')
        return pin

    @contextmanager
    def _desktop(self):
        pin=self._pin()
        with resource_lock(self.lock), self.transport_factory() as transport:
            yield PinnedDesktop(transport,pin,self.root),pin

    def _frame(self, desktop, pin):
        before=desktop._call('PowerShell',command=FOREGROUND_COMMAND,timeout=10)
        native=parse_foreground(before)
        record=desktop._call('Screenshot')
        if native!=parse_foreground(desktop._call('PowerShell',command=FOREGROUND_COMMAND,timeout=10)):
            raise ValueError('WINDOW_CHANGED_DURING_OBSERVATION')
        frame=verify_frame(record,pin,foreground=native)
        return record,frame

    def inspect_controls(self):
        result=control_inventory(self.probe(self.root))
        self._audit('inspect_controls',result['status'],controls=CONTROLS,
                    evidence={'uia_node_count':result['uia_node_count'],'window':result['window']})
        return result

    def get_chat_state(self):
        with self._desktop() as (desktop,pin):
            _,frame=self._frame(desktop,pin)
        result={'status':'VERIFIED_CURRENT_CHAT','chat_id':self.session['target_key'],
                'display_name':self.session['display_name'],'editor_blank':frame['editor_blank'],
                'verification':'native WXWork identity + reviewed header/avatar/window frame',
                'permanent_platform_identity':False}
        self._audit('get_chat_state',result['status'],controls=['chat_header'],evidence={'frame_sha256':frame['sha256']})
        return result

    def list_chats(self):
        state=self.get_chat_state()
        return {'status':'OK','scope':'CURRENT_APPROVED_CHAT_ONLY',
                'chats':[{'chat_id':state['chat_id'],'display_name':state['display_name']}],
                'complete_chat_list':False}

    def open_chat(self, chat_id):
        if chat_id!=self.session.get('target_key'):
            raise ValueError('CHAT_OUTSIDE_ALLOWLIST')
        # No guessing/searching another conversation from a name-only match.
        try:
            state=self.get_chat_state()
        except (ValueError,PreflightFailure) as exc:
            if str(exc) not in ('WECOM_NOT_FOREGROUND','FOREGROUND_PROBE_FAILED') or self.backend!='NATIVE':raise
            self._require_input()
            with resource_lock(self.lock),self.transport_factory() as transport:
                transport.activate()
            state=self.get_chat_state()
        self._audit('open_chat','ALREADY_OPEN',controls=['chat_header'])
        return {**state,'status':'ALREADY_OPEN','navigation_performed':False}

    def _message(self, message_id):
        matches=[m for m in self.session.get('messages',[]) if m.get('id')==message_id]
        if len(matches)!=1:
            raise ValueError('MESSAGE_RECIPE_NOT_UNIQUE')
        return matches[0]

    def _require_input(self):
        if not self.allow_input:
            raise ValueError('INPUT_DISABLED')
        with closing(Store(self.database)) as db:
            if Workflow(db)._stopped():
                raise ValueError('STOPPED')

    def _checked_region(self,record,recipe):
        from PIL import Image
        path=next(c['path'] for c in record['content'] if c.get('type')=='image')
        with Image.open(path) as image:
            hashes=[recipe['region_hash']]
            # WeCom leaves copied text selected. Only an explicitly reviewed
            # alternate frame for the same exact text may be accepted.
            if recipe.get('selected_region_hash'):
                if not re.fullmatch(r'[0-9a-f]{64}',recipe.get('text_sha256','')):
                    raise ValueError('EXACT_TEXT_HASH_REQUIRED_FOR_SELECTION_VARIANT')
                hashes.append(recipe['selected_region_hash'])
            if pixel_hash(image,recipe['region_box']) not in hashes:
                raise ValueError('MESSAGE_LAYOUT_CHANGED_RECAPTURE_REQUIRED')

    def get_messages(self, chat_id):
        if chat_id!=self.session.get('target_key'):
            raise ValueError('CHAT_OUTSIDE_ALLOWLIST')
        recipes=[m for m in self.session.get('messages',[]) if m.get('kind')=='text']
        if not recipes:
            raise ValueError('MESSAGE_CONTROLS_NOT_EXPOSED_NO_REVIEWED_FALLBACK')
        self._require_input()
        messages=[]
        with self._desktop() as (desktop,pin):
            for recipe in recipes:
                record,_=self._frame(desktop,pin);self._checked_region(record,recipe)
                nonce='wecom-read-pending-'+str(time.time_ns())
                desktop._call('Clipboard',mode='set',text=nonce)
                desktop._call('Click',loc=recipe['point'],button='right')
                # Native mouse dispatch precedes menu rendering; one bounded
                # settle period, never a repeated click or message submission.
                time.sleep(.2)
                menu=desktop._call('Screenshot')
                self._checked_region(menu,recipe['copy_menu'])
                desktop._call('Click',loc=recipe['copy_menu']['point'])
                time.sleep(.15)
                result=desktop._call('Clipboard',mode='get')
                values=[c.get('text','') for c in result.get('content',[]) if c.get('type')=='text']
                if len(values)!=1 or not values[0].startswith('Clipboard content:\n'):
                    raise ValueError('MESSAGE_CLIPBOARD_UNCONFIRMED')
                text=values[0].removeprefix('Clipboard content:\n')
                if not text.strip() or text==nonce or (recipe.get('text_sha256') and sha256(text.encode()).hexdigest()!=recipe['text_sha256']):
                    raise ValueError('COPIED_MESSAGE_CHANGED')
                messages.append({'id':recipe['id'],'text':text,'platform_message_id':None,
                    'source_sent_at':None,'time_status':'REQUIRES_ORIGINAL_TIME_EVIDENCE',
                    'observed_at':now(),'evidence_attempt':result.get('attempt_id')})
        self._audit('get_messages','VISIBLE_RECORDS_READ',controls=['message_list','copy_menu'],
                    evidence={'count':len(messages)})
        return {'status':'OK','messages':messages,'scope':'REVIEWED_VISIBLE_RECORDS_ONLY',
                'continuous_listener':False,'complete_history':False}

    def open_image(self,message_id):
        recipe=self._message(message_id)
        if recipe.get('kind')!='image':
            raise ValueError('REVIEWED_IMAGE_REQUIRED')
        self._require_input()
        with self._desktop() as (desktop,pin):
            record,_=self._frame(desktop,pin);self._checked_region(record,recipe)
            result=desktop._call('Click',loc=recipe['point'],clicks=2)
        self._audit('open_image','INVOKED_UNVERIFIED',controls=['incoming_image'],
                    evidence={'attempt_id':result.get('attempt_id')})
        return {'status':'INVOKED_UNVERIFIED','message_id':message_id,'image_content_verified':False}

    def _bound_reply(self,db,desktop,pin,outbox_id):
        if Workflow(db)._stopped():
            raise ValueError('STOPPED')
        row=db.one('SELECT * FROM outbox WHERE id=?',(outbox_id,))
        if not row or row['purpose'] not in ('TEST_ANSWER','PROGRESS'):
            raise ValueError('AUTHORIZED_TEST_OUTBOX_REQUIRED')
        bound=Workflow(db,desktop=desktop)._bound(row)
        if (bound.group_key,bound.student_key)!=('wecom',self.session['target_key']):
            raise ValueError('OUTBOX_TARGET_MISMATCH')
        if db.one("SELECT 1 FROM outbox WHERE binding_id=? AND state IN ('SENDING','SEND_UNKNOWN')",(row['binding_id'],)):
            raise ValueError('PRIOR_SEND_REQUIRES_RECONCILIATION')
        desktop.pin.update(outbox_id=outbox_id,binding_id=bound.binding_id,body_hash=bound.body_hash)
        Workflow(db,desktop=desktop)._validate(row)
        return row,bound

    def prepare_reply(self,outbox_id):
        if not self.allow_input:
            raise ValueError('INPUT_DISABLED')
        with self._desktop() as (desktop,pin), closing(Store(self.database)) as db:
            row,bound=self._bound_reply(db,desktop,pin,outbox_id)
            if row['state']!='PENDING':
                raise ValueError('ONLY_PENDING_REPLY_CAN_BE_PREPARED')
            _,frame=self._frame(desktop,pin)
            if not frame['editor_blank']:
                raise ValueError('EXISTING_DRAFT_REQUIRES_RECONCILIATION')
            # Persist possible draft effects before the first input.
            self._audit('prepare_reply','PREPARING',controls=['reply_editor'],outbox=outbox_id,
                        evidence={'body_hash':bound.body_hash})
            desktop._call('Click',loc=pin['compose_point'])
            desktop._call('Clipboard',mode='set',text=bound.body)
            desktop._call('Shortcut',shortcut='ctrl+v')
            desktop._call('Shortcut',shortcut='ctrl+a');desktop._call('Shortcut',shortcut='ctrl+c')
            if not same_clipboard_body(desktop._clipboard_text(),bound.body):
                raise ValueError('PREPARED_DRAFT_READBACK_MISMATCH')
            desktop._call('Shortcut',shortcut='right');self._frame(desktop,pin)
            self._audit('prepare_reply','PREPARED_VERIFIED',controls=['reply_editor'],outbox=outbox_id,
                        evidence={'body_hash':bound.body_hash,'question_version':row['question_version']})
        return {'status':'PREPARED_VERIFIED','outbox_id':outbox_id,'sent':False}

    def send_reply(self,outbox_id):
        if not self.allow_send or not self.allow_input:
            raise ValueError('SEND_DISABLED')
        pin=self._pin()
        # Workflow owns the same desktop mutex for the entire send/reconcile.
        with closing(Store(self.database)) as db:
            row=db.one('SELECT * FROM outbox WHERE id=?',(outbox_id,))
            if not row:raise ValueError('UNKNOWN_OUTBOX')
            bound=Workflow(db)._bound(row)
            if row['purpose'] not in ('TEST_ANSWER','PROGRESS') or (bound.group_key,bound.student_key)!=('wecom',self.session['target_key']):
                raise ValueError('OUTBOX_TARGET_MISMATCH')
            if row['state']!='PENDING':
                return {'status':row['state'],'resubmitted':False,'outbox_id':outbox_id}
            events=db.all("SELECT details FROM audit WHERE event='WECOM_CONTROL_OPERATION' AND outbox_id=? ORDER BY id",(outbox_id,))
            prepared=[json.loads(r[0]) for r in events if json.loads(r[0]).get('action')=='prepare_reply']
            if not prepared or prepared[-1]['status']!='PREPARED_VERIFIED':
                raise ValueError('VERIFIED_PREPARED_REPLY_REQUIRED')
            if prepared[-1]['evidence']['body_hash']!=sha256(row['body'].encode()).hexdigest():
                raise ValueError('PREPARED_REPLY_CHANGED')
            with self.transport_factory() as transport:
                desktop=PinnedDesktop(transport,pin,self.root,resume_existing_draft=True)
                self._bound_reply(db,desktop,pin,outbox_id)
                state=Workflow(db,desktop=desktop).dispatch(outbox_id)
        self._audit('send_reply',state,controls=['reply_editor','send_button','outgoing_bubble','copy_menu'],outbox=outbox_id)
        return {'status':state,'outbox_id':outbox_id,'verified_delivery':state=='SENT_UI_CONFIRMED'}

    def download_attachment(self,message_id):
        # Only already saved originals are returned. A native Save As recipe
        # will be added after that dialog's current UIA/fallback is reviewed.
        recipe=self._message(message_id)
        configured=self.database.parent/'attachments'
        allowed=configured.resolve(strict=True)
        if configured!=allowed:
            raise ValueError('ATTACHMENT_DIRECTORY_REDIRECTED')
        path=(allowed/recipe.get('attachment_name','')).resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(allowed) or sha256(path.read_bytes()).hexdigest()!=recipe.get('attachment_sha256'):
            raise ValueError('VERIFIED_ORIGINAL_ATTACHMENT_UNAVAILABLE')
        self._audit('download_attachment','EXISTING_ORIGINAL_VERIFIED',controls=['image_save','file_name','file_save'])
        return {'status':'EXISTING_ORIGINAL_VERIFIED','message_id':message_id,'path':str(path),
                'sha256':recipe['attachment_sha256'],'new_download_performed':False}

    def call(self,operation,**arguments):
        try:
            if operation not in {'inspect_controls','get_chat_state','list_chats','open_chat','get_messages',
                                 'open_image','prepare_reply','send_reply','download_attachment'}:
                raise ValueError('OPERATION_OUTSIDE_ALLOWLIST')
            return getattr(self,operation)(**arguments)
        except Exception as exc:
            reason=str(exc) if isinstance(exc,(ValueError,PreflightFailure)) and re.fullmatch(r'[A-Z0-9_]+',str(exc)) else 'OPERATION_UNCONFIRMED'
            self._audit(operation,'UNAVAILABLE',evidence={'reason':reason})
            return {'status':'UNAVAILABLE','reason':reason,'operation':operation,'automatic_retry':False}
