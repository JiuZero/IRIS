"""Invariants of the failure taxonomy, and the guards that keep it honest.

The taxonomy exists to be counted. Two things can silently break that: a kind
with no stage or no hint (it lands in a table nobody can act on), and a new
failure site that invents its own string instead of picking a kind -- which is
exactly how the codebase ended up with three incompatible literals and a document
claiming eight failure stages that no code could produce.

Both are checked here by reading the source, not by calling into it: the guard is
meant to hold for code nobody has exercised yet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iris.failures import (
    INFORMATIONAL_KINDS,
    STAGES,
    BootDiagnosis,
    Failure,
    FailureKind,
    hint_of,
    kind_of,
    stage_of,
)

PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src" / "iris"


class TestTaxonomyIsComplete:
    def test_every_kind_has_a_stage(self):
        for kind in FailureKind:
            assert stage_of(kind) in STAGES, kind

    def test_every_kind_has_a_hint(self):
        for kind in FailureKind:
            assert hint_of(kind).strip(), f"{kind} has no hint"

    def test_every_stage_is_reachable_from_some_kind(self):
        """A stage nothing can fail in is a column that is always empty."""
        used = {stage_of(kind) for kind in FailureKind}
        assert used == set(STAGES), set(STAGES) - used

    def test_the_informational_set_is_a_subset_of_the_kinds(self):
        assert INFORMATIONAL_KINDS <= set(FailureKind)

    def test_an_informational_kind_is_not_counted_as_a_failure(self):
        assert not Failure(FailureKind.NETWORK_FALLBACK_OK, "ran").is_failure
        assert Failure(FailureKind.REBOOT_LOOP, "looping").is_failure



class TestMessageShape:
    def test_the_message_still_starts_with_the_kind(self):
        """Every log line and CLI message reads `error`, which is the message. If
        the prefix went away the taxonomy would exist only in memory."""
        assert Failure(FailureKind.ARCH_MISMATCH, "detail").message == "arch-mismatch: detail"

    def test_str_is_the_message(self):
        assert str(Failure(FailureKind.NO_ROOTFS, "nothing found")) \
            == Failure(FailureKind.NO_ROOTFS, "nothing found").message

    def test_kind_of_reads_a_message_back(self):
        assert kind_of("arch-mismatch: rootfs is dominated by armel ELFs") \
            is FailureKind.ARCH_MISMATCH

    def test_kind_of_returns_none_for_prose_without_a_kind(self):
        assert kind_of("emulation failed: the guest never served :80") is None

    def test_kind_of_does_not_invent_kinds_from_a_colon_in_prose(self):
        """`detail` may contain colons; only the head before the first one counts,
        and an unknown head must not be coerced into the nearest kind."""
        assert kind_of("image-build-failed: run_qemu.sh: line 63") is FailureKind.IMAGE_BUILD_FAILED
        assert kind_of("emulation failed: no such thing: 42") is None

    def test_a_failure_is_immutable(self):
        failure = Failure(FailureKind.NO_ROOTFS, "x")
        try:
            failure.kind = FailureKind.REBOOT_LOOP  # type: ignore[misc]
        except AttributeError:
            pass  # the dataclass is frozen
        else:
            raise AssertionError("Failure is mutable; a recorded row could be edited after the fact")

    def test_its_evidence_is_immutable_too(self):
        """`frozen=True` only stops the attribute being rebound. A plain dict in
        the same field would still let a recorded diagnosis be rewritten in
        place, which is exactly what the persisted evidence must not allow --
        including by whoever built the Failure and kept their reference."""
        source = {"reboots": 28}
        failure = Failure(FailureKind.REBOOT_LOOP, "rebooted 28 times", source)

        with pytest.raises(TypeError):
            failure.evidence["reboots"] = 0  # type: ignore[index]
        source["reboots"] = 999
        assert failure.evidence["reboots"] == 28


class TestBootDiagnosisCarriesFindings:
    def _findings(self):
        return (
            Failure(FailureKind.REBOOT_LOOP, "rebooted 28 times", {"reboots": 28}),
            Failure(FailureKind.NETWORK_FALLBACK_OK, "fallback ran"),
            Failure(FailureKind.NO_GUEST_IP, "no address"),
        )

    def _diagnosis(self) -> BootDiagnosis:
        """Built the way the orchestrator builds it, so the prose really is the
        joined details rather than a string that happens to look like one."""
        findings = self._findings()
        prose = "emulation failed: " + "; ".join(f.detail for f in findings) + "."
        return BootDiagnosis(prose, findings)

    def test_it_is_still_a_string(self):
        """Twenty-odd assertions and every log line treat this as prose; making it
        a plain object would break all of them for no gain."""
        diagnosis = self._diagnosis()
        assert diagnosis.startswith("emulation failed: ")
        assert "fallback ran" in diagnosis
        assert diagnosis.index("rebooted") < diagnosis.index("no address")

    def test_the_prose_is_exactly_the_joined_details(self):
        """Two representations of one diagnosis, so they must not be able to
        disagree: the message a human reads is built from the same objects."""
        diagnosis = self._diagnosis()
        expected = "emulation failed: " + "; ".join(
            f.detail for f in self._findings()) + "."
        assert str(diagnosis) == expected

    def test_the_findings_come_back_unchanged(self):
        diagnosis = self._diagnosis()
        assert [f.kind for f in diagnosis.findings] == [
            FailureKind.REBOOT_LOOP, FailureKind.NETWORK_FALLBACK_OK, FailureKind.NO_GUEST_IP,
        ]

    def test_primary_skips_an_informational_first_finding(self):
        """"The fallback ran" can legitimately open a diagnosis; filing that as
        the cause would put a working signal in the failure histogram."""
        diagnosis = BootDiagnosis(
            "emulation failed: fallback ran; no web.",
            (Failure(FailureKind.NETWORK_FALLBACK_OK, "ran"),
             Failure(FailureKind.WEB_NOT_STARTED, "no web")),
        )
        assert diagnosis.primary.kind is FailureKind.WEB_NOT_STARTED

    def test_primary_falls_back_when_nothing_failed(self):
        only_good = BootDiagnosis("x", (Failure(FailureKind.NETWORK_FALLBACK_OK, "ran"),))
        assert only_good.primary.kind is FailureKind.NETWORK_FALLBACK_OK

    def test_an_empty_diagnosis_has_no_primary(self):
        assert BootDiagnosis("emulation failed: nothing found.").primary is None


def _sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if p.name != "failures.py")


class TestNoModuleInventsItsOwnFailureString:
    """The anti-drift guard: a bare assignment is how the vocabulary fractured."""

    def test_no_bare_error_or_failure_assignment(self):
        offenders = []
        for path in _sources():
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"^\s*\w+\.(?:error|failure)\s*=\s*(f?[\"'])", text,
                                     re.MULTILINE):
                line = text[:match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(PROJECT)}:{line}")
        assert offenders == [], (
            "assign a Failure (or call result.fail(...)) instead of a bare string: "
            + ", ".join(offenders)
        )

    def test_the_helpers_they_should_use_are_importable(self):
        """A guard that names the wrong remedy is worse than none."""
        from iris.emulate.orchestrator import EmulationResult

        result = EmulationResult(rootfs_dir=Path("."), arch="armel")
        result.fail(Failure(FailureKind.QEMU_START_FAILED, "no kernel asset"))
        assert result.error == "qemu-start-failed: no kernel asset"
        assert result.failure.kind is FailureKind.QEMU_START_FAILED

    def test_every_kind_is_named_somewhere_in_the_code(self):
        """A kind nothing produces is dead vocabulary: it looks like coverage in
        the enum while the histogram stays empty."""
        blob = "\n".join(
            p.read_text(encoding="utf-8")
            for p in [*_sources(), *(PROJECT / "tests").glob("*.py")]
        )
        missing = [kind.name for kind in FailureKind
                   if not re.search(rf"FailureKind\.{kind.name}\b", blob)]
        assert missing == [], f"kinds no code ever constructs: {missing}"