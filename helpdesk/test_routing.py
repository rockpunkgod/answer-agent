"""Operator-controlled test routing contract; this module never sends messages.

The original student/group binding remains part of every envelope for audit. A
future sender must verify the selected UI account against the configured
platform and stable key before it can use the test target.
"""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re

from .delivery import BoundMessage


TEST_DISPLAY_NAME = "苇中鹤"
TEST_PLATFORM = "wecom"
PERSONAL_TEST_PLATFORM = 'personal_wechat_test'


@dataclass(frozen=True)
class PersonalWeChatTestRecipient:
    """One explicitly reviewed account + test room, separate from WeCom routing."""
    account_id: str
    room_id: str
    verification_evidence: str
    verified_at: datetime
    display_name: str = '测试'
    platform: str = PERSONAL_TEST_PLATFORM

    def __post_init__(self):
        if (self.platform != PERSONAL_TEST_PLATFORM or self.display_name != '测试'
                or not isinstance(self.account_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]+', self.account_id)
                or not isinstance(self.room_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]+@chatroom', self.room_id)
                or not isinstance(self.verification_evidence, str)
                or not self.verification_evidence.strip()
                or not isinstance(self.verified_at, datetime)
                or self.verified_at.utcoffset() is None):
            raise ValueError('EXPLICIT_PERSONAL_TEST_ACCOUNT_AND_ROOM_REQUIRED')

    @property
    def stable_key(self):
        return 'personal-wechat-test:' + sha256(json.dumps(
            [self.account_id, self.room_id], separators=(',', ':')).encode()).hexdigest()

    def frozen_evidence(self):
        return json.dumps({'format': 'personal-test-target-v1', 'account_id': self.account_id,
                           'room_id': self.room_id, 'evidence': self.verification_evidence}, sort_keys=True)


def test_recipient_from_mapping(mapping):
    """Validate existing frozen target columns, including during stale-source readback."""
    if mapping['target_platform'] == PERSONAL_TEST_PLATFORM:
        try:
            evidence = json.loads(mapping['verification_evidence'])
            if set(evidence) != {'format', 'account_id', 'room_id', 'evidence'} or evidence['format'] != 'personal-test-target-v1':
                raise ValueError()
            recipient = PersonalWeChatTestRecipient(evidence['account_id'], evidence['room_id'],
                evidence['evidence'], datetime.fromisoformat(mapping['verified_at']),
                mapping['target_display_name'])
            if recipient.stable_key != mapping['target_key']:
                raise ValueError()
            return recipient
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('TEST_TARGET_INVALID') from None
    if (mapping['target_platform'] != TEST_PLATFORM or mapping['target_display_name'] != TEST_DISPLAY_NAME
            or not mapping['target_key'] or mapping['target_key'] == TEST_DISPLAY_NAME):
        raise ValueError('TEST_TARGET_INVALID')
    return TestRecipient(TEST_PLATFORM, mapping['target_key'], TEST_DISPLAY_NAME,
                         mapping['verification_evidence'], datetime.fromisoformat(mapping['verified_at']))


@dataclass(frozen=True)
class TestRecipient:
    """A test account entered and verified through trusted operator config."""

    platform: str
    stable_key: str
    display_name: str
    verification_evidence: str
    verified_at: datetime

    def __post_init__(self):
        for field in ("platform", "stable_key", "verification_evidence"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{field} must be an explicit non-empty operator value")
        if self.display_name != TEST_DISPLAY_NAME:
            raise ValueError("Test recipient must be 苇中鹤")
        if self.platform != TEST_PLATFORM:
            raise ValueError("User authorized only the WeCom contact, not personal Weixin")
        if self.stable_key == self.display_name:
            raise ValueError("A display name is not a stable target key")
        if not isinstance(self.verified_at, datetime) or self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("verified_at must be a timezone-aware datetime")


@dataclass(frozen=True)
class TestEnvelope:
    """Resolved test target plus unchanged original business destination."""

    original: BoundMessage
    test_target: TestRecipient

    @property
    def outbox_id(self) -> str:
        return self.original.outbox_id

    @property
    def body(self) -> str:
        return self.original.body

    @property
    def body_hash(self) -> str:
        return sha256(self.body.encode("utf-8")).hexdigest()

    @property
    def original_binding_id(self) -> str:
        return self.original.binding_id

    @property
    def original_group_key(self) -> str:
        return self.original.group_key

    @property
    def original_student_key(self) -> str:
        return self.original.student_key

    @property
    def target_platform(self) -> str:
        return self.test_target.platform

    @property
    def target_key(self) -> str:
        return self.test_target.stable_key

    def require_selected_target(self, platform: str, stable_key: str) -> None:
        """Fail closed if a future UI adapter selected only a same-name contact."""
        if (platform, stable_key) != (self.target_platform, self.target_key):
            raise ValueError("TEST_TARGET_IDENTITY_MISMATCH")


class TestRoutingPolicy:
    """Disabled by default; only an explicit operator recipient enables routing.

    ``resolve`` accepts only the already-bound business message. It offers no
    target argument, so student/model text cannot choose a recipient.
    """

    def __init__(self, recipient: TestRecipient | None = None, *, enabled: bool = False):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        if recipient is not None and not isinstance(recipient, TestRecipient):
            raise ValueError("recipient must be a verified TestRecipient")
        if enabled and recipient is None:
            raise ValueError("Enabled test routing requires an operator recipient")
        self._recipient = recipient
        self._enabled = enabled

    def resolve(self, original: BoundMessage) -> TestEnvelope:
        if not self._enabled or self._recipient is None:
            raise ValueError("REAL_SEND_DISABLED_TEST_ROUTE_NOT_CONFIGURED")
        if not isinstance(original, BoundMessage) or not all((
            original.outbox_id, original.binding_id, original.group_key,
            original.student_key, original.body,
        )):
            raise ValueError("Original verified business binding is required")
        return TestEnvelope(original=original, test_target=self._recipient)
