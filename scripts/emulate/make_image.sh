#!/bin/bash
set -e

IID=$1
ARCH=$2
WORK_DIR=/work/scratch/${IID}
IMAGE=${WORK_DIR}/image.raw
IMAGE_DIR=${WORK_DIR}/image
TARBALL=/work/scratch/${IID}/${IID}.tar.gz
BINARIES=/work/binaries

# Record the launch arch next to the image. A guardian that restarts the
# container has no other way to learn it: the container's entrypoint is
# `sleep 3600`, so nothing inside the container records what QEMU was
# started with. Without this marker a WEB_SERVER_RESTART can only bring the
# container back, never the emulation.
mkdir -p "${WORK_DIR}"
printf '%s\n' "${ARCH}" > "${WORK_DIR}/arch"

# Use /tmp for loop mount operations (overlay2 doesn't support loop devices)
TMP_BUILD=/tmp/build-${IID}
rm -rf "${TMP_BUILD}"
mkdir -p "${TMP_BUILD}"
TMP_IMAGE=${TMP_BUILD}/image.raw
TMP_IMAGE_DIR=${TMP_BUILD}/image

echo "----Creating QEMU Image (1G raw)----"
FS=ext2
[ "${ARCH}" = "arm64" ] && FS=ext4   # Alpine arm64 generic kernel ships no ext2 driver
qemu-img create -f raw "${TMP_IMAGE}" 1G
chmod a+rw "${TMP_IMAGE}"

echo "----Creating ${FS} filesystem directly----"
mkfs.${FS} -F "${TMP_IMAGE}"

echo "----Mounting image----"
mkdir -p "${TMP_IMAGE_DIR}"
mount -o loop "${TMP_IMAGE}" "${TMP_IMAGE_DIR}"

echo "----Extracting Filesystem Tarball----"
tar -xf "${TARBALL}" -C "${TMP_IMAGE_DIR}"
echo "Extracted $(find ${TMP_IMAGE_DIR} -type f | wc -l) files"

echo "----Creating firmadyne Directories----"
mkdir -p "${TMP_IMAGE_DIR}/firmadyne/libnvram"
mkdir -p "${TMP_IMAGE_DIR}/firmadyne/libnvram.override"

cp /bin/busybox "${TMP_IMAGE_DIR}/firmadyne/busybox" 2>/dev/null || cp /usr/bin/busybox "${TMP_IMAGE_DIR}/firmadyne/busybox"

echo "----Patching Filesystem----"
cp /work/scripts/fix_image.sh "${TMP_IMAGE_DIR}/fix_image.sh"
chmod +x "${TMP_IMAGE_DIR}/fix_image.sh"
chroot "${TMP_IMAGE_DIR}" /firmadyne/busybox ash /fix_image.sh || true
rm "${TMP_IMAGE_DIR}/fix_image.sh"

echo "----Injecting Binaries----"
for f in busybox console libnvram.so libnvram_ioctl.so; do
    SRC="${BINARIES}/${f}.${ARCH}"
    if [ -e "${SRC}" ]; then
        cp "${SRC}" "${TMP_IMAGE_DIR}/firmadyne/${f}"
        chmod a+x "${TMP_IMAGE_DIR}/firmadyne/${f}"
    fi
done

mknod -m 666 "${TMP_IMAGE_DIR}/firmadyne/ttyS1" c 4 65 2>/dev/null || true

cp /work/scripts/pre_init.sh "${TMP_IMAGE_DIR}/firmadyne/preInit.sh"
chmod +x "${TMP_IMAGE_DIR}/firmadyne/preInit.sh"

cp /work/scripts/network.sh "${TMP_IMAGE_DIR}/firmadyne/network.sh"
chmod +x "${TMP_IMAGE_DIR}/firmadyne/network.sh"

touch "${TMP_IMAGE_DIR}/firmadyne/debug.sh"
chmod +x "${TMP_IMAGE_DIR}/firmadyne/debug.sh"

echo "----Injecting IRIS Network Fix----"
# /etc is not always /etc: Tenda AC15 points it at a writable overlay (`etc ->
# /var/etc`, empty on disk) and keeps the real tree in /etc_ro, which its inittab
# references directly. Probe for the one that actually holds init.d, or the
# scripts land in a tree that `cp -rf /etc_ro/* /etc/` overwrites at boot and the
# fallback never runs.
IRIS_ETC="${TMP_IMAGE_DIR}/etc"
if [ ! -d "${IRIS_ETC}/init.d" ] && [ -d "${TMP_IMAGE_DIR}/etc_ro/init.d" ]; then
    IRIS_ETC="${TMP_IMAGE_DIR}/etc_ro"
fi
if [ -d "${IRIS_ETC}/init.d" ]; then
    echo "init.d found under ${IRIS_ETC}"
    cp /work/scripts/iris_net_fix.sh "${IRIS_ETC}/init.d/iris_net_fix"
    chmod +x "${IRIS_ETC}/init.d/iris_net_fix"
    cp /work/scripts/iris_net_fix_bg.sh "${IRIS_ETC}/init.d/iris_net_fix_bg"
    chmod +x "${IRIS_ETC}/init.d/iris_net_fix_bg"
    mkdir -p "${IRIS_ETC}/rc.d"
    ln -sf "../init.d/iris_net_fix" "${IRIS_ETC}/rc.d/S99iris_net_fix"
else
    echo "WARNING: no init.d under /etc or /etc_ro; guest will get no network/web fallback"
fi

# Hooks the guest boot. Architecture-independent on purpose: the sysinit hook, the
# rcS tracing and the rcS tail hook all key off whichever tree the firmware's own
# inittab names, and the reasoning behind their ordering lives in
# inject_boot_hooks.sh. Leaving this inside the arm64 block is what starved every
# other architecture of the fallback.
if ! /work/scripts/inject_boot_hooks.sh "${TMP_IMAGE_DIR}" /work/scripts; then
    echo "WARNING: boot hook injection failed; the guest may get no network/web fallback"
fi

echo "----Arm64 Generic-Kernel Channel----"
if [ "${ARCH}" = "arm64" ]; then
    # Alpine busybox: the x86_64 one copied above cannot run inside an aarch64 guest
    [ -e "${BINARIES}/busybox.arm64" ] && cp "${BINARIES}/busybox.arm64" "${TMP_IMAGE_DIR}/firmadyne/busybox.arm64"

    # NTFS/dev-mode-less hosts silently drop symlinks when the rootfs is staged
    # on the Windows side; vendor /sbin -> /bin is what makes /sbin/init exist.
    # The target must be RELATIVE: an absolute /bin resolves against the
    # initramfs, so switch_root would not find /root/sbin/init.
    if [ ! -e "${TMP_IMAGE_DIR}/sbin" ] && [ -d "${TMP_IMAGE_DIR}/bin" ]; then
        ln -sfn bin "${TMP_IMAGE_DIR}/sbin"
    fi

    # Interactive shell needs a ttyAMA0 node: switch_root discards the initramfs
    # devtmpfs and the vendor squashfs ships a static /dev without pl011 entries.
    if [ -d "${TMP_IMAGE_DIR}/dev" ] && [ ! -e "${TMP_IMAGE_DIR}/dev/ttyAMA0" ]; then
        mknod -m 620 "${TMP_IMAGE_DIR}/dev/ttyAMA0" c 204 0 || true
    fi

    # interactive shell on the pl011 console (console=ttyAMA0)
    if [ -f "${TMP_IMAGE_DIR}/etc/inittab" ] && ! grep -q ttyAMA0 "${TMP_IMAGE_DIR}/etc/inittab"; then
        printf '\nttyAMA0::respawn:-/bin/sh\n' >> "${TMP_IMAGE_DIR}/etc/inittab"
    fi
fi

echo "----Finding Init----"
cp /work/scripts/infer_init.sh "${TMP_IMAGE_DIR}/infer_init.sh"
chmod +x "${TMP_IMAGE_DIR}/infer_init.sh"
chroot "${TMP_IMAGE_DIR}" /firmadyne/busybox ash /infer_init.sh || true
rm "${TMP_IMAGE_DIR}/infer_init.sh"

if [ -e "${TMP_IMAGE_DIR}/firmadyne/init" ]; then
    cp "${TMP_IMAGE_DIR}/firmadyne/init" "${WORK_DIR}/init"
    echo "Init: $(cat ${WORK_DIR}/init)"
fi


echo "----Unmounting and copying to output----"
sync
umount "${TMP_IMAGE_DIR}"
e2fsck -y "${TMP_IMAGE}" || true
sync

# Copy the final image to the output directory
cp "${TMP_IMAGE}" "${IMAGE}"
# A state disk from an earlier bake describes a different filesystem, and run_qemu.sh
# only creates one when it is missing. Leaving it here would boot the new image with
# the old one's writes still in it.
rm -f "${WORK_DIR}/state.raw"
# Same reasoning for the guest address a previous boot measured: it described the
# network of the image being replaced, and run_qemu.sh would keep deriving the bridge
# and the port forward from it.
rm -f "${WORK_DIR}/guest_ip"
rm -rf "${TMP_BUILD}"

echo "==== Image built: ${IMAGE} ===="
