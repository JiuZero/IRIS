#!/firmadyne/sh
BUSYBOX=/firmadyne/busybox

# use busybox for all commands
if [ ! -e /bin/sh ]; then
    ${BUSYBOX} ln -s /firmadyne/busybox /bin/sh 2>/dev/null || true
fi
${BUSYBOX} ln -s /firmadyne/busybox /firmadyne/sh 2>/dev/null || true

# create essential directories
for d in /proc /dev/pts /etc_ro /tmp /var /run /sys /root /tmp/var /tmp/media /tmp/etc /tmp/var/run /tmp/home/root /tmp/mnt /tmp/opt /tmp/www /var/run /var/lock /usr/bin /usr/sbin; do
    mkdir -p "$d" 2>/dev/null || true
done

# make all bin/sbin dirs executable
${BUSYBOX} find / -type d \( -name bin -o -name sbin \) -exec ${BUSYBOX} chmod a+x {} + 2>/dev/null || true

# essential files
mkdir -p /etc
[ ! -s /etc/TZ ] && echo "EST5EDT" > /etc/TZ
[ ! -s /etc/hosts ] && echo "127.0.0.1 localhost" > /etc/hosts
[ ! -s /etc/passwd ] && echo "root::0:0:root:/root:/bin/sh" > /etc/passwd

# device nodes
FILECOUNT="$(${BUSYBOX} find /dev -maxdepth 1 \( -type b -o -type c \) -print 2>/dev/null | ${BUSYBOX} wc -l)"
if [ "${FILECOUNT:-0}" -lt "5" ]; then
    ${BUSYBOX} mknod -m 666 /dev/null c 1 3 2>/dev/null || true
    ${BUSYBOX} mknod -m 666 /dev/zero c 1 5 2>/dev/null || true
    ${BUSYBOX} mknod -m 444 /dev/random c 1 8 2>/dev/null || true
    ${BUSYBOX} mknod -m 444 /dev/urandom c 1 9 2>/dev/null || true
    ${BUSYBOX} mknod -m 666 /dev/tty c 5 0 2>/dev/null || true
    ${BUSYBOX} mknod -m 622 /dev/console c 5 1 2>/dev/null || true
    ${BUSYBOX} mknod -m 666 /dev/ptmx c 5 2 2>/dev/null || true
    ${BUSYBOX} mknod -m 622 /dev/tty0 c 4 0 2>/dev/null || true
    ${BUSYBOX} mknod -m 660 /dev/ttyS0 c 4 64 2>/dev/null || true
    ${BUSYBOX} mknod -m 660 /dev/ttyS1 c 4 65 2>/dev/null || true
    mkdir -p /dev/mtdblock
    for i in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        ${BUSYBOX} mknod -m 644 /dev/mtdblock${i} b 31 ${i} 2>/dev/null || true
        ${BUSYBOX} mknod -m 644 /dev/mtd${i} c 90 $((i*2)) 2>/dev/null || true
    done
    ${BUSYBOX} mknod -m 666 /dev/mem c 1 1 2>/dev/null || true
    ${BUSYBOX} mknod -m 600 /dev/watchdog c 10 130 2>/dev/null || true
fi

# apply L3 rule-engine guest fixes (device nodes, aliases) if present
if [ -x /firmadyne/iris_rules.sh ] || [ -f /firmadyne/iris_rules.sh ]; then
    ${BUSYBOX} sh /firmadyne/iris_rules.sh 2>/dev/null || true
fi

# prevent reboot
rm -f /sbin/reboot 2>/dev/null || true
rm -f /etc/scripts/sys_resetbutton 2>/dev/null || true