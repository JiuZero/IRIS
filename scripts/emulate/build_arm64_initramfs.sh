#!/bin/bash
# Build binaries/initramfs.arm64 from staged sources.
# Run inside any Linux container with cpio+gzip, with the project root mounted:
#   docker run --rm -v <project-root>:/w ubuntu:22.04 bash /w/scripts/emulate/build_arm64_initramfs.sh
set -e

ROOT=${1:-/w}
SRC=$ROOT/scripts/emulate/arm64-initrd
OUT=$ROOT/binaries/initramfs.arm64

[ -f "$SRC/busybox" ] || { echo "missing $SRC/busybox (copy of binaries/busybox.arm64)"; exit 1; }
[ -f "$SRC/ld-musl-aarch64.so.1" ] || { echo "missing $SRC/ld-musl-aarch64.so.1 (Alpine aarch64 musl loader)"; exit 1; }

W=$(mktemp -d)
cd "$W"
mkdir -p bin dev lib modules proc root sys
cp "$SRC/busybox" bin/busybox
for a in sh mount insmod sleep setsid switch_root mkdir echo cat ln rm ls grep sed cut stat readlink nproc env dmesg cp sync chroot umount; do
    ln -sf busybox "bin/$a"
done
cp "$SRC/ld-musl-aarch64.so.1" lib/ld-musl-aarch64.so.1
cp "$SRC"/modules/*.ko modules/ 2>/dev/null || true
chmod 755 bin/busybox lib/ld-musl-aarch64.so.1 modules/*.ko 2>/dev/null || true

cat > init <<'EOF'
#!/bin/sh
mount -t devtmpfs devtmpfs /dev
mount -t proc proc /proc
mount -t sysfs sysfs /sys
# load order honours module dependencies (ext4<-crc16; libcrc32c needs the
# crypto API, so crc32c_generic must be inserted first or insmod fails)
for m in crc32c_generic mbcache crc16 libcrc32c jbd2 ext4 virtio_mmio virtio_blk failover net_failover virtio_net; do
    [ -e /modules/$m.ko ] && insmod /modules/$m.ko
done
i=0
while [ ! -e /dev/vda ] && [ $i -lt 200 ]; do sleep 0.1; i=$((i+1)); done
mkdir -p /root
mount -t ext4 -o rw /dev/vda /root || mount -t ext4 -o ro /dev/vda /root
if [ $? -ne 0 ]; then
    echo "IRIS-INITRD: cannot mount /dev/vda, dropping to debug shell" > /dev/console
    exec setsid /bin/sh </dev/console >/dev/console 2>&1
fi
# Vendor squashfs ships a static /dev with pre-2.6 node numbers (legacy pty
# 3:*, 5:64 ttyMX, and /dev/null that this kernel no longer backs). A write to
# such a /dev/null fails with ENXIO, which is what kills guest startup.
# Overwrite the standard nodes with the kernel's own devtmpfs versions.
for e in console null zero tty random urandom ptmx ttyAMA0; do
    if [ -e "/dev/$e" ]; then
        rm -f "/root/dev/$e" 2>/dev/null
        cp -a "/dev/$e" "/root/dev/" 2>/dev/null
    fi
done
# switch_root only returns on failure (e.g. an absolute /sbin -> /bin symlink
# inside the image resolves against the initramfs, so /root/sbin/init is gone).
# Fall back to mounting the API filesystems into the new root and chrooting.
switch_root -c /dev/console /root /sbin/init
echo "IRIS-INITRD: switch_root failed, falling back to chroot init" > /dev/console
mount --move /dev /root/dev 2>/dev/null || cp -a /dev/. /root/dev/ 2>/dev/null
mount --move /proc /root/proc 2>/dev/null
mount --move /sys /root/sys 2>/dev/null
exec chroot /root /sbin/init </dev/console >/dev/console 2>&1
echo "IRIS-INITRD: no usable init, dropping to debug shell on ttyAMA0" > /dev/console
while true; do
    setsid /bin/sh </dev/ttyAMA0 >/dev/ttyAMA0 2>&1
    sleep 1
done
EOF
chmod 755 init

find . | cpio -R 0:0 -o -H newc 2>/dev/null | gzip -9 > "$OUT"
cd /
rm -rf "$W"
echo "built $OUT ($(stat -c %s "$OUT") bytes)"
