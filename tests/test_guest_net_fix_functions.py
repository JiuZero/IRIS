"""The guest fallback's network probes, exercised by running the real script.

The one that decides reachability is "does this guest have an address yet". It used
to be ``ifconfig "$1" | grep "inet addr"``, and that had two measured failure modes
on real guests:

* BusyBox changed its ifconfig output after v1.20 -- old builds print
  `inet addr:192.168.1.1`, current builds print `inet 192.168.1.1` -- so a hardcoded
  ``grep "inet addr"`` reported every OpenWrt 24.x guest as unconfigured.
* Worse, ``ifconfig`` never returned at all on the Linksys WRT1200AC image. The
  console shows the fallback's own ``probing eth0`` and then nothing for the rest of
  the run: ifconfig opens an AF_INET socket and asks SIOCGIFCONF, which walks every
  network device, and netifd holds eth0's device lock while it retries
  wpa_supplicant/hostapd about once a second. A probe that can hang in the boot path
  skips everything below it -- the fallback address, the command channel, the web
  server.

So the probe reads /proc/net/fib_trie instead. The sibling table /proc/net/route does
not work and its failure is the quieter of the two: on OpenWrt 24.10 guests it
carries a header row and no data at all, so a probe built on it reports every guest
as unconfigured and the fallback assigns an address the vendor chain already
assigned -- which on a router whose LAN is 192.168.0.0/24 is a second, conflicting
address on the same wire.

The script is sourced with ``add_func`` pre-defined so the source registers boot()
rather than running the fixup, and ``IRIS_FIB_TRIE``/``IRIS_TCP_TABLE`` point the
real functions at a fixture table.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

NET_FIX = Path(__file__).resolve().parents[1] / "scripts" / "emulate" / "iris_net_fix.sh"

#: A /proc/net/fib_trie as Linux prints it: the main table, then the local one. Only
#: a row followed by `<mask> host LOCAL` is an address this guest holds.
CONFIGURED_GUEST = """\
Main:
 +-- 0.0.0.0/0 1 0 1 0 0
    0.0.0.0
       /0 universe UNIVERSE
    +-- 192.168.1.0/24
    |  |
    |  +-- 192.168.1.1
    |       /32 host LOCAL
    +-- 192.168.1.1
    |  +-- 192.168.1.1
    |       /32 host LOCAL

Local:
 +-- 0.0.0.0
    0.0.0.0
       /0 universe UNIVERSE
 +-- 127.0.0.1
    +-- 127.0.0.1
       /32 host LOCAL
"""

#: The guest this fallback exists for: loopback and nothing else.
BARE_GUEST = """\
Main:
 +-- 0.0.0.0/0 1 0 1 0 0
    0.0.0.0
       /0 universe UNIVERSE

Local:
 +-- 0.0.0.0
    0.0.0.0
       /0 universe UNIVERSE
 +-- 127.0.0.1
    +-- 127.0.0.1
       /32 host LOCAL
"""

#: How a router's LAN bridge looks: a statically addressed subnet, no gateway, and
#: no default route anywhere. A probe that required a default route calls this guest
#: unconfigured.
STATIC_LAN_GUEST = CONFIGURED_GUEST.replace("192.168.1.1", "192.168.0.1")

TCP_HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
    "   uid  timeout inode\n"
)


def _tcp(*rows: str) -> str:
    return TCP_HEADER + "".join(f"{row}\n" for row in rows)


LISTENING_80 = "   0: 00000000:0050 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 19760 1 0 0 0 10 0"
ESTABLISHED_80 = "   1: 0101A8C0:0050 0201A8C0:1F90 01 00000000:00000000 00:00000000 00000000     0        0 19888 1 0 0 0 10 0"
LISTENING_7002 = "   0: 00000000:1B5A 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 19900 1 0 0 0 10 0"
LISTENING_2050 = "   0: 00000000:0802 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 19901 1 0 0 0 10 0"


@pytest.fixture(scope="module")
def sh() -> str:
    for candidate in (shutil.which("bash"), "C:/Program Files/Git/usr/bin/bash.exe"):
        if candidate and Path(candidate).exists():
            probe = subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True, check=False)
            if probe.returncode == 0 and probe.stdout.strip() == "ok":
                return candidate
    pytest.skip("no working POSIX shell available to run iris_net_fix.sh")


def _source(
    sh: str,
    tmp_path: Path,
    call: str,
    *,
    extra: tuple[str, ...] = (),
    fib: str | None = CONFIGURED_GUEST,
    tcp: str | None = None,
) -> subprocess.CompletedProcess:
    """Source the real script and run one call against fixture kernel tables.

    Sourcing runs the registration logic, which calls fixup() only when the shell
    lacks ``add_func`` -- defining it as a no-op first makes the source register
    boot() and return. The table paths are exported rather than prefixed onto the
    call, so the functions under test resolve them exactly as they do on a guest.
    The trailing printf reports the status of ``call`` and nothing else, so callers
    read ``rc=0`` as that call's own answer.
    """
    prelude = ["add_func() { :; }", *extra]
    if fib is not None:
        fib_file = tmp_path / "fib_trie"
        fib_file.write_text(fib, encoding="utf-8")
        prelude += [f"IRIS_FIB_TRIE={fib_file.as_posix()}", "export IRIS_FIB_TRIE"]
    if tcp is not None:
        tcp_file = tmp_path / "tcp"
        tcp_file.write_text(tcp, encoding="utf-8")
        prelude += [f"IRIS_TCP_TABLE={tcp_file.as_posix()}", "export IRIS_TCP_TABLE"]
    source = "\n".join([*prelude, f". {NET_FIX.as_posix()}", call, 'printf "rc=%s\\n" "$?"'])
    return subprocess.run(
        [sh, "-c", source],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=False,
    )


def _probed(sh: str, tmp_path: Path, call: str, **kwargs) -> tuple[bool, subprocess.CompletedProcess]:
    run = _source(sh, tmp_path, call, **kwargs)
    assert run.returncode == 0, run.stderr
    return "rc=0" in run.stdout, run


class TestGuestHasIpv4:
    def test_a_guest_with_an_address_is_recognised(self, sh, tmp_path):
        found, _ = _probed(sh, tmp_path, "guest_has_ipv4")
        assert found

    def test_a_guest_with_only_loopback_is_not(self, sh, tmp_path):
        """127.0.0.1 is exactly the guest this fallback exists for."""
        found, _ = _probed(sh, tmp_path, "guest_has_ipv4", fib=BARE_GUEST)
        assert not found

    def test_a_statically_addressed_lan_is_recognised(self, sh, tmp_path):
        """How a router's bridge looks: 192.168.0.1 and no gateway anywhere.

        A default-route test reads this as unconfigured, which is the judgement that
        sends the fallback to configure eth0 a second, conflicting address.
        """
        found, _ = _probed(sh, tmp_path, "guest_has_ipv4", fib=STATIC_LAN_GUEST)
        assert found

    def test_an_empty_table_is_not_an_address(self, sh, tmp_path):
        found, _ = _probed(sh, tmp_path, "guest_has_ipv4", fib="")
        assert not found

    def test_a_universe_entry_is_not_read_as_an_address(self, sh, tmp_path):
        """`0.0.0.0` appears in every table, followed by `/0 universe UNIVERSE`.

        Matching the candidate row without checking what follows it makes every guest
        look configured, which is the opposite failure from the one this probe was
        rewritten to fix.
        """
        table = (
            "Main:\n"
            " +-- 0.0.0.0/0 1 0 1 0 0\n"
            "    0.0.0.0\n"
            "       /0 universe UNIVERSE\n"
        )
        found, _ = _probed(sh, tmp_path, "guest_has_ipv4", fib=table)
        assert not found

    def test_a_local_address_is_not_read_from_the_row_above_its_own_marker(self, sh, tmp_path):
        """The candidate has to be dropped by any row that is not a marker.

        fib_trie marks a local address on the row *below* its `+--` row, and between
        the two a kernel may print a bare continuation bar for a subtree it has not
        expanded. Carrying the candidate across such a row attributes the local
        address to the branch above it, so a guest holding nothing but an unexpanded
        branch is reported as configured -- and the fallback then declines to assign
        the address it exists to assign.
        """
        table = (
            "Local:\n"
            " +-- 0.0.0.0\n"
            "    0.0.0.0\n"
            "       /32 host LOCAL\n"
        )
        found, _ = _probed(sh, tmp_path, "guest_has_ipv4", fib=table)
        assert not found

    def test_a_missing_table_is_not_an_address_and_not_an_error(self, sh, tmp_path):
        """A kernel or mount without fib_trie must read as unconfigured."""
        run = _source(sh, tmp_path, "guest_has_ipv4", fib=None)
        assert run.returncode == 0, run.stderr
        run = subprocess.run(
            [sh, "-c", "\n".join([
                "add_func() { :; }",
                f"IRIS_FIB_TRIE={(tmp_path / 'absent').as_posix()}",
                "export IRIS_FIB_TRIE",
                f". {NET_FIX.as_posix()}",
                "guest_has_ipv4",
                'printf "rc=%s\\n" "$?"',
            ])],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False,
        )
        assert run.returncode == 0, run.stderr
        assert "rc=0" not in run.stdout

    def test_the_probe_does_not_invoke_ifconfig(self, sh, tmp_path):
        """The regression: ifconfig blocked this probe for the rest of the run.

        A socket lookup cannot be made non-blocking here -- there is no linked
        timeout applet and the device walk can wait on netifd indefinitely -- so the
        fix is to stop calling it. Shadowing ifconfig with a function that reports the
        call and exits non-zero already fails an implementation that still uses it,
        because the answer comes from the table either way; the stderr assertion is
        what names the regression.
        """
        run = _source(
            sh, tmp_path, "guest_has_ipv4",
            extra=("ifconfig() { echo 'ifconfig was called' >&2; exit 66; }",),
        )
        assert run.returncode == 0, run.stderr
        assert "rc=0" in run.stdout
        assert "ifconfig was called" not in run.stderr


class TestPortListeningLine:
    def test_listening_port_answers_yes(self, sh, tmp_path):
        found, _ = _probed(sh, tmp_path, "port_listening 80", tcp=_tcp(LISTENING_80))
        assert found

    def test_listening_port_returns_the_kernel_line(self, sh, tmp_path):
        found, run = _probed(sh, tmp_path, "port_listening_line 80", tcp=_tcp(LISTENING_80))
        assert found
        assert "0050" in run.stdout

    def test_port_in_established_state_is_not_a_listener(self, sh, tmp_path):
        """The address is bound but nobody is accepting -- that is not :80 live."""
        found, _ = _probed(sh, tmp_path, "port_listening 80", tcp=_tcp(ESTABLISHED_80))
        assert not found

    def test_a_different_port_is_not_a_match(self, sh, tmp_path):
        found, _ = _probed(sh, tmp_path, "port_listening 80", tcp=_tcp(LISTENING_7002))
        assert not found

    def test_a_shared_hex_prefix_is_not_a_match(self, sh, tmp_path):
        """80 is 0x0050 and 2050 is 0x0802: a substring match would confuse :80."""
        found, _ = _probed(sh, tmp_path, "port_listening 80", tcp=_tcp(LISTENING_2050))
        assert not found

    def test_another_listening_port_still_answers_yes(self, sh, tmp_path):
        found, _ = _probed(sh, tmp_path, "port_listening 7002", tcp=_tcp(LISTENING_80, LISTENING_7002))
        assert found

    def test_readable_socket_table_means_netstat_is_never_asked(self, sh, tmp_path):
        """Same reason as the ifconfig guard: netstat needs a socket too, and a
        negative answer from the kernel's own table is the final answer."""
        run = _source(
            sh, tmp_path, "port_listening 80",
            extra=("netstat() { echo 'netstat was called' >&2; exit 66; }",),
            tcp=_tcp(LISTENING_80),
        )
        assert run.returncode == 0, run.stderr
        assert "rc=0" in run.stdout
        assert "netstat was called" not in run.stderr

    def test_readable_socket_table_with_no_listener_means_netstat_is_never_asked(self, sh, tmp_path):
        """The branch that must not fall back: "not bound" is a real answer, and
        falling back would reintroduce the hang this avoids."""
        run = _source(
            sh, tmp_path, "port_listening 80",
            extra=("netstat() { echo 'netstat was called' >&2; exit 66; }",),
            tcp=_tcp(LISTENING_7002),
        )
        assert run.returncode == 0, run.stderr
        assert "rc=0" not in run.stdout
        assert "netstat was called" not in run.stderr


NEVER_RETURNS = 'sh -c "while :; do sleep 1; done"'


class TestRunBounded:
    """The bound that keeps a hanging command from taking the fallback with it."""

    def _bounded(self, sh: str, tmp_path: Path, command: str, bound: str = "2") -> tuple[int, str]:
        run = _source(
            sh, tmp_path, f"run_bounded {command}",
            extra=(f"IRIS_BOUNDED_TIMEOUT={bound}", "export IRIS_BOUNDED_TIMEOUT"),
        )
        assert run.returncode == 0, run.stderr
        last = run.stdout.strip().splitlines()[-1]
        return int(last.split("=", 1)[1]), run.stdout

    def test_a_command_that_finishes_keeps_its_own_status(self, sh, tmp_path):
        rc, _ = self._bounded(sh, tmp_path, 'sh -c "exit 3"')
        assert rc == 3

    def test_a_command_that_succeeds_keeps_status_zero(self, sh, tmp_path):
        rc, _ = self._bounded(sh, tmp_path, "true")
        assert rc == 0

    def test_its_output_is_printed(self, sh, tmp_path):
        _, out = self._bounded(sh, tmp_path, "echo hello")
        assert "hello" in out

    def test_a_command_that_never_returns_is_killed_at_the_bound(self, sh, tmp_path):
        """The WRT1200AC failure in miniature: the guest's ifconfig never came back,
        so every step after it was silently skipped. 124 is what GNU timeout reports,
        which is why the call sites read the way they do."""
        rc, _ = self._bounded(sh, tmp_path, NEVER_RETURNS)
        assert rc == 124

    def test_the_bound_is_wall_clock_not_nominal(self, sh, tmp_path):
        """A loop that never trips its own bound would pass the test above."""
        started = time.monotonic()
        rc, _ = self._bounded(sh, tmp_path, NEVER_RETURNS)
        elapsed = time.monotonic() - started
        assert rc == 124
        assert elapsed < 45, f"a 2s bound took {elapsed:.1f}s"

    def test_only_stdout_is_captured(self, sh, tmp_path):
        """port_listening_line's contract is a single line of answer.

        Merging stderr here would make it return whatever netstat complained about
        instead of the listener it was asked for.
        """
        rc, out = self._bounded(sh, tmp_path, 'sh -c "echo out; echo err >&2"')
        assert rc == 0
        assert "out" in out
        assert "err" not in out

    def test_the_killed_command_leaves_no_output_file_behind(self, sh, tmp_path):
        """The scratch file is removed on both paths; a leftover in the guest's /tmp
        would be read by nothing but would still accumulate across boots."""
        leftovers_before = set(Path("/tmp").glob("iris_net_fix.bounded.*"))
        self._bounded(sh, tmp_path, NEVER_RETURNS)
        self._bounded(sh, tmp_path, "true")
        assert set(Path("/tmp").glob("iris_net_fix.bounded.*")) == leftovers_before

class TestLogFallback:
    """log() has to reach the console even where the console cannot be opened.

    On the Tenda DIR-868L the explicit open of the device node fails, and a
    redirection that cannot be opened takes its command down with it — so every
    line the fallback wanted to say was gone, with no error, while the surrounding
    rcS printed to the same console through the descriptor init handed it. The
    guest log therefore showed the boot chain talking and the fallback silent.
    """

    def test_it_writes_the_console_file_when_the_device_opens(self, sh, tmp_path):
        console = tmp_path / "console"
        run = _source(
            sh, tmp_path, "log hello",
            extra=(f"IRIS_CONSOLE={console.as_posix()}", "export IRIS_CONSOLE"),
        )
        assert run.returncode == 0, run.stderr
        assert "IRIS-NETFIX: hello" in console.read_text(encoding="utf-8")

    def test_it_falls_back_to_the_inherited_stdout_when_the_open_fails(self, sh, tmp_path):
        """Pointed at a path that cannot be opened, as on the DIR-868L.

        stdout here is the pipe the test reads, which stands in for the descriptor
        the boot chain inherited — the same channel the vendor rcS itself uses.
        """
        unwritable = tmp_path / "no" / "such" / "dir" / "console"
        run = _source(
            sh, tmp_path, "log hello",
            extra=(f"IRIS_CONSOLE={unwritable.as_posix()}", "export IRIS_CONSOLE"),
        )
        assert run.returncode == 0, run.stderr
        assert "IRIS-NETFIX: hello" in run.stdout, run.stdout
        assert not unwritable.exists()

    def test_it_does_not_double_log_when_the_device_opens(self, sh, tmp_path):
        """The fallback must stay silent where the first channel worked."""
        console = tmp_path / "console"
        run = _source(
            sh, tmp_path, "log hello",
            extra=(f"IRIS_CONSOLE={console.as_posix()}", "export IRIS_CONSOLE"),
        )
        assert run.stdout.count("IRIS-NETFIX: hello") == 0, run.stdout
