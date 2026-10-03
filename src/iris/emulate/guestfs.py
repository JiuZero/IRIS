"""The guest's filesystem, reached from outside the guest.

Every other part of IRIS looks at the guest from the outside and gets nothing back.
The probes live in the container, and the guest's root filesystem is a block image
inside that container -- ``image.raw`` when it was baked, ``state.raw`` since the
guest is allowed to write to it. Nothing in the container mounts it, so
``docker exec`` cannot see a single guest file or process. That is not a gap in a
probe, it is the shape of the thing: the guardian's repair scripts report ``n=0``,
``n=0`` means UNKNOWN rather than clean, and the documentation has carried that
sentence for several versions.

This module is the missing channel, and it is deliberately the smallest one that
is real: a loop mount of the guest image inside the privileged emulation
container, which is the same operation ``make_image.sh`` already performs at build
time. Read a file out, list a directory, put a file in.

Three properties are worth stating, because each one is a decision and not an
implementation detail:

**Two disks, not one.** ``image.raw`` is the baked image and is never attached to
QEMU, so mounting it is always safe and shows what the firmware shipped with.
``state.raw`` is what the guest booted from and wrote to; it is mounted only while
QEMU is not running, and touching it then is refused rather than attempted --
mounting a filesystem that a running guest has open read-write corrupts it, and
the corruption would surface much later as an unexplained boot failure.

**No arbitrary command execution.** Every operation is ``ls``/``test``/``cp`` on a
path. ``make_image.sh`` does run busybox under a chroot of this same image, so
"execute something in the guest filesystem" is possible here too; it is left out
because a repair channel that runs arbitrary text is a different thing to review,
and this one is meant to be boring.

**The mount is always unmounted.** A leaked loop mount inside a long-lived
container makes the next mount fail with "already mounted", which reads as a
corrupt image. Cleanup is on an EXIT trap, so it also runs when ``cp`` fails
half-way through a write.

What this does not do: it cannot see a *running* guest's view of its own
filesystem. Anything the guest has buffered in memory, and anything mounted over
the top of a path (JFFS2 volumes, /proc, /sys), is not the file on the disk. The
state disk is the last boot's leftovers, not the live system.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from iris.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "GuestBusy",
    "GuestError",
    "GuestMissing",
    "GuestReadOnly",
    "disk_path",
    "guest_path",
    "list_guest",
    "pull_guest_file",
    "push_guest_file",
    "qemu_pid",
    "reset_guest_state",
]

#: Where the emulation container keeps one directory per iid.
WORK_ROOT = "/work/scratch"
#: The baked image. Never attached to QEMU, so it can be mounted at any time.
IMAGE_DISK = "image"
#: What the guest actually boots from and writes to. Attached while QEMU runs.
STATE_DISK = "state"
_DISK_FILES = {IMAGE_DISK: "image.raw", STATE_DISK: "state.raw"}

#: ``bash -c`` exit codes this module gives meaning to.
EXIT_MISSING = 4
EXIT_MOUNT_FAILED = 5
EXIT_NOT_A_FILE = 6

#: Scratch paths inside the container. Fixed names rather than random ones so that a
#: leftover from a killed command is recognisable and cleaned by the next run.
_STAGING_OUT = "/tmp/iris-guestfs-out"
_STAGING_IN = "/tmp/iris-guestfs-in"

#: Git Bash rewrites a POSIX argument into a Windows path before the process ever
#: sees it, so ``iris guest ls 1 /etc/passwd`` arrives as ``C:/Program Files/Git/etc/passwd``
#: and every documented example would fail on the platform this project is used from.
#: No guest path begins with a drive letter, so matching that shape is unambiguous.
_MSYS_PREFIX = re.compile(r"^[A-Za-z]:[/" + chr(92) + chr(92) + r"].*?[/" + chr(92) + chr(92) + r"]Git[/" + chr(92) + chr(92) + r"]")

_PROLOGUE = """set -e
DISK=$1
TARGET=$2
MNT=$(mktemp -d /tmp/iris-guestfs.XXXXXX)
MNTED=0
cleanup() {
    if [ "$MNTED" = 1 ]; then
        umount "$MNT" 2>/dev/null || umount -l "$MNT" 2>/dev/null || true
    fi
    rmdir "$MNT" 2>/dev/null || true
    return 0
}
trap cleanup EXIT
"""


class GuestError(RuntimeError):
    """An operation on the guest filesystem did not succeed."""


class GuestBusy(GuestError):
    """QEMU has the state disk open, so it must not be mounted."""


class GuestMissing(GuestError):
    """No such path inside the guest."""


class GuestReadOnly(GuestError):
    """The guest path exists but is not a regular file."""


def _docker(args: list[str], timeout: int = 120):
    """Run a docker command. Indirection so tests can stand in for docker."""
    from iris.emulate.orchestrator import _run

    return _run(args, timeout=timeout)


def disk_path(iid: int, disk: str = STATE_DISK) -> str:
    """Where ``disk`` lives inside the emulation container."""
    try:
        name = _DISK_FILES[disk]
    except KeyError:
        raise GuestError(f"unknown guest disk {disk!r}; expected one of "
                         f"{', '.join(sorted(_DISK_FILES))}") from None
    return f"{WORK_ROOT}/{iid}/{name}"


def guest_path(path: str) -> str:
    """A guest path, refused unless it is plainly a path inside the guest.

    The value is passed to a shell as ``$2`` and concatenated onto the mount point,
    so ``../../work`` would reach out of the guest filesystem and ``/proc`` would
    resolve to the container's own. Neither is a repair.
    """
    if not path:
        raise GuestError("empty guest path")
    mangled = _MSYS_PREFIX.match(path)
    if mangled:
        logger.warning(f"recovering a path Git Bash rewrote: {path}")
        # ``/`` arrives as ``C:/Program Files/Git/`` and everything else arrives with
        # the separator eaten too, so the root has to be put back either way.
        path = "/" + path[mangled.end():]
    pure = PurePosixPath(path)
    if not pure.is_absolute():
        raise GuestError(f"guest path must be absolute: {path}")
    if ".." in pure.parts:
        raise GuestError(f"guest path must not leave the guest: {path}")
    return str(pure)


def qemu_pid(container: str, iid: int) -> int | None:
    """The pid of the QEMU writing the state disk, or ``None`` if it is not running.

    The pid file is left behind by every launch and is not removed on exit, so the
    pid is checked against ``/proc`` inside the container. A stale file naming a
    pid that has since been reused reports "running" -- the conservative direction,
    because the alternative is mounting a disk the guest is using.
    """
    result = _docker(["docker", "exec", container, "cat", f"{WORK_ROOT}/{iid}/qemu.pid"],
                     timeout=15)
    text = (result.stdout or "").strip()
    if result.returncode != 0 or not text.isdigit():
        return None
    pid = int(text)
    alive = _docker(["docker", "exec", container, "test", "-d", f"/proc/{pid}"], timeout=15)
    return pid if alive.returncode == 0 else None


def _require_idle(container: str, iid: int, disk: str) -> None:
    if disk != STATE_DISK:
        # The baked image is not attached to anything, by construction.
        return
    pid = qemu_pid(container, iid)
    if pid is not None:
        raise GuestBusy(
            f"qemu is running as pid {pid} and has {disk_path(iid, disk)} open; "
            f"stop the emulation first (`iris emulate stop {iid}`), or read the "
            f"baked image instead (--disk {IMAGE_DISK})"
        )


def _failure(proc, container: str) -> GuestError:
    """One place that decides what a failed command means.

    Every operation reports the same three failures the same way. Four functions
    each inventing their own mapping is how "permission denied on mount" becomes
    "no such file" for one caller and a bare error code for another.
    """
    stderr = (proc.stderr or "").strip()
    if "IRIS_GUESTFS_MISSING" in stderr:
        return GuestMissing(stderr.splitlines()[-1])
    if "IRIS_GUESTFS_NOT_A_FILE" in stderr:
        return GuestReadOnly(stderr.splitlines()[-1])
    if "IRIS_GUESTFS_MOUNT_FAILED" in stderr:
        return GuestError(f"could not mount the guest filesystem in {container}: {stderr}")
    return GuestError(stderr or f"guest filesystem command failed (rc={proc.returncode})")


def _exec(container: str, script: str, *args: str, timeout: int = 120) -> None:
    """Run ``script`` with ``args`` as $1..$n, mapping its failure onto an exception."""
    proc = _docker(["docker", "exec", container, "bash", "-c", script,
                    "iris-guestfs", *args], timeout=timeout)
    if proc.returncode != 0:
        raise _failure(proc, container)


def guest_exists(container: str, iid: int, path: str, *, disk: str = STATE_DISK) -> bool:
    """Whether ``path`` exists on ``disk``."""
    target = guest_path(path)
    _require_idle(container, iid, disk)
    script = (_PROLOGUE
              + f'mount -o ro,loop "$DISK" "$MNT" || {{ echo IRIS_GUESTFS_MOUNT_FAILED >&2; exit {EXIT_MOUNT_FAILED}; }}\nMNTED=1\n'
              + '[ -e "$MNT$TARGET" ]\n')
    proc = _docker(["docker", "exec", container, "bash", "-c", script, "iris-guestfs",
                    disk_path(iid, disk), target], timeout=120)
    if proc.returncode in (0, 1):
        # 1 is `test -e` saying no. Anything else went wrong before it could answer.
        return proc.returncode == 0
    raise _failure(proc, container)


def list_guest(container: str, iid: int, path: str = "/", *,
               disk: str = STATE_DISK) -> list[str]:
    """Names directly inside ``path``, sorted, without ``.`` and ``..``.

    Names only. A line per entry is what a listing is for; anything richer (mode,
    size, ownership) would be a second parser to keep correct against whatever
    ``ls`` the image happens to ship, and pulling the file is one command away.
    """
    target = guest_path(path)
    _require_idle(container, iid, disk)
    script = (_PROLOGUE
              + f'mount -o ro,loop "$DISK" "$MNT" || {{ echo IRIS_GUESTFS_MOUNT_FAILED >&2; exit {EXIT_MOUNT_FAILED}; }}\nMNTED=1\n'
              + '[ -d "$MNT$TARGET" ] || { echo "IRIS_GUESTFS_MISSING: $TARGET" >&2;'
                f' exit {EXIT_MISSING}; }}\n'
              + 'ls -1A "$MNT$TARGET"\n')
    proc = _docker(["docker", "exec", container, "bash", "-c", script, "iris-guestfs",
                    disk_path(iid, disk), target], timeout=120)
    if proc.returncode != 0:
        raise _failure(proc, container)
    return sorted(line for line in (proc.stdout or "").splitlines() if line.strip())


def pull_guest_file(container: str, iid: int, path: str, dest: Path, *,
                    disk: str = STATE_DISK) -> Path:
    """Copy a file out of the guest filesystem to ``dest`` on the host."""
    target = guest_path(path)
    _require_idle(container, iid, disk)
    script = (_PROLOGUE
              + f'mount -o ro,loop "$DISK" "$MNT" || {{ echo IRIS_GUESTFS_MOUNT_FAILED >&2; exit {EXIT_MOUNT_FAILED}; }}\nMNTED=1\n'
              + '[ -e "$MNT$TARGET" ] || { echo "IRIS_GUESTFS_MISSING: $TARGET" >&2;'
                f' exit {EXIT_MISSING}; }}\n'
              + '[ -f "$MNT$TARGET" ] || { echo "IRIS_GUESTFS_NOT_A_FILE: $TARGET" >&2;'
                f' exit {EXIT_NOT_A_FILE}; }}\n'
              + f'cp "$MNT$TARGET" "{_STAGING_OUT}"\n')
    _exec(container, script, disk_path(iid, disk), target)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    copied = _docker(["docker", "cp", f"{container}:{_STAGING_OUT}", str(dest)], timeout=300)
    _docker(["docker", "exec", container, "rm", "-f", _STAGING_OUT], timeout=30)
    if copied.returncode != 0:
        raise GuestError((copied.stderr or "").strip() or "docker cp out failed")
    logger.info(f"pulled {path} from {container} (iid {iid}, {disk} disk) to {dest}")
    return dest


def push_guest_file(container: str, iid: int, source: Path, path: str, *,
                    disk: str = STATE_DISK, mode: str | None = None) -> None:
    """Copy a host file into the guest filesystem at ``path``.

    ``mode`` is octal and applied inside the guest, not inherited from the host: a
    file that has to be executable in the guest usually comes from a host where the
    executable bit is not a thing that can be set portably.
    """
    source = Path(source)
    if not source.is_file():
        raise GuestError(f"not a file on the host: {source}")
    if mode is not None and not _is_octal_mode(mode):
        raise GuestError(f"mode must be octal, e.g. 755, got {mode!r}")
    target = guest_path(path)
    _require_idle(container, iid, disk)
    staged = _docker(["docker", "cp", str(source), f"{container}:{_STAGING_IN}"], timeout=300)
    if staged.returncode != 0:
        raise GuestError((staged.stderr or "").strip() or "docker cp in failed")
    script = (_PROLOGUE
              + f'mount -o loop "$DISK" "$MNT" || {{ echo IRIS_GUESTFS_MOUNT_FAILED >&2; exit {EXIT_MOUNT_FAILED}; }}\nMNTED=1\n'
              + 'mkdir -p "$(dirname "$MNT$TARGET")"\n'
              + f'cp "{_STAGING_IN}" "$MNT$TARGET"\n'
              + 'if [ -n "$3" ]; then chmod "$3" "$MNT$TARGET"; fi\n'
              + 'sync\n'
              + 'umount "$MNT"\nMNTED=0\n'
              + f'rm -f "{_STAGING_IN}"\n'
              + 'e2fsck -p "$DISK" || true\n')
    _exec(container, script, disk_path(iid, disk), target, mode or "")
    logger.info(f"put {source} at {path} in {container} (iid {iid}, {disk} disk)")


def reset_guest_state(container: str, iid: int) -> bool:
    """Throw the state disk away so the next launch boots the baked image again.

    Returns whether there was one, so a caller can tell "reset" from "there was
    nothing to reset" instead of both printing success. The escape hatch for a state
    disk left inconsistent by a hard kill, and the way to undo an injected repair.
    """
    _require_idle(container, iid, STATE_DISK)
    target = disk_path(iid, STATE_DISK)
    existed = _docker(["docker", "exec", container, "test", "-e", target], timeout=30)
    if existed.returncode != 0:
        return False
    result = _docker(["docker", "exec", container, "rm", "-f", target], timeout=60)
    if result.returncode != 0:
        raise GuestError((result.stderr or "").strip() or "could not remove the state disk")
    logger.info(f"removed the state disk of {container} (iid {iid}); the next launch "
                f"boots the baked image")
    return True


def _is_octal_mode(mode: str) -> bool:
    stripped = mode.strip()
    return bool(stripped) and all(c in "01234567" for c in stripped) and len(stripped) <= 4