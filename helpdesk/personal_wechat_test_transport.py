"""Test-group-only native input boundary; not a collector or a delivery receipt.

Reuse the existing Win32 implementation. No upstream sending package, Qt gate,
MCP process, account modification or production source promotion is loaded.
The caller must still bind the current chat, task/version and original Outbox
through its guard; matching the Weixin process alone never identifies a chat.
"""
from copy import deepcopy
import math
import re
import time

from .wecom_native import NativeWeComTransport


class NativePersonalWeChatTestTransport(NativeWeComTransport):
    process_name = 'Weixin'
    window_titles = ('微信', 'Weixin')
    backend_name = 'PERSONAL_WECHAT_TEST_NATIVE'
    private_subdirectory = 'personal-wechat-test-native'

    @staticmethod
    def validate_pin(pin):
        if (not isinstance(pin, dict)
                or pin.get('platform') != 'personal_wechat_test'
                or pin.get('display_name') != '测试'
                or pin.get('reviewed') is not True
                or not isinstance(pin.get('target_key'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]+@chatroom', pin['target_key'])
                or not isinstance(pin.get('verification_evidence'), str)
                or not pin['verification_evidence'].strip()):
            raise ValueError('REVIEWED_PERSONAL_TEST_GROUP_REQUIRED')
        expires = pin.get('expires_at')
        if (type(expires) not in (int, float) or not math.isfinite(expires)
                or expires <= time.time()):
            raise ValueError('PERSONAL_TEST_PIN_EXPIRED')

    def __init__(self, pin, root, *, guard):
        self.validate_pin(pin)
        if not callable(guard):
            raise ValueError('PERSONAL_TEST_TASK_AND_TARGET_GUARD_REQUIRED')
        super().__init__(deepcopy(pin), root, guard=guard)

    def _check_input(self, point=None):
        self.validate_pin(self.pin)
        super()._check_input(point)

    def activate(self):
        self.validate_pin(self.pin)
        return super().activate()
