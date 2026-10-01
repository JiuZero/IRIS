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

# Start fallback network configuration in background
# Waits for firmware boot, then ensures the LAN IP is reachable
(
    ${BUSYBOX} sleep 20

    # Ensure eth0 is up
    ${BUSYBOX} ifconfig eth0 up 2>/dev/null || true

    # Check if eth0 already has an IP
    ETH_IP=$(${BUSYBOX} ifconfig eth0 2>/dev/null | ${BUSYBOX} grep "inet addr" | ${BUSYBOX} awk -F: '{print $2}' | ${BUSYBOX} awk '{print $1}')
    if [ -z "${ETH_IP}" ]; then
        ${BUSYBOX} ifconfig eth0 192.168.1.1 netmask 255.255.255.0 up 2>/dev/null || true
    fi

    # Also check br-lan
    BR_IP=$(${BUSYBOX} ifconfig br-lan 2>/dev/null | ${BUSYBOX} grep "inet addr" | ${BUSYBOX} awk -F: '{print $2}' | ${BUSYBOX} awk '{print $1}')
    if [ -z "${BR_IP}" ]; then
        # Try to create br-lan and add eth0
        ${BUSYBOX} brctl addbr br-lan 2>/dev/null || true
        ${BUSYBOX} brctl addif br-lan eth0 2>/dev/null || true
        ${BUSYBOX} ifconfig br-lan 192.168.1.1 netmask 255.255.255.0 up 2>/dev/null || true
    fi
) &
