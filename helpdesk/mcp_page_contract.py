"""DeepSeek text-page contract for official Windows-MCP Snapshot results.

No desktop calls occur here. The executor supplies fresh observations and calls
the returned actions through Windows-MCP. An ambiguous page produces no action.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from urllib.parse import urlsplit


class PageUnconfirmed(ValueError):
    pass


def snapshot_text(record: dict, *, tool='Snapshot') -> str:
    if record.get('is_error') is not False or record.get('tool') != tool:
        raise PageUnconfirmed('SNAPSHOT_FAILED')
    parts = []
    for item in record.get('content', []):
        if item.get('type') != 'text':
            continue
        raw = item['text']
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            value = raw
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            parts.extend(value)
        elif isinstance(value, str):
            parts.append(value)
        else:
            raise PageUnconfirmed('SNAPSHOT_FORMAT_CHANGED')
    return '\n'.join(parts)


@dataclass(frozen=True)
class DeepSeekPage:
    url: str
    display_index: int | None = None

    def __post_init__(self):
        p = urlsplit(self.url)
        if (p.scheme != 'https' or p.netloc != 'chat.deepseek.com'
                or not re.fullmatch(r'/a/chat/s/[a-zA-Z0-9-]+', p.path)
                or p.query or p.fragment):
            raise ValueError('A verified exact DeepSeek conversation URL is required')
        if self.display_index is not None and (type(self.display_index) is not int or self.display_index < 0):
            raise ValueError('display_index must be a nonnegative integer')

    def observation_arguments(self, *, dom=True, vision=False):
        arguments = {'use_dom': dom, 'use_vision': vision}
        if self.display_index is not None:
            arguments['display'] = [self.display_index]
        return arguments

    def display_region(self, text):
        """Use current MCP virtual-desktop bounds, never saved screen coordinates."""
        summary = text.split('UI Tree:', 1)[0]
        selected = re.findall(r'^Selected Displays: (.*)$', summary, re.M)
        regions = re.findall(r'^Screenshot Region: \((-?\d+),(-?\d+),(-?\d+),(-?\d+)\)\s*$',
                             summary, re.M)
        if self.display_index is not None and (selected != [str(self.display_index)] or len(regions) != 1):
            raise PageUnconfirmed('DISPLAY_SCOPE_UNCONFIRMED')
        if not regions and self.display_index is None:
            return None  # Legacy offline evidence has no display metadata.
        if len(regions) != 1:
            raise PageUnconfirmed('DISPLAY_SCOPE_UNCONFIRMED')
        left, top, right, bottom = map(int, regions[0])
        if left >= right or top >= bottom:
            raise PageUnconfirmed('DISPLAY_SCOPE_UNCONFIRMED')
        return left, top, right, bottom

    def checked_point(self, text, point):
        point = list(map(int, point))
        region = self.display_region(text)
        if region is not None:
            left, top, right, bottom = region
            if not (left <= point[0] < right and top <= point[1] < bottom):
                raise PageUnconfirmed('ACTION_OUTSIDE_SELECTED_DISPLAY')
        return point

    def inspect(self, record: dict) -> str:
        text = snapshot_text(record)
        self.display_region(text)
        # Require the active tree to be Edge, rather than a background window in
        # the list of opened applications. Both current MCP indentations occur.
        tree = text.split('UI Tree:', 1)
        if len(tree) != 2:
            raise PageUnconfirmed('NO_ACTIVE_TREE')
        tree = tree[1]
        first_window = re.search(r'window "([^\n]+)"', tree)
        if not first_window or not re.search(r'Microsoft\u200b? Edge', first_window[1]):
            raise PageUnconfirmed('WRONG_FOREGROUND_APPLICATION')
        urls = re.findall(r'文档 .*?\[value:"(https://chat\.deepseek\.com/[^"\n]+)"\]', tree)
        if urls != [self.url]:
            raise PageUnconfirmed('CONVERSATION_MISMATCH')
        return tree

    def stage_action(self, record: dict, prompt: str) -> dict:
        if not prompt.strip() or '\n' in prompt or '\r' in prompt:
            raise ValueError('Only a nonempty single-line prompt can be staged')
        tree = self.inspect(record)
        editors = re.findall(r'\((-?\d+),(-?\d+)\) 编辑 "给 DeepSeek 发送消息"([^\n]*)', tree)
        if len(editors) != 1 or '[value:' in editors[0][2]:
            raise PageUnconfirmed('EDITOR_NOT_EMPTY_OR_AMBIGUOUS')
        if '正在思考' in tree:
            raise PageUnconfirmed('GENERATION_IN_PROGRESS')
        return {'tool': 'Type', 'arguments': {'loc': self.checked_point(snapshot_text(record), editors[0][:2]),
                                             'text': prompt, 'press_enter': False}}

    def submit_action(self, record: dict, prompt: str) -> dict:
        tree = self.inspect(record)
        editors = re.findall(r'编辑 "给 DeepSeek 发送消息"([^\n]*)', tree)
        if (len(editors) != 1 or '[focused]' not in editors[0]
                or f'[value:"{prompt}"]' not in editors[0]):
            raise PageUnconfirmed('STAGED_PROMPT_MISMATCH')
        if self.display_index is not None:
            points = re.findall(r'\((-?\d+),(-?\d+)\) 编辑 "给 DeepSeek 发送消息"[^\n]*', tree)
            if len(points) != 1:
                raise PageUnconfirmed('FOCUSED_EDITOR_POSITION_UNCONFIRMED')
            self.checked_point(snapshot_text(record), points[0])
        return {'tool': 'Shortcut', 'arguments': {'shortcut': 'enter'}}

    def expand_pasted_text_action(self, record: dict) -> dict:
        """Restore DeepSeek's long-paste attachment to its empty composer.

        This click never submits. The caller must read back its full original
        prompt again before requesting submit_action.
        """
        tree = self.inspect(record)
        editors = re.findall(r'编辑 "给 DeepSeek 发送消息"([^\n]*)', tree)
        buttons = re.findall(r'\((-?\d+),(-?\d+)\) 按钮 "粘贴原文至输入框"[^\n]*', tree)
        if (len(editors) != 1 or '[value:' in editors[0] or '[focused]' not in editors[0]
                or len(buttons) != 1 or '正在思考' in tree):
            raise PageUnconfirmed('PASTED_TEXT_EXPANSION_UNCONFIRMED')
        if self.display_index is not None:
            points = re.findall(r'\((-?\d+),(-?\d+)\) 编辑 "给 DeepSeek 发送消息"[^\n]*', tree)
            if len(points) != 1:
                raise PageUnconfirmed('FOCUSED_EDITOR_POSITION_UNCONFIRMED')
            self.checked_point(snapshot_text(record), points[0])
        return {'tool': 'Click', 'arguments': {'loc': self.checked_point(snapshot_text(record), buttons[0])}}

    def completed_text(self, record: dict, token: str) -> str:
        """Require unique response boundary nodes plus the visible final controls.

        Callers ask for boundary markers on separate lines. These markers are
        removed before review/delivery; unmarked or partial output stays pending.
        """
        if not re.fullmatch(r'[a-zA-Z0-9_]{12,80}', token):
            raise ValueError('Invalid run-specific response token')
        tree = self.inspect(record)
        if '正在思考' in tree or '按钮 "朗读"' not in tree:
            raise PageUnconfirmed('OUTPUT_NOT_COMPLETE')
        nodes = re.findall(r'^[ \t│├└─]*text "(.*)"\s*$', tree, re.M)
        begin, end = f'BEGIN_{token}', f'END_{token}'
        if nodes.count(begin) != 1:
            raise PageUnconfirmed('RESPONSE_BOUNDARIES_UNCONFIRMED')
        i = nodes.index(begin)
        # DeepSeek may mention the closing marker in its completed reasoning
        # before the final response starts. Only a closing node after the one
        # unique opening node can bound this response. Multiple possible
        # closures after that opening remain ambiguous and are rejected.
        endings = [j for j in range(i + 1, len(nodes)) if nodes[j] == end]
        if len(endings) != 1:
            raise PageUnconfirmed('RESPONSE_BOUNDARIES_UNCONFIRMED')
        j = endings[0]
        if j <= i + 1:
            raise PageUnconfirmed('EMPTY_OR_REVERSED_RESPONSE')
        answer = '\n'.join(nodes[i + 1:j]).strip()
        if not answer:
            raise PageUnconfirmed('EMPTY_RESPONSE')
        return answer
