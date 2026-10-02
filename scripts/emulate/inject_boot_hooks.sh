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

# ---------------------------------------------------------------- sysinit hook
if [ ! -f "${BG_SCRIPT}" ]; then
    echo "iris_net_fix_bg missing at ${BG_SCRIPT}, sysinit hook skipped"
elif [ ! -f "${INITTAB}" ]; then
    echo "no ${INITTAB}, sysinit hook skipped"
else
    # Strip any previous injection first so re-running the build (same rootfs,
    # new image) is idempotent instead of stacking duplicates.
    CLEAN="$(mktemp "${IMAGE_DIR}/inittab.XXXXXX")"
    grep -v 'IRIS-NETFIX-SYSINIT\|iris_net_fix_bg' "${INITTAB}" > "${CLEAN}" || true

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
# covers those firmwares.
if [ ! -f "${RCS}" ]; then
    echo "no ${RCS}, rcS fallback hook skipped"
elif [ ! -f "${BG_SCRIPT}" ]; then
    echo "iris_net_fix_bg missing at ${BG_SCRIPT}, rcS fallback hook skipped"
elif grep -q 'iris_net_fix_bg' "${RCS}"; then
    echo "rcS fallback hook already present"
else
    printf '\n/bin/sh %s/init.d/iris_net_fix_bg\n' "${GUEST_ETC}" >> "${RCS}"
    echo "iris_net_fix_bg appended to rcS"
fi