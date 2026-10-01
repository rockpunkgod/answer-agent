"""One-time real-workbench local task intake acceptance; no generation or delivery."""
import json
from pathlib import Path
import sqlite3


def main():
    from playwright.sync_api import sync_playwright
    evidence = Path('data/private/acceptance/operator-workbench-20260930.json')
    if evidence.exists():
        raise ValueError('Acceptance already recorded; do not create another task')
    snapshot = json.loads(Path('data/private/automatic-upload-20260930/rework2-snapshot.json').read_text(encoding='utf-8'))
    question = snapshot['student_question']
    database = Path('data/automatic-upload-real-test.db').resolve()
    def checks():
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        try:
            return {'delivery_checks': connection.execute('SELECT COUNT(*) FROM delivery_checks').fetchone()[0],
                    'performance_units': connection.execute('SELECT COUNT(*) FROM performance_units').fetchone()[0]}
        finally:
            connection.close()
    before = checks()
    errors = []
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1365, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:8766/')
            page.get_by_text('真实已准备任务就绪').wait_for()
            form = page.locator('#operator-form')
            for name, value in {'passage': snapshot['student_material'], 'stem': question['verified_stem'],
                                'number': question['number'], **{o['label']: o['verified_text'] for o in question['options']}}.items():
                form.locator(f'[name="{name}"]').fill(value)
            existing = page.locator('#operator-tasks details').count()
            page.get_by_role('button', name='保存新题草稿').click()
            page.locator('#status').filter(has_text='新题草稿已保存').wait_for()
            assert page.locator('#operator-tasks details').count() == existing + 1
            page.get_by_role('button', name='保存新题草稿').click()
            page.locator('#status').filter(has_text='新题草稿已保存').wait_for()
            assert page.locator('#operator-tasks details').count() == existing + 1
            page.locator('#operator-reviewer').fill('Codex local operator test')
            page.locator('#operator-evidence').fill('OPERATOR_TEST: copy of previously photo-verified rework2-snapshot; no new student message or timestamp')
            item = page.locator('#operator-tasks details').first
            item.locator('summary').click()
            item.get_by_role('button', name='核对题面并建立测试任务').click()
            page.locator('#status').filter(has_text='题面审核已保存').wait_for()
            item = page.locator('#operator-tasks details').first
            item.locator('summary').click()
            item.get_by_role('button', name='冻结已审核生成输入').click()
            page.locator('#status').filter(has_text='输入已冻结').wait_for()
            data = page.request.get('http://127.0.0.1:8766/api/operator-tasks').json()
            draft = data['drafts'][0]
            task = next(row for row in data['tasks'] if row['id'] == draft['task_id'])
            assert task['run_id'] and task['generation_submitted_by_intake'] is False
            assert task['label'] == 'OPERATOR_TEST'
            page.locator('#operator-tasks').scroll_into_view_if_needed()
            image = evidence.with_suffix('.png')
            page.screenshot(path=str(image))
            assert not errors, errors
        finally:
            browser.close()
    after = checks()
    assert before == after, (before, after)
    result = {'draft_id': draft['id'], 'task_id': task['id'], 'run_id': task['run_id'],
              'duplicate_save_created_new_draft': False, 'generation_submitted': False,
              'desktop_actions': False, 'outbound_actions': False,
              'formal_statistics_eligible': False, 'counts_before': before, 'counts_after': after,
              'screenshot': str(image)}
    with evidence.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
