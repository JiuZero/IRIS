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

/bin/sh /etc/init.d/iris_net_fix >> /dev/console 2>&1 &