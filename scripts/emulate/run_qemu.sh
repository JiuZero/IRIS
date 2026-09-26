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

case "${ARCH}" in
    armel)
        KERNEL="${BINARIES}/zImage.armel"
        QEMU="qemu-system-arm"
        QEMU_MACHINE="virt"
        QEMU_ROOTFS="/dev/vda"
        QEMU_DISK="-drive if=none,file=${IMAGE},format=raw,id=rootfs -device virtio-blk-device,drive=rootfs"
        QEMU_NET="-device virtio-net-device,netdev=net0 -netdev user,id=net0,hostfwd=tcp::${HOST_PORT}-:80"
        ;;
    mipseb)
        KERNEL="${BINARIES}/vmlinux.mipseb.4"
        QEMU="qemu-system-mips"
        QEMU_MACHINE="malta"
        QEMU_ROOTFS="/dev/sda"
        QEMU_DISK="-drive if=ide,format=raw,file=${IMAGE}"
        QEMU_NET="-device e1000,netdev=net0 -netdev user,id=net0,hostfwd=tcp::${HOST_PORT}-:80"
        ;;
    mipsel)
        KERNEL="${BINARIES}/vmlinux.mipsel.4"
        QEMU="qemu-system-mipsel"
        QEMU_MACHINE="malta"
        QEMU_ROOTFS="/dev/sda"
        QEMU_DISK="-drive if=ide,format=raw,file=${IMAGE}"
        QEMU_NET="-device e1000,netdev=net0 -netdev user,id=net0,hostfwd=tcp::${HOST_PORT}-:80,hostfwd=tcp::2222-:22"
        ;;
    *)
        echo "Error: Unsupported architecture: ${ARCH}"
        exit 1
        ;;
esac

APPEND="firmadyne.syscall=1 root=${QEMU_ROOTFS} console=ttyS0 nandsim.parts=64,64,64,64,64,64,64,64,64,64 rw debug ignore_loglevel print-fatal-signals=1 FIRMAE_NET=true FIRMAE_NVRAM=true FIRMAE_KERNEL=true FIRMAE_ETC=true user_debug=31"

echo "Starting QEMU: ${QEMU} ${QEMU_MACHINE} kernel=${KERNEL}"
echo "Disk: ${IMAGE}"
echo "Network: guest:80 -> host:${HOST_PORT}"

${QEMU} -m 256 -M ${QEMU_MACHINE} -kernel ${KERNEL} \
    ${QEMU_DISK} \
    -append "${APPEND}" \
    -serial file:${WORK_DIR}/qemu.serial.log \
    -display none \
    ${QEMU_NET} &

QEMU_PID=$!
echo "QEMU PID: ${QEMU_PID}"
echo ${QEMU_PID} > ${WORK_DIR}/qemu.pid

# Wait for QEMU to exit
wait ${QEMU_PID}
echo "QEMU exited with code $?"