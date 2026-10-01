"""Boundaries only: real integrations are deliberately not reported as working."""
from dataclasses import dataclass
from typing import Protocol

from .domain import Intent


class IntentClassifier(Protocol):
    def classify(self, text: str) -> Intent: ...


class OCRAdapter(Protocol):
    def extract(self, attachment_id: str) -> dict: ... # Candidates, not verified fields.


class ReferenceSearch(Protocol):
    def search(self, query: str) -> list[dict]: ... # Authorized sources only.


class AnswerGenerator(Protocol):
    def generate(self, context: dict) -> "GenerationResult": ...


class DesktopAdapter(Protocol):
    def inspect(self) -> dict: ...
    # Sending is deliberately absent until the Outbox execution gate is implemented.


@dataclass(frozen=True)
class GenerationResult:
    text: str
    complete: bool
    adapter: str
    simulated: bool


class MockClassifier:
    """Demo heuristics, not Luna integration. Cannot supply identities or verified text."""
    def classify(self, text: str) -> Intent:
        if any(s in text for s in ("拍错", "发错", "更正")):
            return Intent.CORRECTION
        if any(s in text for s in ("老师答案", "答案不对", "有争议")):
            return Intent.DISPUTE
        if "为什么不选" in text:
            return Intent.FOLLOWUP
        if "补图" in text or "补充" in text:
            return Intent.SUPPLEMENT
        if "那第" in text:
            return Intent.SUBQUESTION
        if text.strip() in ("谢谢", "好的", "明白了"):
            return Intent.IRRELEVANT
        return Intent.UNKNOWN


class MockDeepSeek:
    def generate(self, context: dict) -> GenerationResult:
        if context.get("simulation") is not True:
            raise ValueError("Mock adapter only accepts explicitly simulated work")
        q = context["student_question"]
        options = "; ".join(f"{o['label']}: {o['verified_text']}" for o in q["options"])
        return GenerationResult(f"[模拟输出，不是教学答案] 第{q['number']}题。学生选项：{options}",
                                True, "MOCK_NOT_REAL_DEEPSEEK", True)


class DisabledDesktop:
    def inspect(self) -> dict:
        return {"available": False, "reason": "No verified Windows-MCP/WeCom adapter"}


def require_simulation_config(config: dict):
    """Stage one refuses production mode even when configuration requests it."""
    if config.get("mode", "dry_run") != "dry_run" or config.get("allow_real_send", False):
        raise ValueError("Real sending is not implemented or authorized in phase one")
    if config.get("stop_requested", False):
        raise ValueError("Stop switch enabled")
    if config.get("deepseek", {}).get("adapter", "mock") != "mock":
        raise ValueError("Only mock DeepSeek is implemented in phase one")
