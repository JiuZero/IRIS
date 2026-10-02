"""The guest command channel: does the guest actually offer one, or only claim to?

``iris_net_fix.sh`` used to start telnetd, log that it was starting, and move on --
no binary test, no port test, no failure path. On the Tenda TES7002 (the corpus'
arm64 sample) the log line appeared on every boot and the port was never bound:
``/bin/telnetd`` is a symlink to a busybox that does carry the applet, and a
telnetd launched from a ``::sysinit:`` hook with no controlling terminal can exit
before it binds. A channel that is only claimed is worse than one that is absent,
because it makes every later "the guardian reached into the guest" true of the
container instead.

So ``ensure_command_channel`` is driven here rather than asserted about. What it
must guarantee is a link between the return code and the line that lands in
qemu.serial.log: a boot that reports a channel has to have one. Both halves come
from the same branch, so a test that checks only the text can be satisfied by a
function that always claims success.

Finding the binary by absolute path was the first fix and it was not enough. With
telnetd located and started, the Tenda TES7002 still logged ``fallback shell is
listening on :7002 (after 0s)`` on a guest where every connect attempt was refused:
``netstat``'s output matched the port pattern somewhere it was not a listener, on
the very first poll. So the claim now needs two independent signals -- netstat must
name a listener *and* a client must be able to connect -- and
``TestAPortThatOnlyLooksOpen`` holds that line.

``netstat``, ``sleep`` and ``telnetd`` are replaced with shell functions -- the
same trick ``test_guest_net_fix_lock.py`` uses with ``add_func``. The host is
Windows, whose ``netstat`` rejects the busybox flags outright, so probing the real
one would measure the host rather than the script.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

NET_FIX = Path(__file__).resolve().parents[1] / "scripts" / "emulate" / "iris_net_fix.sh"

# `add_func` makes the script register rather than run, so the 45s fixup never
# executes and the helpers stay callable. $1 scratch dir, $2 script path.
_SOURCE = """
add_func() { :; }
export IRIS_NET_FIX_LOCK_DIR="$1/var/run/.iris_net_fix.lock"
export IRIS_CONSOLE="$1/console.log"
. "$2"
"""

#: busybox netstat prints `HOST:PORT` in decimal on some builds and in hex on
#: others. 7002 == 0x1b5a.
LISTEN_DECIMAL = "tcp   0  0 0.0.0.0:7002          0.0.0.0:*               LISTEN\n"
LISTEN_HEX = "tcp   0  0 0.0.0.0:1B5A          0.0.0.0:*               LISTEN\n"
LISTEN_OTHER_PORT = "tcp   0  0 0.0.0.0:8080          0.0.0.0:*               LISTEN\n"

#: Records what the function did instead of doing it, so a test can tell a probe
#: that polled from one that decided without asking. The telnetd *stub* is a real
#: executable file, not a shell function: the script looks for a binary by
#: absolute path and tests it with `[ -x ]`, and a function would satisfy neither.
#: It leaves a mark file rather than a variable because the script starts it with
#: ``&`` -- a background subshell's assignment never reaches the parent, and
#: whether the script waits is not what this is testing; it must not wait.
_STUBS = """
netstat() { cat "${NETSTAT_FIXTURE}"; }
sleep() { _slept=$((_slept + 1)); }
_slept=0
"""

#: No telnetd stub and a candidate list that cannot match, so the script's own
#: search is what decides.
_NO_APPLET_STUBS = """
netstat() { cat "${NETSTAT_FIXTURE}"; }
sleep() { _slept=$((_slept + 1)); }
_slept=0
IRIS_TELNETD_CANDIDATES="/nonexistent/telnetd /also/missing/telnetd"
"""

_REPORT = """
ensure_command_channel
echo "rc=$?"
echo "slept=${_slept}"

echo "--- console ---"
while IFS= read -r _line; do echo "${_line}"; done < "${IRIS_CONSOLE}"
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


def _source(bash: str, scratch: Path, body: str) -> str:
    posix = str(scratch).replace("\\", "/")
    proc = subprocess.run(
        [bash, "-c", _SOURCE + body, "_", posix, str(NET_FIX).replace("\\", "/")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


#: Stands in for the guest's telnetd. The script searches for a binary by
#: absolute path and tests it with `[ -x ]`, so this has to be a real file with
#: the exec bit rather than a shell function.
#:
#: Whether this stub actually *runs* is deliberately not asserted: a background
#: process started by a Git Bash that Python created (as subprocess does) does
#: not reliably land, while the identical flow typed at the prompt does. That is
#: an MSYS process-tree fact about the test host, not something the guest script
#: decides, and the behaviour that matters -- what the script concludes from what
#: it can see -- is asserted from the return code and the console line below.
#: The launch line itself is pinned by a static guard in TestTheGateIsStillThere.
_TELNETD_STUB = """#!/bin/sh
exit 0
"""


def _posix(path: Path) -> str:
    """The guest is POSIX; a Windows path would be a literal that never resolves."""
    return str(path).replace("\\", "/")


#: A real /proc/net/tcp6 line for the port, hex and state 0A (TCP_LISTEN) -- the
#: shape BusyBox netstat renders as ":::7002 :::* LISTEN". 7002 == 0x1b5a.
PROC_LISTEN_V6 = (
    "  sl  local_address                         remote_address"
    "                       st tx_queue rx_queue tr tm->when retrnsmt"
    "   uid  timeout inode\n"
    "   0: 00000000000000000000000000000000:1B5A 00000000000000000000000000000000:0000"
    " 0A 00000000:00000000 00:00000000 00000000     0        0 466 1 0000 100 0 0 10 -1\n"
)
#: The same table with the port gone -- what a daemon that died looks like.
PROC_NO_LISTENER = (
    "  sl  local_address                         remote_address"
    "                       st tx_queue rx_queue tr tm->when retrnsmt"
    "   uid  timeout inode\n"
    "   0: 00000000000000000000000000000000:1B58 00000000000000000000000000000000:0000"
    " 0A 00000000:00000000 00:00000000 00000000     0        0 465 1 0000 100 0 0 10 -1\n"
)
#: The port is in the table and the socket is not listening. State 06 is
#: TIME_WAIT: a connection that has already been closed. Matching the port
#: without the state would report this as a bound socket, which is the difference
#: between "the daemon is there" and "the port number once existed".
PROC_PORT_NOT_LISTENING = (
    "  sl  local_address                         remote_address"
    "                       st tx_queue rx_queue tr tm->when retrnsmt"
    "   uid  timeout inode\n"
    "   0: 0100007F:1B5A 0100007F:C1B2 06 00000000:00000000 00:00000000 00000000"
    "     0        0 512 1 0000 100 0 0 10 -1\n"
)

_PROC_REPORT = """
_probe=$(port_listening_in_proc 7002)
echo "rc=$?"
echo "probe=[${_probe}]"
echo "evidence=$(proc_net_evidence)"
"""


def _proc_table(bash: str, tmp_path: Path, proc_net: str) -> str:
    """Ask the socket-table probe directly, rather than through channel_live --
    it is the function that has to know 0A from 06, and routing that question
    through a caller would only test the caller."""
    table = tmp_path / "proc-net-tcp6"
    table.write_bytes(proc_net.encode("utf-8"))
    return _source(bash, tmp_path, f'IRIS_PROC_NET_FILES="{_posix(table)}"\n' + _PROC_REPORT)


def _real_timeout() -> str:
    """Where this host's `timeout` lives, in a form the guest-side search
    understands. Empty when there is none, which is the case the bare branch
    exists for."""
    found = shutil.which("timeout") or ""
    if found and Path(found).exists():
        return _posix(Path(found).resolve())
    return "/nonexistent/timeout"


#: Stands in for the guest's nc. Real executable for the same reason telnetd is:
#: ``port_connects`` finds its client with ``[ -x ]`` on absolute paths. Its exit
#: code is the whole point -- 0 is a port that accepted us, 1 is a port that
#: refused, and neither is simulated by netstat's opinion about a line of text.
#:
#: Anything that is not an address is rejected, because that is what the guest's
#: client does. BusyBox 1.22.1's nc takes only ``nc [IPADDR PORT]`` and answers
#: ``-w`` and ``-6`` with "invalid option" -- and a client handed an option it
#: does not know exits non-zero for that reason, which in a return code is
#: indistinguishable from a port that refused the connection. Stubbing a client
#: that ignores its arguments would let that whole class of bug through.
_NC_STUB = """#!/bin/sh
for _arg in "$@"; do
    case "${_arg}" in
        -*) echo "nc: invalid option -- '${_arg}'" >&2; exit 1 ;;
    esac
done
exit {rc}
"""

#: A guest that binds only the IPv6 wildcard: every IPv4 connect is refused and
#: the IPv6 one is answered. Distinguishes the two by the address it was handed,
#: which is the only thing the script varies.
_NC_STUB_V6_ONLY = """#!/bin/sh
_addr=""
for _arg in "$@"; do
    case "${_arg}" in
        -*) echo "nc: invalid option -- '${_arg}'" >&2; exit 1 ;;
        *:*) _addr="${_arg}" ;;
    esac
done
case "${_addr}" in
    ::1) exit 0 ;;
    *) exit 1 ;;
esac
"""


def _channel(
    bash: str,
    tmp_path: Path,
    netstat_output: str,
    stubs: str = _STUBS,
    nc_rc: int | None = 0,
    nc_v6_only: bool = False,
    with_timeout: bool = False,
    proc_net: str = "",
) -> str:
    """Drive ``ensure_command_channel`` against a guest that says one thing and
    does another.

    ``nc_rc`` is the exit status the fake client reports: ``0`` accepted the
    connection, ``1`` refused it. ``None`` means the guest ships no client at all,
    which is the one case where netstat's line is allowed to stand alone.
    ``nc_v6_only`` makes the client refuse IPv4 and answer IPv6.
    ``with_timeout`` gives the guest a ``timeout`` to wrap the client in.
    """
    fixture = tmp_path / "netstat.out"
    # bytes, not write_text: this host is Windows, where text mode rewrites \n to
    # \r\n -- and a stub whose shebang reads "#!/bin/sh\r" fails with "interpreter
    # not found", which looks exactly like the script never having looked.
    fixture.write_bytes(netstat_output.encode("utf-8"))
    stub = tmp_path / "telnetd-stub.sh"
    stub.write_bytes(_TELNETD_STUB.encode("utf-8"))
    stub.chmod(0o755)

    if nc_rc is None:
        candidates = "/nonexistent/nc /also/missing/netcat"
    else:
        nc = tmp_path / "nc-stub.sh"
        # replace, not format: the stub is shell, and `${_arg}` reads as a format
        # placeholder to str.format -- which turns a stub into a KeyError.
        nc.write_bytes(_NC_STUB.replace("{rc}", str(nc_rc)).encode("utf-8"))
        nc.chmod(0o755)
        candidates = _posix(nc)

    # Default is a guest with no `timeout`, because the host running these tests
    # has one and would otherwise silently decide which branch is covered: with it
    # present, the wrapped call is the only line exercised and the bare one is
    # never run at all.
    timeout = _real_timeout() if with_timeout else "/nonexistent/timeout"

    # Likewise /proc/net/tcp: present on a guest, and on the host it would answer
    # questions about the host's own sockets.
    proc_files = "/nonexistent/tcp /nonexistent/tcp6"
    if proc_net:
        proc = tmp_path / "proc-net-tcp6"
        proc.write_bytes(proc_net.encode("utf-8"))
        proc_files = _posix(proc)

    return _source(
        bash, tmp_path,
        f'NETSTAT_FIXTURE="{_posix(fixture)}"\n'
        f'IRIS_TELNETD_CANDIDATES="{_posix(stub)}"\n'
        f'IRIS_NC_CANDIDATES="{candidates}"\n'
        f'IRIS_TIMEOUT_CANDIDATES="{timeout}"\n'
        f'IRIS_PROC_NET_FILES="{proc_files}"\n' + stubs + _REPORT,
    )


def _field(out: str, name: str) -> str:
    for line in out.splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{name} not reported in:\n{out}")


class TestAChannelThatExists:
    def test_a_bound_port_is_reported_as_a_channel(self, bash, tmp_path):
        out = _channel(bash, tmp_path, LISTEN_DECIMAL)
        assert _field(out, "rc") == "0", out
        assert "listening on :7002" in out, out

    def test_the_probe_survives_a_client_that_rejects_options_it_was_handed(self, bash, tmp_path):
        """The stub refuses anything that is not an address, the way the guest's
        client does. The first version of this probe passed ``-w 2`` and ``-6``,
        and on the real guest both came back "invalid option" -- reported, by a
        return code, as a port that refused us."""
        out = _channel(bash, tmp_path, LISTEN_DECIMAL)
        assert _field(out, "rc") == "0", out
        assert "invalid option" not in out, "the probe handed the client something it does not take"


    def test_the_hex_form_is_recognised_too(self, bash, tmp_path):
        """A probe that only knows decimal answers "not listening" on a hex
        build -- which turns a channel that started into a report that it did
        not, in the other direction."""
        out = _channel(bash, tmp_path, LISTEN_HEX)
        assert _field(out, "rc") == "0", out
        assert "listening on :7002" in out, out

    def test_a_bound_channel_does_not_keep_polling(self, bash, tmp_path):
        assert _field(_channel(bash, tmp_path, LISTEN_DECIMAL), "slept") == "0"


class TestAChannelThatDoesNotExist:
    def test_nothing_bound_is_reported_as_no_channel(self, bash, tmp_path):
        """The regression. The old code logged that telnetd was starting and
        nothing more, so this boot's log read like a guest the guardian could
        reach into."""
        out = _channel(bash, tmp_path, "")
        assert _field(out, "rc") == "1", out
        assert "no command channel" in out, out
        assert "listening on :7002" not in out, out

    def test_it_actually_polled_before_concluding(self, bash, tmp_path):
        """One netstat read races the daemon's bind: telnetd is a separate
        process, so the first answer is routinely "not yet"."""
        assert _field(_channel(bash, tmp_path, ""), "slept") == "5"

    def test_another_port_is_not_mistaken_for_the_channel(self, bash, tmp_path):
        out = _channel(bash, tmp_path, LISTEN_OTHER_PORT)
        assert _field(out, "rc") == "1", out

    def test_a_guest_without_the_binary_says_so_and_starts_nothing(self, bash, tmp_path):
        out = _channel(bash, tmp_path, "", stubs=_NO_APPLET_STUBS)
        assert "no telnetd binary" in out, out
        assert _field(out, "rc") == "1", out
        # Not one poll: nothing was started, so there is nothing to wait for.
        assert _field(out, "slept") == "0", out

    def test_the_search_is_by_absolute_path_not_by_path_variable(self, bash, tmp_path):
        """A guest that ships telnetd in /sbin with /sbin absent from PATH is a
        guest with a shell and, by a `command -v` test, no command channel. The
        report has to name the paths it looked at so that case is visible in the
        serial log instead of looking like a guest with no shell at all."""
        text = NET_FIX.read_text(encoding="utf-8")
        assert "command -v telnetd" not in text, "PATH-based lookup is back"
        assert "IRIS_TELNETD_CANDIDATES" in text
        out = _channel(bash, tmp_path, "", stubs=_NO_APPLET_STUBS)
        assert "/nonexistent/telnetd" in out, "the paths searched for are not reported"


class TestAPortThatOnlyLooksOpen:
    """netstat agreeing that a port is bound is not the port being bound.

    This is the second half of the fix, and it is the half that only a real guest
    would have told us: with telnetd found and launched by absolute path, the
    TES7002 logged ``fallback shell is listening on :7002 (after 0s)`` -- zero
    polls, so the very first read of netstat matched -- while every connect attempt
    to that guest was refused. The text matched somewhere that was not a listener.
    """

    def test_a_line_that_merely_mentions_the_port_is_not_a_channel(self, bash, tmp_path):
        out = _channel(bash, tmp_path, LISTEN_DECIMAL, nc_rc=1)
        assert _field(out, "rc") == "1", out
        assert "no command channel" in out, out
        assert "listening on :7002" not in out, out

    def test_it_keeps_polling_while_the_text_looks_right_but_nothing_connects(self, bash, tmp_path):
        """The mismatch can be transient -- a daemon that binds between reads is
        real -- so it has to be given the same five seconds a missing port gets
        rather than condemned on the first answer."""
        assert _field(_channel(bash, tmp_path, LISTEN_DECIMAL, nc_rc=1), "slept") == "5"

    def test_it_reports_the_netstat_output_it_refused_to_believe(self, bash, tmp_path):
        """A refusal nobody can act on is half a bug. The next question about this
        firmware is always "what did netstat claim", and the answer has to be in
        the serial log rather than in the summary that turned out to be wrong."""
        out = _channel(bash, tmp_path, LISTEN_DECIMAL, nc_rc=1)
        assert "netstat -lan said:" in out, out
        assert "LISTEN" in out, "the line that produced the false claim is not shown"

    def test_a_guest_with_no_client_falls_back_to_the_netstat_line(self, bash, tmp_path):
        """Refusing to believe netstat leaves a guest that ships neither nc nor
        netcat permanently channel-less, which would be a worse answer than the
        one it replaced. Degraded but honest beats strict and silent."""
        out = _channel(bash, tmp_path, LISTEN_DECIMAL, nc_rc=None)
        assert _field(out, "rc") == "0", out
        assert "on the netstat line alone" in out, out

    def test_a_guest_with_no_client_and_no_listener_is_still_honest(self, bash, tmp_path):
        """The fallback is for the probe, not for the outcome: nothing bound means
        no channel, whether or not there was a client to ask."""
        out = _channel(bash, tmp_path, "", nc_rc=None)
        assert _field(out, "rc") == "1", out
        assert "no command channel" in out, out

    def test_the_client_is_looked_for_by_absolute_path_too(self, bash, tmp_path):
        """Same fault as telnetd: nc shipped in /sbin with /sbin absent from PATH is
        an absent client to a PATH lookup, and the log has to say where it looked
        rather than just that it found nothing."""
        text = NET_FIX.read_text(encoding="utf-8")
        assert "command -v nc" not in text, "PATH-based lookup is back"
        assert "IRIS_NC_CANDIDATES" in text
        out = _channel(bash, tmp_path, LISTEN_DECIMAL, nc_rc=None)
        assert "/nonexistent/nc" in out, "the paths searched for are not reported"


class TestAPortOnlyTheGuestCanReach:
    """A port the guest's own IPv4 stack cannot reach is not a channel.

    The whole IPv6 wildcard came out of a real guest rather than from a theory:
    netstat on the TES7002 read ``tcp 0 0 :::7002 :::* LISTEN`` -- telnetd was up
    and had really bound -- while every IPv4 connect was refused, because the
    OpenWrt-derived guest ships ``net.ipv6.bindv6only=1``.
    """

    def test_a_client_that_answers_over_ipv6_counts_as_a_channel(self, bash, tmp_path):
        out = _channel(bash, tmp_path, LISTEN_DECIMAL, nc_v6_only=True)
        assert _field(out, "rc") == "0", out
        assert "listening on :7002" in out, out

    def test_the_probe_tries_ipv6_and_not_only_ipv4(self):
        """Probing 127.0.0.1 alone reports a live daemon as dead, which sends the
        next person looking for a telnetd that is already running."""
        text = NET_FIX.read_text(encoding="utf-8")
        body = text.split("port_connects() {", 1)[1].split("\n}", 1)[0]
        assert "127.0.0.1" in body, body
        assert "::1" in body, body

    def test_bindv6only_is_cleared_before_telnetd_starts(self):
        """The socket is created when telnetd starts, so a sysctl applied after
        that point cannot have caused the bind it appears to explain -- which
        would be a line in the log reading like a fix that changed nothing."""
        text = NET_FIX.read_text(encoding="utf-8")
        body = text.split("ensure_command_channel() {", 1)[1].split("\n}", 1)[0]
        assert "/proc/sys/net/ipv6/bindv6only" in body, body
        assert body.index("bindv6only") < body.index('"${_telnetd}" -l /bin/sh'), body

    def test_the_daemon_is_detached_from_the_boot_hook(self):
        """telnetd is a child of a hook that is seconds from exiting, and that
        exit is not a clean failure: the socket was already created, so it keeps
        showing up in netstat as LISTEN with nothing accepting on it. Refused
        connections are then the same symptom as a daemon that died, which is why
        the report below has to say which of the two it was.

        Pinned as the conditions themselves rather than as a count of launch
        lines: three launches are present whether or not either guard is still
        testing for the binary, and a count is exactly the sort of assertion that
        stays green while the thing it was written about is gone.
        """
        text = NET_FIX.read_text(encoding="utf-8")
        body = text.split("ensure_command_channel() {", 1)[1].split("\n}", 1)[0]
        assert re.search(r"if \[ -x /bin/setsid \]; then", body), (
            "the daemon shares the boot hook's session at /bin/setsid"
        )
        assert re.search(r"elif \[ -x /usr/bin/setsid \]; then", body), (
            "only one setsid location is tried"
        )
        # A guest with neither still has to get the daemon started, which is what
        # the else branch is for.
        assert re.search(r"else\n", body), "no fallback for a guest without setsid"
        assert body.count('"${_telnetd}" -l /bin/sh -p 7002') == 3, body

    def test_the_failure_report_says_whether_the_daemon_survived(self, bash, tmp_path):
        """Distinguishing "no process" from "a process refusing us" is the whole
        reason the report carries evidence; without the daemon's own state the two
        read identically as a refused connection.

        What goes in the report is pidof's raw output, not a verdict made from it.
        This guest does answer, with a pid -- and a verdict built on it would then
        have to be reconciled against netstat asserting the opposite at the same
        moment, when a daemon that had died would have taken its socket with it,
        so only one of the two could be right. The raw output leaves that judgement
        to whoever reads it, with both halves in front of them."""
        out = _channel(bash, tmp_path, "", nc_rc=1)
        assert "pidof said: " in out, out
        assert "client said: " in out, out


class TestTheClientIsProbedTheSameWayEitherWay:
    """``port_connects`` has two shapes -- wrapped in ``timeout`` when the guest
    has one, bare when it does not -- and the bare one is the one this host cannot
    reach by accident. With a real ``timeout`` on PATH it would have stayed the
    only branch ever executed, and every mutation to the bare call would have
    gone unnoticed; ``TestTheGuestHasNoTimeoutToWrapItIn`` is why that is not the
    default here.
    """

    def test_a_guest_with_timeout_still_reaches_a_live_channel(self, bash, tmp_path):
        out = _channel(bash, tmp_path, LISTEN_DECIMAL, with_timeout=True)
        assert _field(out, "rc") == "0", out
        assert "invalid option" not in out, out

    def test_a_guest_without_timeout_still_reports_a_live_channel(self, bash, tmp_path):
        out = _channel(bash, tmp_path, LISTEN_DECIMAL, with_timeout=False)
        assert _field(out, "rc") == "0", out

    def test_a_refused_port_is_reported_either_way(self, bash, tmp_path):
        for with_timeout in (True, False):
            out = _channel(bash, tmp_path, "", nc_rc=1, with_timeout=with_timeout)
            assert _field(out, "rc") == "1", (with_timeout, out)
            assert "no command channel" in out, (with_timeout, out)


class TestAPortBoundButOutOfReach:
    """The state the Tenda TES7002 actually sits in, and the one a boolean cannot
    describe. telnetd is alive -- pidof names it, and the kernel lists the socket
    as LISTEN in /proc/net/tcp6 -- but it bound the IPv6 wildcard with IPV6_V6ONLY,
    QEMU's user-mode network does not carry IPv6, and every connect from the host
    is refused. "No channel" is true and "a channel" is true, and a report that
    says either without saying which is how the earlier versions of this function
    ended up lying in both directions.
    """

    def test_it_is_not_reported_as_a_usable_channel(self, bash, tmp_path):
        out = _channel(bash, tmp_path, LISTEN_DECIMAL, nc_rc=1, proc_net=PROC_LISTEN_V6)
        assert _field(out, "rc") == "1", out
        assert "no command channel" in out, out

    def test_the_report_says_the_socket_is_bound_anyway(self, bash, tmp_path):
        """Without this the next reader has exactly what the last one had: a
        refused connect, and no way to tell a dead daemon from the wrong address
        family short of running the daemon themselves."""
        out = _channel(bash, tmp_path, LISTEN_DECIMAL, nc_rc=1, proc_net=PROC_LISTEN_V6)
        assert "NOT usable" in out, out
        assert "1B5A" in out, out

    def test_a_dead_daemon_is_not_described_as_bound(self, bash, tmp_path):
        """The distinction is the whole reason /proc is read at all. If a missing
        socket were reported the same way, "bound" would carry no information and
        the line would be decoration."""
        out = _channel(bash, tmp_path, "", nc_rc=1, proc_net=PROC_NO_LISTENER)
        assert _field(out, "rc") == "1", out
        assert "NOT usable" not in out, "a socket that is not there was reported as bound"
        assert "no command channel" in out, out

    def test_the_socket_table_is_read_for_both_families(self):
        """tcp6 is where an IPv6-wildcard bind actually shows up, and reading only
        tcp is what made this look like a dead daemon in the first place."""
        text = NET_FIX.read_text(encoding="utf-8")
        assert "/proc/net/tcp /proc/net/tcp6" in text, "the IPv6 table is not read"
        assert "0A" in text, "the LISTEN state is not the one being looked for"


class TestTheSocketTableKnowsAListeningPortFromADeadOne:
    """``/proc/net/tcp`` lists every socket that ever held the port. Reading the
    port out of it and calling that a listener is how a TIME_WAIT remnant would be
    reported as a daemon that is up and refusing us -- the same wrong answer, in
    the other direction, that the netstat-only version gave.
    """

    def test_a_listening_socket_is_recognised(self, bash, tmp_path):
        out = _proc_table(bash, tmp_path, PROC_LISTEN_V6)
        assert _field(out, "rc") == "0", out
        assert "1B5A" in _field(out, "probe"), out

    def test_a_port_that_is_not_listening_is_not_recognised(self, bash, tmp_path):
        """State 06 against state 0A: same port, opposite answers."""
        out = _proc_table(bash, tmp_path, PROC_PORT_NOT_LISTENING)
        assert _field(out, "rc") == "1", out
        assert _field(out, "probe") == "[]", "a TIME_WAIT socket was reported as a listener"

    def test_an_absent_port_is_not_recognised(self, bash, tmp_path):
        assert _field(_proc_table(bash, tmp_path, PROC_NO_LISTENER), "rc") == "1"

    def test_a_guest_with_no_proc_net_reads_nothing(self, bash, tmp_path):
        """Every firmware without /proc/net -- a great many of them -- has to fall
        through to "nothing known", not to an error that reads as a verdict."""
        out = _source(bash, tmp_path, 'IRIS_PROC_NET_FILES="/nonexistent/tcp"\n' + _PROC_REPORT)
        assert _field(out, "rc") == "1", out

    def test_an_unreadable_table_is_reported_as_unreadable(self, bash, tmp_path):
        """"[]" means four different things -- no file, an unreadable file, a file
        with no entry for this port, or a format the pattern does not fit. That
        last one is the real risk: a pattern quietly that fits nothing reports a
        daemon as absent on the strength of a regex nobody checked. The evidence
        line is what tells them apart, so it has to quote the file itself."""
        out = _source(bash, tmp_path, 'IRIS_PROC_NET_FILES="/nonexistent/tcp"\n' + _PROC_REPORT)
        assert "evidence=" in out, out
        assert "unreadable" in out, out

    def test_a_readable_table_is_quoted_in_the_evidence(self, bash, tmp_path):
        out = _proc_table(bash, tmp_path, PROC_LISTEN_V6)
        assert "unreadable" not in out, out
        assert "local_address" in out, out


class TestTheReportAndTheReturnCodeAgree:
    """The point of the whole change: one branch produces both."""

    def test_every_outcome_pairs_the_right_code_with_the_right_line(self, bash, tmp_path):
        cases = [
            (LISTEN_DECIMAL, "0", "listening on :7002"),
            (LISTEN_HEX, "0", "listening on :7002"),
            ("", "1", "no command channel"),
            (LISTEN_OTHER_PORT, "1", "no command channel"),
        ]
        for output, rc, line in cases:
            out = _channel(bash, tmp_path, output)
            assert _field(out, "rc") == rc, (output, out)
            assert line in out, (output, out)
            # And never the opposite claim in the same boot.
            other = "no command channel" if rc == "0" else "listening on :7002"
            assert other not in out, (output, out)

    def test_a_refused_connection_never_reports_the_opposite(self, bash, tmp_path):
        """The case the single-probe version got wrong: netstat's text says open and
        the kernel says closed. Whichever of the two the script believes, the
        return code and the log line must still agree with each other."""
        out = _channel(bash, tmp_path, LISTEN_DECIMAL, nc_rc=1)
        assert _field(out, "rc") == "1", out
        assert "listening on :7002" not in out, out


class TestTheGateIsStillThere:
    def test_the_found_binary_is_what_gets_launched(self):
        """Pinning the launch line, because whether that background process
        really lands is a property of the MSYS process tree this test runs under
        (a Git Bash created by Python does not fork reliably) rather than of the
        script -- so the behaviour cases above cover what the script concludes,
        and this covers that it launches what it found rather than a bare name
        that PATH may not resolve."""
        text = NET_FIX.read_text(encoding="utf-8")
        body = text.split("ensure_command_channel() {", 1)[1].split("\n}", 1)[0]
        assert '"${_telnetd}" -l /bin/sh -p 7002' in body, (
            "the located binary is not the one being started"
        )

    def test_rc_common_images_keep_their_own_shell(self):
        """An OpenWrt image already has a remote shell; stacking a second one is
        how two daemons end up fighting over the port. The early return has to
        come first or that becomes a regression of its own."""
        text = NET_FIX.read_text(encoding="utf-8")
        body = text.split("ensure_command_channel() {", 1)[1].split("\n}", 1)[0]
        assert "[ -e /etc/rc.common ] && return 0" in body, body
        assert body.index("/etc/rc.common") < body.index("[ -x "), body

    def test_the_channel_is_wired_into_the_fixup(self):
        """A function nothing calls is a function that reports nothing."""
        text = NET_FIX.read_text(encoding="utf-8")
        assert "ensure_command_channel || true" in text
        # ...and called from fixup, not from the sourcing guard at the bottom.
        fixup = text.split("fixup() {", 1)[1]
        assert "ensure_command_channel" in fixup

    def test_port_80_still_goes_through_the_same_probe(self):
        """The web probe is the reason the helper was generalised; it must not
        have been left on a private copy of the old grep."""
        text = NET_FIX.read_text(encoding="utf-8")
        body = text.split("port80_listening() {", 1)[1].split("\n}", 1)[0]
        assert body.strip() == "port_listening 80", body