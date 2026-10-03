#!/bin/sh
# Guest boot-hook injection, extracted from make_image.sh so it can be exercised
# directly against a synthetic /etc instead of only through a full image build.
#
# Usage: inject_boot_hooks.sh <image-dir> [--script-source <dir>]
#
# Three hooks are installed:
#
#  1. iris_net_fix_bg on inittab's ::sysinit:, inserted *ahead* of the vendor
#     chain. BusyBox init runs ::sysinit: entries in order and blocks on each, so
#     an entry appended after rcS only runs once the whole vendor chain finished.
#     On TES7002 that chain never finishes (rc50/rc63 block forever waiting on
#     hardware a rehost cannot provide), which starved IRIS's own network/Web
#     fallback of its only chance to run.
#     BusyBox execs the process field verbatim — it does not interpret shell
#     metacharacters — so a trailing `&` here would be a literal argument rather
#     than a background job; iris_net_fix_bg does the backgrounding itself.
#     procd cannot take this hook at all; see the init-family branch below.
#
#  2. a marker around each rc script in rcS, because rcS redirects every rc
#     script to /dev/null: a boot that stalls looks identical to a boot that
#     succeeded.
#
#  3. iris_net_fix appended to the tail of rcS, as a second line of defence for
#     firmwares whose inittab has no ::sysinit: entry to hook (or whose init
#     ignores inittab entirely and only ever runs rcS).
#
# /etc is not always /etc. Tenda AC15 ships a read-only /etc_ro with the real
# configuration, points /etc at a writable overlay (`etc -> /var/etc`, which is
# empty on disk), and its inittab names /etc_ro/init.d/rcS — so init never reads
# anything under /etc at all. Injecting into /etc there writes into a tree that is
# overwritten at boot by `cp -rf /etc_ro/* /etc/`, and the fallback silently never
# runs. The tree is therefore located by probing for the one that holds inittab.
set -e

IMAGE_DIR=${1:?usage: inject_boot_hooks.sh <image-dir>}
SCRIPT_DIR=${2:-/work/scripts}

# The directory that actually holds the boot configuration, and the same path as
# the guest will see it. Probing inittab rather than init.d is deliberate: a
# vendor can ship an init.d with no inittab next to it (the two are independent
# choices), and init is what decides whether anything here runs at all.
HOST_ETC="${IMAGE_DIR}/etc"
GUEST_ETC="/etc"
if [ ! -f "${HOST_ETC}/inittab" ] && [ -f "${IMAGE_DIR}/etc_ro/inittab" ]; then
    HOST_ETC="${IMAGE_DIR}/etc_ro"
    GUEST_ETC="/etc_ro"
    echo "boot config lives under /etc_ro, not /etc (inittab names ${GUEST_ETC}/init.d/rcS)"
fi

BG_SCRIPT="${HOST_ETC}/init.d/iris_net_fix_bg"
INITTAB="${HOST_ETC}/inittab"
RCS="${HOST_ETC}/init.d/rcS"
MARKER='#IRIS-NETFIX-SYSINIT'
ENTRY="::sysinit:${GUEST_ETC}/init.d/iris_net_fix_bg"

# ------------------------------------------------------- launcher path baked in
# The launcher has to find its sibling, and every shell-side way of doing that is
# missing on at least one real guest: no dirname applet, and — measured on the Tenda
# DIR-868L — `${0%/*}` expanding to the empty string, which left the fixup to be
# looked up at /iris_net_fix and the launcher exiting 0 having run nothing. So the
# install path goes in here, where a full shell is available, and the guest does no
# parsing at all. Leaving the placeholder in place would silently restore that
# failure, so it is checked rather than assumed.
if [ -f "${BG_SCRIPT}" ]; then
    if sed -i "s|@IRIS_GUEST_INIT_D@|${GUEST_ETC}/init.d|g" "${BG_SCRIPT}" \
       && ! grep -q '@IRIS_GUEST_INIT_D@' "${BG_SCRIPT}"; then
        echo "launcher path baked into ${GUEST_ETC}/init.d/iris_net_fix_bg"
    else
        echo "WARNING: could not bake the install path into the launcher;"
        echo "  it will look for its sibling at the filesystem root and find nothing"
    fi
else
    echo "no ${BG_SCRIPT}, launcher path not baked in"
fi

# ------------------------------------------------------------------ init family
# procd and BusyBox init read the same file, but procd's sysinit channel cannot
# take a second entry, and injecting one there does not merely lose the race — it
# destroys the vendor boot chain:
#
#   * procd_inittab_run() walks the action list and `break`s after the first match
#     unless the handler is flagged `multi`. sysinit and shutdown are not, so an
#     injected entry *replaces* the vendor rcS entry rather than preceding it.
#   * the handler is runrc(), which refuses a line without the `<S|K> <param>`
#     tail and only then logs "valid format is rcS <S|K> <param>".
#
# Together those mean a bare `::sysinit:<script>` entry runs neither IRIS's
# fallback nor the vendor chain: on OpenWrt the guest comes up with no netifd and
# no uhttpd, and the only symptom is that one procd line in the serial log.
#
# The 3-field test is procd's own requirement read back from its source, not a
# firmware sniff: procd only ever writes `<process> <S|K> <param>` into sysinit /
# shutdown lines, and BusyBox init never execs an extra argument there. Matching
# on `respawn` lines instead would misfire — those legitimately carry many fields
# on either init.
IS_PROCD=""
if [ -f "${INITTAB}" ]; then
    IS_PROCD="$(awk '
        /^[[:space:]]*#/ { next }
        {
            if ($0 !~ /^[^:]*:[^:]*:(sysinit|shutdown):/) next
            rest = $0
            sub(/^[^:]*:[^:]*:[^:]*:/, "", rest)
            n = split(rest, f, /[ \t]+/)
            seen = 0
            for (i = 1; i <= n; i++) if (f[i] != "") seen++
            if (seen >= 3) { print "yes"; exit }
        }
    ' "${INITTAB}")"
fi

# ---------------------------------------------------------------- sysinit hook
# Strip any previous injection first so re-running the build (same rootfs, new
# image) is idempotent instead of stacking duplicates — and so a tree that was
# hooked while it was treated as BusyBox does not keep a dead entry once it is
# recognised as procd.
CLEAN=""
if [ -f "${INITTAB}" ]; then
    CLEAN="$(mktemp "${IMAGE_DIR}/inittab.XXXXXX")"
    grep -v 'IRIS-NETFIX-SYSINIT\|iris_net_fix_bg' "${INITTAB}" > "${CLEAN}" || true
fi

if [ -n "${IS_PROCD}" ]; then
    if [ -n "${CLEAN}" ]; then
        mv "${CLEAN}" "${INITTAB}"
    fi
    echo "inittab is procd's (sysinit carries <S|K> <param>): sysinit hook skipped"
    echo "  procd runs only the first ::sysinit: entry, so injecting one would"
    echo "  replace the vendor rcS and starve the whole boot chain"
    # procd has no rcS script to tail-hook either: it walks /etc/rc.d/S* itself,
    # so that directory is the only channel it offers. The link make_image.sh
    # installs is the fallback's entry point; ensure it here too so the guarantee
    # does not depend on that call order, and because a procd firmware whose
    # vendor set ships no rc.d still gets a working channel.
    RCD="${HOST_ETC}/rc.d"
    mkdir -p "${RCD}"
    if [ ! -e "${RCD}/S99iris_net_fix" ]; then
        # A failed link must not abort the rest of the injection, and must not be
        # reported as success: without it the guest has no fallback at all.
        if ln -s ../init.d/iris_net_fix "${RCD}/S99iris_net_fix" 2>/dev/null; then
            echo "  fallback linked into ${GUEST_ETC}/rc.d as S99iris_net_fix"
        else
            echo "  WARNING: could not link the fallback into ${GUEST_ETC}/rc.d"
            echo "  (host cannot create symlinks?) — guest will get no network/web fallback"
        fi
    else
        echo "  ${GUEST_ETC}/rc.d/S99iris_net_fix already present"
    fi
elif [ -z "${CLEAN}" ]; then
    echo "no ${INITTAB}, sysinit hook skipped"
elif [ ! -f "${BG_SCRIPT}" ]; then
    rm -f "${CLEAN}"
    echo "iris_net_fix_bg missing at ${BG_SCRIPT}, sysinit hook skipped"
else
    if awk -v entry="${ENTRY}" '
            /^::sysinit:/ && !inserted { print "'"${MARKER}"'"; print entry; inserted = 1 }
            { print }
            END { exit inserted ? 0 : 1 }
        ' "${CLEAN}" > "${CLEAN}.out"; then
        mv "${CLEAN}.out" "${INITTAB}"
        rm -f "${CLEAN}"
        echo "iris_net_fix_bg injected ahead of the first ::sysinit: entry"
    else
        # No ::sysinit: line at all (an inittab that boots straight into an
        # action). Prepend so the entry is still first.
        { printf '%s\n%s\n' "${MARKER}" "${ENTRY}"; cat "${CLEAN}"; } > "${INITTAB}"
        rm -f "${CLEAN}"
        echo "iris_net_fix_bg prepended: inittab has no ::sysinit: entry to insert before"
    fi
fi

# ----------------------------------------------------------------- rcS tracing
if [ ! -f "${RCS}" ]; then
    echo "no ${RCS}, rc tracing skipped"
elif grep -q 'IRIS-RC: begin' "${RCS}"; then
    echo "rcS tracing already enabled"
elif grep -q 'sh \$rc_file > */dev/null 2>&1' "${RCS}"; then
    # Every & in the replacement must be escaped: sed reads a bare & as "the
    # whole match", which silently rewrites 2>&1 into a splice of the matched
    # text — the symptom being rcS failing with "can't create sh".
    #
    # The traced line still contains `sh $rc_file > /dev/null 2>&1`, so the
    # branch above is what keeps a rebuild from wrapping the wrappers in a second
    # layer, one per build.
    sed -i 's|sh \$rc_file > */dev/null 2>&1|echo "IRIS-RC: begin $rc_file" > /dev/console; sh $rc_file > /dev/null 2>\&1; echo "IRIS-RC: end $rc_file" > /dev/console|' "${RCS}"
    echo "rcS tracing enabled"
else
    echo "rcS tracing skipped: no 'sh \$rc_file > /dev/null 2>&1' line to mark"
fi
# --------------------------------------------------- rcS tail fallback hook
# The sysinit hook above is the one that runs early, but an inittab can have no
# ::sysinit: entry at all (this script then only prepends, which works) or an init
# can ignore inittab and run rcS directly. Appending here costs nothing — the
# script stands down on its lock if the sysinit hook already won the race — and
# covers those firmwares. On procd there is no rcS file to append to, and the
# branch above already put the fallback on the channel procd does drive.
#
# When there is no inittab at all, the sysinit hook above was skipped and this
# tail hook is the ONLY channel the fallback gets — so appending to the end of
# rcS is not good enough. Tenda DIR-868L's rcS ends with `/etc/init0.d/rcS`, a
# handoff script that blocks or loops, and anything appended underneath it is
# starved the same way an entry after a stuck rcS sequence would be. In that
# configuration the hook is inserted *before the final non-blank, non-comment
# line* instead, so the vendor's own S??* chain still runs first (the network is
# configured by the time the fallback wakes) but no trailing handoff can eat it.
if [ ! -f "${RCS}" ]; then
    echo "no ${RCS}, rcS fallback hook skipped"
elif [ ! -f "${BG_SCRIPT}" ]; then
    echo "iris_net_fix_bg missing at ${BG_SCRIPT}, rcS fallback hook skipped"
elif grep -q 'iris_net_fix_bg' "${RCS}"; then
    echo "rcS fallback hook already present"
elif [ -f "${INITTAB}" ]; then
    printf '\n/bin/sh %s/init.d/iris_net_fix_bg\n' "${GUEST_ETC}" >> "${RCS}"
    echo "iris_net_fix_bg appended to rcS"
else
    if awk -v entry="/bin/sh ${GUEST_ETC}/init.d/iris_net_fix_bg" '
            { lines[NR] = $0 }
            END {
                last = 0
                for (i = NR; i >= 1; i--) {
                    s = lines[i]
                    sub(/^[ \t]+/, "", s)
                    if (s != "" && s !~ /^#/) { last = i; break }
                }
                for (i = 1; i <= NR; i++) {
                    if (i == last) { print entry }
                    print lines[i]
                }
                if (!NR) { print entry }
            }
        ' "${RCS}" > "${RCS}.iris"; then
        mv "${RCS}.iris" "${RCS}"
        chmod +x "${RCS}"
        echo "iris_net_fix_bg inserted before the final non-comment line of rcS (no inittab to hook)"
    else
        rm -f "${RCS}.iris"
        echo "WARNING: could not insert boot hook into rcS (no inittab); guest may get no network/web fallback"
    fi
fi
