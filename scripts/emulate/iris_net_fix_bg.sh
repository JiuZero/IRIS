#!/bin/sh
# Background launcher for iris_net_fix, referenced from inittab's ::sysinit:.
#
# BusyBox init execs an inittab process field verbatim — it does not interpret
# shell metacharacters — so appending `&` there passes it as a literal argument
# and runs the fixup in the foreground, blocking every later ::sysinit: step and
# the vendor rcS chain behind a 45-second sleep. Doing the backgrounding in a
# script is the only way to get "start now, do not block" out of an inittab entry.
#
# Output is not discarded: iris_net_fix writes to /dev/console itself, which is
# what lands in qemu.serial.log. stdout is sent to the console too so an
# unexpected shell-level error is still visible.
#
# The sibling is resolved from $0, never from a hardcoded /etc/init.d: this
# launcher is installed into whichever tree the firmware's own inittab names, and
# on a firmware with a read-only /etc_ro layered under an empty /etc that
# directory does not exist yet when the sysinit hook runs — it is the vendor rcS
# that populates it, one step later. Naming the tree that is actually here is the
# difference between the hook working and a silent "can't open".
#
# Stripped with parameter expansion rather than dirname(1), which this busybox
# does not carry: the command fails, $0's directory silently collapses to the
# empty string, and the fixup is then looked up at /iris_net_fix.

DIR=${0%/*}
[ "${DIR}" = "${0}" ] && DIR="."
[ -n "${DIR}" ] || DIR="/"

/bin/sh "${DIR}/iris_net_fix" >> "${IRIS_CONSOLE:-/dev/console}" 2>&1 &