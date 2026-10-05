"""Installing, validating and removing third-party rule plugins.

Kept apart from ``web_data``: that module's docstring opens by calling itself the
workbench's **read-only** view, and putting writes in there would void the claim.
Every function here either writes to disk or explicitly declines to.

**What "installed" means.** ``install_plugin`` returning success means the engine has
accepted the document *whole*: top-level, ``detect`` and ``actions`` keys are all in
the engine's whitelist, the ``id`` is well-formed and does not collide, and re-reading
the file after it lands produces **zero warnings**. That last one is the point. The
engine's answer to a key it does not know is "record a warning and ignore it", so an
upload check that only validates syntax would report success for a rule carrying
``detect: {fil_glob: ...}`` whose condition is then never evaluated. Refusing is
better than a page that says "installed" while the plugin does nothing.

**Why collisions are refused.** Built-in rules load first and plugins second, so a
colliding ``id`` is rejected at upload time (422). At run time a collision can still
arrive -- somebody dropped a file in by hand -- and there the first definition wins
and the log says so. See ``iris.rules.engine.load_all_rules``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from iris.config import get_settings
from iris.log import get_logger
from iris.rules.engine import (
    ACTION_KEYS,
    DETECT_KEYS,
    RULE_KEYS,
    load_all_rules,
    load_rule_file,
)

logger = get_logger(__name__)

#: Rule ids end up in ``repair_action.rule_id`` and in the CLI's aligned table, so
#: they are restricted to lowercase letters, digits, dots, underscores and hyphens.
#: The shape is taken from the six built-in rules; admitting uppercase or spaces
#: would only make logs and tables harder to read without helping a real rule.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: Ceiling for one uploaded plugin. A rule file is a few hundred lines of YAML, not a
#: firmware image, so 1 MiB already exceeds any real rule while still refusing a
#: mistarred tarball. A constant rather than a new setting: one more environment
#: variable is one more default and one more doc line to keep in step.
MAX_PLUGIN_BYTES = 1024 * 1024


class PluginRejected(ValueError):
    """An upload the engine will not take. ``detail`` is the Chinese reason shown
    to the operator, not the exception name.

    Raising rather than warning follows the module's bargain: a plugin that is
    partly ignored is harder to debug than one that never installed.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def plugin_dir() -> Path:
    return get_settings().plugin_dir


def list_plugins() -> list[dict[str, Any]]:
    """Every rule the engine would load, with where it came from, built-ins first.

    Reads through ``load_all_rules`` rather than re-globbing the two directories, so
    what this returns is what a run will actually apply -- including the fact that a
    hand-placed colliding document was dropped. ``origin`` and ``source_file`` travel
    with each rule because a list that merges two origins without saying which is
    which cannot answer "can I uninstall this one", and editing built-ins is exactly
    what an installed plugin is not.
    """
    settings = get_settings()
    builtin = _resolved(settings.rules_dir)
    found: list[dict[str, Any]] = []
    for rule in load_all_rules(settings.effective_rules_dirs):
        origin = "builtin" if _resolved(rule.source_dir) == builtin else "external"
        found.append({"rule": rule, "origin": origin, "source_file": rule.source_file})
    return found


def install_plugin(filename: str, content: bytes) -> dict[str, Any]:
    """Validate an uploaded rule document and write it into the plugin directory.

    Validation happens on the bytes *before* anything touches the disk, so a rejected
    upload leaves no trace; the re-load afterwards is the only step that writes, and
    it rolls itself back if the engine ends up unhappy.
    """
    if len(content) > MAX_PLUGIN_BYTES:
        raise PluginRejected(
            f"插件文件 {len(content)} 字节,超过 {MAX_PLUGIN_BYTES // 1024} KiB 上限"
        )
    if not content.strip():
        raise PluginRejected("上传的文件是空的")

    safe_name = Path(filename or "").name
    if not safe_name or safe_name != filename:
        # `Path("a/b.yaml").name` is `b.yaml`; a name that survives normalisation
        # unchanged cannot have been trying to escape the directory.
        raise PluginRejected("文件名无效,只接受不含路径的 .yaml 文件名")
    if not safe_name.lower().endswith((".yaml", ".yml")):
        raise PluginRejected("只接受 .yaml 或 .yml 结尾的规则文档")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginRejected(f"插件文件不是 UTF-8 文本:第 {exc.start} 字节无法解码") from exc

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PluginRejected(f"YAML 语法错误:{_one_line(exc)}") from exc

    if doc is None:
        raise PluginRejected("插件文档解析后为空")
    if not isinstance(doc, dict):
        raise PluginRejected(f"插件文档顶层必须是映射,实际是 {type(doc).__name__}")

    rule_id = doc.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise PluginRejected("插件缺少必填的 id 字段")
    if not _ID_RE.match(rule_id):
        raise PluginRejected(
            f"id {rule_id!r} 不合法:只允许小写字母、数字、点、下划线与连字符,长度 1–64"
        )

    unknown_top = sorted(set(doc) - RULE_KEYS)
    if unknown_top:
        raise PluginRejected(f"不支持的顶层键:{', '.join(unknown_top)};可用键为 {_rule_keys_desc()}")
    unknown_detect = _unknown_keys(doc.get("detect"), DETECT_KEYS)
    if unknown_detect:
        raise PluginRejected(f"detect 中不支持的键:{', '.join(unknown_detect)}")
    unknown_actions = _unknown_keys(doc.get("actions"), ACTION_KEYS)
    if unknown_actions:
        raise PluginRejected(f"actions 中不支持的键:{', '.join(unknown_actions)}")

    # A `.yml` upload stored under its own name could sit beside a *different*
    # `.yml` of the same stem and shadow it; storing under the id avoids that, and
    # the id is unique by the check below.
    if rule_id in {item["rule"].id for item in list_plugins()}:
        raise PluginRejected(f"id {rule_id!r} 与既有规则冲突,请换一个 id")

    target_dir = plugin_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{rule_id}.yaml"
    if target.exists():
        # The id check above already covers every rule the engine can see, so
        # reaching this means the file is there without a loadable id in it -- a
        # leftover. Say so rather than clobbering whatever the author left.
        raise PluginRejected(f"{target.name} 已存在,请先移除它再上传")

    target.write_text(text, encoding="utf-8", newline="\n")
    loaded = load_rule_file(target)
    if loaded is None or loaded.id != rule_id:
        target.unlink(missing_ok=True)
        raise PluginRejected("写入后无法被引擎加载,已撤销;文档为空或解析结果不是规则")
    if loaded.warnings:
        # The engine accepts unknown keys by ignoring them and warning. Installing
        # anyway would hand back a plugin that silently does less than it declares,
        # so the file goes and the warnings go back to the author verbatim.
        target.unlink(missing_ok=True)
        raise PluginRejected("引擎不接受其中的声明,已撤销:" + "; ".join(loaded.warnings))

    logger.info(f"installed rule plugin {rule_id!r} from {filename}")
    # No host path in the response: the workbench shows the file name, and a local
    # tool echoing absolute paths back to a browser is a habit worth not forming.
    return {
        "id": rule_id,
        "origin": "external",
        "source_file": target.name,
    }


def remove_plugin(filename: str) -> Path:
    """Delete one installed plugin. Returns the path that was removed.

    Only ``.yaml`` is removable, because that is the only extension the installer
    produces; a hand-placed ``.yml`` was never in the engine's load set to begin with,
    so refusing it keeps "what I can uninstall" and "what I can see" the same set.

    The name is checked *before* joining, the same way ``install_plugin`` checks the
    upload's name: ``Path(filename).name`` reduces ``../../rules/x.yaml`` to
    ``x.yaml``, which would silently delete an unrelated file of that name in the
    plugin directory instead of refusing the traversal. The ``resolve()`` comparison
    then catches the remaining case -- a symlink inside the plugin directory pointing
    out of it.
    """
    target_dir = plugin_dir().resolve()
    name = Path(filename or "").name
    if not name or name != filename:
        raise PluginRejected("路径无效,只能移除插件目录下的文件名")
    target = (target_dir / name).resolve()
    if target.parent != target_dir:
        raise PluginRejected("路径无效,只能移除插件目录下的文件")
    if target.suffix.lower() != ".yaml" or not target.is_file():
        raise PluginRejected("插件目录下没有这个文件")
    target.unlink()
    logger.info(f"removed rule plugin {target.name}")
    return target


def _resolved(path: Path | str) -> Path:
    """Absolute, symlink-free form of *path*, for comparing two directories.

    ``iris_home`` defaults to the relative ``iris-home`` and ``rules_dir`` is either
    absolute or the relative ``rules``, so the two spellings of the same directory
    differ until both are resolved. ``strict=False`` keeps a not-yet-created plugin
    directory from raising here -- it is created on first install.
    """
    return Path(path).resolve()


def _unknown_keys(entries: Any, allowed: frozenset[str]) -> list[str]:
    """Every unsupported key inside a list of condition/action mappings.

    ``all``/``any`` groups are walked as well, matching ``engine._unknown_keys``: a
    sub-condition's keys are evaluated by the same predicate as a top-level one's, so
    a typo in one is just as inert -- and just as invisible -- without this.
    """
    if not isinstance(entries, list):
        return []
    unknown: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        unknown |= set(entry) - allowed
        for joiner in ("all", "any"):
            nested = entry.get(joiner)
            if isinstance(nested, list):
                unknown |= set(_unknown_keys(nested, allowed))
    return sorted(unknown)


def _rule_keys_desc() -> str:
    return "id / description / stage / detect / actions / post_action_verify"


def _one_line(exc: Exception) -> str:
    return " ".join(str(exc).split())