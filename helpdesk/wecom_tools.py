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
from uuid import uuid4

from .locking import resource_lock
from .mcp_test_delivery import MCPTestAnswerDesktop, verify_frame, pixel_hash, same_clipboard_body
from .mcp_window_probe import FOREGROUND_COMMAND, parse_foreground
from .storage import Store, now, encode
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
        result=super()._call(tool,**arguments)
        if getattr(self.transport,'native_backend',False) and tool in ('Click','Shortcut'):
            # Win32 dispatch returns before WeCom renders or updates clipboard.
            # No additional input gesture is sent during this bounded settle.
            time.sleep(.2 if tool=='Click' else .05)
        return result

    def preflight(self,message):
        super().preflight(message)
        if getattr(self.transport,'native_backend',False):
            required=('send_point','receipt_point','receipt_box','copy_menu_point','copy_menu_box','copy_menu_hash')
            if (self.pin.get('observation_mode')!='fixed_foreground_probe'
                    or not self.pin.get('copy_menu_verified') or any(not self.pin.get(k) for k in required)):
                raise PreflightFailure('NATIVE_SEND_CONTROLS_NOT_REVIEWED')
            self._send_receipt_baseline=self._receipt_hash()

    def _receipt_hash(self):
        from PIL import Image
        path=next(c['path'] for c in self.last_record['content'] if c.get('type')=='image')
        with Image.open(path) as image:
            return pixel_hash(image,self.pin['receipt_box'])

    def reconcile(self,message):
        if getattr(self.transport,'native_backend',False) and not getattr(self,'_historical_readback',False):
            # Copying an older identical 收到 does not prove this submission.
            # A restart without the pre-send frame requires manual reconciliation.
            try:
                self._frame(message)
                baseline=getattr(self,'_send_receipt_baseline',None)
                if baseline is None or baseline==self._receipt_hash():
                    raise ValueError('NEW_RECEIPT_NOT_OBSERVED')
            except Exception:
                return {'confirmed':False,'simulated':False,'reason':'NEW_RECEIPT_NOT_OBSERVED',
                        'source_student_delivered':False}
        return super().reconcile(message)


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

    def search_chat(self,chat_id):
        """Search only the session-bound contact, using reviewed local controls."""
        if chat_id!=self.session.get('target_key'):
            raise ValueError('CHAT_OUTSIDE_ALLOWLIST')
        search=self.session.get('search') or {}
        if self.backend!='NATIVE' or search.get('reviewed') is not True or not search.get('result'):
            raise ValueError('SEARCH_CONTROLS_NOT_REVIEWED')
        self._require_input()
        with self._desktop() as (desktop,pin):
            bounds=pin.get('display_bounds')
            if not bounds or len(bounds)!=4 or bounds[2]<=bounds[0] or bounds[3]<=bounds[1]:
                raise ValueError('SEARCH_DISPLAY_BOUNDS_REQUIRED')
            for spec,box in ((search['editor'],search['editor'].get('input_box')),
                             (search['result'],search['result']['region_box'])):
                if not box or len(box)!=4:raise ValueError('SEARCH_CONTROL_BOUNDS_REQUIRED')
                x=(spec['point'][0]-bounds[0])*pin['image_size'][0]/(bounds[2]-bounds[0])
                y=(spec['point'][1]-bounds[1])*pin['image_size'][1]/(bounds[3]-bounds[1])
                if not (box[0]<=x<box[2] and box[1]<=y<box[3]):
                    raise ValueError('SEARCH_POINT_OUTSIDE_CONTROL')
            def window_frame():
                before=parse_foreground(desktop._call('PowerShell',command=FOREGROUND_COMMAND,timeout=10))
                if before.get('process')!='WXWork' or [before['width'],before['height'],before['handle']]!=pin['window_geometry_handle']:
                    raise ValueError('SEARCH_MAIN_WINDOW_CHANGED')
                record=desktop._call('Screenshot')
                if before!=parse_foreground(desktop._call('PowerShell',command=FOREGROUND_COMMAND,timeout=10)):
                    raise ValueError('WINDOW_CHANGED_DURING_OBSERVATION')
                return record
            try:
                record=window_frame()
            except ValueError as exc:
                if str(exc)!='WECOM_NOT_FOREGROUND':raise
                desktop.transport.activate()
                time.sleep(.2)
                record=window_frame()
            try:
                self._checked_region(record,search['editor'])
            except ValueError:
                variants=[search[k] for k in ('query_region_hash','closed_region_hash') if search.get(k)]
                for digest in variants:
                    try:
                        self._checked_region(record,{**search['editor'],'region_hash':digest})
                        break
                    except ValueError:
                        continue
                else:
                    raise ValueError('SEARCH_CONTROL_LAYOUT_CHANGED')
            desktop._call('Click',loc=search['editor']['point'])
            desktop._call('Clipboard',mode='set',text=self.session['display_name'])
            desktop._call('Shortcut',shortcut='ctrl+a');desktop._call('Shortcut',shortcut='ctrl+v')
            desktop._call('Shortcut',shortcut='ctrl+a');desktop._call('Shortcut',shortcut='ctrl+c')
            if not same_clipboard_body(desktop._clipboard_text(),self.session['display_name']):
                raise ValueError('SEARCH_QUERY_READBACK_MISMATCH')
            desktop._call('Shortcut',shortcut='right')
            time.sleep(.5)
            record=window_frame();self._checked_region(record,search['result'])
            desktop._call('Click',loc=search['result']['point'])
            time.sleep(.3)
            _,frame=self._frame(desktop,pin)
        self._audit('search_chat','OPENED_VERIFIED',controls=['chat_search','chat_result','chat_header'],
                    evidence={'frame_sha256':frame['sha256']})
        return {'status':'OPENED_VERIFIED','chat_id':chat_id,'navigation_performed':True,'sent':False}

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

    def _copy_point(self,record,recipe,pin):
        try:
            if recipe.get('ocr_only') is True:
                raise ValueError('EXPLICIT_LOCAL_COPY_OCR')
            self._checked_region(record,recipe)
            return recipe['point']
        except ValueError:
            # Explicit local opt-in, limited to a reviewed menu search region.
            # OCR cannot authorize a send or widen a target/window whitelist.
            if not recipe.get('ocr_search_box'):raise
            if record.get('backend')!='WECOM_NATIVE':
                raise ValueError('COPY_OCR_NATIVE_CAPTURE_REQUIRED')
            from .wecom_control_ocr import locate_copy
            path=self.root/'data/private/wecom-native'/(record['attempt_id']+'.json')
            suggestion=locate_copy(path,recipe['ocr_search_box'],display_index=pin['display_index'])
            return suggestion['loc']

    def _message_frame(self,desktop,pin,recipe):
        record,_=self._frame(desktop,pin)
        try:
            self._checked_region(record,recipe)
        except ValueError as exc:
            menu=recipe.get('copy_menu',{})
            if str(exc)!='MESSAGE_LAYOUT_CHANGED_RECAPTURE_REQUIRED' or not menu.get('ocr_search_box'):
                raise
            # An interrupted read may leave its own reviewed context menu over
            # the message. Confirm that menu before one Escape; never blindly
            # dismiss a dialog or retry changed message content.
            self._copy_point(record,menu,pin)
            desktop._call('Shortcut',shortcut='escape')
            record,_=self._frame(desktop,pin)
            self._checked_region(record,recipe)
            self._audit('get_messages','DISMISSED_CONFIRMED_COPY_MENU',controls=['copy_menu'])
        return record

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
                record=self._message_frame(desktop,pin,recipe)
                nonce='wecom-read-pending-'+str(time.time_ns())
                desktop._call('Clipboard',mode='set',text=nonce)
                desktop._call('Click',loc=recipe['point'],button='right')
                # Native mouse dispatch precedes menu rendering; one bounded
                # settle period, never a repeated click or message submission.
                time.sleep(.2)
                menu=desktop._call('Screenshot')
                copy_point=self._copy_point(menu,recipe['copy_menu'],pin)
                current,_=self._frame(desktop,pin)
                from PIL import Image
                scope=recipe['copy_menu'].get('ocr_search_box',recipe['copy_menu']['region_box'])
                image_path=next(c['path'] for c in menu['content'] if c.get('type')=='image')
                with Image.open(image_path) as image:
                    menu_hash=pixel_hash(image,scope)
                self._checked_region(current,{'region_box':scope,'region_hash':menu_hash})
                desktop._call('Click',loc=copy_point)
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

    def history_page(self, chat_id):
        """One reviewed native upward scroll; never claims a history import."""
        if chat_id!=self.session.get('target_key'):
            raise ValueError('CHAT_OUTSIDE_ALLOWLIST')
        if self.backend!='NATIVE':
            raise ValueError('HISTORY_NATIVE_BACKEND_REQUIRED')
        recipe=self.session.get('history') or {}
        if recipe.get('reviewed') is not True:
            raise ValueError('HISTORY_CONTROLS_NOT_REVIEWED')
        self._require_input()
        with self._desktop() as (desktop,pin):
            bounds=pin.get('display_bounds');point=recipe.get('point');box=recipe.get('region_box')
            if (not isinstance(bounds,list) or len(bounds)!=4 or not isinstance(point,list) or len(point)!=2
                    or not isinstance(box,list) or len(box)!=4 or bounds[2]<=bounds[0] or bounds[3]<=bounds[1]):
                raise ValueError('HISTORY_VIEWPORT_REQUIRED')
            x=(point[0]-bounds[0])*pin['image_size'][0]/(bounds[2]-bounds[0])
            y=(point[1]-bounds[1])*pin['image_size'][1]/(bounds[3]-bounds[1])
            if not (box[0]<=x<box[2] and box[1]<=y<box[3]):
                raise ValueError('HISTORY_POINT_OUTSIDE_VIEWPORT')
            before,_=self._frame(desktop,pin)
            # A reviewed viewport snapshot anchors the initial action. A second
            # page needs a fresh review if the material changed; no blind loop.
            self._checked_region(before,recipe)
            result=desktop._call('Scroll',loc=recipe['point'],direction='up')
            time.sleep(.3)
            after,_=self._frame(desktop,pin)
            from PIL import Image
            def viewport(record):
                path=next(c['path'] for c in record['content'] if c.get('type')=='image')
                with Image.open(path) as image:return pixel_hash(image,recipe['region_box'])
            changed=viewport(before)!=viewport(after)
        status='HISTORY_VIEW_CHANGED' if changed else 'HISTORY_VIEW_UNCHANGED'
        self._audit('history_page',status,controls=['message_list'],evidence={
            'attempt_id':result.get('attempt_id'),'before_attempt':before.get('attempt_id'),
            'after_attempt':after.get('attempt_id'),'messages_imported':0})
        return {'status':status,'messages_imported':0,'complete_history':False,
                'history_exhausted':False,'automatic_retry':False}

    def scan_messages(self,chat_id):
        """Locate visible incoming text candidates, then copy actual text."""
        if chat_id!=self.session.get('target_key'):
            raise ValueError('CHAT_OUTSIDE_ALLOWLIST')
        config=self.session.get('incoming_discovery') or {}
        if self.backend!='NATIVE' or config.get('reviewed') is not True:
            raise ValueError('INCOMING_DISCOVERY_NOT_REVIEWED')
        self._require_input()
        from PIL import Image
        from .wecom_visible import incoming_bubbles
        with self._desktop() as (desktop,pin):
            record,_=self._frame(desktop,pin)
            path=next(c['path'] for c in record['content'] if c.get('type')=='image')
            with Image.open(path) as image:
                boxes=incoming_bubbles(image,config)
                recipes=[]
                bounds=pin['display_bounds'];width,height=pin['image_size']
                for box in boxes:
                    digest=pixel_hash(image,box)
                    recipes.append({'id':'visible-region:'+digest,'kind':'text',
                        'region_box':box,'region_hash':digest,
                        'point':[bounds[0]+int((box[0]+box[2])*(bounds[2]-bounds[0])/(2*width)),
                                 bounds[1]+int((box[1]+box[3])*(bounds[3]-bounds[1])/(2*height))],
                        'copy_menu':{'ocr_only':True,'region_box':config['copy_menu_search_box'],
                                     'ocr_search_box':config['copy_menu_search_box']}})
        if not recipes:
            return {'status':'NO_VISIBLE_TEXT_CANDIDATES','messages':[],
                    'scope':'REVIEWED_VISIBLE_INCOMING_LANE_ONLY','chat_empty':None,'chat_empty_verified':False,
                    'continuous_listener':False,'automatic_ack_eligible':False}
        reader=WeComTools(self.database,root=self.root,session={**self.session,'messages':recipes},
            transport_factory=self.transport_factory,allow_input=self.allow_input,allow_send=False)
        result=reader.get_messages(chat_id)
        for message in result['messages']:
            message['visible_locator']=message['id']
            # This key deduplicates observations, not two identical messages.
            message['id']='visible-text:'+sha256(encode([chat_id,message['text']]).encode()).hexdigest()
        return {**result,'scope':'DISCOVERED_VISIBLE_INCOMING_TEXT_ONLY',
                'platform_identity_verified':False,'automatic_ack_eligible':False}

    def poll_messages(self, chat_id):
        """Persist reviewed observations, not verified new platform messages.

        Identity includes recipe ID and text. Identical platform messages cannot
        be distinguished by this fallback and must never trigger automatic ACKs.
        """
        result=(self.scan_messages(chat_id) if self.session.get('incoming_discovery',{}).get('reviewed') is True
                else self.get_messages(chat_id))
        if result.get('status')!='OK':
            return result
        fresh=[]
        with closing(Store(self.database)) as db, db.transaction():
            for message in result['messages']:
                key=sha256(encode([chat_id,message['id'],message['text']]).encode()).hexdigest()
                exists=db.one("SELECT 1 FROM audit WHERE event='WECOM_VISIBLE_OBSERVATION' "
                              "AND json_extract(details,'$.observation_key')=? LIMIT 1",(key,))
                if exists:
                    continue
                Workflow(db)._event('WECOM_VISIBLE_OBSERVATION',details={
                    'observation_key':key,'chat_id':chat_id,'message':message,
                    'platform_identity_verified':False,'automatic_ack_eligible':False})
                fresh.append(message)
        return {**result,'status':'OBSERVATIONS_SAVED' if fresh else 'NO_NEW_OBSERVATIONS',
                'messages':fresh,'new_observation_count':len(fresh),
                'automatic_ack_eligible':False,'platform_identity_verified':False}

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

    def verify_reply(self,outbox_id):
        """Read a previously verified receipt, never reconcile an unknown send.

        A historical same-body bubble is insufficient to prove a new submission.
        This command cannot change Outbox, completion or performance state.
        """
        self._require_input()
        with self._desktop() as (desktop,pin),closing(Store(self.database)) as db:
            row,bound=self._bound_reply(db,desktop,pin,outbox_id)
            if row['state']!='SENT_UI_CONFIRMED':
                raise ValueError('VERIFIED_HISTORICAL_REPLY_REQUIRED')
            if (not pin.get('copy_menu_verified') or
                    any(not pin.get(k) for k in ('receipt_point','copy_menu_point','copy_menu_box','copy_menu_hash'))):
                raise ValueError('RECEIPT_CONTROLS_NOT_REVIEWED')
            desktop._historical_readback=True
            result=desktop.reconcile(bound)
            status='HISTORICAL_TEXT_VERIFIED' if result.get('confirmed') else 'HISTORICAL_TEXT_UNCONFIRMED'
            directory=self.root/'data/private/wecom-native-receipts'
            directory.mkdir(parents=True,exist_ok=True)
            path=directory/(uuid4().hex+'.json')
            raw=encode({'outbox_id':outbox_id,'new_delivery':False,'result':result}).encode('utf-8')
            with path.open('xb') as output:output.write(raw)
            proof={'path':str(path),'sha256':sha256(raw).hexdigest()}
            self._audit('verify_reply',status,controls=['outgoing_bubble','copy_menu'],outbox=outbox_id,
                        evidence={'body_hash':bound.body_hash,'new_delivery':False,'proof':proof})
            return {'status':status,'outbox_id':outbox_id,'new_delivery':False,'outbox_changed':False,
                    'server_receipt':False,'recipient_read':False,'evidence':proof}

    def save_open_image(self,message_id):
        """Save the currently reviewed native preview through its real dialog."""
        recipe=self._message(message_id)
        save=recipe.get('save_preview') or {}
        if self.backend!='NATIVE' or recipe.get('kind')!='image' or save.get('reviewed') is not True:
            raise ValueError('IMAGE_SAVE_CONTROLS_NOT_REVIEWED')
        self._require_input()
        directory=self.database.parent/'attachments'
        if directory.resolve(strict=True)!=directory or not directory.is_dir():
            raise ValueError('ATTACHMENT_DIRECTORY_REDIRECTED')
        if not directory.is_relative_to(self.root/'data/private'):
            raise ValueError('SAVE_PATH_OUTSIDE_PRIVATE_ROOT')
        destination=directory/('native-'+uuid4().hex+'.jpg')
        # The business tool receives no filename, window handle or coordinates.
        with self._desktop() as (desktop,pin):
            transport=desktop.transport
            current=transport.foreground()
            if current['process']!='WXWork' or [current['width'],current['height'],current['handle']]!=save['window_geometry_handle']:
                raise ValueError('IMAGE_PREVIEW_WINDOW_CHANGED')
            transport.last_native=current
            record=desktop._call('Screenshot')
            self._checked_region(record,save['source_region'])
            self._checked_region(record,save['save_button'])
            self._audit('save_open_image','SAVE_INTENT',controls=['image_save','file_name','file_save'],
                        evidence={'message_id':message_id,'destination':str(destination)})
            desktop._call('Click',loc=save['save_button']['point'])
            result=subprocess.run(['powershell.exe','-NoProfile','-File',str(self.root/'tools/wecom_save_image.ps1'),
                '-MainHandle',str(pin['window_geometry_handle'][2]),'-PreviewHandle',str(current['handle']),
                '-Destination',str(destination)],capture_output=True,timeout=20,shell=False,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode or not destination.is_file():
                raise ValueError('IMAGE_SAVE_RESULT_UNCONFIRMED')
            from PIL import Image
            with Image.open(destination) as image:image.verify()
            digest=sha256(destination.read_bytes()).hexdigest()
            if recipe.get('attachment_sha256') and digest!=recipe['attachment_sha256']:
                raise ValueError('SAVED_ORIGINAL_HASH_MISMATCH')
        self._audit('save_open_image','ORIGINAL_SAVED',controls=['image_save','file_name','file_save'],
                    evidence={'message_id':message_id,'path':str(destination),'sha256':digest})
        return {'status':'ORIGINAL_SAVED','message_id':message_id,'path':str(destination),
                'sha256':digest,'new_download_performed':True,'automatic_retry':False}

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
                                 'open_image','prepare_reply','send_reply','verify_reply','download_attachment','poll_messages','history_page','save_open_image','search_chat','scan_messages'}:
                raise ValueError('OPERATION_OUTSIDE_ALLOWLIST')
            return getattr(self,operation)(**arguments)
        except Exception as exc:
            reason=str(exc) if isinstance(exc,(ValueError,PreflightFailure)) and re.fullmatch(r'[A-Z0-9_]+',str(exc)) else 'OPERATION_UNCONFIRMED'
            self._audit(operation,'UNAVAILABLE',evidence={'reason':reason})
            return {'status':'UNAVAILABLE','reason':reason,'operation':operation,'automatic_retry':False}
