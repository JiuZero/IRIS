from iris.extract.rootfs import find_rootfs


def test_rootfs_at_depth0():
    paths = [
        "bin/busybox",
        "bin/sh",
        "etc/init.d/rcS",
        "etc/passwd",
        "lib/ld-uClibc.so.0",
        "sbin/init",
        "usr/bin/httpd",
        "var/log/messages",
    ]
    cand = find_rootfs(paths)
    assert cand is not None
    assert cand.prefix == ""
    assert cand.is_rootfs
    assert cand.has_busybox
    assert cand.has_initd
    assert cand.score >= 8


def test_rootfs_nested_one_level():
    paths = [
        "squashfs-lzma.bin/bin/busybox",
        "squashfs-lzma.bin/etc/passwd",
        "squashfs-lzma.bin/lib/libc.so.0",
        "squashfs-lzma.bin/sbin/init",
        "squashfs-lzma.bin/usr/www/index.asp",
        "kernel.bin",
    ]
    cand = find_rootfs(paths)
    assert cand is not None
    assert cand.prefix == "squashfs-lzma.bin"
    assert cand.is_rootfs
    assert cand.has_busybox


def test_no_rootfs():
    cand = find_rootfs(["firmware.bin", "readme.txt", "changelog"])
    assert cand is None


def test_below_threshold():
    paths = ["bin/tool", "etc/config", "usr/bin/app"]
    cand = find_rootfs(paths)
    assert cand is not None
    assert not cand.is_rootfs


def test_deep_nested_ignored_beyond_depth2():
    paths = ["a/b/c/d/bin/busybox", "a/b/c/d/etc/passwd"]
    cand = find_rootfs(paths)
    assert cand is None or not cand.is_rootfs

def test_generator_input_scanned_fully():
    def gen():
        yield from [
            "tc-rootfs/bin/busybox",
            "tc-rootfs/etc/init.d/rcS",
            "tc-rootfs/etc/passwd",
            "tc-rootfs/lib/ld.so",
            "tc-rootfs/sbin/init",
            "tc-rootfs/usr/bin/httpd",
            "tc-rootfs/var/log/messages",
            "tc-rootfs/bin/sh",
        ]

    cand = find_rootfs(gen())
    assert cand is not None
    assert cand.prefix == "tc-rootfs"
    assert cand.has_busybox
    assert cand.has_initd


def test_prefix_membership_is_boundary_safe():
    paths = [
        "fw/bin/busybox",
        "fw/bin/sh",
        "fw/etc/passwd",
        "fw/lib/ld.so",
        "fw/sbin/init",
        "fw/usr/x",
        "fw/var/y",
        "fw2/etc/init.d/rcS",
    ]
    cand = find_rootfs(paths)
    assert cand is not None
    assert cand.prefix == "fw"
    assert cand.has_busybox
    assert not cand.has_initd  # fw2/etc must not count as a member of prefix "fw"
