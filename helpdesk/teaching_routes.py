"""Explicit course routing from ANSWER's documented split, not student commands."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TeachingRoute:
    skill: str
    module: str
    agent: str | None
    checker: str | None


OBJECTIVE = 'gaokao-english'
GRAMMAR = 'gaokao-english-formal-backup-20260919-01'
WRITING = 'gaokao-writing'
QA = 'kaiming-english-qa'
ROUTES = {
    '阅读理解': TeachingRoute(OBJECTIVE, 'references/reading-comprehension.md', 'gaokao-objective', 'scripts/check_lesson.py'),
    '七选五': TeachingRoute(OBJECTIVE, 'references/seven-five.md', 'gaokao-objective', 'scripts/check_lesson.py'),
    '完形填空': TeachingRoute(OBJECTIVE, 'references/cloze.md', 'gaokao-objective', 'scripts/check_lesson.py'),
    '语法填空': TeachingRoute(GRAMMAR, 'references/grammar-fill.md', 'gaokao-grammar-fill', 'scripts/check_lesson.py'),
    '应用文': TeachingRoute(WRITING, 'references/application-writing-and-correction.md', 'gaokao-writing', None),
    '读后续写': TeachingRoute(WRITING, 'references/continuation-writing-and-correction.md', 'gaokao-writing', None),
}


def resolve_route(question_type, request_kind='answer'):
    """Inputs must be verified classification, never a file path from a message."""
    if request_kind == 'course_basis':
        return TeachingRoute(QA, 'references/topics.md', None, None)
    if request_kind not in ('answer', 'method', 'correction'):
        raise ValueError('Unknown teaching request kind')
    if question_type not in ROUTES:
        raise ValueError('Question type requires review')
    route = ROUTES[question_type]
    if request_kind == 'correction' and route.skill != WRITING:
        raise ValueError('Essay correction requires a writing type')
    return route


def inspect_route(repository, question_type, request_kind='answer'):
    """Inspect dependencies without installing skills or executing their scripts.

    Presence is not an approval of teaching contents or answer correctness.
    Missing checkers are never borrowed from another skill.
    """
    root = Path(repository).resolve(strict=True)
    route = resolve_route(question_type, request_kind)
    names = [f'{route.skill}/SKILL.md', f'{route.skill}/{route.module}']
    if route.skill == OBJECTIVE:
        names += [f'{route.skill}/references/{name}.md' for name in ('delivery-contract', 'router', 'evidence-gaps')]
    if route.agent:
        names.append(f'agents/{route.agent}.md')
    if route.checker:
        names.append(f'{route.skill}/{route.checker}')
    missing = []
    for name in names:
        path = root / name
        if not path.resolve().is_relative_to(root):
            raise ValueError('Course dependency escapes repository')
        if not path.is_file():
            missing.append(name)
    return {'question_type': question_type, 'request_kind': request_kind,
            'skill': route.skill, 'agent': route.agent, 'module': route.module,
            'dependencies': names, 'missing_dependencies': missing,
            'dependency_status': 'MISSING' if missing else 'PRESENT',
            'generation_authorized': False,
            'note': '文件齐全仅证明依赖存在，尚需课程内容审核与真实流程验收。'}
