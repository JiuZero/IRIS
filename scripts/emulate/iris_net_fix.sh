#!/bin/sh
# IRIS network/service fallback, injected into the guest rootfs at /etc/init.d/iris_net_fix.
# Two invocation styles must both work:
#   - OpenWrt-style images source it via /etc/rc.common (START=99; boot())
#   - arm64 generic-kernel channel runs it directly, backgrounded by the caller
# Everything that matters is logged to /dev/console so it lands in qemu.serial.log.

START=99

# Overridable only so the lock can be exercised against a scratch directory; the
# guest always takes the default. Spelled as an explicit test rather than the
# shell's own default-value expansion, which the Tenda DIR-868L busybox drops to
# the empty string.
LOCK_DIR=$IRIS_NET_FIX_LOCK_DIR
[ -n "${LOCK_DIR}" ] || LOCK_DIR=/var/run/.iris_net_fix.lock

# Where the log lines go. /dev/console is the guest's serial console and the only
# thing qemu.serial.log captures, so it stays the default; overridable because
# the same script is driven off-target by the test suite, where the open fails
# outright. Truncating rather than appending is not a hazard: /dev/console is a
# character device, so a truncating redirect has no truncate to do -- and appending
# is not an option, because opening a console device with O_APPEND fails outright,
# which silenced every line the fallback ever wanted to say.
CONSOLE=$IRIS_CONSOLE
[ -n "${CONSOLE}" ] || CONSOLE=/dev/console

# The kernel's own network tables, which the probes below read instead of asking
# a socket-based tool. Overridable only so the test suite can drive the real
# functions against a fixture; the guest always reads the live ones.
FIB_TRIE=$IRIS_FIB_TRIE
[ -n "${FIB_TRIE}" ] || FIB_TRIE=/proc/net/fib_trie
TCP_TABLE=$IRIS_TCP_TABLE
[ -n "${TCP_TABLE}" ] || TCP_TABLE=/proc/net/tcp

log() {
    # Two channels, in order, and the second costs nothing where the first works.
    #
    # Opening the console device explicitly fails on the Tenda DIR-868L, and a
    # redirection that cannot be opened takes its command down with it -- so every
    # line this script had to say was lost, with no error anywhere. The surrounding
    # rcS printed to the same console through the descriptor init already handed it,
    # which is why the guest log showed the boot chain talking and the fallback
    # silent. Where the device does open, the first echo succeeds and the fallback
    # never runs; the last-resort echo still carries the discard of stderr so a
    # closed descriptor cannot turn a log line into a second failure.
    echo "IRIS-NETFIX: $*" > "${CONSOLE}" 2>/dev/null \
        || echo "IRIS-NETFIX: $*" 2>/dev/null
}

# The first line of stdin, or nothing.
#
# This exists because head(1) is not in every vendor BusyBox. The Tenda DIR-868L
# ships a reduced build with no head applet at all -- running it there prints
# "head: applet not found" and yields no output, so a pipeline into head reads as
# "nothing matched" rather than "cannot ask". That distinction is the whole job of
# the port probes below: the empty answer is what makes this script start its own
# web server on a guest whose vendor server is already listening.
#
# read builtin is a shell builtin in every ash this script has to run in, so
# reading the first line needs no external command at all. Returns 1 when stdin
# is empty, so callers can tell "no line" from "a line that happens to be blank".
first_line() {
    while IFS= read -r _fl_line; do
        printf '%s\n' "${_fl_line}"
        return 0
    done
    return 1
}

# Two channels can reach this script: inittab's ::sysinit: (which does not wait
# for the vendor rcS chain) and the tail of rcS itself (which only runs if that
# chain finished). mkdir is atomic on every filesystem this touches, so it is
# enough to keep the loser out -- without it both would probe :80, both would
# find it empty, and two goahead processes would fight over the port.
#
# Returns 0 when the fixup may proceed, 1 when another instance holds the lock.
# A lock dir that cannot be created at all (read-only /var/run) is *not* a
# conflict: standing down there would silently disable the only fallback, which
# is the failure this whole script exists to prevent.
acquire_lock() {
    # mkdir with -p for the parents (some firmwares ship no /var/run), then a
    # plain mkdir for the lock itself, which is the atomic part: -p on the lock
    # itself would report success to both racers -- and would also create the lock,
    # which is the one thing the second caller has to be able to lose.
    #
    # The parent is named by stripping the last path component with sed rather than
    # with a last-path-component expansion. That form measures *empty* on the Tenda
    # DIR-868L -- an echo of it on a three-component path printed empty brackets
    # there, a vendor BusyBox with part of ash's parameter expansion compiled out.
# And dirname(1) is absent on the AC15. sed is present on both, and its output
    # for a path with no slash in it is the input unchanged, which is what the
    # normalisation on the next line is there to catch.
    lock_parent=$(printf '%s' "${LOCK_DIR}" | sed 's|/[^/]*/*$||')
    [ "${lock_parent}" = "${LOCK_DIR}" ] && lock_parent="."
    [ -n "${lock_parent}" ] || lock_parent="/"
    mkdir -p "${lock_parent}" 2>/dev/null
    if mkdir "${LOCK_DIR}" 2>/dev/null; then
        return 0
    fi
    if [ -d "${LOCK_DIR}" ]; then
        # A lock left by a previous boot of the same writable volume is stale;
        # the owner is only alive for the duration of this fixup.
        owner=$(cat "${LOCK_DIR}/pid" 2>/dev/null)
        if [ -n "${owner}" ] && [ ! -d "/proc/${owner}" ]; then
            log "clearing stale lock from pid ${owner}"
            rm -rf "${LOCK_DIR}" 2>/dev/null || true
            mkdir "${LOCK_DIR}" 2>/dev/null && return 0
        fi
        return 1
    fi
    log "cannot create ${LOCK_DIR}, proceeding without a lock"
    return 0
}

guest_has_ipv4() {
    # Reading /proc is not a style choice. The probe used to be
    # ifconfig piped into a grep for the legacy address form, and that had two
    # on real guests:
    #
    # * BusyBox changed its ifconfig output after v1.20 -- old builds print
    #   measured failure modes on real guests:
    #   the legacy output was inet addr:192.168.1.1 and the current one is
    #   unconfigured.
    # * Worse, ifconfig never returned at all on the Linksys WRT1200AC image. The
    #   console shows the fallback's own "probing eth0" and then nothing for the rest
    #   of the run: ifconfig opens an AF_INET socket and asks SIOCGIFCONF, which walks
    #   every network device, and netifd holds eth0's device lock while it retries
    #   wpa_supplicant/hostapd about once a second, so the walk never completes. A
    #   probe that can hang cannot sit in the boot path -- everything below it
    #   (assigning the fallback address, opening the command channel, starting a web
    #   server) is skipped, which is precisely how a guest ends up with no address.
    #   /proc is a file read: it answers or it does not, and never waits on another
    #   process.
    #
    # /proc/net/fib_trie is the table that answers it. /proc/net/route does not: on
    # OpenWrt 24.10 guests it carries the header row and no data at all (measured on
    # Newifi D2 and WRT1200AC, both of which did have an address on eth0), so a probe
    # built on it reports every guest as unconfigured and the fallback then assigns
    # an address the vendor chain already assigned -- which on a router whose LAN is
    # 192.168.0.0/24 means a second, conflicting address on the same wire.
    #
    # fib_trie lists local addresses as a bar-or-plus followed by dashes and an
    # address, then a masked row ending in host LOCAL. The marker row carries bars
    # and indentation of its own, so the address is the last field rather than the
    # second, and any row without a marker clears the candidate -- otherwise the
    # all-zeroes address of the default branch is read as the address of whatever
    # preceded it. 127/8 is loopback and does not count: a guest whose only address is
    # 127.0.0.1 is exactly the guest this fallback exists for. The question is
    # deliberately "does the guest have an address", not "which interface has it" --
    # the fallback only ever assigns to eth0 and fib_trie does not name interfaces,
    # so asking per interface bought nothing but the socket lookup that hangs.
    awk '
        /(\||\+)-- / { candidate = $NF; next }
        {
            if (candidate != "" && $0 ~ /host LOCAL/ \
                && candidate ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ \
                && candidate !~ /^127\./) {
                found = 1
            }
            candidate = ""
        }
        END { exit found ? 0 : 1 }
    ' "${FIB_TRIE}" 2>/dev/null
}

# Wall-clock bound for the calls that open a socket, which on some guests never
# return.
#
# Two measured cases on the Linksys WRT1200AC image, both on the same device lock:
# ifconfig on eth0 never returned, and on a later run so did the netstat call that
# the channel report prints after everything else has already failed -- a
# diagnostic that hangs is the worst case of all, because it is the last thing that
# would have said anything at all. iptables goes through netlink for the same
# reason, and a write that follows a read on the same interface cannot be assumed
# safe when the read hung.
#
# The read paths that *could* be rewritten were rewritten rather than bounded:
# guest_has_ipv4 and port_listening_line answer from /proc, which needs no timeout at all.
#
# The guest's busybox carries the timeout applet but does not link it into the
# path, so the bound is assembled from what any shell has: the call runs in the
# background, the parent polls, and anything still alive at the bound is killed and
# reported as 124 -- the status GNU timeout uses, so the call sites read the way
# they do with the real tool.
#
# Five seconds, not ten: a working socket call returns in microseconds and a
# blocked one is blocked on a lock netifd is holding across a retry cycle, so a
# longer bound buys nothing and costs the guest boot time the web fallback still
# needs. On WRT1200AC the five bounded calls below were spending fifty seconds of
# a twenty-second budget.
BOUNDED_TIMEOUT=$IRIS_BOUNDED_TIMEOUT
[ -n "${BOUNDED_TIMEOUT}" ] || BOUNDED_TIMEOUT=5

# Prints the bounded command's stdout, returns its status (or 124 when it hung).
# Only stdout is captured. The call sites that need stderr gone say so with their
# own stderr discard, and merging it here would break the ones whose contract is a
# single line of answer -- port_listening_line would start returning whatever
# netstat complained about instead of the listener it is asked for.
run_bounded() {
    _rb_tmp=$TMPDIR
    [ -n "${_rb_tmp}" ] || _rb_tmp=/tmp
    _rb_out="${_rb_tmp}/iris_net_fix.bounded.$$"
    "$@" > "${_rb_out}" &
    _rb_pid=$!
    _rb_waited=0
    while [ "${_rb_waited}" -lt "${BOUNDED_TIMEOUT}" ] && kill -0 "${_rb_pid}" 2>/dev/null; do
        # The wait here is the bound itself, so it has to be real time. Tests that
        # replace sleep with a poll counter set this to stand the replacement down;
        # on a guest nothing reads it.
        _IRIS_IN_BOUND=1
        sleep 1
        _IRIS_IN_BOUND=0
        _rb_waited=$((_rb_waited + 1))
    done
    if kill -0 "${_rb_pid}" 2>/dev/null; then
        kill -9 "${_rb_pid}" 2>/dev/null
        _rb_rc=124
    else
        wait "${_rb_pid}" 2>/dev/null
        _rb_rc=$?
    fi
    cat "${_rb_out}" 2>/dev/null
    rm -f "${_rb_out}"
    return "${_rb_rc}"
}

# Busybox netstat is unreliable here: several vendor builds print the service
# name ("0.0.0.0:http") instead of the numeric port, so a ':0050' grep reports
# a live web server as absent and IRIS ends up launching a second one (which
# then logs "Cannot bind to address *:80, errno 98"). Process presence is what
# the vendor's own supervisor checks, so use that first.
web_running() {
    for p in goahead boa lighttpd httpd uhttpd thttpd apache2 nginx; do
        pidof "$p" >/dev/null 2>&1 && return 0
        ps 2>/dev/null | grep -w "$p" | grep -v grep >/dev/null 2>&1 && return 0
    done
    return 1
}

# The line that made this script believe a port was bound. Empty when nothing
# matched. Kept separate from the yes/no answer because on a real guest the text
# that satisfied this probe was an unrelated IPv6 address rather than a listener,
# and there is no way to tell those apart without seeing the line.
#
# /proc/net/tcp is read first for the reason spelled out in guest_has_ipv4:
# netstat needs a
# socket too, so it inherits ifconfig's ability to hang the boot hook. When
# /proc/net/tcp is readable the answer is authoritative and netstat is not asked --
# falling back after a real "not bound" would reintroduce the hang this avoids.
# netstat remains for guests whose kernel predates /proc/net/tcp.
port_listening_line() {
    _pll_dec=$1
    _pll_hex=$(printf '%04X' "${_pll_dec}" 2>/dev/null)
    if [ -r "${TCP_TABLE}" ]; then
        # Column 2 is local_address as HEXADDR:HEXPORT and column 4 is the state,
        # where 0A is TCP_LISTEN. Ports in /proc/net/tcp are big-endian hex, which
        # is what printf produced above.
        awk -v want="${_pll_hex}" '
            NR > 1 {
                if (split($2, addr, ":") == 2 && toupper(addr[2]) == want && $4 == "0A") {
                    print
                    found = 1
                    exit
                }
            }
            END { exit found ? 0 : 1 }
        ' "${TCP_TABLE}" 2>/dev/null
        return $?
    fi
    run_bounded netstat -lan 2>/dev/null \
        | grep -E "[:.]${_pll_dec}([[:space:]]|$)|[:.]${_pll_hex}([[:space:]]|$)" \
        | first_line
}

# Whether a TCP port is bound, as far as netstat's own output goes. Takes the port
# as an argument so the fallback shell below can ask the same question about :7002
# that the web probe asks about :80 -- a second copy of this probe is a second
# thing that can be wrong about the guest. Both the decimal and the hex form are
# matched because busybox netstat prints whichever its build was compiled for.
port_listening() {
    [ -n "$(port_listening_line "$1")" ]
}

port80_listening() {
    port_listening 80
}

# Whether something actually accepts a connection to a port. netstat reports what
# it was told; this reports what the kernel did. Returns 2 when the guest has no
# usable client at all, which is a different answer from "the port refused us" and
# must not be read as a live channel.
#
# Searched by absolute path for the same reason telnetd is: PATH in a firmware
# guest is whatever the vendor's init left behind, and a guest that ships nc
# outside PATH is a guest this probe would call dead.
# Both lists are spelled as explicit tests rather than as the shell's own
# assign-if-unset expansion: that is the same family of parameter expansion that
# measures empty on the Tenda DIR-868L, and an empty candidate list here would
# read as "the guest has no client", which is the answer this probe is trying to
# earn rather than assume.
IRIS_NC_CANDIDATES=$IRIS_NC_CANDIDATES
[ -n "${IRIS_NC_CANDIDATES}" ] || IRIS_NC_CANDIDATES="/bin/nc /usr/bin/nc /bin/netcat /usr/bin/netcat"
IRIS_TIMEOUT_CANDIDATES=$IRIS_TIMEOUT_CANDIDATES
[ -n "${IRIS_TIMEOUT_CANDIDATES}" ] || IRIS_TIMEOUT_CANDIDATES="/bin/timeout /usr/bin/timeout"

port_connects() {
    _pc_port=$1
    NC_PROBE=""
    for _pc_nc in ${IRIS_NC_CANDIDATES}; do
        [ -x "${_pc_nc}" ] || continue
        # Wrapped in a timeout when the guest has one: a client that connects
        # successfully and then waits on input would otherwise hang the boot hook
        # that is checking on it, and a hung fixup is a silent one.
        _pc_to=""
        for _pc_to_candidate in ${IRIS_TIMEOUT_CANDIDATES}; do
            [ -x "${_pc_to_candidate}" ] && { _pc_to="${_pc_to_candidate}"; break; }
        done
        _pc_log=""
        # No flags at all, and IPv6 addressed by spelling rather than by -6. The
        # guest's BusyBox 1.22.1 nc takes only "nc [-l] [-p PORT] [IPADDR PORT]";
        # -w and -6 are both rejected with "invalid option", and in a return code
        # that refusal is indistinguishable from a port that refused us -- which is
        # how a wrong probe came to be reported as a dead channel.
        for _pc_addr in 127.0.0.1 ::1; do
            if [ -n "${_pc_to}" ]; then
                _pc_out=$("${_pc_to}" 3 "${_pc_nc}" "${_pc_addr}" "${_pc_port}" </dev/null 2>&1)
            else
                _pc_out=$("${_pc_nc}" "${_pc_addr}" "${_pc_port}" </dev/null 2>&1)
            fi
            _pc_rc=$?
            # The client's own words go to NC_PROBE rather than to /dev/null,
            # because a probe whose failure mode is invisible is how this script
            # came to log a channel that was never there.
            _pc_log="${_pc_log} ${_pc_addr}=rc${_pc_rc}[$(echo "${_pc_out}" | tr '\n' ' ')]"
            [ "${_pc_rc}" -eq 0 ] && return 0
        done
        NC_PROBE="${_pc_nc}${_pc_log}"
        return 1
    done
    return 2
}

# The kernel's own socket table, which needs neither a client nor netstat's
# formatting. A listening socket carries state 0A in /proc/net/tcp{,6} with the
# port in hex. This is the tie-breaker for the case where netstat and a client
# disagree about a port that is plainly there: the daemon bound the IPv6 wildcard,
# every IPv4 connect is refused, and nothing else in this script can say whether
# the daemon died or the network is simply the wrong family.
#
# Overridable for the same reason the other searches are: /proc/net/tcp is where
# this evidence lives on a real guest and is a path that does not exist on the
# host that has to drive it, so the test suite reads a fixture instead.
IRIS_PROC_NET_FILES=$IRIS_PROC_NET_FILES
[ -n "${IRIS_PROC_NET_FILES}" ] || IRIS_PROC_NET_FILES="/proc/net/tcp /proc/net/tcp6"

port_listening_in_proc() {
    _pip_dec=$1
    _pip_hex=$(printf '%04X' "${_pip_dec}")
    for _pip_f in ${IRIS_PROC_NET_FILES}; do
        [ -r "${_pip_f}" ] || continue
        # local_address ":PORT", then the remote address (8 hex digits on tcp, 32
        # on tcp6, each plus a 4-digit port), then the state -- 0A is TCP_LISTEN.
        # Matched into a variable rather than piped straight out, so that "there is
        # one" and "it exited zero" are the same answer: left to the loop, a guest
        # whose files are all unreadable exits zero having printed nothing.
        _pip_hit=$(grep -E ":${_pip_hex} [0-9A-F]+:[0-9A-F]{4} 0A" "${_pip_f}" 2>/dev/null | first_line)
        if [ -n "${_pip_hit}" ]; then
            echo "${_pip_hit}"
            return 0
        fi
    done
    return 1
}

# What the socket tables actually offered, as opposed to what was matched out of
# them. An empty bracket here is ambiguous on its own -- absent file, unreadable
# file, file with no entry for this port, or a format the pattern does not fit --
# and "NOT usable: bound as []" is the same report for all four, which is a claim
# about the daemon made out of a fact about the filesystem. On the Tenda TES7002
# it was the third of the four, and the difference is not guessable from "[]".
proc_net_evidence() {
    _pne_out=""
    for _pne_f in ${IRIS_PROC_NET_FILES}; do
        if [ -r "${_pne_f}" ]; then
            _pne_out="${_pne_out}${_pne_f}:[$(tr '\n' '|' < "${_pne_f}" 2>/dev/null | cut -c1-200)] "
        else
            _pne_out="${_pne_out}${_pne_f}:unreadable "
        fi
    done
    echo "${_pne_out}"
}

# The channel claim is only believed when netstat names a listener *and* a client
# can connect to it. Either probe alone repeats the fault this script already
# had once: a port described as open that nothing will ever answer on.
#
# How the claim was established comes back in CHANNEL_PROBE rather than in a log
# line of its own, for the reason log() documents: a second line overwrites the
# first on anything that is not a character device, and "listening" with the basis
# for believing it lost is a claim this script already made once.
CHANNEL_PROBE=""

channel_live() {
    CHANNEL_PROBE=""
    port_listening 7002 || return 1
    port_connects 7002
    _cl_rc=$?
    if [ "${_cl_rc}" -eq 2 ]; then
        CHANNEL_PROBE="on the netstat line alone -- no nc/netcat at [${IRIS_NC_CANDIDATES}]"
        return 0
    fi
    if [ "${_cl_rc}" -eq 0 ]; then
        CHANNEL_PROBE="netstat named a listener and a client connected to it"
        return 0
    fi

    # Refused, yet the kernel still lists a listening socket. Not a channel -- the
    # only client that can use this one is inside the guest -- but calling it
    # absent would be wrong too, and the report below needs to be able to say so
    # rather than leave the next reader to rediscover it from a refused connect.
    CHANNEL_PROBE="NOT usable: bound as [$(port_listening_in_proc 7002 | tr -s ' ' | cut -c1-100)] but no client outside the guest can reach it; proc net said: [$(proc_net_evidence)]"
    return 1
}

# nginx configuration files to inspect, in the order they are trusted. Overridable
# because the layout is a per-firmware guess: a firmware with its own tree (the
# AC15's /etc_ro) needs a different list, and hardcoding one set of absolute
# paths is how the fallback ends up reading a file that is not there.
IRIS_NGINX_CONF=$IRIS_NGINX_CONF
[ -n "${IRIS_NGINX_CONF}" ] || IRIS_NGINX_CONF="/etc/nginx/conf/nginx.conf /etc_ro/nginx/conf/nginx.conf /etc/nginx/nginx.conf /etc_ro/nginx/nginx.conf"

# The port a vendor web server actually bound, when it is not 80.
#
# AC15's nginx.conf hardcodes a listen directive on 8180 and relies on a vendor redirector
# (cfmd) to forward port 80 to 8180. That redirector segfaults within seconds of boot,
# so the port the rehost depends on is served by nothing while nginx is healthy
# and listening. Reading the config is the only way to learn the real port: the
# supervisor that would normally own the redirect is exactly what is missing.
vendor_web_port() {
    for conf in ${IRIS_NGINX_CONF}; do
        [ -f "${conf}" ] || continue
        # Only uncommented, non-ssl listen directives: the stock nginx.conf ships
        # three commented examples (8000/443/somename) that would otherwise be
        # read as the real port.
        sed -n 's/^[[:space:]]*listen[[:space:]]\{1,\}\([0-9]\{1,\}\)[;[:space:]].*/\1/p' \
            "${conf}" 2>/dev/null | first_line
    done
}

# Put something on :80 when the vendor's own web server is not there.
#
# DNAT is tried first because it leaves the vendor's configuration untouched: if
# the guest reboots or nginx restarts, the redirect still holds. Rewriting the
# config is the fallback for the many firmwares whose busybox has no iptables
# applet at all, and it is applied only when the config is writable -- a
# read-only /etc_ro would otherwise turn every build into a silent no-op.
redirect_to_port80() {
    target=$1
    if [ "${target}" = "80" ]; then
        log "vendor web server is already on :80, nothing to redirect"
        return 0
    fi
    if run_bounded iptables -t nat -C PREROUTING -p tcp --dport 80 -j DNAT --to-destination ":${target}" 2>/dev/null; then
        log "port 80 already redirected to ${target}"
        return 0
    fi
    if run_bounded iptables -t nat -A PREROUTING -p tcp --dport 80 -j DNAT --to-destination ":${target}" 2>/dev/null; then
        # OUTPUT covers traffic originating inside the guest (a health check on
        # 127.0.0.1, say), which PREROUTING never sees.
        run_bounded iptables -t nat -A OUTPUT -p tcp --dport 80 -j DNAT --to-destination ":${target}" 2>/dev/null || true
        log "DNAT :80 -> :${target} installed"
        return 0
    fi

    for conf in ${IRIS_NGINX_CONF}; do
        [ -f "${conf}" ] || continue
        if grep -q "^[[:space:]]*listen[[:space:]]\{1,\}${target};" "${conf}"; then
            # The captured group is the whole "  listen       " prefix, so the
            # replacement is just the group and the new port. Appending the
            # keyword again here would emit "listen listen 80;", which nginx
            # refuses to start with -- a silent loss of the only web server.
            if sed -i "s/^\([[:space:]]*listen[[:space:]]\{1,\}\)${target};/\1 80;/" "${conf}" 2>/dev/null; then
                log "nginx listen ${target} rewritten to 80 in ${conf}"
                pkill -HUP nginx 2>/dev/null || true
                return 0
            fi
            log "cannot rewrite ${conf} (read-only filesystem)"
        fi
    done
    log "no way to expose vendor web server on :80 (no iptables, config not writable)"
    return 1
}

#: Where a fallback shell may live, most likely first. Overridable only so the
#: test suite can point the search at a stub; the guest always takes the default.
IRIS_TELNETD_CANDIDATES=$IRIS_TELNETD_CANDIDATES
[ -n "${IRIS_TELNETD_CANDIDATES}" ] || IRIS_TELNETD_CANDIDATES="/bin/telnetd /sbin/telnetd /usr/bin/telnetd /usr/sbin/telnetd"

# Start the guest's fallback shell and report what actually happened.
#
# Returns 0 when a command channel exists, 1 when it does not -- and says which,
# in a line that lands in qemu.serial.log. Everything downstream that claims to
# reach into a running guest rests on that line, so it has to carry the outcome
# and not the intention.
#
# This used to be three inline statements: start telnetd, log that it was
# starting, move on. No binary test, no port test, no failure path. On the Tenda
# TES7002 the line appeared on every boot and the port was never bound --
# /bin/telnetd is a symlink to a busybox that does carry the applet, and
# telnetd launched from a ::sysinit: hook with no controlling terminal can exit
# before it binds. A channel that is only claimed is worse than one that is
# absent: it makes every later "the guardian reached into the guest" true of
# the container instead.
#
# Fixing the path lookup was not enough on its own. With telnetd found and
    # started, the boot log still claimed ":7002 (after 0s)" on a guest where every
    # IPv4 connect was refused. Two more things came out of chasing that down:
    # channel_live below, which does not let one probe's opinion of a line of text
    # stand in for a connection, and a failure report carrying the netstat output
    # and the client's own words -- the evidence is what turned "something is
    # wrong with the channel" into "telnetd bound :: and refuses IPv4", which no
    # amount of staring at a boolean would have produced.
#
# A function rather than inline code so the test suite can drive it -- the
# outcome that matters here is invisible from the host.
ensure_command_channel() {
    # OpenWrt-style images bring their own remote shell via rc.common; stacking a
    # second one on top of theirs is how two daemons end up fighting over a port.
    [ -e /etc/rc.common ] && return 0

    # Searched by absolute path rather than through command -v, because PATH is
    # the vendor's init left behind: a guest that ships telnetd under /sbin with
    # /sbin absent from PATH is a guest with a shell and, by the PATH test, no
    # command channel. The path is also overridable so the test suite can point
    # the search at a stub -- the outcome here is invisible from the host, so
    # this function is driven directly rather than asserted about as text.
    _telnetd=""
    for _candidate in ${IRIS_TELNETD_CANDIDATES}; do
        if [ -x "${_candidate}" ]; then
            _telnetd="${_candidate}"
            break
        fi
    done
    if [ -z "${_telnetd}" ]; then
        # PATH goes into the line deliberately: "there is no telnetd" and
        # "telnetd is somewhere PATH does not reach" are different faults and
        # only one of them is a dead end.
        log "no telnetd binary at [${IRIS_TELNETD_CANDIDATES}] (PATH=${PATH}): no command channel on :7002"
        return 1
    fi

    # OpenWrt-derived guests set net.ipv6.bindv6only=1 and busybox telnetd binds
    # the IPv6 wildcard, so the channel can come up on :: and be unreachable from
    # exactly the IPv4 network the guardian would use. Observed on the Tenda
    # TES7002, where netstat reads "tcp 0 0 :::7002 :::* LISTEN" -- telnetd
    # genuinely bound -- while every IPv4 connect is refused.
    #
    # Kept even though it does not save that guest: BusyBox 1.22.1's telnetd sets
    # IPV6_V6ONLY on its own socket, and a socket-level flag outranks a sysctl.
    # A guest whose telnetd does not set it still gets an IPv4-reachable channel
    # here, and the setting costs one line. Applied before telnetd starts, since
    # the socket does not exist until it does.
    if [ -w /proc/sys/net/ipv6/bindv6only ]; then
        echo 0 > /proc/sys/net/ipv6/bindv6only 2>/dev/null || true
    fi
    BIND_V6ONLY=$(cat /proc/sys/net/ipv6/bindv6only 2>/dev/null || echo "no such sysctl")

    # Started through setsid where the guest has one. telnetd here is a child of
    # a boot hook that is seconds from exiting, and the failure that produces is
    # deceptive rather than obvious: the socket is created and shows up in netstat
    # as LISTEN, and then nothing ever accepts on it -- so "no process is left" and
    # "a process is there and refusing us" both present as a refused connection.
    #
    # stdin from /dev/null in every case: telnetd otherwise inherits whatever init
    # handed the ::sysinit: hook, and a login that reads it can swallow the rest of
    # the boot's console input.
    if [ -x /bin/setsid ]; then
        /bin/setsid "${_telnetd}" -l /bin/sh -p 7002 </dev/null >/dev/null 2>&1 &
    elif [ -x /usr/bin/setsid ]; then
        /usr/bin/setsid "${_telnetd}" -l /bin/sh -p 7002 </dev/null >/dev/null 2>&1 &
    else
        "${_telnetd}" -l /bin/sh -p 7002 </dev/null >/dev/null 2>&1 &
    fi

    _waited=0
    while [ "${_waited}" -lt 5 ] && ! channel_live; do
        _waited=$((_waited + 1))
        sleep 1
    done
    if channel_live; then
        log "fallback shell is listening on :7002 after ${_waited}s, proven ${CHANNEL_PROBE}: $(port_listening_line 7002)"
        return 0
    fi

    # One log call, not two: log() writes with a truncating redirect because
    # /dev/console is a character device, and on any other target each call would
    # replace the last.
    # The verdict and the evidence belong on the same line anyway -- the next
    # question about this firmware is "what did netstat claim", and a summary that
    # may be the thing that was wrong is no answer to it.
    #
    # pidof's own words rather than a verdict derived from them. This guest does
    # answer -- with a real pid -- but a verdict built on it would have to be
    # reconciled against netstat asserting the opposite at the same moment, and a
    # daemon that had died would have taken its socket with it, so exactly one of
    # the two probes could be right. Raw output does not need reconciling: it
    # settles the question in a second, for whoever reads it.
    log "telnetd (${_telnetd}) ran but nothing answers on :7002 after ${_waited}s: no command channel; why: ${CHANNEL_PROBE}; pidof said: [$(pidof telnetd 2>&1)]; netstat -lan said: [$(run_bounded netstat -lan 2>/dev/null | tr '\n' '|')]; client said: [${NC_PROBE}]; bindv6only=${BIND_V6ONLY}"
    return 1
}

fixup() {
    if ! acquire_lock; then
        log "another iris_net_fix instance holds the lock, standing down"
        return 0
    fi
    echo $$ > "${LOCK_DIR}/pid" 2>/dev/null || true
    log "start: brief pause before taking over the network"
    # Short grace period: the vendor chain (configd) may configure itself. The IP
    # is the critical path for reachability, so fix it early and only wait longer
    # before deciding whether a web server has to be launched.
    sleep 15
    log "grace period over, probing for an address"

    # One question, not one per interface: whether to assign the fallback at all.
    # Asking per interface was only ever a way to ask this, and it needed a probe
    # that could name an interface -- which is what forced the socket-based lookup
    # that hangs. fib_trie answers the question that matters without naming one.
    if guest_has_ipv4; then
        HAS_IP=1
        log "guest already has a non-loopback address, leaving the vendor's addressing alone"
    else
        HAS_IP=0
        log "no non-loopback address, assigning fallback 192.168.1.1 to eth0"
        run_bounded ifconfig eth0 192.168.1.1 netmask 255.255.255.0 up || true
    fi
    log "address probe finished (has_ip=${HAS_IP}); fib_trie said: [$(tr '\n' '|' < "${FIB_TRIE}" 2>/dev/null)]"

    run_bounded ifconfig eth0 up || true

    # Vendor firewalls block rehosted-internal traffic by default.
    run_bounded iptables -F 2>/dev/null || true
    run_bounded iptables -P INPUT ACCEPT 2>/dev/null || true
    run_bounded iptables -P OUTPUT ACCEPT 2>/dev/null || true
    run_bounded iptables -P FORWARD ACCEPT 2>/dev/null || true

    # The verdict after the writes, not before: this is the line the host reads to
    # decide whether the guest ever got an address, so it has to be the last word on
    # the question rather than the state before the fallback's own attempt.
    if guest_has_ipv4; then
        log "final: guest has a non-loopback address"
    else
        log "final: guest still has no non-loopback address"
    fi

    # Start the guest's fallback shell; it reports its own outcome to the console.
    ensure_command_channel || true

    # Web: vendor config may have prevented its own web server from starting
    # (e.g. configd needs a UBIFS /var/config that does not exist under rehosting).
    # Wait a bit longer first so the vendor's own server wins the race for :80.
    sleep 30
    log "probing for a web server on :80"
    if web_running; then
        log "vendor web server is already running, leaving :80 to it"
        if ! port80_listening; then
            # Running but not on :80 is the AC15 case: nginx is healthy on 8180
            # and the redirector that owned 80 crashed. Detect and repair rather
            # than launching a second server that would only fail to bind.
            vendor_port=$(vendor_web_port)
            if [ -n "${vendor_port}" ] && [ "${vendor_port}" != "80" ]; then
                log "vendor web server is on :${vendor_port}, not :80"
                redirect_to_port80 "${vendor_port}"
            else
                log "vendor web server is running but its port could not be determined"
            fi
        fi
    elif port80_listening; then
        log "something else already listens on :80"
    elif [ -x /opt/goahead/goahead ] && [ -f /opt/goahead/route.txt ]; then
        log "web not listening on :80, launching goahead"
        /opt/goahead/goahead --home /opt/goahead --route /opt/goahead/route.txt &
    elif [ -x /usr/bin/boa ] && [ -f /etc/boa/boa.conf ]; then
        log "web not listening on :80, launching boa"
        /usr/bin/boa -c /etc/boa &
    else
        log "no web server fallback available"
    fi

    log "done"
}

# rc.common defines add_func before sourcing: register boot() there, run fixup
# directly when invoked as a plain script (arm64 channel).
if type add_func >/dev/null 2>&1; then
    add_func boot
else
    fixup
fi
