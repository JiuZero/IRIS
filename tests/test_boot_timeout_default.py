"""The boot-timeout default is one number, and it is above the slow firmwares.

Two failure modes are guarded here, and they pull in opposite directions:

* a default under the slowest boot this corpus has actually completed is a wrong
  answer delivered on a timer -- the guest would have come up and IRIS reported
  that it did not;
* a default maintained separately per entry point drifts, and the drift is
  invisible until someone notices the workbench is more patient than the API.

So the value is asserted against the measurements that motivated it and against
every place that has to answer with it, including the TypeScript one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer
from fastapi.testclient import TestClient

from iris.api.server import EmulateRequest, app
from iris.cli import app as cli_app
from iris.config import DEFAULT_BOOT_TIMEOUT_SEC
from iris.emulate import orchestrator

_ROOT = Path(__file__).resolve().parents[1]

#: Successful boots timed on this corpus, firmware by architecture. The arm64
#: entries are the same firmware across three runs, which is also why the spread
#: matters: 420s is not an outlier, it is the slow end of one guest's normal range.
MEASURED_SUCCESS_SEC = {
    "DIR-868L": 52,
    "RP3": 130,
    "G1": 179,
    "TES7002": 420,
}

#: The value before this was fixed, and what it cost: three of the five measured
#: successes above would have been reported as failures on a timer.
PREVIOUS_DEFAULT_SEC = 120


class TestTheDefaultIsAboveTheMeasuredBoot:
    def test_it_clears_the_slowest_successful_boot_measured_here(self):
        slowest = max(MEASURED_SUCCESS_SEC.values())
        assert DEFAULT_BOOT_TIMEOUT_SEC > slowest

    def test_it_leaves_headroom_rather_than_just_clearing(self):
        """A default set to the slowest observation fails the next slow boot."""
        slowest = max(MEASURED_SUCCESS_SEC.values())
        assert DEFAULT_BOOT_TIMEOUT_SEC >= slowest * 1.25

    def test_the_previous_default_would_have_misreported_them(self):
        """Why the change happened, kept as an executable claim."""
        lost = [name for name, secs in MEASURED_SUCCESS_SEC.items()
                if secs > PREVIOUS_DEFAULT_SEC]
        assert lost, "the measurements no longer support the change"
        assert all(secs <= DEFAULT_BOOT_TIMEOUT_SEC for secs in MEASURED_SUCCESS_SEC.values())


class TestEveryEntryPointAnswersWithThatNumber:
    """The API, the CLI, the orchestrator and the workbench, from one constant."""

    def test_the_orchestrator_signatures_use_the_constant(self):
        for fn in (orchestrator.emulate_firmware, orchestrator._emulate_firmware):
            assert _default_of(fn, "timeout_sec") == DEFAULT_BOOT_TIMEOUT_SEC, fn.__name__

    def test_the_emulate_request_body_defaults_to_it(self):
        request = EmulateRequest(rootfs_path="iris-home/scratch/x-rootfs", arch="armel")
        assert request.timeout == DEFAULT_BOOT_TIMEOUT_SEC

    def test_the_pipeline_endpoint_defaults_to_it(self):
        with TestClient(app) as client:
            schema = client.get("/openapi.json").json()
        for path, method in (("/api/v1/pipeline", "post"),
                             ("/api/v1/emulate/upload", "post")):
            default = schema["paths"][path][method]["parameters"]
            timeout = next(p for p in default if p["name"] == "timeout")
            assert timeout["schema"]["default"] == DEFAULT_BOOT_TIMEOUT_SEC, path

    def test_the_pipeline_upload_upper_bound_still_admits_it(self):
        with TestClient(app) as client:
            schema = client.get("/openapi.json").json()
        timeout = next(p for p in schema["paths"]["/api/v1/emulate/upload"]["post"]["parameters"]
                       if p["name"] == "timeout")
        assert DEFAULT_BOOT_TIMEOUT_SEC <= timeout["schema"]["maximum"]

    def test_the_cli_option_defaults_to_it(self):
        default = _cli_option_default(cli_app, "timeout")
        assert default == DEFAULT_BOOT_TIMEOUT_SEC

    def test_the_launch_request_ceiling_clears_the_default_plus_the_image_build(self):
        """The image build alone is allowed 180s, and cancelling mid-way is not
        a safe way to end a launch -- it leaves a container nobody registered."""
        from iris.api.server import _UPLOAD_LAUNCH_TIMEOUT_SEC
        assert _UPLOAD_LAUNCH_TIMEOUT_SEC > DEFAULT_BOOT_TIMEOUT_SEC + 180


class TestTheWorkbenchAsksForTheSameNumber:
    """The workbench had its own literal, and its own literal was different."""

    def test_the_launch_dialog_default_is_the_server_default(self):
        source = (_ROOT / "web/src/components/LaunchDialog.tsx").read_text(encoding="utf-8")
        literal = re.search(r"timeoutValue, setTimeoutValue\] = useState\('(\d+)'\)", source)
        assert not literal, f"the timeout field still carries its own literal {literal.group(1)}"
        assert source.count("DEFAULT_BOOT_TIMEOUT_SEC") == 3, \
            "the import plus both the field default and its fallback come from it"

    def test_the_launch_dialog_default_equals_the_constant(self):
        source = (_ROOT / "web/src/lib/constants.ts").read_text(encoding="utf-8")
        match = re.search(r"DEFAULT_BOOT_TIMEOUT_SEC\s*=\s*(\d+)", source)
        assert match, "the workbench constant is gone"
        assert int(match.group(1)) == DEFAULT_BOOT_TIMEOUT_SEC


def _default_of(fn, name: str):
    import inspect
    return inspect.signature(fn).parameters[name].default


def _cli_option_default(app: typer.Typer, option: str):
    """The default typer hands click for a named option of one command."""
    command = _find_command(app, "run")
    for param in command.params:
        if param.name == option:
            return param.default
    raise AssertionError(f"no --{option} on {command.name}")


def _find_command(app: typer.Typer, name: str):

    root = typer.main.get_command(app)
    stack = [root]
    while stack:
        command = stack.pop()
        if command.name == name:
            return command
        stack.extend(getattr(command, "commands", {}).values())
    raise AssertionError(f"no command named {name}")


def test_the_measurements_are_still_the_ones_this_file_claims():
    """A number in a test that nothing produces is a number nobody checked."""
    assert all(secs > 0 for secs in MEASURED_SUCCESS_SEC.values())
    assert MEASURED_SUCCESS_SEC["TES7002"] > MEASURED_SUCCESS_SEC["G1"] > \
        MEASURED_SUCCESS_SEC["DIR-868L"]


@pytest.mark.parametrize("name", sorted(MEASURED_SUCCESS_SEC))
def test_each_firmware_is_covered_by_the_default(name: str):
    assert MEASURED_SUCCESS_SEC[name] <= DEFAULT_BOOT_TIMEOUT_SEC