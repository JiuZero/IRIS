"""The workbench names every failure kind the server can emit, and only those.

`format.ts` held eight kinds when the server had thirty, and four of its eight
were kinds the server no longer produces at all -- so most rows in the root-cause
view rendered a raw machine slug, and the ones that were mapped read as if that
were the whole taxonomy. Nothing caught it because the lookup falls back to the
slug, which makes a missing entry invisible at runtime by design.

So the two lists are compared here, exactly, in both directions: a server kind with
no label is an unmapped row in the UI, and a label for a kind the server cannot
emit is a claim about IRIS that is not true.
"""

from __future__ import annotations

import re
from pathlib import Path

from iris.failures import FailureKind

_ROOT = Path(__file__).resolve().parents[1]
_FORMAT_TS = _ROOT / "web/src/lib/format.ts"

_LABEL_BLOCK = re.compile(
    r"const FAILURE_LABELS: Record<string, string> = \{(?P<body>.*?)\n\}",
    re.DOTALL,
)


def _labelled_kinds() -> set[str]:
    block = _LABEL_BLOCK.search(_FORMAT_TS.read_text(encoding="utf-8"))
    assert block, "FAILURE_LABELS is gone or no longer a plain object literal"
    return set(re.findall(r"^\s*'([^']+)':", block.group("body"), re.MULTILINE))


SERVER_KINDS = {kind.value for kind in FailureKind}


class TestTheLabelMappingMatchesTheTaxonomy:
    def test_every_kind_the_server_can_emit_is_named(self):
        missing = sorted(SERVER_KINDS - _labelled_kinds())
        assert not missing, f"服务端会下发、前端没有中文标签的分类：{missing}"

    def test_no_label_names_a_kind_the_server_cannot_emit(self):
        stale = sorted(_labelled_kinds() - SERVER_KINDS)
        assert not stale, f"前端标签指向服务端不存在的分类：{stale}"

    def test_the_mapping_is_not_a_subset_by_accident(self):
        """The reverse check alone passes on an empty mapping; both together pin it."""
        assert len(_labelled_kinds()) == len(SERVER_KINDS) == 30

    def test_the_new_kernel_kinds_are_named(self):
        """The two added with the kernel-crash probe, by their values."""
        labels = _labelled_kinds()
        assert "guest-kernel-panic" in labels
        assert "guest-kernel-oops" in labels


def test_the_fallback_still_exists_for_pre_taxonomy_records():
    """Records written before the taxonomy name kinds this mapping does not."""
    source = _FORMAT_TS.read_text(encoding="utf-8")
    assert "FAILURE_LABELS[kind] ?? kind" in source