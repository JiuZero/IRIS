"""What the README promises has to match what the code does.

Every check here exists because the README was wrong once and nothing caught
it. The failures were not typos -- they were claims, in the document a new user
reads first, that the code did not support:

* the positioning table said "rule engine / **LLM dual-track** recovery" while
  ``grep -r llm src/`` matched nothing;
* the sample table still said DIR-868L "service up, VLAN route down" a release
  after that conclusion was overturned, and listed two failures whose cause had
  been traced to a host-side kernel BUG rather than anything IRIS could fix;
* the API section documented ``http://127.0.0.1:9000/docs`` while
  ``serve start`` bound ``0.0.0.0``.

Documents do not fail CI, so the claims drifted for as long as the release
count. These assertions make the drift red instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
README = PROJECT / "README.md"
EVAL_LOG = PROJECT / "docs" / "eval-log.md"
DOCS = PROJECT / "docs"


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def eval_log() -> str:
    return EVAL_LOG.read_text(encoding="utf-8")


class TestNoCapabilityIsClaimedThatDoesNotExist:
    def test_no_llm_track_is_claimed(self, readme: str) -> None:
        """The LLM recovery track does not exist; saying it does is the bug.

        Checked against the phrases the README actually used, not a general
        "llm" ban, so a future line that explicitly says the track is absent
        still passes.
        """
        for phrase in ("LLM 双轨", "LLM 修复", "LLM 自愈"):
            assert phrase not in readme, f"README claims {phrase!r}, which no code implements"

    def test_llm_is_stated_as_not_connected(self, readme: str) -> None:
        assert "LLM 尚未接入" in readme, (
            "the README must say plainly that no model is wired up, so the "
            "absence is a stated boundary rather than a silent omission"
        )

    def test_the_guardian_is_not_described_as_model_driven(self, readme: str) -> None:
        """`ai_guardian.py` is a regex + state machine; the table row must say so."""
        assert "不含模型调用" in readme

    def test_extraction_formats_are_not_overstated(self, readme: str) -> None:
        """Only squashfs is unpackable; JFFS2 only inside TendaW, FIT only by magic."""
        assert "squashfs 为唯一解包路径" in readme
        assert "加密厂商格式**仅识别不解密**" in readme

    def test_network_topology_limits_are_stated(self, readme: str) -> None:
        """Single-plane topology is a real ceiling; a user planning a VLAN lab
        needs to learn it from the README, not from a failed weekend."""
        assert "网络拓扑单平面" in readme
        assert "无无线" in readme


class TestDeliverySurfaceIsDocumented:
    def test_the_api_token_is_documented(self, readme: str) -> None:
        assert "IRIS_API_TOKEN" in readme

    def test_the_loopback_default_is_documented(self, readme: str) -> None:
        """The docs URL and the shipped default must agree.

        They disagreed for the whole 0.1.x-0.3.x line: the README showed
        127.0.0.1 while the server bound 0.0.0.0, so the documentation described
        a safety the code did not have.
        """
        from iris.api.auth import is_loopback_host

        assert is_loopback_host("127.0.0.1")
        lines = readme.splitlines()
        for index, line in enumerate(lines):
            match = re.search(r"iris serve start --host (\S+)", line)
            if not match:
                continue
            host = match.group(1).strip("'\"")
            if is_loopback_host(host):
                continue
            # A non-loopback bind is legitimate only when the surrounding snippet
            # sets a token first -- that is exactly the pairing the startup gate
            # enforces, so the document has to show the same pairing.
            window = "\n".join(lines[max(0, index - 4):index])
            assert "IRIS_API_TOKEN" in window, (
                f"README shows a serve example bound to {host} with no token in the "
                "preceding lines; the command refuses to start in that combination"
            )

    def test_ownership_is_documented(self, readme: str) -> None:
        assert "越权一律返回 404" in readme

    def test_the_upload_cap_is_documented(self, readme: str) -> None:
        assert "IRIS_API_MAX_UPLOAD_MB" in readme


class TestTargetsAreNotPresentedAsResults:
    def test_the_target_line_is_marked_unreached(self, readme: str) -> None:
        """The >=80% / >=60% row is a goal; reading it as today's number is the
        failure mode this guards."""
        assert "尚未达成" in readme

    def test_a_current_measurement_is_stated(self, readme: str) -> None:
        from iris.db.engine import get_engine

        # Sanity: the stated rate must be a real fraction of the M1 set, not a
        # standalone number that drifts out of range entirely.
        assert "3/5" in readme

        del get_engine  # imported only to keep the DB layer import path exercised


class TestTheSampleTableMatchesTheEvalLog:
    def test_every_duration_in_the_sample_table_appears_in_the_eval_log(self, readme: str, eval_log: str) -> None:
        """A stale number in the README is a claim nobody measured.

        The sample table is copied from a real run, so each duration in it must
        be traceable to the eval log. This is what caught the table still saying
        DIR-868L was unreachable a release after it was fixed.
        """
        # `62.2s` / `53.0s` / `312.8s` -- skip the "289s / 258s" style pairs by
        # only taking numbers that look like a single measurement.
        durations = set(re.findall(r"(\d+\.\d)s\b", readme))
        assert durations, "the sample table has no durations to check"
        missing = sorted(d for d in durations if d not in eval_log)
        assert not missing, f"README durations absent from the eval log: {missing}"

    def test_the_overturn_conclusion_is_recorded(self, readme: str) -> None:
        """DIR-868L's conclusion was reversed twice; the README must say which
        one is current, or the old narrative comes back with the next edit."""
        assert "被推翻两次" in readme

    def test_the_environment_failures_are_labelled_as_such(self, readme: str) -> None:
        """Conflating "the environment broke" with "IRIS cannot do this" is the
        distinction that keeps getting lost in summaries."""
        assert "环境适配失败" in readme
        assert "项目内不可修" in readme


class TestNoDeadDirectoriesAreAdvertised:
    def test_planned_directories_are_marked_as_planned(self, readme: str) -> None:
        """`kernel/`, `libnvram/` and `tools/` are empty placeholders.

        Listing them in the tree without a marker reads as "this exists, go look
        inside", which is how an empty `.gitkeep` becomes someone's afternoon.
        """
        for name in ("kernel/", "libnvram/", "tools/"):
            line = next(
                (ln for ln in readme.splitlines() if ln.strip().startswith(f"├── {name}")
                 or ln.strip().startswith(f"└── {name}")),
                None,
            )
            assert line is not None, f"{name} disappeared from the tree listing"
            assert "规划中" in line, f"{name} is listed without a 规划中 marker"

class TestEveryDocumentTheReadmeLinksExists:
    """A broken link in the index is worse than no index.

    The `docx/` → `docs/` move left three stale links here for a while, and nothing
    failed: documents do not fail CI, which is the whole reason this file exists.
    Checked against the filesystem rather than a list of names, so a document that
    moves without updating the README is caught rather than trusted.
    """

    def test_no_readme_link_points_at_a_missing_file(self, readme: str) -> None:
        missing = []
        for target in re.findall(r"\]\((?!https?:)([^)#]+\.md)\)", readme):
            if not (PROJECT / target).is_file():
                missing.append(target)
        assert not missing, f"README links to documents that are not there: {missing}"

    def test_the_retired_docx_directory_is_not_referenced(self, readme: str) -> None:
        """`docx/` was folded into `docs/`. A pointer to it now names a directory
        that does not exist."""
        assert "docx/" not in readme

    def test_every_indexed_document_is_registered(self, readme: str) -> None:
        """The reverse direction: a document on disk that the index does not list is
        the other half of the same problem -- it exists, and nobody can find it."""
        listed = set(re.findall(r"\]\((docs/[^)#]+\.md)\)", readme))
        on_disk = {f"docs/{p.name}" for p in DOCS.glob("*.md")}
        assert on_disk <= listed, f"documents present but absent from the README index: {sorted(on_disk - listed)}"


class TestThePluginGuideMatchesTheEngine:
    """The development guide is a promise about which keys the engine accepts.

    It is also the only place an external plugin author learns the format, so a key
    added to the engine without the guide following silently makes the documented
    format a subset of the real one -- and a key *removed* from the engine makes the
    guide wrong in the direction that costs someone an afternoon.
    """

    @pytest.fixture(scope="module")
    def guide(self) -> str:
        path = DOCS / "09-规则插件开发指南.md"
        assert path.is_file(), "the plugin development guide is missing from docs/"
        return path.read_text(encoding="utf-8")

    def test_every_accepted_key_is_documented(self, guide: str) -> None:
        from iris.rules.engine import ACTION_KEYS, DETECT_KEYS, RULE_KEYS

        for key in sorted(RULE_KEYS | DETECT_KEYS | ACTION_KEYS):
            assert key in guide, f"the engine accepts {key!r} but the guide never mentions it"

    def test_the_documented_install_cap_matches_the_constant(self, guide: str) -> None:
        """Stated in MiB rather than KiB because that is how a rule author reads a
        size limit; the assertion therefore does the same conversion rather than
        searching for the byte count."""
        from iris.api.plugins import MAX_PLUGIN_BYTES

        mib = MAX_PLUGIN_BYTES // (1024 * 1024)
        assert f"{mib} MiB" in guide, (
            f"the guide states a size limit that is not the enforced {mib} MiB"
        )

    def test_the_documented_id_pattern_is_the_enforced_one(self, guide: str) -> None:
        from iris.api.plugins import _ID_RE

        assert _ID_RE.pattern in guide, (
            "the guide quotes a different id pattern than the installer enforces"
        )

    def test_the_guide_does_not_promise_that_stage_filters(self, guide: str) -> None:
        """`stage` is recorded and displayed but nothing filters on it, and a guide
        that implied otherwise would send plugin authors after a stage field that has
        no effect. Asserted in the negative because the claim is easy to add by
        habit: every other field here does filter."""
        assert "不参与任何筛选" in guide