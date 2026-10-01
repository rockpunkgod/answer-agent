"""One-shot Windows-MCP upload and independent readback candidate for DeepSeek.

The result is deliberately not a PreparedDeepSeekGenerator approval record.
Every desktop mutation is journaled before dispatch and never replayed.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import time

from PIL import Image

from .mcp_generation import input_fingerprint
from .mcp_page_contract import DeepSeekPage, PageUnconfirmed, snapshot_text


class PreparationUnconfirmed(RuntimeError):
    pass


def _unique_node(tree: str, pattern: str, label: str):
    matches = re.findall(pattern, tree, re.M)
    if len(matches) != 1:
        raise PreparationUnconfirmed(f'{label}: expected one reviewed node, found {len(matches)}')
    return [int(matches[0][0]), int(matches[0][1])]


def question_text_fields(snapshot):
    question = snapshot['student_question']
    options = question.get('options', [])
    if len(options) != 4 or {x.get('label') for x in options} != set('ABCD'):
        raise ValueError('Text-only preparation requires four verified A-D options')
    fields = {'passage': snapshot['student_material'], 'number': question.get('number'),
              'stem': question.get('verified_stem'),
              'options': {x['label']: x.get('verified_text') for x in options}}
    values = [fields['passage'], fields['number'], fields['stem'], *fields['options'].values()]
    if any(not isinstance(x, str) or not x.strip() for x in values):
        raise ValueError('Text-only preparation requires complete frozen question text')
    return fields


def prepare_question_text(snapshot, evidence_directory, *, create=True):
    """Derive immutable local text from frozen input; never use model readback."""
    fingerprint = input_fingerprint(snapshot)
    fields = question_text_fields(snapshot)
    content = json.dumps({'input_fingerprint': fingerprint, 'question': fields},
                         ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    path = (Path(evidence_directory).resolve() / 'question-text' / (fingerprint + '.txt'))
    if create and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(content)
    if not path.is_file() or path.read_bytes() != content:
        raise ValueError('Derived frozen question text changed or is missing')
    return {'kind': 'question_text', 'path': str(path), 'name': path.name,
            'sha256': sha256(content).hexdigest()}


def _files(snapshot: dict, *, question_text_path=None):
    skills = snapshot['teaching_skills']
    attachments = snapshot['attachments']
    if not skills or len(attachments) > 20:
        raise ValueError('Preparation requires at least one course and zero to twenty question images')
    result = []
    for kind, entries in (('course', skills), ('question_image', attachments)):
        for entry in entries:
            path = Path(entry['path']).resolve(strict=True)
            digest = sha256(path.read_bytes()).hexdigest()
            if digest != entry['sha256']:
                raise ValueError(f'Frozen file hash changed: {path}')
            if kind == 'course' and path.suffix.lower() != '.md':
                raise ValueError('Course file must be Markdown')
            if kind == 'question_image' and path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
                raise ValueError('Question attachment must be an image')
            if any(x['name'].casefold() == path.name.casefold() for x in result):
                raise ValueError('Upload filenames must be unique for readback')
            result.append({'kind': kind, 'path': str(path), 'name': path.name, 'sha256': digest})
    if not attachments:
        if question_text_path is None:
            raise ValueError('Text-only preparation requires a derived frozen question file')
        path = Path(question_text_path).resolve(strict=True)
        item = prepare_question_text(snapshot, path.parent.parent, create=False)
        if item['path'] != str(path) or any(x['name'].casefold() == item['name'].casefold() for x in result):
            raise ValueError('Derived question path mismatch or duplicate upload filename')
        result.append(item)
    return result


class DeepSeekSessionPreparer:
    """Upload exactly the frozen files, then request a source-checkable readback.

    ``controls`` contains reviewed picker_window, file_input, open_button node
    names or a visual_picker pin, and either upload_button or visual_upload.
    Visual controls are pinned to fresh Snapshot image crops.
    """

    def __init__(self, transport, snapshot: dict, session_url: str, evidence_path,
                 controls: dict, *, poll_interval=2, timeout=120, store_path=None):
        from .mcp_transport import MCPProcess
        if isinstance(transport, MCPProcess):
            transport.bound_input_process = 'msedge'
        self.transport = transport
        self.snapshot = snapshot
        self.page = DeepSeekPage(session_url)
        from .session_isolation import claim_deepseek_chat
        claim_deepseek_chat(snapshot, self.page.url, store_path=store_path)
        self.evidence_path = Path(evidence_path)
        self.controls = controls
        self.preparation_mode = controls.get('preparation_mode', 'STRICT_READBACK')
        if self.preparation_mode not in ('STRICT_READBACK', 'FAST_UPLOAD_THEN_GENERATE'):
            raise ValueError('Unsupported preparation_mode')
        self.material_order = controls.get('material_order', 'ALL_THEN_READBACK')
        if self.material_order not in ('ALL_THEN_READBACK', 'COURSE_THEN_QUESTION'):
            raise ValueError('Unsupported material_order')
        self.poll_interval = poll_interval
        self.timeout = timeout
        text = prepare_question_text(snapshot, self.evidence_path.parent) if not snapshot['attachments'] else None
        self.files = _files(snapshot, question_text_path=text['path'] if text else None)
        if ('visual_picker' in controls) == all(k in controls for k in
                                                ('picker_window', 'file_input', 'open_button')):
            raise ValueError('Choose native picker nodes or one reviewed visual picker pin')
        if 'visual_picker' not in controls and not all(
                isinstance(controls.get(k), str) and controls[k].strip()
                for k in ('picker_window', 'file_input', 'open_button')):
            raise ValueError('Reviewed native picker names are required')
        if ('upload_button' in controls) == ('visual_upload' in controls):
            raise ValueError('Choose exactly one reviewed upload control method')
        if 'upload_button' in controls and not isinstance(controls['upload_button'], str):
            raise ValueError('Reviewed upload button name must be text')
        if any('\n' in controls[k] or '"' in controls[k]
               for k in ('picker_window', 'file_input', 'open_button', 'upload_button') if k in controls):
            raise ValueError('Control names must be single-line literal node names')
        if 'visual_upload' in controls:
            pin = controls['visual_upload']
            base = {'screen_size', 'screenshot_size', 'editor_offset', 'crop_size', 'crop_sha256'}
            anchor = {'anchor_role', 'anchor_name'}
            if (not isinstance(pin, dict) or not base <= set(pin)
                    or set(pin) not in (base, base | anchor)):
                raise ValueError('Visual upload pin fields are incomplete')
            if anchor <= set(pin):
                if (pin['anchor_role'] != '组' or pin['anchor_name'] != '智能搜索'):
                    raise ValueError('Unsupported visual upload anchor')
            for key in ('screen_size', 'screenshot_size', 'editor_offset', 'crop_size'):
                pair = pin[key]
                if not isinstance(pair, list) or len(pair) != 2 or any(type(n) is not int for n in pair):
                    raise ValueError(f'Invalid visual pin {key}')
            if (any(n <= 0 for n in pin['screen_size'] + pin['screenshot_size'] + pin['crop_size'])
                    or any(abs(n) > 1000 for n in pin['editor_offset'])
                    or not re.fullmatch(r'[0-9a-f]{64}', str(pin['crop_sha256']))):
                raise ValueError('Invalid visual pin geometry or hash')
        if 'visual_picker' in controls:
            pin = controls['visual_picker']
            if not isinstance(pin, dict) or set(pin) != {
                    'screen_size', 'screenshot_size', 'regions', 'file_input_point', 'open_point'}:
                raise ValueError('Visual picker pin fields are incomplete')
            for key in ('screen_size', 'screenshot_size', 'file_input_point', 'open_point'):
                pair = pin[key]
                if not isinstance(pair, list) or len(pair) != 2 or any(type(n) is not int for n in pair):
                    raise ValueError(f'Invalid visual picker {key}')
            if (not isinstance(pin['regions'], dict) or set(pin['regions']) !=
                    {'header', 'file_label', 'open_button'}):
                raise ValueError('Visual picker requires three reviewed image regions')
            for region in pin['regions'].values():
                if (not isinstance(region, dict) or set(region) != {'box', 'sha256'}
                        or not isinstance(region['box'], list) or len(region['box']) != 4
                        or any(type(n) is not int for n in region['box'])
                        or not re.fullmatch(r'[0-9a-f]{64}', str(region['sha256']))):
                    raise ValueError('Invalid visual picker region')
        self.record = {
            'status': 'UPLOAD_NOT_STARTED', 'session_url': self.page.url,
            'run_id': snapshot['run_id'], 'input_fingerprint': input_fingerprint(snapshot),
            'uploaded_teaching_hashes': {x['path']: x['sha256'] for x in self.files if x['kind'] == 'course'},
            'files': self.files, 'controls': controls, 'events': [],
            'material_order': self.material_order, 'preparation_mode': self.preparation_mode,
            'material_stages': [],
            'automatic_retry_allowed': False, 'operator_verified': False,
        }
        if text:
            self.record['question_text_fields'] = question_text_fields(snapshot)
            self.record['question_text_sha256'] = text['sha256']

    def _save(self):
        self.evidence_path.write_text(json.dumps(self.record, ensure_ascii=False, indent=2), encoding='utf-8')

    def _call(self, tool, arguments, *, intent=None):
        event = {'tool': tool, 'arguments': arguments, 'status': 'OUTCOME_UNCONFIRMED'}
        if intent:
            event['intent'] = intent
        self.record['events'].append(event)
        self._save()
        result = self.transport.call(tool, arguments)
        event['status'] = 'TOOL_RETURNED'
        event['result'] = result
        self._save()
        return result

    def _snap(self, *, page=True, vision=False):
        arguments = {'use_dom': page, 'use_vision': vision}
        if vision:
            arguments['use_annotation'] = False
        result = self._call('Snapshot', arguments)
        tree = self.page.inspect(result) if page else snapshot_text(result)
        return result, tree

    def _upload_button(self, tree):
        name = re.escape(self.controls['upload_button'])
        return _unique_node(tree, rf'^.*?\((\d+),(\d+)\) 按钮 "{name}"[^\n]*$', 'upload button')

    def _visual_upload_point(self, observed, tree):
        pin = self.controls['visual_upload']
        editors = re.findall(r'\((\d+),(\d+)\) 编辑 "给 DeepSeek 发送消息"([^\n]*)', tree)
        if len(editors) != 1 or '[value:' in editors[0][2]:
            raise PreparationUnconfirmed('Visual upload editor anchor is ambiguous or occupied')
        if 'anchor_role' in pin:
            anchor = re.escape(pin['anchor_name'])
            points = re.findall(rf'^.*?\((\d+),(\d+)\) {pin["anchor_role"]} "{anchor}"[^\n]*$', tree, re.M)
            if len(points) != 1:
                raise PreparationUnconfirmed('Reviewed visual upload group anchor is ambiguous')
            anchor_xy = (int(points[0][0]), int(points[0][1]))
        else:
            anchor_xy = (int(editors[0][0]), int(editors[0][1]))
        screen = pin['screen_size']
        full_text = snapshot_text(observed)
        if f'Screenshot Original Size: ({screen[0]},{screen[1]})' not in full_text:
            raise PreparationUnconfirmed('Visual upload screen dimensions changed')
        images = [item for item in observed.get('content', []) if item.get('type') == 'image']
        if len(images) != 1 or not isinstance(images[0].get('path'), str):
            raise PreparationUnconfirmed('Fresh screenshot is missing or ambiguous')
        x = anchor_xy[0] + pin['editor_offset'][0]
        y = anchor_xy[1] + pin['editor_offset'][1]
        if not (0 <= x < screen[0] and 0 <= y < screen[1]):
            raise PreparationUnconfirmed('Visual upload point is outside screen')
        with Image.open(images[0]['path']) as image:
            if list(image.size) != pin['screenshot_size']:
                raise PreparationUnconfirmed('Visual upload screenshot dimensions changed')
            sx = round(x * image.width / screen[0])
            sy = round(y * image.height / screen[1])
            half_w, half_h = pin['crop_size'][0] // 2, pin['crop_size'][1] // 2
            box = (sx-half_w, sy-half_h, sx-half_w+pin['crop_size'][0], sy-half_h+pin['crop_size'][1])
            if box[0] < 0 or box[1] < 0 or box[2] > image.width or box[3] > image.height:
                raise PreparationUnconfirmed('Visual upload crop is outside screenshot')
            digest = sha256(image.crop(box).convert('RGB').tobytes()).hexdigest()
        if digest != pin['crop_sha256']:
            raise PreparationUnconfirmed('Reviewed upload icon crop changed')
        self.record['last_visual_upload_check'] = {'screenshot_path': images[0]['path'],
                                                    'crop_box': box, 'crop_sha256': digest,
                                                    'click_point': [x, y]}
        self._save()
        return [x, y]

    def _upload_point(self, observed, tree):
        if 'visual_upload' in self.controls:
            return self._visual_upload_point(observed, tree)
        return self._upload_button(tree)

    def _picker(self, tree, role, name):
        # Native dialog must be first active window; reject page/no dialog.
        active = tree.split('UI Tree:', 1)
        if len(active) != 2 or not re.search(r'window "[^"\n]+"', active[1]):
            raise PreparationUnconfirmed('Native file dialog not observed')
        first = re.search(r'window "([^"\n]+)"', active[1])
        if not first or first[1] != self.controls['picker_window']:
            raise PreparationUnconfirmed('Reviewed native file picker is not foreground')
        return _unique_node(active[1], rf'^.*?\((\d+),(\d+)\) {role} "{re.escape(name)}"[^\n]*$', 'file picker control')

    def _visual_picker_check(self):
        pin = self.controls['visual_picker']
        # The native picker can stall desktop UIA traversal. Screenshot is the
        # official image-only observation and retains its own tool identity.
        observed = self._call('Screenshot', {'use_annotation': False})
        if observed.get('tool') != 'Screenshot' or observed.get('is_error') is not False:
            raise PreparationUnconfirmed('Visual picker screenshot tool failed')
        text_parts = [c.get('text') for c in observed.get('content', []) if c.get('type') == 'text']
        if len(text_parts) != 1 or not isinstance(text_parts[0], str):
            raise PreparationUnconfirmed('Visual picker screenshot text missing')
        tree = text_parts[0]
        if f'Screenshot Original Size: ({pin["screen_size"][0]},{pin["screen_size"][1]})' not in tree:
            raise PreparationUnconfirmed('Visual picker screen dimensions changed')
        images = [c for c in observed.get('content', []) if c.get('type') == 'image']
        if len(images) != 1 or not isinstance(images[0].get('path'), str):
            raise PreparationUnconfirmed('Visual picker screenshot missing or ambiguous')
        with Image.open(images[0]['path']) as image:
            if list(image.size) != pin['screenshot_size']:
                raise PreparationUnconfirmed('Visual picker screenshot dimensions changed')
            checked = {}
            for name, region in pin['regions'].items():
                x0, y0, x1, y1 = region['box']
                if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
                    raise PreparationUnconfirmed('Visual picker region outside screenshot')
                digest = sha256(image.crop((x0, y0, x1, y1)).convert('RGB').tobytes()).hexdigest()
                if digest != region['sha256']:
                    raise PreparationUnconfirmed(f'Visual picker {name} region changed')
                checked[name] = digest
        for key in ('file_input_point', 'open_point'):
            x, y = pin[key]
            if not (0 <= x < pin['screen_size'][0] and 0 <= y < pin['screen_size'][1]):
                raise PreparationUnconfirmed('Visual picker action point outside screen')
        self.record['last_visual_picker_check'] = {'screenshot_path': images[0]['path'],
                                                    'region_hashes': checked}
        self._save()
        return pin

    def _clipboard_text(self):
        result = self._call('Clipboard', {'mode': 'get'})
        parts = [x['text'] for x in result.get('content', []) if x.get('type') == 'text']
        if len(parts) != 1 or not parts[0].startswith('Clipboard content:\n'):
            raise PreparationUnconfirmed('Clipboard readback unavailable')
        return parts[0].removeprefix('Clipboard content:\n')

    def _visual_picker_submit(self, item):
        pin = self._visual_picker_check()
        self._call('Type', {'loc': pin['file_input_point'], 'text': item['path'],
                            'clear': True, 'press_enter': False}, intent='stage frozen path in reviewed picker')
        self._visual_picker_check()
        self._call('Click', {'loc': pin['file_input_point']}, intent='focus reviewed picker input')
        self._call('Clipboard', {'mode': 'set', 'text': 'file-path-readback-empty'})
        self._call('Shortcut', {'shortcut': 'ctrl+a'})
        self._call('Shortcut', {'shortcut': 'ctrl+c'})
        if self._clipboard_text() != item['path']:
            raise PreparationUnconfirmed('Picker path clipboard readback mismatch')
        self._visual_picker_check()
        self._call('Click', {'loc': pin['open_point']}, intent='submit reviewed visual picker once')

    def _await_attachment(self, filename):
        deadline = time.monotonic() + self.timeout
        stable = 0
        while time.monotonic() < deadline:
            _, tree = self._snap()
            if filename in tree and not re.search(r'上传中|正在上传|uploading|进度条', tree, re.I):
                stable += 1
                if stable >= 2:
                    return tree
            else:
                stable = 0
            time.sleep(self.poll_interval)
        raise PreparationUnconfirmed(f'Attachment completion not visible: {filename}')

    def _readback(self, *, course_only=False):
        token = 'run_' + self.snapshot['run_id']
        if not re.fullmatch(r'run_[a-zA-Z0-9]{12,64}', token):
            raise ValueError('Invalid run identifier')
        if course_only:
            token = 'course_' + token
        courses = [x['name'] for x in self.files if x['kind'] == 'course']
        images = [x['name'] for x in self.files if x['kind'] == 'question_image']
        question_files = images or [x['name'] for x in self.files if x['kind'] == 'question_text']
        question_source = '题图' if images else '冻结题面文本附件'
        prompt = ('请只根据本次附件实际内容作核对，不解题。逐一读取课程文件：'
                  + '、'.join(courses) + '；再读取' + question_source + '：' + '、'.join(question_files)
                  + '。不要从文件名猜内容，也不要引用本条指令作为文件内容。'
                  + '第一行单独写 BEGIN_' + token + '。接下来每份课程各写一行，格式为课程文件名：该文件正文中连续至少10字的原文摘录。'
                  + '再写一行 题干：' + question_source + '中实际可见的题干。最后一行单独写 END_' + token + '。'
                  + '如果无法读取任意附件，明确写无法读取。')
        if course_only:
            prompt = ('请只根据本次已上传的课程附件实际内容作核对，不解题：'
                      + '、'.join(courses)
                      + '。逐一实际读取，不得从文件名猜内容，不得引用本条指令作为文件内容。'
                      + '第一行单独写 BEGIN_' + token
                      + '。每份课程各写一行，格式为课程文件名：该文件正文中连续至少10字的原文摘录。'
                      + '最后一行单独写 END_' + token
                      + '。如果无法读取任意附件，明确写无法读取。')
        phase = {'phase': 'COURSE_READBACK' if course_only else 'QUESTION_AND_COURSE_READBACK',
                 'status': 'SUBMISSION_UNCONFIRMED', 'response_token': token,
                 'prompt_sha256': sha256(prompt.encode()).hexdigest()}
        self.record['material_stages'].append(phase)
        self._save()
        observed, _ = self._snap()
        stage = self.page.stage_action(observed, prompt)
        self._call(stage['tool'], stage['arguments'], intent='stage attachment readback request')
        observed, _ = self._snap()
        submit = self.page.submit_action(observed, prompt)
        self.record['status'] = 'READBACK_SUBMISSION_UNCONFIRMED'
        self.record['readback_prompt_sha256'] = sha256(prompt.encode()).hexdigest()
        self._save()
        self._call(submit['tool'], submit['arguments'], intent='submit readback request once')
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            observed, _ = self._snap()
            try:
                answer = self.page.completed_text(observed, token)
            except PageUnconfirmed:
                time.sleep(self.poll_interval)
                continue
            normalized = lambda s: re.sub(r'\s+', '', s).strip('"“”')
            excerpt_matches = {}
            for item in self.files:
                if item['kind'] != 'course':
                    continue
                rows = [line.split('：', 1)[1].strip() for line in answer.splitlines()
                        if line.startswith(item['name'] + '：')]
                raw = Path(item['path']).read_bytes()
                if sha256(raw).hexdigest() != item['sha256']:
                    raise PreparationUnconfirmed('Frozen course hash changed during readback')
                source = raw.decode('utf-8')
                if len(rows) == 1 and len(rows[0]) >= 10 and rows[0] in source:
                    excerpt_matches[item['path']] = rows[0]
            stem_rows = [line.split('：', 1)[1].strip() for line in answer.splitlines()
                         if line.startswith('题干：')]
            expected_stem = self.snapshot['student_question'].get('verified_stem', '')
            stem_matches = (len(stem_rows) == 1 and len(normalized(expected_stem)) >= 10
                            and normalized(expected_stem) in normalized(stem_rows[0]))
            phase.update(status='CONTENT_MATCHED' if len(excerpt_matches) == len(courses)
                         else 'CONTENT_MISMATCH_REQUIRES_REVIEW', snapshot=observed,
                         readback_text=answer, course_readback_excerpts=excerpt_matches,
                         all_course_excerpts_match=len(excerpt_matches) == len(courses))
            if course_only:
                self.record['course_stage_verified'] = len(excerpt_matches) == len(courses)
                self.record['status'] = 'COURSE_CONTENT_MATCHED' if self.record['course_stage_verified'] else 'OUTCOME_REQUIRES_REVIEW'
                self._save()
                if not self.record['course_stage_verified']:
                    raise PreparationUnconfirmed('Course content readback mismatch; question upload prohibited')
                return
            phase['question_stem_matches'] = stem_matches
            self.record.update(status='READBACK_CANDIDATE_REQUIRES_OPERATOR_REVIEW',
                               readback_snapshot=observed, readback_text=answer,
                               course_readback_excerpts=excerpt_matches,
                               question_stem_matches=stem_matches,
                               all_course_excerpts_match=len(excerpt_matches) == len(courses))
            self._save()
            return
        raise PreparationUnconfirmed('Readback response not complete before deadline')

    def _upload_files(self, files):
        for item in files:
            if sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Frozen file hash changed before upload: ' + item['path'])
            observed, tree = self._snap(vision='visual_upload' in self.controls)
            self.page.stage_action(observed, 'probe')
            if item['name'] in tree:
                raise PreparationUnconfirmed(f'Filename already visible; cannot infer upload ownership: {item["name"]}')
            self._call('Click', {'loc': self._upload_point(observed, tree)}, intent='open reviewed upload picker')
            if 'visual_picker' in self.controls:
                self._visual_picker_submit(item)
                visible = self._await_attachment(item['name'])
                self.record.setdefault('visible_attachment_names', []).append(item['name'])
                self.record['last_upload_tree'] = visible
                self._save()
                continue
            _, picker_tree = self._snap(page=False)
            input_loc = self._picker(picker_tree, '编辑', self.controls['file_input'])
            self._picker(picker_tree, '按钮', self.controls['open_button'])
            self._call('Type', {'loc': input_loc, 'text': item['path'], 'press_enter': False},
                       intent='stage frozen file path')
            _, picker_tree = self._snap(page=False)
            if f'[value:"{item["path"]}"]' not in picker_tree:
                raise PreparationUnconfirmed('Picker path readback mismatch')
            open_loc = self._picker(picker_tree, '按钮', self.controls['open_button'])
            self._call('Click', {'loc': open_loc}, intent='submit file picker once')
            visible = self._await_attachment(item['name'])
            self.record.setdefault('visible_attachment_names', []).append(item['name'])
            self.record['last_upload_tree'] = visible
            self._save()

    def run(self):
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        with self.evidence_path.open('x', encoding='utf-8') as stream:
            json.dump(self.record, stream, ensure_ascii=False, indent=2)
        try:
            # Recheck every byte before the first desktop observation/mutation.
            text = next((x['path'] for x in self.files if x['kind'] == 'question_text'), None)
            if _files(self.snapshot, question_text_path=text) != self.files:
                raise ValueError('Frozen upload manifest changed')
            observed, tree = self._snap()
            # Empty composer and absence of active generation are required before
            # upload. Page stage_action checks both without typing.
            self.page.stage_action(observed, 'probe')
            if self.preparation_mode == 'FAST_UPLOAD_THEN_GENERATE':
                self._upload_files([x for x in self.files if x['kind'] == 'course'])
                self._upload_files([x for x in self.files if x['kind'] != 'course'])
            elif self.material_order == 'COURSE_THEN_QUESTION':
                self._upload_files([x for x in self.files if x['kind'] == 'course'])
                self._readback(course_only=True)
                self._upload_files([x for x in self.files if x['kind'] != 'course'])
            else:
                self._upload_files(self.files)
            self.record['status'] = 'ATTACHMENT_NAMES_VISIBLE_CONTENT_UNVERIFIED'
            self._save()
            if self.preparation_mode == 'FAST_UPLOAD_THEN_GENERATE':
                # Attachment names prove readiness, not that the model read them.
                # This unreviewed candidate cannot generate. Independent source review
                # can approve the distinct ATTACHMENTS_READY_SOURCE_REVIEWED contract.
                observed, tree = self._snap()
                self.page.stage_action(observed, 'probe')
                if any(item['name'] not in tree for item in self.files):
                    raise PreparationUnconfirmed('Final attachment readiness missing')
                self.record.update(
                    readiness_snapshot=observed,
                    status='ATTACHMENTS_READY_REQUIRES_SOURCE_REVIEW',
                    effective_material_order='COURSE_THEN_QUESTION',
                    model_readback_performed=False,
                    generation_authorized=False,
                    generation_blocked_reason='INDEPENDENT_SOURCE_REVIEW_REQUIRED',
                    generation_instruction=('先实际读取本会话课程Skill附件，按Skill自行核对题目附件与冻结题面，'
                                            '确认题干和选项后在同一次生成中完成解答。'
                                            '若课程或题目无法读取，明确说明缺口，停止猜测。'))
                self._save()
            else:
                self._readback()
            return self.record
        except Exception as exc:
            self.record['status'] = 'OUTCOME_REQUIRES_REVIEW'
            self.record['error'] = f'{type(exc).__name__}: {exc}'
            self._save()
            raise

