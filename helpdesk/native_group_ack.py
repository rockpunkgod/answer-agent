"""Reviewed native group ACKs using the existing Outbox, source gate and lock.

No chat search, target inference, teaching or message-source promotion. A local
operator must supply a current visual recipe; unknown sends are never replayed.
"""
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import time

from .delivery import PreflightFailure, NotSubmitted
from .mcp_group_delivery import MCPGroupDesktop
from .mcp_test_delivery import pixel_hash, same_clipboard_body, verify_frame
from .storage import now


class NativeGroupAckDesktop(MCPGroupDesktop):
    allowed_purposes = ("ACK",)

    @staticmethod
    def validate_config(config):
        if (not isinstance(config, dict) or config.get("backend") != "NATIVE_ACK"
                or config.get("allow_real_send") is not True
                or config.get("platform") != "wecom"
                or config.get("controls_verified") is not True
                or not all(isinstance(config.get(k), str) and config[k].strip()
                           for k in ("reviewer", "verification_evidence"))
                or not isinstance(config.get("groups"), list) or not config["groups"]):
            raise ValueError("REVIEWED_NATIVE_ACK_CONFIG_REQUIRED")
        keys, names = set(), set()
        for group in config["groups"]:
            if not isinstance(group, dict):
                raise ValueError("REVIEWED_NATIVE_ACK_GROUP_REQUIRED")
            key, name, students, pin = (group.get(k) for k in ("group_key", "header_name", "student_keys", "pin"))
            if (not all(isinstance(v, str) and v.strip() and not any(c in v for c in "\r\n") for v in (key, name))
                    or key in keys or name in names or not isinstance(students, list) or not students
                    or any(not isinstance(v, str) or not v.strip() for v in students)
                    or not isinstance(pin, dict) or pin.get("target_key") != key
                    or pin.get("display_name") != name or pin.get("platform") != "wecom"):
                raise ValueError("REVIEWED_NATIVE_ACK_GROUP_REQUIRED")
            size, bounds, geometry = (pin.get(k) for k in ("image_size", "display_bounds", "window_geometry_handle"))
            if (not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size)
                    or not isinstance(bounds, list) or len(bounds) != 4 or any(type(v) is not int for v in bounds)
                    or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]
                    or not isinstance(geometry, list) or len(geometry) != 3
                    or any(type(v) is not int or v <= 0 for v in geometry)
                    or type(pin.get("display_index")) is not int or pin["display_index"] < 0
                    or type(pin.get("expires_at")) not in (int, float)
                    or not math.isfinite(pin["expires_at"])):
                raise ValueError("NATIVE_ACK_GEOMETRY_REQUIRED")
            for field in ("header", "avatar", "send_button"):
                box = pin.get(field + "_box")
                digest = pin.get(field + "_hash")
                if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                        or not 0 <= box[0] < box[2] <= size[0] or not 0 <= box[1] < box[3] <= size[1]
                        or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
                    raise ValueError("NATIVE_ACK_VISUAL_RECIPE_REQUIRED")
            for field in ("editor_box", "outgoing_viewport"):
                box = pin.get(field)
                if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                        or not 0 <= box[0] < box[2] <= size[0] or not 0 <= box[1] < box[3] <= size[1]):
                    raise ValueError("NATIVE_ACK_VIEWPORT_REQUIRED")
            for field, region in (("compose_point", "editor_box"), ("send_point", "send_button_box")):
                point = pin.get(field)
                if not isinstance(point, list) or len(point) != 2 or any(type(v) is not int for v in point):
                    raise ValueError("NATIVE_ACK_CONTROL_POINT_REQUIRED")
                x = (point[0] - bounds[0]) * size[0] / (bounds[2] - bounds[0])
                y = (point[1] - bounds[1]) * size[1] / (bounds[3] - bounds[1])
                box = pin[region]
                if not (box[0] <= x < box[2] and box[1] <= y < box[3]):
                    raise ValueError("NATIVE_ACK_POINT_OUTSIDE_CONTROL")
            view = pin["outgoing_viewport"]
            if (type(pin.get("outgoing_right")) is not int or not view[0] < pin["outgoing_right"] <= view[2]
                    or not isinstance(pin.get("outgoing_color"), list) or len(pin["outgoing_color"]) != 3
                    or any(type(v) is not int or not 0 <= v <= 255 for v in pin["outgoing_color"])):
                raise ValueError("NATIVE_ACK_OUTGOING_RECIPE_REQUIRED")
            keys.add(key)
            names.add(name)

    def __init__(self, config, workspace, store, source_validator, *, native_factory=None):
        self.validate_config(config)
        if not callable(source_validator):
            raise ValueError("ACTUAL_ACK_SOURCE_VALIDATOR_REQUIRED")
        self.config = deepcopy(config)
        self.workspace = Path(workspace).resolve()
        self.lock_path = str(self.workspace / "data/windows-interactive-desktop.lock")
        self.journal_root = self.workspace / "data/private/native-group-ack"
        self.store, self.source_validator = store, source_validator
        self.native_factory = native_factory
        self.guard = None
        self.transport = None
        self.active_key = None
        self._active_message = None
        self._reading_receipt = False

    def accepts_task(self, row):
        """Scope selection only; the independent source gate still runs later."""
        binding = self.store.one("SELECT group_key,student_key FROM bindings WHERE id=?", (row["binding_id"],))
        return bool(binding and any(group["group_key"] == binding["group_key"]
                    and binding["student_key"] in group["student_keys"] for group in self.config["groups"]))

    def authorize(self, message):
        groups = [g for g in self.config["groups"] if g["group_key"] == message.group_key
                  and message.student_key in g["student_keys"]]
        row = self.store.one("SELECT * FROM outbox WHERE id=?", (message.outbox_id,))
        binding = self.store.one("SELECT * FROM bindings WHERE id=?", (message.binding_id,))
        if (len(groups) != 1 or message.body != "收到" or not row or row["purpose"] != "ACK"
                or row["body"] != message.body or row["binding_id"] != message.binding_id
                or row["simulated"] != 0 or not binding or not binding["verified"]
                or (binding["group_key"], binding["student_key"]) != (message.group_key, message.student_key)):
            raise PreflightFailure("NATIVE_ACK_ONLY_BOUND_TARGET_REQUIRED")
        if time.time() > groups[0]["pin"]["expires_at"]:
            raise PreflightFailure("NATIVE_ACK_RECIPE_EXPIRED")
        if self.source_validator(self.store, row) is not True:
            raise PreflightFailure("ACTUAL_ACK_SOURCE_REQUIRED")
        return groups[0]

    def _native_call(self, message, tool, arguments):
        recipe = self.authorize(message)
        if not self._reading_receipt:
            self._guard(message)
        elif (tool not in ("PowerShell", "Screenshot", "Clipboard", "Click")
              or tool == "Clipboard" and arguments != {"mode": "get"}
              or tool == "Click" and arguments.get("loc") == recipe["pin"]["send_point"]):
            raise PreflightFailure("NATIVE_ACK_READBACK_ONLY")
        self._active_message = message
        if self.transport is None:
            from .wecom_native import NativeWeComTransport
            factory = self.native_factory or NativeWeComTransport
            self.transport = factory(recipe["pin"], self.workspace,
                                     guard=lambda: self.authorize(self._active_message) if self._reading_receipt else self._guard(self._active_message))
            self.active_key = recipe["group_key"]
        if self.active_key != recipe["group_key"]:
            raise PreflightFailure("NATIVE_ACK_TARGET_CHANGED")
        result = self.transport.call(tool, arguments)
        if result.get("is_error") is not False:
            raise RuntimeError("NATIVE_ACK_CALL_FAILED")
        if tool in ("Click", "Shortcut"):
            time.sleep(.15)
        return result

    def _observe(self, message):
        from PIL import Image, ImageOps
        from .mcp_window_probe import FOREGROUND_COMMAND, parse_foreground
        from .wecom_visible import incoming_bubbles
        pin = self.authorize(message)["pin"]
        args = {"command": FOREGROUND_COMMAND, "timeout": 10}
        before = parse_foreground(self._native_call(message, "PowerShell", args))
        record = self._native_call(message, "Screenshot", {})
        after = parse_foreground(self._native_call(message, "PowerShell", args))
        if before != after:
            raise PreflightFailure("NATIVE_ACK_WINDOW_CHANGED")
        frame = verify_frame(record, pin, foreground=after)
        with Image.open(frame["path"]) as image:
            width = image.width
            view = pin["outgoing_viewport"]
            mirrored = incoming_bubbles(ImageOps.mirror(image), {
                "viewport": [width-view[2], view[1], width-view[0], view[3]],
                "incoming_left": width-pin["outgoing_right"], "bubble_color": pin["outgoing_color"]})
            boxes = sorted(([width-b[2], b[1], width-b[0], b[3]] for b in mirrored), key=lambda b: b[1])
            outgoing = [sha256(json.dumps([b[2]-b[0], b[3]-b[1]]).encode() + image.crop(b).convert("RGB").tobytes()).hexdigest() for b in boxes]
            send_enabled = pixel_hash(image, pin["send_button_box"]) == pin["send_button_hash"]
        return {**frame, "native": after, "record": record, "boxes": boxes,
                "outgoing": outgoing, "send_enabled": send_enabled}

    def preflight(self, message):
        self._guard(message)
        if self._path(message).exists():
            raise PreflightFailure("EXISTING_GROUP_ATTEMPT_REQUIRES_REVIEW")
        if not self._observe(message)["editor_blank"]:
            raise PreflightFailure("GROUP_EDITOR_OCCUPIED")

    def _clipboard_native(self, message):
        result = self._native_call(message, "Clipboard", {"mode": "get"})
        content = result.get("content", [])
        if len(content) != 1 or not content[0].get("text", "").startswith("Clipboard content:\n"):
            raise ValueError("NATIVE_ACK_CLIPBOARD_UNAVAILABLE")
        return content[0]["text"].removeprefix("Clipboard content:\n")

    def send(self, message):
        pin = self.authorize(message)["pin"]
        before = self._observe(message)
        if not before["editor_blank"]:
            raise NotSubmitted("PRE_SUBMISSION_CHECK_FAILED")
        record = {"outbox_id": message.outbox_id, "binding_id": message.binding_id,
                  "group_key": message.group_key, "student_key": message.student_key,
                  "body_hash": message.body_hash, "before": before, "state": "BEFORE_INPUT",
                  "recipe_sha256": sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()}
        path = self._path(message)
        self._save(path, record, exclusive=True)
        try:
            self._native_call(message, "Click", {"loc": pin["compose_point"]})
            self._native_call(message, "Clipboard", {"mode": "set", "text": message.body})
            self._native_call(message, "Shortcut", {"shortcut": "ctrl+v"})
            for shortcut in ("ctrl+a", "ctrl+c"):
                self._native_call(message, "Shortcut", {"shortcut": shortcut})
            if not same_clipboard_body(self._clipboard_native(message), message.body):
                raise ValueError("NATIVE_ACK_DRAFT_MISMATCH")
            self._native_call(message, "Shortcut", {"shortcut": "right"})
            current = self._observe(message)
            if current["native"] != before["native"] or not current["send_enabled"]:
                raise ValueError("NATIVE_ACK_SEND_CONTROL_CHANGED")
        except Exception as error:
            record.update(state="DRAFT_REQUIRES_REVIEW", submission_attempted=False)
            self._save(path, record)
            raise NotSubmitted("PRE_SUBMISSION_CHECK_FAILED") from error
        record.update(state="SUBMISSION_UNCONFIRMED", submission_started_at=now())
        self._save(path, record)
        self._native_call(message, "Click", {"loc": pin["send_point"]})
        return self.reconcile(message)

    def reconcile(self, message):
        """Read-only submission reconciliation; never types or clicks Send."""
        self._reading_receipt = True
        try:
            return self._reconcile(message)
        finally:
            self._reading_receipt = False

    def _reconcile(self, message):
        from .wecom_control_ocr import locate_copy
        path = self._path(message)
        try:
            recipe = self.authorize(message)
            record = json.loads(path.read_text(encoding="utf-8"))
            if (record.get("state") not in ("SUBMISSION_UNCONFIRMED", "UI_CONFIRMED")
                    or any(record.get(k) != getattr(message, k) for k in ("outbox_id", "binding_id", "group_key", "student_key", "body_hash"))
                    or record.get("recipe_sha256") != sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()):
                raise ValueError("NATIVE_ACK_ATTEMPT_MISMATCH")
            after = self._observe(message)
            before = record["before"]["outgoing"]
            if not after["editor_blank"] or after["outgoing"][:-1] != before or len(after["outgoing"]) != len(before)+1:
                raise ValueError("NATIVE_ACK_NEW_OUTGOING_NOT_UNIQUE")
            pin = recipe["pin"]
            box = after["boxes"][-1]
            x, y = (box[0]+box[2])//2, (box[1]+box[3])//2
            bounds, size = pin["display_bounds"], pin["image_size"]
            point = [bounds[0]+int(x*(bounds[2]-bounds[0])/size[0]), bounds[1]+int(y*(bounds[3]-bounds[1])/size[1])]
            self._native_call(message, "Click", {"loc": point, "button": "right"})
            menu = self._observe(message)
            # The bounded OCR result only locates the Copy menu, never a recipient.
            search = [max(0,x-250), max(0,y-20), min(size[0],x+150), min(size[1],y+230)]
            source = self.workspace / "data/private/wecom-native" / (menu["record"]["attempt_id"] + ".json")
            located = locate_copy(source, search, display_index=pin["display_index"])
            current = self._observe(message)
            from PIL import Image
            with Image.open(menu["path"]) as first, Image.open(current["path"]) as second:
                if pixel_hash(first, search) != pixel_hash(second, search):
                    raise ValueError("NATIVE_ACK_COPY_MENU_CHANGED")
            self._native_call(message, "Click", {"loc": located["loc"]})
            copied = self._clipboard_native(message)
            final = self._observe(message)
            if not same_clipboard_body(copied, message.body) or not final["editor_blank"] or final["outgoing"] != after["outgoing"]:
                raise ValueError("NATIVE_ACK_RECEIPT_CHANGED")
            record.update(state="UI_CONFIRMED", confirmed_at=record.get("confirmed_at") or now(), after=final)
            self._save(path, record)
            return {"confirmed": True, "simulated": False, "transport": "NATIVE_ACK", "body_hash": message.body_hash,
                    "outbox_id": message.outbox_id, "binding_id": message.binding_id,
                    "group_key": message.group_key, "student_key": message.student_key,
                    "confirmed_at": record["confirmed_at"], "evidence": str(path),
                    "verification": "reviewed group frame + one appended outgoing bubble + exact clipboard + blank editor",
                    "server_receipt": False, "recipient_read": False}
        except Exception:
            return {"confirmed": False, "simulated": False, "reason": "NATIVE_ACK_RECEIPT_UNCONFIRMED"}
