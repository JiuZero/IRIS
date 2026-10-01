#!/firmadyne/sh
BUSYBOX=/firmadyne/busybox

# Network configuration for emulated firmware
# This script runs inside QEMU to configure network interfaces

if [ ! -e /firmadyne/network_type ]; then
    exit 0
fi

NETWORK_TYPE=$(${BUSYBOX} cat /firmadyne/network_type)
NET_INTERFACE=$(${BUSYBOX} cat /firmadyne/net_interface 2>/dev/null || echo "eth0")

case "${NETWORK_TYPE}" in
    bridge)
        ${BUSYBOX} brctl addbr br0 2>/dev/null || true
        ${BUSYBOX} brctl addif br0 ${NET_INTERFACE} 2>/dev/null || true
        ${BUSYBOX} ifconfig br0 up 2>/dev/null || true
        ;;
    *)
        ${BUSYBOX} ifconfig ${NET_INTERFACE} up 2>/dev/null || true
        ;;
esac
