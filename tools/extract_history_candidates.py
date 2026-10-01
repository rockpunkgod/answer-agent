"""Conservative, local extraction of candidate history messages from OCR.

The output is evidence for manual review, not a performance ledger or verified
WeCom export. Screenshots overlap and OCR can corrupt names or timestamps.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


DATE = re.compile(r'(?<!\d)9\s*[/／]\s*(1[7-9]|2\d|30)')
TIME = re.compile(r'(?<!\d)([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)\s*[:：]\s*([0-5]\d)(?!\d)')
TEACHER = re.compile(r'老师|助教|班主任|教研')
THANKS = re.compile(r'^(谢谢|谢谢老师|感谢|收到|好的|明白了|辛苦了|表情|图片|语音)$')
QUESTION = re.compile(r'请问|老师|为什么|怎么|如何|是不是|选[ABCD]|第\s*\d+\s*(?:题|空)|[？?]')
TYPE_RULES = [
    ('读后续写', re.compile('读后续写|续写')),
    ('七选五', re.compile('七选五|7选5')),
    ('完形填空', re.compile('完形')),
    ('语法填空', re.compile(r'语法填空|第\s*\d+\s*空')),
    ('听力', re.compile('听力')),
    ('写作', re.compile('作文|写作|应用文|书面表达')),
    ('阅读理解', re.compile(r'阅读|文章|第\s*\d+\s*题')),
]


def compact(s):
    return re.sub(r'\s+', '', s or '').replace('（', '(').replace('）', ')')


def parse_header(text):
    match = DATE.search(text)
    if not match:
        return None
    prefix = text[:match.start()].strip()
    suffix = text[match.end():]
    clock = TIME.search(suffix.replace('.', ':').replace(';', ':'))
    sent = f'2026-09-{int(match.group(1)):02d}T{int(clock.group(1)):02d}:{clock.group(2)}:{clock.group(3)}+08:00' if clock else None
    return {'date': f'2026-09-{int(match.group(1)):02d}', 'timestamp_candidate': sent,
            'timestamp_ocr': text[match.start():].strip(), 'sender_ocr': prefix}


def lines_for(frame, root):
    name = Path(frame['file']).stem
    path = root / (name + '.easyocr.json')
    method = 'easyocr'
    if not path.exists():
        path = root / (name + '.ocr.json')
        method = 'windows_ocr'
    if not path.exists():
        return [], None
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('image_sha256') != frame.get('sha256'):
        return [], None
    if data['width'] > 1000:
        lines = [x for x in data['lines'] if x['rect']['x'] >= 1572 and x['rect']['y'] >= 340]
    else:
        lines = list(data['lines'])
    lines.sort(key=lambda x: (x['rect']['y'], x['rect']['x']))
    return lines, method


def page_messages(frame, lines, method):
    # Search the original-message header line. OCR sometimes returns sender and
    # timestamp as separate pieces at nearly the same y, so join row neighbors.
    headers = []
    for line in lines:
        header = parse_header(line['text'])
        if not header:
            continue
        y = line['rect']['y']
        neighbors = [x for x in lines if abs(x['rect']['y']-y) <= 11 and x is not line]
        if not header['sender_ocr']:
            left = [x['text'] for x in neighbors if x['rect']['x'] < line['rect']['x']]
            header['sender_ocr'] = ' '.join(left)
        header['y'] = y
        header['header_text'] = line['text']
        headers.append(header)
    headers.sort(key=lambda x: x['y'])
    messages = []
    for index, header in enumerate(headers):
        bottom = headers[index+1]['y'] if index+1 < len(headers) else 10000
        body_lines = [line for line in lines if header['y'] + 14 < line['rect']['y'] < bottom - 5]
        # Repeat page chrome and search labels are never chat messages.
        body_lines = [x for x in body_lines if not re.search('查找聊天记录|发送人|图片与视频|搜索聊天记录', compact(x['text']))]
        body = '\n'.join(x['text'].strip() for x in body_lines)
        messages.append({**header, 'body_ocr': body, 'body_line_count': len(body_lines),
                         'frame': Path(frame['file']).name, 'ocr_method': method,
                         'evidence': [{'file': Path(frame['file']).name,
                                       'header_y': round(header['y'], 1),
                                       'panel_only': bool(frame.get('panel_only'))}]})
    return messages


def identify(message):
    body = compact(message['body_ocr'])
    sender = compact(message['sender_ocr'])
    if TEACHER.search(sender):
        role = 'teacher_or_staff_candidate'
    elif sender:
        role = 'student_candidate'
    else:
        role = 'unknown'
    if role == 'unknown' and re.search(r'加入了?(?:外部)?群聊|移出了?(?:外部)?群聊|退出了?(?:该)?群聊', body):
        activity = 'system_notice_excluded'
    elif role == 'unknown':
        activity = 'unknown_sender_context_review'
    elif THANKS.fullmatch(body) or body.startswith(('好的好的。谢谢老师', '谢谢老师')):
        activity = 'thanks_or_ack_excluded'
    elif re.fullmatch(r'@[^@]{1,45}老师', body):
        activity = 'mention_only_excluded'
    elif role == 'teacher_or_staff_candidate':
        activity = 'answer_or_staff_message_candidate' if body else 'staff_attachment_candidate'
    elif body.startswith('龙坚英语答疑助教'):
        activity = 'quoted_context_followup_candidate'
    elif body.startswith('老师改'):
        activity = 'correction_or_review_request_candidate'
    elif QUESTION.search(body):
        activity = 'student_question_candidate'
    elif not body:
        activity = 'attachment_or_empty_candidate'
    else:
        activity = 'student_other_or_followup_candidate'
    kind = next((label for label, pattern in TYPE_RULES if pattern.search(body)), '题型待核验')
    return role, activity, kind


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    # The 27s-star-2 capture alone has a known transient preview overlay.
    # Frame ordinals in other groups have no such meaning.
    overlay_frame_numbers = set(range(80, 86)) if manifest.get('observed_group') == '27s-star-2' else set()
    seen = {}
    pages = []
    skipped_overlay = []
    for frame in manifest['frames']:
        index = int(Path(frame['file']).stem)
        if index in overlay_frame_numbers:
            skipped_overlay.append(Path(frame['file']).name)
            continue
        lines, method = lines_for(frame, root)
        page = page_messages(frame, lines, method) if method else []
        pages.append({'file': Path(frame['file']).name, 'ocr': method, 'headers': len(page),
                      'panel_only': bool(frame.get('panel_only'))})
        for item in page:
            sender_key = ('teacher_or_staff' if TEACHER.search(compact(item['sender_ocr']))
                          else compact(item['sender_ocr']))
            key = (item['timestamp_candidate'] or (item['date'] + compact(item['timestamp_ocr'])), sender_key)
            # Stable header identity merges overlapping screenshots. For weak
            # OCR headers, a distinct candidate remains pending manual review.
            existing = seen.get(key)
            if existing is None:
                seen[key] = item
            else:
                existing['evidence'].extend(item['evidence'])
                if len(item['body_ocr']) > len(existing['body_ocr']):
                    existing['body_ocr'] = item['body_ocr']
                    existing['body_line_count'] = item['body_line_count']
                    existing['frame'] = item['frame']
    messages = []
    for item in seen.values():
        role, activity, kind = identify(item)
        stable = '|'.join((item['date'], compact(item['timestamp_ocr']), compact(item['sender_ocr'])))
        mid = hashlib.sha256(stable.encode('utf-8')).hexdigest()[:16]
        messages.append({'id': 'ocr-' + mid, 'date': item['date'],
                         'original_send_time_candidate': item['timestamp_candidate'],
                         'timestamp_ocr': item['timestamp_ocr'], 'sender_ocr': item['sender_ocr'],
                         'sender_role_candidate': role, 'activity_candidate': activity,
                         'question_type_candidate': kind, 'body_ocr': item['body_ocr'],
                         'body_line_count': item['body_line_count'], 'evidence': item['evidence'],
                         'time_status': 'VISUAL_REVIEW_REQUIRED',
                         'counting_status': 'NOT_IN_OFFICIAL_LEDGER',
                         'grouping_status': 'MATERIAL_AND_FOLLOWUP_REVIEW_REQUIRED'})
    messages.sort(key=lambda x: (x['date'], x['original_send_time_candidate'] or (x['date']+'T99'), x['id']))
    candidate_units = []
    for message in messages:
        if message['activity_candidate'] != 'student_question_candidate':
            continue
        candidate_units.append({'id': 'candidate-' + message['id'], 'first_message_candidate_id': message['id'],
                                'date_candidate': message['date'], 'student_ocr': message['sender_ocr'],
                                'question_type_candidate': message['question_type_candidate'],
                                'material_identity': None, 'related_followup_ids': [],
                                'delivery_message_candidate_ids': [],
                                'original_send_time_candidate': message['original_send_time_candidate'],
                                'status': 'NEEDS_VISUAL_AND_GROUPING_REVIEW',
                                'confirmed_quantity': None})
    daily = defaultdict(lambda: Counter())
    for item in messages:
        daily[item['date']][item['activity_candidate']] += 1
    pending_ocr_frames = [page['file'] for page in pages if page['ocr'] is None]
    curated_path = root / 'curated_candidates.json'
    curated = json.loads(curated_path.read_text(encoding='utf-8')) if curated_path.exists() else {'groups': []}
    message_ids = {item['id'] for item in messages}
    for group in curated['groups']:
        references = ([group['first_prompt_id']] + group.get('related_student_message_ids', [])
                      + group.get('possible_answer_ids', []) + group.get('exclude_ack_ids', []))
        missing = [ref for ref in references if ref not in message_ids]
        if missing:
            raise ValueError(f"Curated group {group['group_id']} has missing evidence IDs: {missing}")
    result = {'source_group': manifest.get('observed_group'), 'from_requested': manifest.get('from'),
              'capture_manifest_complete': manifest.get('complete', False),
              'capture_scope': 'visible_scroll_range_only', 'not_official_performance': True,
              'ocr_processed_frames': len(pages) - len(pending_ocr_frames),
              'ocr_pending_frames': pending_ocr_frames,
              'notes': ['OCR timestamps and sender names require screenshot review.',
                        'Overlapping screenshot headers are merged only by OCR identity; OCR variants can remain duplicated.',
                        'Attachments and messages without visible headers are not independent countable units.',
                        'Teacher text chunks are never counted as student questions.'],
              'pages': pages, 'overlay_frames_excluded': skipped_overlay,
              'messages': messages, 'candidate_units': candidate_units,
              'curated_material_candidates': curated['groups'],
              'daily_candidates': {date: dict(counts) for date, counts in sorted(daily.items())}}
    output = root / 'extracted.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    # Summary stays in the same private directory. Candidate figures describe
    # only visible OCR headers, never verified performance quantities.
    lines = ['# 群聊历史离线提取（待人工核验）', '',
             f"群标识：{manifest.get('observed_group')}。请求起点：{manifest.get('from')}。",
             f"截图 {len(manifest['frames'])} 张；已 OCR {len(pages)-len(pending_ocr_frames)} 张；"
             f"待 OCR {len(pending_ocr_frames)} 张；遮挡跳过 {len(skipped_overlay)} 张；"
             f"识别到候选消息头 {len(messages)} 条。",
             '此处所有数量均为 OCR 候选记录量，不是答疑篇数或正式绩效。', '',
             '| 日期 | 候选学生提问 | 候选老师/教务记录 | 待区分追问或其他 |',
             '|---|---:|---:|---:|']
    for day, count in sorted(daily.items()):
        lines.append(f"| {day} | {count['student_question_candidate']} | "
                     f"{count['answer_or_staff_message_candidate']+count['staff_attachment_candidate']} | "
                     f"{count['student_other_or_followup_candidate']+count['attachment_or_empty_candidate']} |")
    lines += ['', '经截图抽查整理的材料关联候选（仍非计量结果）：', '',
              '| 日期 | 学生候选 | 题型或材料 | 证据索引 | 未决事项 |',
              '|---|---|---|---|---|']
    for group in curated['groups']:
        lines.append(f"| {group['business_date_candidate']} | {group['student_display_candidate']} | "
                     f"{group['question_type_candidate']} | {group['group_id']} / {', '.join(group['evidence_frames'])} | "
                     f"{'; '.join(group['remaining_checks'])} |")
    earliest = min(daily) if daily else None
    lines += ['', '证据范围与缺口：',
              '- 仅识别右侧历史面板；主聊天区、输入框和侧栏均不作消息证据。',
              (f'- 已知遮挡截图 {", ".join(skipped_overlay)} 已排除。' if skipped_overlay
               else '- 本组没有按序号预设的遮挡排除；仍须逐图核验页面是否可读。'),
              f'- 当前 OCR 可见最早日期为 {earliest or "未知"}；清单 complete={manifest.get("complete", False)}。'
              '未覆盖日期不能记零；即使滚到顶部也须区分当前可见范围与完整平台导出。',
              '- 图片题目材料、同篇归并、追问归属、原始发送时间及老师实际交付，需要逐条核对截图。',
              '- 仅凭 OCR 无法确认答案正确性、正式交付形式或绩效数量。']
    (root / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'pages': len(pages), 'messages': len(messages),
                      'student_question_candidates': len(candidate_units),
                      'dates': sorted(daily), 'manifest_complete': manifest.get('complete', False)}))


if __name__ == '__main__':
    main()
