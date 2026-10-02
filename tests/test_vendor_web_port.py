"""Vendor web servers that answer on a port of their own choosing.

The guest fallback guarantees reachability on :80, and the rehost forwards the
user's port to exactly that. A firmware whose web server binds :8180 and leans on
a vendor redirector therefore looks healthy in the serial log and is still
unreachable over HTTP — and the redirector that would normally own the mapping is
often the process that crashed, so nothing will ever create it.

Reading the config is the only way to learn the real port, and rewriting it (or
DNATing onto it) is the only repair. Both are driven here against real
config files through a real POSIX shell, because both are sed/iptables logic
whose failure mode is a silently unchanged file.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

NET_FIX = Path(__file__).resolve().parents[1] / "scripts" / "emulate" / "iris_net_fix.sh"

# The AC15 stock nginx.conf, trimmed to the listen block. The commented examples
# are the trap: a config reader that does not anchor on the line start reports
# 8000 or 443 and redirects :80 to a port nothing is listening on.
AC15_CONF = """\
user  root;
worker_processes  1;
http {
    include       mime.types;
    #listen       8000;
    #listen       443 ssl;
    #listen       somename;
    server {
        listen       8180;
        server_name  localhost;
        location / {
            root   html;
        }
    }
}
"""

# log() is defined by the script to write to /dev/console, which does not exist
# on the host, so it is redefined *after* sourcing to capture what the repair
# decided to do. vendor_web_port's output is deliberately bare, hence the
# marker line: without it the port and the log lines cannot be told apart.
_SOURCE = """
add_func() { :; }
export IRIS_NGINX_CONF="$1"
. "$2"
log() { echo "LOG: $*"; }
echo "PORT:"
vendor_web_port
echo "END"
redirect_to_port80 "$3"
printf 'rc=%s\\n' "$?"
"""


def _shell() -> str:
    for candidate in (shutil.which("bash"), "C:/Program Files/Git/usr/bin/bash.exe"):
        if candidate and Path(candidate).exists():
            probe = subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True, check=False)
            if probe.returncode == 0 and probe.stdout.strip() == "ok":
                return candidate
    pytest.skip("no working POSIX shell available to run iris_net_fix.sh")


@pytest.fixture(scope="module")
def bash() -> str:
    return _shell()


def _write_conf(tmp_path: Path, text: str = AC15_CONF) -> Path:
    conf = tmp_path / "nginx.conf"
    conf.write_text(text, encoding="utf-8")
    return conf


def _posix(path: Path | str) -> str:
    """A path the POSIX shell can open: Windows backslashes are escape characters
    to /bin/sh. ``C:/...`` works for file arguments."""
    return str(path).replace("\\", "/")


def _msys(path: Path | str) -> str:
    """A path usable in PATH. Unlike a file argument, PATH entries must be in
    MSYS form — ``C:/x/bin`` is simply never found, and the command falls
    through to "not found" with every error message redirected away."""
    text = _posix(path)
    match = re.fullmatch(r"([A-Za-z]):/(.*)", text)
    return f"/{match.group(1).lower()}/{match.group(2)}" if match else text


def _run(bash: str, conf: Path, target: str = "8180", *, iptables: Path | None = None) -> str:
    pre = f'export PATH="{_msys(iptables)}:$PATH"\n' if iptables else ""
    proc = subprocess.run(
        [bash, "-c", pre + _SOURCE, "_", _posix(conf), _posix(NET_FIX), target],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _split(stdout: str) -> tuple[str, str]:
    """(vendor_web_port output, everything the repair logged and returned)."""
    _, _, rest = stdout.partition("PORT:\n")
    body, marker, tail = rest.partition("END\n")
    assert marker, f"no port marker in {stdout!r}"
    return body.strip(), tail


class TestVendorWebPort:
    def test_reads_the_uncommented_listen_port(self, bash, tmp_path):
        assert _split(_run(bash, _write_conf(tmp_path)))[0] == "8180"

    def test_commented_examples_are_never_read(self, bash, tmp_path):
        """The regression: 8000/443/somename appear before the real port."""
        assert _split(_run(bash, _write_conf(tmp_path)))[0] == "8180"

    def test_a_tab_indented_listen_is_still_read(self, bash, tmp_path):
        conf = _write_conf(tmp_path, "http {\n\tserver {\n\t\tlisten\t\t8080;\n\t}\n}\n")
        assert _split(_run(bash, conf))[0] == "8080"

    def test_a_listen_with_trailing_content_is_read(self, bash, tmp_path):
        conf = _write_conf(tmp_path, "server {\n    listen 8000 default_server;\n}\n")
        assert _split(_run(bash, conf))[0] == "8000"

    def test_a_missing_config_reports_nothing(self, bash, tmp_path):
        assert _split(_run(bash, tmp_path / "absent.conf"))[0] == ""

    def test_a_config_with_no_listen_reports_nothing(self, bash, tmp_path):
        conf = _write_conf(tmp_path, "http {\n    # nothing here\n}\n")
        assert _split(_run(bash, conf))[0] == ""

    def test_earlier_configs_are_preferred(self, bash, tmp_path):
        """IRIS_NGINX_CONF is a list, and the first config that answers wins."""
        first = _write_conf(tmp_path, "server {\n    listen 1111;\n}\n")
        second = tmp_path / "second.conf"
        second.write_text("server {\n    listen 2222;\n}\n", encoding="utf-8")
        pre = f'export IRIS_NGINX_CONF="{_posix(first)} {_posix(second)}"\n'
        proc = subprocess.run(
            [bash, "-c", pre + _SOURCE, "_", _posix(first), _posix(NET_FIX), "1111"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert _split(proc.stdout)[0] == "1111"


class TestRedirectWithoutIptables:
    """No iptables applet: the config rewrite is the only remaining repair."""

    def test_listen_is_rewritten_to_80(self, bash, tmp_path):
        conf = _write_conf(tmp_path)
        _, tail = _split(_run(bash, conf))
        assert "rc=0" in tail
        assert re.search(r"^\s*listen\s+80;$", conf.read_text(encoding="utf-8"), re.MULTILINE)
        assert "8180" not in conf.read_text(encoding="utf-8")

    def test_the_rewritten_directive_is_still_one_listen(self, bash, tmp_path):
        """A duplicated keyword makes nginx refuse to start at all."""
        conf = _write_conf(tmp_path)
        _run(bash, conf)
        line = next(l for l in conf.read_text(encoding="utf-8").splitlines() if "80;" in l)
        assert line.split() == ["listen", "80;"]

    def test_the_rest_of_the_config_survives(self, bash, tmp_path):
        conf = _write_conf(tmp_path)
        _run(bash, conf)
        text = conf.read_text(encoding="utf-8")
        assert "root   html;" in text and "server_name  localhost;" in text
        assert "#listen       8000;" in text, "comments must not be touched"

    def test_a_mismatched_target_reports_failure(self, bash, tmp_path):
        conf = _write_conf(tmp_path)
        _, tail = _split(_run(bash, conf, target="9999"))
        assert "rc=1" in tail
        assert "8180" in conf.read_text(encoding="utf-8")

    def test_read_only_config_is_reported_not_rewritten(self, bash, tmp_path):
        conf = _write_conf(tmp_path)
        # A read-only /etc_ro would otherwise make every build a silent no-op.
        subprocess.run(
            [bash, "-c", f'chmod 444 "{conf}"'], capture_output=True, check=False,
        )
        try:
            _, tail = _split(_run(bash, conf))
        finally:
            conf.chmod(0o644)
        if "rc=0" in tail:  # git-bash on Windows does not enforce the mode
            pytest.skip("filesystem does not enforce read-only mode")
        assert "read-only filesystem" in tail
        assert "8180" in conf.read_text(encoding="utf-8")


class TestRedirectWithIptables:
    """With iptables present, the config must be left alone so a vendor restart
    cannot lose the mapping."""

    @staticmethod
    def _fake_iptables(tmp_path: Path, *, fail_append: bool = False) -> Path:
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        script = bindir / "iptables"
        if fail_append:
            script.write_text('#!/bin/sh\nexit 1\n', encoding="utf-8")
        else:
            script.write_text(
                '#!/bin/sh\n'
                'echo "iptables $*" >> "$(dirname "$0")/calls.log"\n'
                'case "$*" in *-C*) exit 1;; esac\nexit 0\n',
                encoding="utf-8",
            )
        script.chmod(0o755)
        return bindir

    def test_dnat_is_preferred_over_a_config_rewrite(self, bash, tmp_path):
        conf = _write_conf(tmp_path)
        _, tail = _split(_run(bash, conf, iptables=self._fake_iptables(tmp_path)))
        assert "rc=0" in tail
        assert "DNAT :80 -> :8180 installed" in tail
        assert conf.read_text(encoding="utf-8") == AC15_CONF, "config must be untouched"

    def test_both_the_inbound_and_the_local_chain_are_covered(self, bash, tmp_path):
        """PREROUTING never sees traffic generated inside the guest."""
        bindir = self._fake_iptables(tmp_path)
        _run(bash, _write_conf(tmp_path), iptables=bindir)
        calls = (bindir / "calls.log").read_text(encoding="utf-8")
        assert "-t nat -A PREROUTING -p tcp --dport 80 -j DNAT --to-destination :8180" in calls
        assert "-t nat -A OUTPUT -p tcp --dport 80 -j DNAT --to-destination :8180" in calls

    def test_an_existing_rule_is_left_alone(self, bash, tmp_path):
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (bindir / "iptables").write_text('#!/bin/sh\nexit 0\n', encoding="utf-8")
        (bindir / "iptables").chmod(0o755)
        _, tail = _split(_run(bash, _write_conf(tmp_path), iptables=bindir))
        assert "already redirected to 8180" in tail
        assert "rc=0" in tail

    def test_a_failing_iptables_falls_through_to_the_config(self, bash, tmp_path):
        conf = _write_conf(tmp_path)
        _, tail = _split(_run(bash, conf, iptables=self._fake_iptables(tmp_path, fail_append=True)))
        assert "rc=0" in tail
        assert re.search(r"^\s*listen\s+80;$", conf.read_text(encoding="utf-8"), re.MULTILINE)


class TestFixupRouting:
    def test_a_running_server_on_80_is_left_alone(self):
        """The repair must only fire for the wrong-port case, never for a healthy one."""
        text = NET_FIX.read_text(encoding="utf-8")
        web_branch = text.split("if web_running; then", 1)[1].split("elif port80_listening", 1)[0]
        assert "port80_listening" in web_branch
        assert "redirect_to_port80" in web_branch

    def test_a_port_80_config_is_never_rewritten(self, bash, tmp_path):
        conf = _write_conf(tmp_path, "server {\n    listen 80;\n}\n")
        before = conf.read_text(encoding="utf-8")
        _, tail = _split(_run(bash, conf, target="80"))
        assert conf.read_text(encoding="utf-8") == before
        assert "rc=0" in tail
        assert "already on :80" in tail