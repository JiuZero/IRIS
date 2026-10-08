"""The workbench's own configuration store: ``<iris_home>/settings.json``.

This exists because the LLM layer's six settings arrived in 0.3.29 with no way
to reach them except hand-editing ``.env``, and because "edit a running
server's environment from a settings panel" -- the thing the panel's own
comment refused to do -- turns out to be doable once the file being written is
a file IRIS owns rather than one the operator owns.

Three properties make it safe to write from a browser:

- **It is not ``.env``.** ``.env`` is the operator's file, is shared with the
  CLI, and is where an image name or a database URL lives that a stray save
  would break. This file holds only the fields below, so the blast radius of a
  wrong save is the settings panel.
- **It is a lower-priority source, not the only source.** Environment
  variables still win when they are set (see :mod:`iris.config`), which keeps
  ``IRIS_*`` in a launch script authoritative over anything a browser clicked.
- **One field is a secret and is never echoed.** ``llm_api_key`` is reported as
  configured or not and written blind, the same bargain ``api_token`` keeps.

What is deliberately *not* here: ``api_token``, ``iris_home`` and
``database_url``. Writing ``api_token`` from the panel means the request that
writes it can lock its own author out of every route including the one that
would undo it; ``iris_home`` and ``database_url`` name stores that already
hold data, so changing them from a panel would promise a move that nothing
performs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

#: The one place that decides which settings a browser may write. The API layer
#: renders its form from this table and rejects anything absent from it, so
#: "what the panel can change" has exactly one definition rather than one in the
#: model, one in the route and one in the component.
SETTING_FILENAME = "settings.json"


@dataclass(frozen=True)
class EditableSetting:
    """One field the panel may write, with everything the form needs.

    ``kind`` drives the input widget and the value's JSON type: a switch sends a
    boolean, a number sends a number, and a text field sends a string. Sending
    a number as a string is how ``"64"`` and ``64`` end up in the same setting
    disagreeing about what it is.
    """

    key: str
    label: str
    kind: Literal["text", "number", "switch", "secret"]
    help: str
    #: ``None`` on a text field; the closed bounds a number must satisfy, because
    #: ``api_max_upload_mb = 0`` silently rejects every upload and
    #: ``llm_timeout_sec = -1`` fails every diagnosis in a way that reads as a
    #: broken endpoint.
    minimum: float | None = None
    maximum: float | None = None
    #: Placeholder shown in an empty field. Never a live value: the point of the
    #: secret row is that nobody can read it back out of the page.
    placeholder: str = ""


#: Closed on purpose. ``log_level`` reaches ``logging.getLevelName``, so a typo
#: is a silently ignored setting, and the panel that let you type one would be
#: the reason nobody found out.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

#: Every setting the panel may write, in the order the form shows them. Grouped
#: by subsystem rather than sorted: the person reading the panel is looking for
#: "the LLM one", and alphabetical order does not put those six together.
EDITABLE_SETTINGS: tuple[EditableSetting, ...] = (
    EditableSetting(
        key="llm_base_url",
        label="模型端点",
        kind="text",
        help="OpenAI 兼容端点，如 http://127.0.0.1:11434/v1；留空则整个 LLM 层停用，仿真不受影响",
        placeholder="http://127.0.0.1:11434/v1",
    ),
    EditableSetting(
        key="llm_api_key",
        label="API 密钥",
        kind="secret",
        help="仅写入本机，永不回显；面板只显示是否已配置",
        placeholder="已配置则留空不修改",
    ),
    EditableSetting(
        key="llm_model",
        label="模型名",
        kind="text",
        help="与端点配套；与端点缺一不可，半配置会让每次诊断都失败",
        placeholder="qwen2.5:7b",
    ),
    EditableSetting(
        key="llm_timeout_sec",
        label="单次诊断超时（秒）",
        kind="number",
        help="一次诊断只有一次往返，超时后会如实报错而不是重试到看不见",
        minimum=1.0,
        maximum=600.0,
    ),
    EditableSetting(
        key="llm_max_retries",
        label="重试次数",
        kind="number",
        help="诊断可手动重新触发，慢端点应该被看见而不是被重试掉",
        minimum=0.0,
        maximum=5.0,
    ),
    EditableSetting(
        key="llm_max_context_chars",
        label="串口日志截断（字符）",
        kind="number",
        help="送进提示词的日志尾部上限；开机日志前 90% 每次都一样",
        minimum=100.0,
        maximum=200000.0,
    ),
    EditableSetting(
        key="log_level",
        label="日志级别",
        kind="text",
        help="重启后生效；取值 " + " / ".join(LOG_LEVELS),
    ),
    EditableSetting(
        key="log_to_file",
        label="同时写日志文件",
        kind="switch",
        help="额外写 iris-home/logs/iris.log 并轮转；重启后生效",
    ),
    EditableSetting(
        key="api_max_upload_mb",
        label="上传上限（MB）",
        kind="number",
        help="单个固件镜像的上限；重启后生效",
        minimum=1.0,
        maximum=4096.0,
    ),
    EditableSetting(
        key="download_mirror",
        label="下载镜像",
        kind="text",
        help="语料下载走的前缀；重启后生效",
    ),
)

#: The keys the panel may write, for membership tests that read as a sentence.
EDITABLE_KEYS: frozenset[str] = frozenset(item.key for item in EDITABLE_SETTINGS)

#: Fields whose value must never appear in a response. Listed apart from
#: ``kind == "secret"`` because the reason to keep the list is the
#: redaction, not the widget.
SECRET_KEYS: frozenset[str] = frozenset(
    item.key for item in EDITABLE_SETTINGS if item.kind == "secret"
)

#: How the panel is told that nothing it saved is in effect yet.
#:
#: ``get_settings()`` is a process-wide singleton with no reload -- ``iris.config``
#: assigns ``_settings`` exactly once, guarded by ``if _settings is None`` -- so
#: *every* field on this list takes effect on the next start, and a field not on
#: it does not. There is no subset of fields that a running process picks up: the
#: diagnosis reads ``get_settings()`` at the moment it runs and gets the same
#: object the process was started with.
#:
#: An earlier draft of this listed four "restart-only" fields and claimed the LLM
#: ones would apply to the next diagnosis, on the reasoning that the client is
#: built per call. The client is built per call; the settings it is built *from*
#: are not re-read. That is the kind of claim that has to be checked against the
#: singleton rather than inferred from the call site, so the distinction is gone:
#: a save is a request for the next start, and the rows say so per field.


class SettingRejected(Exception):
    """A write that must not happen, with the reason to hand back verbatim.

    The panel is the only caller, so this travels to an HTTP 422 rather than to
    a log: "the number you typed is out of range" has to reach the field it
    belongs to, and a traceback in the log reaches nobody.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def settings_path(iris_home: Path) -> Path:
    """Where this store lives. Under ``iris_home`` so it moves with the data."""
    return Path(iris_home) / SETTING_FILENAME


def read_settings_file(iris_home: Path) -> dict[str, Any]:
    """The stored overrides, or ``{}`` when nothing has been saved.

    A corrupt file is an error rather than an empty dict: silently ignoring a
    half-written settings file would make the panel show defaults that no
    longer apply to anything, which is the one outcome nobody could debug.
    """
    path = settings_path(iris_home)
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SettingRejected(f"{SETTING_FILENAME} 无法解析：{exc}") from exc
    if not isinstance(loaded, dict):
        raise SettingRejected(f"{SETTING_FILENAME} 顶层必须是对象，实际是 {type(loaded).__name__}")
    return loaded


def _validate_value(item: EditableSetting, value: Any) -> Any:
    """One field's value against its kind and bounds, or a reason it was refused."""
    if item.kind == "switch":
        if not isinstance(value, bool):
            raise SettingRejected(f"{item.label} 需要 true/false，实际是 {type(value).__name__}")
        return value
    if item.kind in {"number", "secret"}:
        if item.kind == "secret":
            if not isinstance(value, str):
                raise SettingRejected(f"{item.label} 需要字符串，实际是 {type(value).__name__}")
            return value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise SettingRejected(f"{item.label} 需要数字，实际是 {type(value).__name__}")
        number = float(value)
        if item.minimum is not None and number < item.minimum:
            raise SettingRejected(f"{item.label} 不得小于 {item.minimum:g}")
        if item.maximum is not None and number > item.maximum:
            raise SettingRejected(f"{item.label} 不得大于 {item.maximum:g}")
        return value
    if not isinstance(value, str):
        raise SettingRejected(f"{item.label} 需要字符串，实际是 {type(value).__name__}")
    if item.key == "log_level":
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise SettingRejected(f"日志级别须为 {' / '.join(LOG_LEVELS)} 之一")
        return level
    return value


def normalise_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Check a submitted patch field by field, before anything is written.

    Returns the patch with ``log_level`` upper-cased and numbers kept as
    submitted. Rejecting unknown keys matters more here than anywhere else in
    the API: this is the one place a browser can put a name the server has
    never heard of, and accepting it would save a typo nobody would ever see
    again.
    """
    unknown = sorted(set(patch) - EDITABLE_KEYS)
    if unknown:
        raise SettingRejected("不可配置：" + "、".join(unknown))
    return {key: _validate_value(item, patch[key]) for key, item in _by_key(patch).items()}


def _by_key(patch: dict[str, Any]) -> dict[str, EditableSetting]:
    table = {item.key: item for item in EDITABLE_SETTINGS}
    return {key: table[key] for key in patch}


def write_settings_file(iris_home: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into the stored overrides and return the whole file.

    Written to a sibling temp file and renamed over the target: a save
    interrupted halfway must not leave a truncated settings file that the next
    start cannot read -- and that failure would be silent until someone
    noticed a setting had quietly stopped applying.
    """
    path = settings_path(iris_home)
    stored = read_settings_file(iris_home)
    for key, value in normalise_patch(patch).items():
        stored[key] = value
    stored = {key: value for key, value in stored.items() if key in EDITABLE_KEYS}
    _write_json_atomically(path, stored)
    return stored


def clear_settings_file(iris_home: Path, keys: list[str]) -> dict[str, Any]:
    """Drop the named overrides, so those fields fall back to env or default."""
    unknown = sorted(set(keys) - EDITABLE_KEYS)
    if unknown:
        raise SettingRejected("不可配置：" + "、".join(unknown))
    path = settings_path(iris_home)
    stored = read_settings_file(iris_home)
    for key in keys:
        stored.pop(key, None)
    _write_json_atomically(path, stored)
    return stored


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def stored_values(iris_home: Path) -> dict[str, Any]:
    """Stored overrides for reportable fields, secrets reduced to a bool.

    A secret's presence is the reportable fact; its value is not, and putting
    it in a payload that reaches a browser is how ``api_token``'s bargain was
    kept for every other field in this project.
    """
    stored = read_settings_file(iris_home)
    return {key: bool(value) if key in SECRET_KEYS else value for key, value in stored.items()}