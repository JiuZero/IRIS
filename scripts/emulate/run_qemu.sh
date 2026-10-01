#!/bin/bash
set -e

IID=$1
ARCH=$2
HOST_PORT=${3:-8080}
GUEST_IP=${4:-192.168.1.1}
WORK_DIR=/work/scratch/${IID}
IMAGE=${WORK_DIR}/image.raw
BINARIES=/work/binaries

# Copy image to /tmp for QEMU (overlay2 can have issues with large files)
TMP_IMAGE=/tmp/qemu-${IID}.raw
cp "${IMAGE}" "${TMP_IMAGE}"
IMAGE="${TMP_IMAGE}"

KERNEL=""
QEMU=""
QEMU_MACHINE=""
QEMU_ROOTFS=""
QEMU_DISK=""
QEMU_NET=""
QEMU_CPU=""
QEMU_INITRD=""
CONSOLE="ttyS0"
MEMORY=256

# Setup TAP networking for guest-to-host connectivity
TAP_IFACE="tap${IID}"
BR_IFACE="br${IID}"
GUEST_IP="${GUEST_IP}"
HOST_IP=$(echo "${GUEST_IP}" | awk -F. '{print $1"."$2"."$3"."$4-1}')
# If last octet is 0 or 1, use .254 as host IP
LAST_OCTET=$(echo "${GUEST_IP}" | awk -F. '{print $4}')
if [ "${LAST_OCTET}" -le 1 ]; then
    HOST_IP=$(echo "${GUEST_IP}" | awk -F. '{print $1"."$2"."$3".254"}')
fi
NET_PREFIX=$(echo "${GUEST_IP}" | awk -F. '{print $1"."$2"."$3}')

# Clean up any existing interfaces
ip link set "${TAP_IFACE}" down 2>/dev/null || true
ip link set "${BR_IFACE}" down 2>/dev/null || true
brctl delif "${BR_IFACE}" "${TAP_IFACE}" 2>/dev/null || true
tunctl -d "${TAP_IFACE}" 2>/dev/null || true
ip link delete "${BR_IFACE}" type bridge 2>/dev/null || true

# Create TAP device and bridge
tunctl -t "${TAP_IFACE}" -u root
brctl addbr "${BR_IFACE}"
brctl addif "${BR_IFACE}" "${TAP_IFACE}"
ip addr add "${HOST_IP}/16" dev "${BR_IFACE}"
ip link set "${BR_IFACE}" up
ip link set "${TAP_IFACE}" up

# Create VLAN 1 interface on TAP for firmware that uses eth0.1
VLAN_IFACE="${TAP_IFACE}.1"
ip link add link "${TAP_IFACE}" name "${VLAN_IFACE}" type vlan id 1 2>/dev/null || true
ip link set "${VLAN_IFACE}" up 2>/dev/null || true
brctl addif "${BR_IFACE}" "${VLAN_IFACE}" 2>/dev/null || true

echo "Network: bridge=${BR_IFACE} tap=${TAP_IFACE} host=${HOST_IP} guest=${GUEST_IP}"

case "${ARCH}" in
    armel)
        KERNEL="${BINARIES}/zImage.armel"
        QEMU="qemu-system-arm"
        QEMU_MACHINE="virt"
        QEMU_ROOTFS="/dev/vda"
        QEMU_DISK="-drive if=none,file=${IMAGE},format=raw,id=rootfs -device virtio-blk-device,drive=rootfs"
        QEMU_NET="-device virtio-net-device,netdev=net0 -netdev tap,id=net0,ifname=${TAP_IFACE},script=no,downscript=no"
        ;;
    arm64)
        KERNEL="${BINARIES}/Image.arm64"
        QEMU="qemu-system-aarch64"
        QEMU_MACHINE="virt"
        # "max" (not cortex-a72): vendor aarch64 binaries use ARMv8.3 pointer-auth,
        # which cortex-a72 TCG cannot execute (SIGILL).
        QEMU_CPU="-cpu max"
        QEMU_ROOTFS="/dev/vda"
        QEMU_DISK="-drive if=none,file=${IMAGE},format=raw,id=rootfs -device virtio-blk-device,drive=rootfs"
        QEMU_NET="-device virtio-net-device,netdev=net0 -netdev tap,id=net0,ifname=${TAP_IFACE},script=no,downscript=no"
        CONSOLE="ttyAMA0"
        MEMORY=512
        QEMU_INITRD="-initrd ${BINARIES}/initramfs.arm64"
        ;;
    mipseb)
        KERNEL="${BINARIES}/vmlinux.mipseb.4"
        QEMU="qemu-system-mips"
        QEMU_MACHINE="malta"
        QEMU_ROOTFS="/dev/sda"
        QEMU_DISK="-drive if=ide,format=raw,file=${IMAGE}"
        QEMU_NET="-device e1000,netdev=net0 -netdev tap,id=net0,ifname=${TAP_IFACE},script=no,downscript=no"
        ;;
    mipsel)
        KERNEL="${BINARIES}/vmlinux.mipsel.4"
        QEMU="qemu-system-mipsel"
        QEMU_MACHINE="malta"
        QEMU_ROOTFS="/dev/sda"
        QEMU_DISK="-drive if=ide,format=raw,file=${IMAGE}"
        QEMU_NET="-device e1000,netdev=net0 -netdev tap,id=net0,ifname=${TAP_IFACE},script=no,downscript=no"
        ;;
    *)
        echo "Error: Unsupported architecture: ${ARCH}"
        exit 1
        ;;
esac

APPEND="firmadyne.syscall=1 root=${QEMU_ROOTFS} console=${CONSOLE} nandsim.parts=64,64,64,64,64,64,64,64,64,64 rw debug ignore_loglevel print-fatal-signals=1 FIRMAE_NET=true FIRMAE_NVRAM=true FIRMAE_KERNEL=true FIRMAE_ETC=true user_debug=31"

echo "Starting QEMU: ${QEMU} ${QEMU_MACHINE} kernel=${KERNEL}"
echo "Disk: ${IMAGE}"
echo "Network: TAP ${TAP_IFACE} -> bridge ${BR_IFACE} (${HOST_IP}/24)"

${QEMU} -m ${MEMORY} -M ${QEMU_MACHINE} ${QEMU_CPU} -kernel ${KERNEL} ${QEMU_INITRD} \
    ${QEMU_DISK} \
    -append "${APPEND}" \
    -serial file:${WORK_DIR}/qemu.serial.log \
    -display none \
    ${QEMU_NET} &

QEMU_PID=$!
echo "QEMU PID: ${QEMU_PID}"
echo ${QEMU_PID} > ${WORK_DIR}/qemu.pid

# Start socat to forward host port to guest
socat TCP-LISTEN:${HOST_PORT},reuseaddr,fork TCP:${GUEST_IP}:80 &
SOCAT_PID=$!
echo "socat PID: ${SOCAT_PID} (forwarding :${HOST_PORT} -> ${GUEST_IP}:80)"

# Wait for QEMU to exit (never let set -e skip the cleanup below)
QEMU_EXIT=0
wait ${QEMU_PID} || QEMU_EXIT=$?
echo "QEMU exited with code ${QEMU_EXIT}"

# Cleanup
kill ${SOCAT_PID} 2>/dev/null || true
rm -f "${TMP_IMAGE}"
ip link set "${TAP_IFACE}" down 2>/dev/null || true
ip link set "${BR_IFACE}" down 2>/dev/null || true
brctl delif "${BR_IFACE}" "${TAP_IFACE}" 2>/dev/null || true
tunctl -d "${TAP_IFACE}" 2>/dev/null || true
ip link delete "${BR_IFACE}" type bridge 2>/dev/null || true
