"""Versioned business commands. No raw desktop tools, paths or credentials.

The server owns business truth. Worker results describe observations and do not
by themselves change an Outbox to delivered. All dates are aware original UTC
instants; retries must never substitute collection time for a business time.
"""
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from enum import StrEnum
import json
import re

CONTRACT_VERSION = 1


class PermissionMode(StrEnum):
    OBSERVE_ONLY = 'OBSERVE_ONLY'
    ASSISTED = 'ASSISTED'
    CONTROLLED_AUTO = 'CONTROLLED_AUTO'


class Action(StrEnum):
    READ_MESSAGES = 'READ_MESSAGES'
    SAVE_ATTACHMENTS = 'SAVE_ATTACHMENTS'
    CHECK_SESSION = 'CHECK_SESSION'
    UPLOAD_APPROVED_ATTACHMENTS = 'UPLOAD_APPROVED_ATTACHMENTS'
    SUBMIT_BOUND_QUESTION = 'SUBMIT_BOUND_QUESTION'
    READ_BOUND_GENERATION = 'READ_BOUND_GENERATION'
    STAGE_OUTBOX = 'STAGE_OUTBOX'
    VERIFY_OUTBOX = 'VERIFY_OUTBOX'
    EXECUTE_APPROVED_OUTBOX = 'EXECUTE_APPROVED_OUTBOX'


class ExecutionStatus(StrEnum):
    NOT_STARTED = 'NOT_STARTED'
    RUNNING = 'RUNNING'
    SUCCEEDED_VERIFIED = 'SUCCEEDED_VERIFIED'
    FAILED_BEFORE_ACTION = 'FAILED_BEFORE_ACTION'
    FAILED_AFTER_ACTION = 'FAILED_AFTER_ACTION'
    OUTCOME_UNKNOWN = 'OUTCOME_UNKNOWN'
    WAITING_HUMAN = 'WAITING_HUMAN'
    STALE = 'STALE'
    CANCELLED = 'CANCELLED'


class HealthState(StrEnum):
    HEALTHY = 'HEALTHY'
    LOGIN_REQUIRED = 'LOGIN_REQUIRED'
    VERIFICATION_REQUIRED = 'VERIFICATION_REQUIRED'
    RATE_LIMITED = 'RATE_LIMITED'
    ACCESS_DENIED = 'ACCESS_DENIED'
    PAGE_CHANGED = 'PAGE_CHANGED'
    DESKTOP_UNAVAILABLE = 'DESKTOP_UNAVAILABLE'
    UNKNOWN = 'UNKNOWN'


class TakeoverState(StrEnum):
    AUTO_ACTIVE = 'AUTO_ACTIVE'
    REQUESTED = 'REQUESTED'
    QUIESCING = 'QUIESCING'
    OWNED = 'OWNED'
    RESUME_CHECK = 'RESUME_CHECK'


class SideEffect(StrEnum):
    NO_ACTION = 'NO_ACTION'
    UNVERIFIED = 'UNVERIFIED'
    VERIFIED = 'VERIFIED'
    UNKNOWN = 'UNKNOWN'


READ_ACTIONS = frozenset({Action.READ_MESSAGES, Action.SAVE_ATTACHMENTS,
    Action.CHECK_SESSION, Action.READ_BOUND_GENERATION, Action.VERIFY_OUTBOX})
WRITE_ACTIONS = frozenset(Action) - READ_ACTIONS
OUTBOX_ACTIONS = frozenset({Action.STAGE_OUTBOX, Action.VERIFY_OUTBOX, Action.EXECUTE_APPROVED_OUTBOX})
BROWSER_ACTIONS = frozenset({Action.CHECK_SESSION, Action.UPLOAD_APPROVED_ATTACHMENTS,
    Action.SUBMIT_BOUND_QUESTION, Action.READ_BOUND_GENERATION})
TERMINAL_STATUSES = frozenset(ExecutionStatus) - {ExecutionStatus.NOT_STARTED, ExecutionStatus.RUNNING}
_REF = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:@-]{0,199}\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_CODE = re.compile(r'[A-Z][A-Z0-9_:.-]{0,159}\Z')


def utc_now():
    return datetime.now(timezone.utc)


def instant(value):
    if not isinstance(value, str):
        raise ValueError('Aware ISO timestamp required')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Aware ISO timestamp required') from exc
    if parsed.tzinfo is None:
        raise ValueError('Naive time is not allowed')
    return parsed.astimezone(timezone.utc)


def reference(value):
    if not isinstance(value, str) or not _REF.fullmatch(value):
        raise ValueError('Opaque reference required; paths, URLs and free content are forbidden')
    return value


def digest(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError('SHA256 required')
    return value


def bounded_integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Finite bounded integer required')
    return value


def question_version(value, *, allow_empty=False):
    # Existing question_versions IDs are opaque strings, not ordinal numbers.
    if isinstance(value, str):
        return reference(value)
    return bounded_integer(value, 0 if allow_empty else 1, 1000000)


def boolean(value):
    if type(value) is not bool:
        raise ValueError('Boolean required')
    return value


def strict_keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError('Unexpected or missing contract fields')


def _decode(cls, value):
    names = {f.name for f in fields(cls)}
    strict_keys(value, names)
    return cls(**value)


@dataclass(frozen=True)
class ExecutionBudget:
    total_seconds: int = 600
    max_tool_calls: int = 40
    max_state_reads: int = 20
    max_refreshes: int = 0
    max_no_progress: int = 5
    read_retries: int = 2
    generation_wait_seconds: int = 300

    def __post_init__(self):
        for name, low, high in [('total_seconds', 1, 3600), ('max_tool_calls', 1, 500),
                ('max_state_reads', 1, 200), ('max_refreshes', 0, 3),
                ('max_no_progress', 1, 20), ('read_retries', 0, 5),
                ('generation_wait_seconds', 1, 1800)]:
            bounded_integer(getattr(self, name), low, high)
        if self.generation_wait_seconds > self.total_seconds:
            raise ValueError('Generation budget exceeds total budget')

    @classmethod
    def from_dict(cls, value):
        return _decode(cls, value)


def validate_parameters(action, params):
    specification = {
        Action.READ_MESSAGES: ({'cursor_ref'}, {'limit'}),
        Action.SAVE_ATTACHMENTS: ({'message_ref', 'attachment_refs'}, set()),
        Action.CHECK_SESSION: ({'session_ref'}, set()),
        Action.UPLOAD_APPROVED_ATTACHMENTS: ({'session_ref', 'attachment_refs', 'manifest_sha256'}, set()),
        Action.SUBMIT_BOUND_QUESTION: ({'session_ref', 'input_ref', 'input_sha256'}, set()),
        Action.READ_BOUND_GENERATION: ({'session_ref', 'generation_ref'}, set()),
        Action.STAGE_OUTBOX: ({'content_ref', 'body_sha256'}, set()),
        Action.VERIFY_OUTBOX: ({'content_ref', 'body_sha256', 'execution_command_id'}, set()),
        Action.EXECUTE_APPROVED_OUTBOX: ({'content_ref', 'body_sha256', 'purpose'}, set()),
    }
    required, optional = specification[action]
    strict_keys(params, required, optional)
    for key, value in params.items():
        if key == 'attachment_refs':
            if not isinstance(value, list) or not 1 <= len(value) <= 30 or len(set(value)) != len(value):
                raise ValueError('Bounded unique attachment references required')
            for item in value:
                reference(item)
        elif key.endswith('sha256'):
            digest(value)
        elif key == 'limit':
            bounded_integer(value, 1, 200)
        elif key == 'purpose':
            if value not in {'ACK', 'CLARIFICATION', 'ANSWER', 'CORRECTION'}:
                raise ValueError('Unsupported Outbox purpose')
        else:
            reference(value)
    # Remove caller ownership of nested lists/dicts.
    return json.loads(json.dumps(params))


@dataclass(frozen=True)
class WorkerCommand:
    contract_version: int
    command_id: str
    task_id: str
    outbox_id: str | None
    worker_id: str
    account_id: str
    platform: str
    target_scope: str
    profile_id: str | None
    session_id: str | None
    action: Action
    parameters: dict
    question_version: str | int
    context_revision: int
    authorization_ref: str
    created_at: str
    expires_at: str
    idempotency_key: str
    execution_epoch: int
    budget: ExecutionBudget

    def __post_init__(self):
        if type(self.contract_version) is not int or self.contract_version != CONTRACT_VERSION:
            raise ValueError('Unsupported Worker contract')
        for name in ('command_id', 'task_id', 'worker_id', 'account_id', 'target_scope', 'authorization_ref', 'idempotency_key'):
            reference(getattr(self, name))
        for name in ('outbox_id', 'profile_id', 'session_id'):
            if getattr(self, name) is not None:
                reference(getattr(self, name))
        if self.platform not in {'wecom', 'deepseek'}:
            raise ValueError('Platform is not allowed')
        object.__setattr__(self, 'action', Action(self.action))
        object.__setattr__(self, 'budget', self.budget if isinstance(self.budget, ExecutionBudget) else ExecutionBudget.from_dict(self.budget))
        object.__setattr__(self, 'parameters', validate_parameters(self.action, self.parameters))
        if self.action in OUTBOX_ACTIONS and (not self.outbox_id or self.platform != 'wecom'):
            raise ValueError('Original WeCom Outbox required')
        if self.action in BROWSER_ACTIONS and (self.platform != 'deepseek' or not self.profile_id or not self.session_id
                or self.parameters.get('session_ref') != self.session_id):
            raise ValueError('Dedicated profile and bound session required')
        question_version(self.question_version, allow_empty=self.action in READ_ACTIONS)
        bounded_integer(self.context_revision, 1 if self.action in WRITE_ACTIONS else 0, 1000000)
        bounded_integer(self.execution_epoch, 0, 1000000000)
        if instant(self.expires_at) <= instant(self.created_at):
            raise ValueError('Command expiry must follow creation')

    @classmethod
    def from_dict(cls, value):
        return _decode(cls, value)

    def to_dict(self):
        return asdict(self)

    def assert_live(self, at=None):
        at = at or utc_now()
        if not instant(self.created_at) <= at < instant(self.expires_at):
            raise ValueError('Command is not currently valid')


@dataclass(frozen=True)
class WorkerAuthorization:
    authorization_ref: str
    task_id: str
    outbox_id: str | None
    account_id: str
    target_scope: str
    action: Action
    question_version: str | int
    context_revision: int
    content_sha256: str | None
    created_at: str
    expires_at: str
    actor_ref: str

    def __post_init__(self):
        for key in ('authorization_ref', 'task_id', 'account_id', 'target_scope', 'actor_ref'):
            reference(getattr(self, key))
        if self.outbox_id is not None:
            reference(self.outbox_id)
        object.__setattr__(self, 'action', Action(self.action))
        question_version(self.question_version, allow_empty=True)
        bounded_integer(self.context_revision, 0, 1000000)
        if self.content_sha256 is not None:
            digest(self.content_sha256)
        if self.action in WRITE_ACTIONS and not self.content_sha256:
            raise ValueError('Write approval must bind exact content or attachment manifest')
        if instant(self.expires_at) <= instant(self.created_at):
            raise ValueError('Authorization expiry required')

    @classmethod
    def from_dict(cls, value):
        return _decode(cls, value)

    def to_dict(self):
        return asdict(self)

    def validate(self, command, at=None):
        command.assert_live(at)
        if any(getattr(self, name) != getattr(command, name) for name in
                ('authorization_ref', 'task_id', 'outbox_id', 'account_id', 'target_scope', 'action', 'question_version', 'context_revision')):
            raise ValueError('Authorization binding changed')
        at = at or utc_now()
        if not instant(self.created_at) <= at < instant(self.expires_at) or instant(command.expires_at) > instant(self.expires_at):
            raise ValueError('Authorization expired or insufficient for command lifetime')
        content = next((command.parameters[k] for k in ('body_sha256', 'input_sha256', 'manifest_sha256') if k in command.parameters), None)
        if self.content_sha256 != content:
            raise ValueError('Approval does not bind this content')


@dataclass(frozen=True)
class WorkerPolicy:
    mode: PermissionMode = PermissionMode.OBSERVE_ONLY
    allowed_actions: tuple = ()
    allowed_accounts: tuple = ()
    allowed_scopes: tuple = ()
    allowed_profiles: tuple = ()
    human_reads_allowed: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'mode', PermissionMode(self.mode))
        object.__setattr__(self, 'allowed_actions', tuple(Action(v) for v in self.allowed_actions))
        for name in ('allowed_accounts', 'allowed_scopes', 'allowed_profiles'):
            if not isinstance(getattr(self, name), (tuple, list)):
                raise ValueError('Explicit allowlist required')
            object.__setattr__(self, name, tuple(reference(v) for v in getattr(self, name)))
        boolean(self.human_reads_allowed)

    @classmethod
    def from_dict(cls, value):
        return _decode(cls, value)

    def to_dict(self):
        return asdict(self)

    def validate(self, command):
        if (command.action not in self.allowed_actions or command.account_id not in self.allowed_accounts
                or command.target_scope not in self.allowed_scopes
                or command.profile_id and command.profile_id not in self.allowed_profiles):
            raise ValueError('Action, account, scope or profile is outside allowlist')
        if self.mode == PermissionMode.OBSERVE_ONLY and command.action not in READ_ACTIONS:
            raise ValueError('Observe-only does not authorize external writes')
        if self.mode == PermissionMode.ASSISTED and command.action == Action.EXECUTE_APPROVED_OUTBOX:
            # Existing specifically authorized fixed ACK is allowed; formal
            # explanations stay in the human final-send path for this phase.
            if command.parameters.get('purpose') != 'ACK':
                raise ValueError('Assisted formal delivery requires human final send')


@dataclass(frozen=True)
class WorkerHealth:
    worker_id: str
    account_id: str
    connected: bool
    interactive_desktop: bool
    desktop_unlocked: bool
    state: HealthState
    profile_id: str | None
    observed_scope: str | None
    observed_session: str | None
    observed_at: str
    native_call_pending: bool

    def __post_init__(self):
        reference(self.worker_id)
        reference(self.account_id)
        for name in ('profile_id', 'observed_scope', 'observed_session'):
            if getattr(self, name) is not None:
                reference(getattr(self, name))
        for name in ('connected', 'interactive_desktop', 'desktop_unlocked', 'native_call_pending'):
            boolean(getattr(self, name))
        object.__setattr__(self, 'state', HealthState(self.state))
        instant(self.observed_at)

    @property
    def gui_ready(self):
        return bool(self.connected and self.interactive_desktop and self.desktop_unlocked
                    and self.state == HealthState.HEALTHY and not self.native_call_pending)

    @classmethod
    def from_dict(cls, value):
        return _decode(cls, value)

    def to_dict(self):
        return asdict(self)

    def validate(self, command, at=None, max_age_seconds=30):
        if self.worker_id != command.worker_id or self.account_id != command.account_id:
            raise ValueError('Worker/account identity mismatch')
        if not self.gui_ready:
            raise ValueError(self.state if self.state != HealthState.HEALTHY else HealthState.DESKTOP_UNAVAILABLE)
        age = ((at or utc_now()) - instant(self.observed_at)).total_seconds()
        if not -5 <= age <= max_age_seconds:
            raise ValueError('Health observation stale')
        if command.profile_id and self.profile_id != command.profile_id:
            raise ValueError('Profile identity mismatch')
        if self.observed_scope != command.target_scope or command.session_id and self.observed_session != command.session_id:
            raise ValueError('Current group or session changed')


@dataclass(frozen=True)
class WorkerControl:
    stop: bool = False
    takeover: TakeoverState = TakeoverState.AUTO_ACTIVE
    takeover_epoch: int = 0
    paused_reason: HealthState | None = None
    resources_quarantined: bool = False
    lease_live: bool = False
    version_current: bool = False
    authorization_current: bool = False
    manually_handled: bool = False

    def __post_init__(self):
        for name in ('stop', 'resources_quarantined', 'lease_live', 'version_current', 'authorization_current', 'manually_handled'):
            boolean(getattr(self, name))
        object.__setattr__(self, 'takeover', TakeoverState(self.takeover))
        if self.paused_reason is not None:
            object.__setattr__(self, 'paused_reason', HealthState(self.paused_reason))
        bounded_integer(self.takeover_epoch, 0, 1000000000)

    @classmethod
    def from_dict(cls, value):
        return _decode(cls, value)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class WorkerResult:
    contract_version: int
    command_id: str
    task_id: str
    outbox_id: str | None
    worker_id: str
    account_id: str
    execution_epoch: int
    question_version: str | int
    context_revision: int
    status: ExecutionStatus
    side_effect: SideEffect
    action_started: bool
    native_call_pending: bool
    checkpoint: str
    reason_code: str
    observed_at: str
    evidence: tuple

    def __post_init__(self):
        if type(self.contract_version) is not int or self.contract_version != CONTRACT_VERSION:
            raise ValueError('Unsupported result contract')
        for name in ('command_id', 'task_id', 'worker_id', 'account_id'):
            reference(getattr(self, name))
        if self.outbox_id is not None:
            reference(self.outbox_id)
        question_version(self.question_version, allow_empty=True)
        for name in ('execution_epoch', 'context_revision'):
            bounded_integer(getattr(self, name), 0, 1000000000)
        for name in ('action_started', 'native_call_pending'):
            boolean(getattr(self, name))
        object.__setattr__(self, 'status', ExecutionStatus(self.status))
        object.__setattr__(self, 'side_effect', SideEffect(self.side_effect))
        for name in ('checkpoint', 'reason_code'):
            if not isinstance(getattr(self, name), str) or not _CODE.fullmatch(getattr(self, name)):
                raise ValueError('Minimal structured state code required, not raw logs')
        instant(self.observed_at)
        if not isinstance(self.evidence, (tuple, list)) or len(self.evidence) > 30:
            raise ValueError('Bounded evidence descriptors required')
        evidence = []
        for item in self.evidence:
            strict_keys(item, {'evidence_ref', 'sha256', 'kind', 'observed_at'})
            reference(item['evidence_ref'])
            digest(item['sha256'])
            if item['kind'] not in {'HEALTH', 'MESSAGE_CAPTURE', 'ATTACHMENT_MANIFEST', 'SESSION', 'UPLOAD', 'GENERATION', 'DRAFT', 'DELIVERY'}:
                raise ValueError('Unknown evidence kind')
            if instant(item['observed_at']) > instant(self.observed_at):
                raise ValueError('Evidence observed after result')
            evidence.append(dict(item))
        object.__setattr__(self, 'evidence', tuple(evidence))
        if self.status == ExecutionStatus.SUCCEEDED_VERIFIED and (not self.evidence or self.native_call_pending or self.side_effect != SideEffect.VERIFIED):
            raise ValueError('Tool success is not verified business success')
        if self.status == ExecutionStatus.FAILED_BEFORE_ACTION and (self.action_started or self.side_effect != SideEffect.NO_ACTION):
            raise ValueError('Action already started')
        if self.native_call_pending and self.status not in {ExecutionStatus.OUTCOME_UNKNOWN, ExecutionStatus.RUNNING, ExecutionStatus.WAITING_HUMAN}:
            raise ValueError('Pending native call requires uncertainty')
        if self.status == ExecutionStatus.OUTCOME_UNKNOWN and self.side_effect != SideEffect.UNKNOWN:
            raise ValueError('Unknown outcome must retain side-effect uncertainty')

    @classmethod
    def from_dict(cls, value):
        return _decode(cls, value)

    def to_dict(self):
        return asdict(self)

    def validate(self, command):
        if any(getattr(self, name) != getattr(command, name) for name in
                ('command_id', 'task_id', 'outbox_id', 'worker_id', 'account_id', 'execution_epoch', 'question_version', 'context_revision')):
            raise ValueError('Result identity or original execution binding mismatch')
        if instant(self.observed_at) < instant(command.created_at):
            raise ValueError('Result predates original command')
