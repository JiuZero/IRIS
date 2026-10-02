#!/bin/sh
# Guest boot-hook injection, extracted from make_image.sh so it can be exercised
# directly against a synthetic /etc instead of only through a full image build.
#
# Usage: inject_boot_hooks.sh <image-dir> [--script-source <dir>]
#
# Two hooks are installed:
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
set -e

IMAGE_DIR=${1:?usage: inject_boot_hooks.sh <image-dir>}
SCRIPT_DIR=${2:-/work/scripts}

BG_SCRIPT="${IMAGE_DIR}/etc/init.d/iris_net_fix_bg"
INITTAB="${IMAGE_DIR}/etc/inittab"
RCS="${IMAGE_DIR}/etc/init.d/rcS"
MARKER='#IRIS-NETFIX-SYSINIT'
ENTRY='::sysinit:/etc/init.d/iris_net_fix_bg'

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