#!/bin/bash
set -e

IID=$1
ARCH=$2
HOST_PORT=${3:-8080}
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

# Setup TAP networking for guest-to-host connectivity
TAP_IFACE="tap${IID}"
BR_IFACE="br${IID}"
GUEST_IP="192.168.1.1"
HOST_IP="192.168.1.254"

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
ip addr add "${HOST_IP}/24" dev "${BR_IFACE}"
ip link set "${BR_IFACE}" up
ip link set "${TAP_IFACE}" up

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

APPEND="firmadyne.syscall=1 root=${QEMU_ROOTFS} console=ttyS0 nandsim.parts=64,64,64,64,64,64,64,64,64,64 rw debug ignore_loglevel print-fatal-signals=1 FIRMAE_NET=true FIRMAE_NVRAM=true FIRMAE_KERNEL=true FIRMAE_ETC=true user_debug=31"

echo "Starting QEMU: ${QEMU} ${QEMU_MACHINE} kernel=${KERNEL}"
echo "Disk: ${IMAGE}"
echo "Network: TAP ${TAP_IFACE} -> bridge ${BR_IFACE} (${HOST_IP}/24)"

${QEMU} -m 256 -M ${QEMU_MACHINE} -kernel ${KERNEL} \
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

# Wait for QEMU to exit
wait ${QEMU_PID}
QEMU_EXIT=$?
echo "QEMU exited with code ${QEMU_EXIT}"

# Cleanup
kill ${SOCAT_PID} 2>/dev/null || true
ip link set "${TAP_IFACE}" down 2>/dev/null || true
ip link set "${BR_IFACE}" down 2>/dev/null || true
brctl delif "${BR_IFACE}" "${TAP_IFACE}" 2>/dev/null || true
tunctl -d "${TAP_IFACE}" 2>/dev/null || true
ip link delete "${BR_IFACE}" type bridge 2>/dev/null || true