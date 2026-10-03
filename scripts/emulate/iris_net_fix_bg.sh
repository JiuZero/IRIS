#!/bin/sh
# Background launcher for iris_net_fix, referenced from inittab's ::sysinit:.
#
# BusyBox init execs an inittab process field verbatim -- it does not interpret
# shell metacharacters -- so appending a job-control ampersand there passes it as a
# literal argument and runs the fixup in the foreground, blocking every later
# ::sysinit: step and the vendor rcS chain behind a 45-second sleep. Doing the
# backgrounding in a script is the only way to get "start now, do not block" out of
# an inittab entry.
#
# Output is not discarded: iris_net_fix writes to the console itself, which is what
# lands in qemu.serial.log. stdout goes there too so an unexpected shell-level error
# is still visible.
#
# The sibling is named by the install path, not by parsing the invocation path at
# runtime. Both shell-side ways of deriving it are unavailable on real guests: this
# busybox carries no dirname applet, and the Tenda DIR-868L's BusyBox expands the
# last-path-component form of variable expansion to the empty string (measured with
# a three-component path and an echo of that expansion, which printed an empty pair
# of brackets). With the invocation path unusable, the directory collapsed to empty,
# the fixup was looked up at /iris_net_fix, and the launcher exited 0 having run
# nothing at all: the guest logged zero IRIS-NETFIX lines, which reads exactly like
# "the boot hook never ran" and sent the search to the hook installation instead.
#
# So inject_boot_hooks.sh substitutes the install tree when it installs this file,
# which is a plain sed on the build host -- where a full shell is available. That
# also handles the read-only /etc_ro layered under an empty /etc on Tenda AC15: the
# vendor rcS populates /etc one step later, so a runtime path would name the wrong
# tree.
#
# No redirection on the fixup itself. iris_net_fix's log() opens the console with a
# truncating redirect and says why it cannot append: opening a console device with
# O_APPEND fails outright. Redirecting the same device here with the appending form
# hits that same failure, and a redirection that cannot be opened takes its command
# down with it -- so the background fixup never starts and the guest silently gets
# no fallback at all. Nothing is lost by dropping it: stdout and stderr stay on the
# console the launcher already inherits, which is where an unexpected shell-level
# error belongs.
#
# The one line at the bottom is the launcher's own mark. Without it, "the hook never
# ran" and "the fixup never started" are the same silent log, and they need different
# fixes; the mark is printed before the fixup is even spawned, so its presence says
# which of the two happened. It goes to the inherited stdout for the same reason the
# redirection was dropped: on DIR-868L opening the console device fails, while the
# descriptor init already handed this script is writable.
#
# NOTE ON COMMENTING: keep shell metacharacters out of the comments above. This
# BusyBox is a reduced vendor build and it does not confine expansions to code -- a
# comment quoting a parameter expansion or a backquoted command ended this script
# before its first statement, silently, with a success status, while every line of it
# was correct on disk. The launcher measured zero output that way.

DIR=@IRIS_GUEST_INIT_D@
[ -n "${DIR}" ] || DIR="."

echo "IRIS-NETFIX: bg launcher starting ${DIR}/iris_net_fix"
/bin/sh "${DIR}/iris_net_fix" &
