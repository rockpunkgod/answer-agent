"""Explicit course routing from ANSWER's documented split, not student commands."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TeachingRoute:
    skill: str
    module: str
    agent: str | None
    checker: str | None  # Repository-relative dependency, declared by ANSWER.


OBJECTIVE = 'gaokao-english'
GRAMMAR = 'gaokao-grammar-fill'
WRITING = 'gaokao-writing'
QA = 'kaiming-english-qa'
# ANSWER b04ebc26 README explicitly shares this checker with objective types.
# Do not search other skills or manufacture a replacement when it is missing.
OBJECTIVE_CHECKER = GRAMMAR + '/scripts/check_lesson.py'
ROUTES = {
    '阅读理解': TeachingRoute(OBJECTIVE, 'references/reading-comprehension.md', 'gaokao-objective', OBJECTIVE_CHECKER),
    '七选五': TeachingRoute(OBJECTIVE, 'references/seven-five.md', 'gaokao-objective', OBJECTIVE_CHECKER),
    '完形填空': TeachingRoute(OBJECTIVE, 'references/cloze.md', 'gaokao-objective', OBJECTIVE_CHECKER),
    '语法填空': TeachingRoute(GRAMMAR, 'references/grammar-fill.md', 'gaokao-grammar-fill', OBJECTIVE_CHECKER),
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
    The checker path is declared by this ANSWER version, never a fallback.
    """
    root = Path(repository).resolve(strict=True)
    route = resolve_route(question_type, request_kind)
    names = [f'{route.skill}/SKILL.md', f'{route.skill}/{route.module}']
    if route.skill == OBJECTIVE:
        names += [f'{route.skill}/references/{name}.md' for name in ('delivery-contract', 'router', 'evidence-gaps')]
    if route.agent:
        names.append(f'agents/{route.agent}.md')
    if route.checker:
        names.append(route.checker)
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
