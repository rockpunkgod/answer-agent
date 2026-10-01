"""Capture one public reference page with Crawl4AI for local inspection.

This is deliberately not a question-ingestion workflow: output is reference
material only and never modifies student prompts or application data.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import ipaddress
from importlib.metadata import version as package_version
import json
from pathlib import Path
import socket
import sys
import uuid
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "private" / "reference-crawl"
MAX_BODY_CHARS = 1_500_000


def validate_public_url(url: str, *, expected_host: str | None = None) -> str:
    """Validate one HTTP(S) public hostname; reject local/private targets."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid URL: {exc}") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("only http:// and https:// URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not allowed")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise ValueError("URL must include a hostname")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("invalid port")
    if expected_host is not None and host != expected_host:
        raise ValueError("cross-host navigation/resource blocked")

    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("local hostnames are not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip.is_global:
            raise ValueError("non-public IP addresses are not allowed")
    else:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        except OSError as exc:
            raise ValueError(f"hostname did not resolve: {exc}") from exc
        if not addresses:
            raise ValueError("hostname did not resolve")
        for address in addresses:
            resolved = ipaddress.ip_address(address.split("%", 1)[0])
            if not resolved.is_global:
                raise ValueError("hostname resolves to a non-public IP address")
    return host


def _offline_self_test() -> None:
    # A public literal IP keeps this check independent of DNS/network access.
    assert validate_public_url("https://8.8.8.8/path") == "8.8.8.8"
    for blocked in (
        "file:///etc/passwd",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://169.254.10.20/",
        "https://user:pass@example.com/",
    ):
        try:
            validate_public_url(blocked)
        except ValueError:
            continue
        raise AssertionError(f"expected rejection: {blocked}")
    try:
        validate_public_url("https://example.net/", expected_host="example.com")
    except ValueError:
        pass
    else:
        raise AssertionError("expected cross-host rejection")
    print("offline self-test passed: public URL accepted; local/private/credential/cross-host URLs rejected")


async def crawl_one(url: str, output_dir: Path) -> int:
    source_host = validate_public_url(url)
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    except ImportError as exc:
        raise RuntimeError("Crawl4AI is not installed in this Python environment") from exc
    crawl4ai_version = package_version("crawl4ai")

    output_dir.mkdir(parents=True, exist_ok=True)
    config = BrowserConfig(browser_type="chromium", headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.DISABLED,
        check_robots_txt=True,
        word_count_threshold=1,
        page_timeout=45_000,
        exclude_external_links=True,
        remove_overlay_elements=True,
    )

    async with AsyncWebCrawler(config=config) as crawler:
        # Constrain all browser requests and navigations to the explicitly
        # supplied host. This also prevents third-party resources from being
        # fetched during this single-page capture.
        async def same_public_host_only(page, context=None, config=None, **kwargs):
            async def guard(route):
                try:
                    validate_public_url(route.request.url, expected_host=source_host)
                except ValueError:
                    await route.abort()
                    return
                await route.continue_()
            await page.route("**/*", guard)

        crawler.crawler_strategy.set_hook("on_page_context_created", same_public_host_only)
        result = await crawler.arun(url, config=run_config)

    final_url = getattr(result, "url", None) or url
    try:
        validate_public_url(final_url, expected_host=source_host)
        final_url_allowed = True
    except ValueError:
        final_url_allowed = False

    markdown = (result.markdown or "").strip()
    if not markdown:
        markdown = (result.cleaned_html or "").strip()
    # Never persist content if the browser ended at an address outside the
    # validated public host boundary.
    if not final_url_allowed:
        markdown = ""
    markdown = markdown[:MAX_BODY_CHARS]
    body_text = markdown + ("" if markdown.endswith("\n") else "\n")
    digest = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
    crawl_success = bool(result.success and final_url_allowed)

    metadata = {
        "requested_url": url,
        "final_url": final_url,
        "final_url_allowed": final_url_allowed,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "crawl4ai_version": crawl4ai_version,
        "success": crawl_success,
        "status_code": getattr(result, "status_code", None),
        "title": getattr(result, "title", None),
        "body_file": "reference-crawl.md",
        "body_chars": len(markdown),
        "body_sha256": digest,
        "source_hash": digest,
        "reference_only": True,
        "student_content_uploaded": False,
        "robots_txt_checked": True,
        "automatically_followed_links": False,
        "body_truncated": len(markdown) >= MAX_BODY_CHARS,
        "error": (
            "final URL rejected by public-host restriction"
            if not final_url_allowed
            else getattr(result, "error_message", None)
        ),
    }

    (output_dir / "reference-crawl.md").write_text(body_text, encoding="utf-8")
    (output_dir / "reference-crawl.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0 if crawl_success else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="one public HTTP(S) URL; no crawling of linked pages")
    parser.add_argument("--out-dir", type=Path, help="exact output directory; default creates a unique dated subdirectory")
    parser.add_argument("--self-test", action="store_true", help="run offline URL-safety assertions")
    args = parser.parse_args(argv)
    if args.self_test:
        _offline_self_test()
        return 0
    if not args.url:
        parser.error("--url is required unless --self-test is used")
    try:
        output_dir = args.out_dir.resolve() if args.out_dir else (
            DEFAULT_OUTPUT_ROOT / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        )
        return asyncio.run(crawl_one(args.url, output_dir))
    except Exception as exc:
        print(f"crawl failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
