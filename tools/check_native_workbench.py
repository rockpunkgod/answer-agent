"""Verify actual staged originals in the local workbench; read-only browser actions."""
import json
from pathlib import Path
import sys
from urllib.request import urlopen


def main():
    from playwright.sync_api import sync_playwright
    url = 'http://127.0.0.1:8766'
    with urlopen(url + '/api/native-records', timeout=10) as response:
        data = json.load(response)
    assert data['coverage_complete'] is False
    assert data['formal_statistics_eligible'] is False
    assert data['count'] == len(data['records'])
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1365, 'height': 900})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_text('真实已准备任务就绪').wait_for()
            page.get_by_role('button', name='查看已采集原文').click()
            page.locator('#native-records details').first.wait_for()
            rows = page.locator('#native-records details')
            assert rows.count() == data['count']
            for index, record in enumerate(data['records']):
                row = rows.nth(index)
                row.locator('summary').click()
                assert row.locator('pre').text_content() == record['original_text']
                assert record['observed_group_label'] in row.locator('summary').text_content()
                assert '原始时间待核验' in row.locator('summary').text_content()
                assert record['raw_text_sha256'] in row.text_content()
                assert record['original_message_time'] is None
                assert record['sender'] is None
                if index != 0:
                    row.locator('summary').click()
            page.locator('#native-records').scroll_into_view_if_needed()
            evidence = Path('data/private/acceptance/native-workbench-20260930.png')
            page.screenshot(path=str(evidence))
            assert not errors, errors
        finally:
            browser.close()
    print(json.dumps({'records_verified': data['count'], 'original_text_equal': True,
                     'coverage_complete': False, 'formal_statistics_eligible': False,
                     'desktop_actions': False, 'outbound_actions': False,
                     'screenshot': str(evidence)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
