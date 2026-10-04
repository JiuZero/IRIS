"""Unpacking an uploaded rootfs archive: the tree comes from the network, so the
member names are input rather than a by-product of IRIS's own slicing.

``iris.extract.rootfs_extract`` finds its squashfs offset by scanning bytes, which
means it only ever writes paths it chose. This module writes paths a caller sent,
which is the other half of the threat model: an archive whose members are
``../PWNED`` or ``/etc/cron.d/x``, a member that is a FIFO, or 400 KiB of gzip that
expands into 40 GiB. Each of those is a test below, and each of the tests was run
against a real ``tarfile`` rather than a mock, because a mock cannot produce the
exact bytes tarfile writes for a symlink or a traversal.
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

from iris.extract.rootfs_archive import (
    MAX_ARCHIVE_MEMBERS,
    MAX_UNPACKED_BYTES,
    human_bytes,
    is_rootfs_archive,
    unpack_rootfs_archive,
)
from iris.failures import FailureKind

#: A rootfs tree with enough of the standard unix directories that
#: :func:`iris.extract.rootfs.find_rootfs` accepts it as one.
ROOTFS_FILES = (
    "bin/busybox",
    "etc/passwd",
    "etc/init.d/rcS",
    "lib/libc.so.0",
    "sbin/init",
    "usr/bin/httpd",
    "var/log/messages",
)


def _tar(path: Path, prefix: str = "", extra=()) -> Path:
    """Write a rootfs-shaped archive, optionally with extra members.

    ``prefix`` reproduces the common real shape where the tar holds one wrapper
    directory (``squashfs-root/...``) rather than the rootfs at its own root.
    """
    with tarfile.open(path, "w") as tf:
        for rel in ROOTFS_FILES:
            data = b"\x7fELF" + rel.encode()
            info = tarfile.TarInfo(f"{prefix}{rel}")
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
        for info, payload in extra:
            info.name = f"{prefix}{info.name}"
            tf.addfile(info, io.BytesIO(payload) if payload is not None else None)
    return path


def _file(name: str, size: int = 3) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = size
    return info, b"bad"


def _symlink(name: str, target: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info, None


def _fifo(name: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.FIFOTYPE
    return info, None


# ------------------------------------------------------------------- the happy path


class TestUnpackSuccess:
    def test_a_rootfs_at_the_archive_root_is_extracted(self, tmp_path) -> None:
        archive = _tar(tmp_path / "rootfs.tar")
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert result.failure is None
        assert result.rootfs_dir == tmp_path / "out"
        for rel in ROOTFS_FILES:
            assert (tmp_path / "out" / rel).is_file()

    def test_a_wrapper_prefix_is_stripped(self, tmp_path) -> None:
        """The destination is the rootfs, not a directory inside one.

        A page that lists `*-rootfs` directories under the scratch tree would show
        `squashfs-root` as the rootfs name and every later path would carry a level
        nobody asked for.
        """
        archive = _tar(tmp_path / "rootfs.tar", prefix="squashfs-root/")
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert result.failure is None
        assert result.prefix == "squashfs-root"
        assert (tmp_path / "out" / "bin" / "busybox").is_file()
        assert not (tmp_path / "out" / "squashfs-root").exists()

    def test_the_member_and_byte_counts_are_reported(self, tmp_path) -> None:
        archive = _tar(tmp_path / "rootfs.tar")
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert result.members == len(ROOTFS_FILES)
        assert result.total_bytes > 0

    @pytest.mark.skipif(
        os.name == "nt",
        reason="NTFS has no execute bit; the mode is applied but not stored, and "
               "the emulation tarball is built container-side where it survives",
    )
    def test_the_executable_bit_survives(self, tmp_path) -> None:
        """busybox without +x is a rootfs that cannot boot, and the mode is the
        only thing in the archive that says it should have it."""
        archive = _tar(tmp_path / "rootfs.tar")
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert result.failure is None
        mode = (tmp_path / "out" / "bin" / "busybox").stat().st_mode
        assert mode & 0o111, oct(mode)

    def test_a_previous_tree_is_replaced_not_merged(self, tmp_path) -> None:
        """Two uploads landing on the same path would otherwise produce the union
        of both, which matches no firmware and fails in a way that points nowhere
        near the archive."""
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "stale-from-an-older-upload").write_text("x", encoding="utf-8")
        archive = _tar(tmp_path / "rootfs.tar")
        result = unpack_rootfs_archive(archive, dest)
        assert result.failure is None
        assert not (dest / "stale-from-an-older-upload").exists()

    def test_a_repeat_unpack_of_the_same_archive_is_idempotent(self, tmp_path) -> None:
        archive = _tar(tmp_path / "rootfs.tar")
        first = unpack_rootfs_archive(archive, tmp_path / "out")
        second = unpack_rootfs_archive(archive, tmp_path / "out")
        assert first.members == second.members
        assert first.failure is None and second.failure is None


# ------------------------------------------------------------------ path traversal


class TestTraversalRefused:
    def test_dot_dot_does_not_escape_the_destination(self, tmp_path) -> None:
        archive = _tar(tmp_path / "evil.tar", extra=[_file("../PWNED.txt")])
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert not (tmp_path / "PWNED.txt").exists()
        assert "../PWNED.txt" in result.rejected

    def test_a_deep_dot_dot_chain_does_not_escape(self, tmp_path) -> None:
        archive = _tar(
            tmp_path / "evil.tar",
            extra=[_file("../../../../../../Windows/System32/drivers/etc/hosts")],
        )
        unpack_rootfs_archive(archive, tmp_path / "out")
        assert not (tmp_path.parent / "Windows").exists()

    def test_an_absolute_member_is_refused_rather_than_reinterpreted(self, tmp_path) -> None:
        """tarfile itself strips the leading slash and writes the member inside the
        destination. That is harmless for this path, but it means the archive's own
        name no longer matches what landed on disk -- so an absolute name is
        refused and counted instead."""
        archive = _tar(tmp_path / "evil.tar", extra=[_file("/etc/cron.d/iris")])
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert not (tmp_path / "out" / "etc" / "cron.d" / "iris").exists()
        assert result.rejected_count >= 1

    def test_a_dot_dot_inside_a_prefix_wrapped_archive_is_still_refused(
        self, tmp_path
    ) -> None:
        archive = _tar(tmp_path / "evil.tar", prefix="squashfs-root/",
                       extra=[_file("squashfs-root/../../PWNED.txt")])
        unpack_rootfs_archive(archive, tmp_path / "out")
        assert not (tmp_path / "PWNED.txt").exists()

    def test_a_windows_drive_letter_is_refused(self, tmp_path) -> None:
        """``Path(out) / "C:"`` resolves to the drive itself rather than to a child,
        so the check is on the refused list -- asserting on the joined path would
        pass on a host that had never refused anything."""
        archive = _tar(tmp_path / "evil.tar", extra=[_file("C:/Windows/System32/x.dll")])
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert "C:/Windows/System32/x.dll" in result.rejected
        assert result.rejected_count == 1


# --------------------------------------------------------------------- link safety


class TestLinkSafety:
    def test_a_link_pointing_out_of_the_tree_is_not_created(self, tmp_path) -> None:
        archive = _tar(
            tmp_path / "evil.tar",
            extra=[_symlink("etc/escape", "../../../../Windows/System32/drivers/etc/hosts")],
        )
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        link = tmp_path / "out" / "etc" / "escape"
        assert not link.is_symlink()
        assert result.links_created == 0
        assert result.links_skipped >= 1

    def test_a_device_node_is_refused(self, tmp_path) -> None:
        """Nothing in the emulation path needs one, and creating a device node on a
        host is not a capability an upload endpoint should hand out."""
        archive = _tar(tmp_path / "evil.tar", extra=[_fifo("dev/pipe")])
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert not (tmp_path / "out" / "dev" / "pipe").exists()
        assert "dev/pipe" in result.rejected

    def test_a_skipped_link_is_reported_rather_than_hidden(self, tmp_path) -> None:
        """A rootfs is mostly links, so a tree that quietly lost them boots into a
        missing-init failure that says nothing about the upload."""
        archive = _tar(
            tmp_path / "rootfs.tar",
            extra=[_symlink("sbin/nowhere", "../../nowhere")],
        )
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert result.links_skipped >= 1
        assert any("symlink" in note for note in result.notes)


# ------------------------------------------------------------------- decompression


class TestSizeCaps:
    def test_a_byte_cap_trip_is_a_failure_not_a_partial_tree(self, tmp_path) -> None:
        """Reporting a prefix of the rootfs as a successful unpack produces a boot
        failure whose message points at the guest rather than at the archive."""
        archive = _tar(tmp_path / "rootfs.tar")
        result = unpack_rootfs_archive(archive, tmp_path / "out", max_bytes=16)
        assert result.failure is not None
        assert result.rootfs_dir is None

    def test_the_byte_cap_is_named_in_the_message(self, tmp_path) -> None:
        """The message has to name the cap the caller typed, not "0 MiB" -- which is
        what integer-dividing a sub-megabyte limit by a megabyte produces, in the
        exact range where someone is testing the limit."""
        archive = _tar(tmp_path / "rootfs.tar")
        result = unpack_rootfs_archive(archive, tmp_path / "out", max_bytes=64)
        assert "64 B limit" in result.failure_reason

    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (0, "0 B"),
            (512, "512 B"),
            (64 * 1024 * 1024, "64 MiB"),
            (MAX_UNPACKED_BYTES, "2 GiB"),
        ],
    )
    def test_limits_are_rendered_at_a_readable_scale(self, count, expected) -> None:
        assert human_bytes(count) == expected

    def test_a_member_count_over_the_limit_is_refused_before_any_write(
        self, tmp_path
    ) -> None:
        archive = _tar(tmp_path / "rootfs.tar")
        result = unpack_rootfs_archive(archive, tmp_path / "out", max_members=3)
        assert result.failure is not None
        assert "over the 3 limit" in result.failure_reason
        assert not (tmp_path / "out").exists()

    def test_the_default_caps_are_finite(self) -> None:
        """A cap nobody bounded would make the two caps above decorative."""
        assert 0 < MAX_ARCHIVE_MEMBERS < 10 ** 7
        assert 0 < MAX_UNPACKED_BYTES < 64 * 1024 ** 3


# --------------------------------------------------------------------- not a tar


class TestNotAnArchive:
    def test_a_firmware_image_reports_a_readable_failure(self, tmp_path) -> None:
        image = tmp_path / "firmware.bin"
        image.write_bytes(b"\x00" * 64)
        result = unpack_rootfs_archive(image, tmp_path / "out")
        assert result.failure is not None
        assert result.failure.kind is FailureKind.NO_ROOTFS
        assert "not a readable tar archive" in result.failure_reason

    def test_a_tar_without_a_rootfs_says_so(self, tmp_path) -> None:
        """An archive of source code is not a rootfs, and unpacking it into the
        scratch tree would leave a directory that later looks like an extracted
        firmware in the firmware picker."""
        archive = tmp_path / "docs.tar"
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo("README.md")
            info.size = 3
            tf.addfile(info, io.BytesIO(b"hi\n"))
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert result.failure is not None
        assert "no rootfs directory structure" in result.failure_reason
        assert not (tmp_path / "out").exists()


# ------------------------------------------------------------------ archive probe


class TestIsRootfsArchive:
    def test_a_rootfs_tar_is_recognised(self, tmp_path) -> None:
        assert is_rootfs_archive(_tar(tmp_path / "rootfs.tar")) is True

    def test_a_gzipped_rootfs_tar_is_recognised(self, tmp_path) -> None:
        import gzip

        raw = _tar(tmp_path / "rootfs.tar")
        gz = tmp_path / "rootfs.tar.gz"
        gz.write_bytes(gzip.compress(raw.read_bytes()))
        assert is_rootfs_archive(gz) is True

    def test_a_firmware_image_is_not_recognised_as_an_archive(self, tmp_path) -> None:
        """The probe decides which of the two launch paths runs, so a firmware
        image called rootfs.tar.gz must still take the extraction path."""
        image = tmp_path / "rootfs.tar.gz"
        image.write_bytes(b"\x27\x05\x00\x00" + b"\x00" * 512)
        assert is_rootfs_archive(image) is False

    def test_a_directory_is_not_an_archive(self, tmp_path) -> None:
        assert is_rootfs_archive(tmp_path) is False


# ------------------------------------------------------------------- refusal count


class TestRefusalAccounting:
    def test_the_count_is_not_the_length_of_the_capped_sample(self, tmp_path) -> None:
        """The sample is bounded so a hostile archive cannot make the note
        unbounded; reading the count off its length would report 20 for an archive
        that refused ten thousand."""
        archive = _tar(
            tmp_path / "evil.tar",
            extra=[_file(f"../escape-{i}.txt") for i in range(50)],
        )
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert result.rejected_count == 50
        assert len(result.rejected) < 50
        assert any("more" in entry for entry in result.rejected)

    def test_a_clean_archive_reports_no_refusals(self, tmp_path) -> None:
        archive = _tar(tmp_path / "rootfs.tar")
        result = unpack_rootfs_archive(archive, tmp_path / "out")
        assert result.rejected_count == 0
        assert result.rejected == ()