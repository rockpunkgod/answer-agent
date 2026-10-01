# Crawl4AI reference-page capture

This tool captures one explicitly supplied public URL as local reference material. It does not search for a question, confirm an exact question match, change a student's prompt, or connect the reference to the helpdesk workflow. The page captured so far is related subject material only; it is not established as the student's original question.

## Environment

- Python: 3.13.15
- Crawl4AI: 0.9.4
- Browser: headless Chromium installed by `crawl4ai-setup` (Playwright browser build 153.0.8010.12)
- Environment: isolated at `.venv-crawl4ai`; not the helpdesk dependency environment

Install and browser setup commands used:

```powershell
python -m venv .venv-crawl4ai
.\.venv-crawl4ai\Scripts\python.exe -m pip install --upgrade pip
.\.venv-crawl4ai\Scripts\python.exe -m pip install crawl4ai
$env:PYTHONUTF8='1'; .\.venv-crawl4ai\Scripts\crawl4ai-setup.exe
```

The setup command installed the Playwright Chromium browser and FFmpeg. Its optional next step began downloading a second Patchright browser; that download was interrupted to avoid duplicating the browser payload. The completed Chromium installation worked for the capture.

## Run

```powershell
.\.venv-crawl4ai\Scripts\python.exe tools\crawl_reference.py --url https://zy.21cnjy.com/23140098
.\.venv-crawl4ai\Scripts\python.exe tools\crawl_reference.py --self-test
```

Each default run writes to a unique dated directory under `data/private/reference-crawl/`. `--out-dir` can set an exact output directory. The tool accepts one HTTP(S) public URL, rejects credentials and local/private IP destinations, constrains browser requests to the supplied host, checks `robots.txt`, and does not crawl linked pages. The offline self-test checks URL validation without making network requests.

## Captured page

The single live capture completed on 2026-09-30 at 01:20 UTC:

- URL: `https://zy.21cnjy.com/23140098`
- Result: HTTP 200, Crawl4AI reported success, final URL stayed on the validated host
- Captured content: 28,036 Markdown characters; the saved Markdown file is 31,913 bytes
- Body SHA-256: `944a14cc6f64303d4e0e4af6b2eee41063848664e89c27bf359637eff22b816e`
- Files: `data/private/reference-crawl/reference-crawl.md` and `data/private/reference-crawl/reference-crawl.json`

The visible page includes a high-school English cloze passage beginning “Launched in 2021, the United Nations World Tourism Organization’s Best Tourism Villages program...”. Searching the topic phrase found this related passage, but the exact phrase pair “Best Tourism Villages” / “To recognize rural villages” did not establish an exact source match. This one-page capture only demonstrates that this related public page can be captured; it does not establish an exact match or end-to-end integration. No student image or prompt was uploaded, and the captured text was not applied to any student question.
