#!/firmadyne/sh
BUSYBOX=/firmadyne/busybox

[ -d /dev ] || mkdir -p /dev
[ -d /root ] || mkdir -p /root
[ -d /sys ] || mkdir -p /sys
[ -d /proc ] || mkdir -p /proc
[ -d /tmp ] || mkdir -p /tmp
mkdir -p /var/lock

${BUSYBOX} mount -t sysfs sysfs /sys 2>/dev/null || true
${BUSYBOX} mount -t proc proc /proc 2>/dev/null || true
${BUSYBOX} ln -sf /proc/mounts /etc/mtab 2>/dev/null || true

mkdir -p /dev/pts
${BUSYBOX} mount -t devpts devpts /dev/pts 2>/dev/null || true
${BUSYBOX} mount -t tmpfs tmpfs /run 2>/dev/null || true