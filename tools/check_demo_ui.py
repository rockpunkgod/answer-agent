"""Open a temporary demo server in Chromium and verify the visible flow.

Usage: python tools/check_demo_ui.py [--screenshot path.png]
"""
import argparse
from pathlib import Path
import sys
import tempfile
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpdesk.demo_server import DemoHTTPServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as directory:
        server = DemoHTTPServer(("127.0.0.1", 0), Path(directory) / "ui.db")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1365, "height": 900}, device_scale_factor=1)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                page.get_by_text("模拟环境就绪").wait_for()
                for name in ("Alice 提交第 12 题", "Bob 提交同一题", "模拟发送一条确认收到",
                             "Alice 追问：为什么不选 B", "生成最新一轮答复", "批准最新待审核答案",
                             "模拟发送已批准答案", "Alice 追加第 13 题", "Alice 更正为否定题干",
                             "生成最新一轮答复", "Alice 更正选项顺序", "生成最新一轮答复",
                             "批准最新待审核答案", "模拟发送已批准答案", "Alice 提出异议",
                             "无法判断的消息 → 人工队列", "模拟发送状态未知"):
                    page.get_by_role("button", name=name).click()
                    page.locator("#status").filter(has_text="已执行").wait_for()
                page.locator("#outbox").get_by_text("发送状态未知", exact=False).first.wait_for()
                page.locator("#answers").get_by_text("已失效", exact=False).first.wait_for()
                if page.locator("#answers").count() != 1 or page.locator("#answers .entry").count() < 2:
                    raise AssertionError("Answer version history is not visible")
                page.get_by_role("button", name="停止发送").click()
                page.locator("#status").filter(has_text="已执行").wait_for()
                page.get_by_role("button", name="恢复发送").click()
                page.locator("#status").filter(has_text="已执行").wait_for()
                page.get_by_role("button", name="查看统计草稿").click()
                page.locator("#performance-result").filter(has_text="白天综合").wait_for()
                page.locator("#performance-result").filter(has_text="语法听力").wait_for()
                page.locator("#performance-result").filter(has_text="夜间答题").wait_for()
                if "消息尚未归入绩效计量单元" not in page.locator("#performance-result").inner_text():
                    raise AssertionError("Missing evidence must appear in performance review queue")
                for clock, expected in (("2026-09-30T06:59:59+08:00", "2026-09-29"),
                                        ("2026-09-30T07:00:00+08:00", "2026-09-30")):
                    page.clock.set_fixed_time(clock)
                    page.reload()
                    page.get_by_text("模拟环境就绪").wait_for()
                    if page.locator("#report-date").input_value() != expected:
                        raise AssertionError("Default report date must switch at 07:00 Shanghai time")
                if errors:
                    raise AssertionError("Browser errors: " + "; ".join(errors))
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.screenshot), full_page=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    print("Chromium demo UI acceptance passed")


if __name__ == "__main__":
    main()
