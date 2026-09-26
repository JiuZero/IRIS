"""L2 emulation orchestrator — end-to-end firmware rehosting pipeline.

Uses docker cp + docker exec to avoid Windows volume mount issues.
Pipeline:
  1. Take extracted rootfs directory (from L1)
  2. Create rootfs tarball via Docker container
  3. Build ext2 QEMU disk image inside privileged container
  4. Start QEMU with port forwarding for web access
  5. Check network reachability (curl) via port forwarding
  6. Report results
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from iris.emulate.qemu_config import get_config, supported_archs


# maps an ELF-census arch label (L1 vocabulary) to the emulation arch that can run it
_CENSUS_TO_RUNNABLE = {
    "mipsel": "mipsel",
    "mipseb": "mipseb",
    "armel": "armel",
    "aarch64": "arm64",
}


def preflight_arch(rootfs_dir: Path, arch: str) -> str:
    """Validate the requested arch before spinning up docker.

    Returns '' when the emulation may proceed, else a structured failure line:
      unsupported-arch: requested arch has no QEMU config
      arch-mismatch:    rootfs ELF census disagrees with the requested arch
    """
    supported = supported_archs()
    if arch not in supported:
        return (
            f"unsupported-arch: '{arch}' has no QEMU config "
            f"(supported: {', '.join(supported)})"
        )

    from iris.extract.rootfs_extract import _census_elfs

    _count, counter = _census_elfs(Path(rootfs_dir))
    known = {a: n for a, n in counter.items() if not a.startswith("unk(")}
    if not known:
        return ""  # no ELF evidence (script-only rootfs etc.) — can't judge
    dominant = max(known, key=known.get)
    runnable = _CENSUS_TO_RUNNABLE.get(dominant)
    if runnable and runnable != arch:
        return (
            f"arch-mismatch: rootfs is dominated by {dominant} ELFs "
            f"({known[dominant]} samples), which cannot run under the '{arch}' kernel; "
            f"use --arch {runnable}"
        )
    return ""


@dataclass
class EmulationResult:
    firmware_path: Path
    arch: str
    success: bool = False
    ping_ok: bool = False
    web_ok: bool = False
    web_url: str = ""
    qemu_pid: int = 0
    serial_log: str = ""
    error: str = ""
    duration_sec: float = 0.0
    container_id: str = ""


def _env() -> dict[str, str]:
    return {**os.environ, "MSYS_NO_PATHCONV": "1"}


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=_env(), timeout=timeout, check=False)


def _build_baked_image() -> str:
    image_name = "iris-emulate-baked:latest"
    result = _run(["docker", "image", "inspect", image_name])
    if result.returncode == 0:
        return image_name
    print("Building iris-emulate-baked Docker image...")
    project_root = Path(__file__).parent.parent.parent.parent
    dockerfile = project_root / "docker" / "emulate" / "Dockerfile.baked"
    result = _run(["docker", "build", "-t", image_name, "-f", str(dockerfile), str(project_root)], timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Docker build failed: {result.stderr}")
    return image_name


def _create_tarball(rootfs_dir: Path, tarball_path: Path) -> Path:
    tarball_path.parent.mkdir(parents=True, exist_ok=True)
    env = _env()

    create_cmd = [
        "docker", "create",
        "-v", f"{rootfs_dir.resolve()}:/rootfs:ro",
        "alpine:3.20",
        "tar", "czf", "/tmp/rootfs.tar.gz", "-C", "/rootfs", ".",
    ]
    create_result = subprocess.run(create_cmd, capture_output=True, text=True, env=env, timeout=30, check=False)
    if create_result.returncode != 0:
        raise RuntimeError(f"docker create failed: {create_result.stderr}")
    container_id = create_result.stdout.strip()

    start_result = subprocess.run(["docker", "start", "-a", container_id], capture_output=True, text=True, env=env, timeout=120, check=False)
    if start_result.returncode != 0:
        subprocess.run(["docker", "rm", "-f", container_id], env=env, capture_output=True, check=False)
        raise RuntimeError(f"tar creation failed: {start_result.stderr}")

    cp_result = subprocess.run(["docker", "cp", f"{container_id}:/tmp/rootfs.tar.gz", str(tarball_path)], capture_output=True, text=True, env=env, timeout=60, check=False)
    subprocess.run(["docker", "rm", "-f", container_id], env=env, capture_output=True, check=False)
    if cp_result.returncode != 0:
        raise RuntimeError(f"docker cp failed: {cp_result.stderr}")
    return tarball_path


def _docker_produce(volumes: list[tuple[str, str]], image: str, script: str,
                    container_out: str, host_out: Path, timeout: int = 240) -> Path:
    """Run a throwaway container with bind mounts, copy one artifact back to host."""
    env = _env()
    create_cmd = ["docker", "create"]
    for host, cont in volumes:
        create_cmd += ["-v", f"{host}:{cont}"]
    create_cmd += [image, "sh", "-c", script]
    create_result = subprocess.run(create_cmd, capture_output=True, text=True, env=env, timeout=30, check=False)
    if create_result.returncode != 0:
        raise RuntimeError(f"docker create failed: {create_result.stderr}")
    container_id = create_result.stdout.strip()
    if not container_id or " " in container_id:
        raise RuntimeError(f"docker create returned unexpected output: {container_id[:200]!r}")
    start_result = subprocess.run(
        ["docker", "start", "-a", container_id],
        capture_output=True, text=True, env=env, timeout=timeout, check=False,
    )
    if start_result.returncode != 0:
        subprocess.run(["docker", "rm", "-f", container_id], env=env, capture_output=True, check=False)
        raise RuntimeError(f"container script failed: {start_result.stderr[-500:]}")
    cp_result = subprocess.run(
        ["docker", "cp", f"{container_id}:{container_out}", str(host_out)],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    subprocess.run(["docker", "rm", "-f", container_id], env=env, capture_output=True, check=False)
    if cp_result.returncode != 0:
        raise RuntimeError(f"docker cp failed: {cp_result.stderr}")
    return host_out


def _compose_rootfs_from_slices(
    slices_dir: Path,
    partition_mounts: list[tuple[str, str]],
    tarball_path: Path,
    guest_script: str = "",
    image: str = "python:3.11-alpine",
) -> Path:
    """Rebuild a multi-partition rootfs entirely inside a Linux container.

    ``partition_mounts`` is ordered base-first: ``(slice_stem, mount_point)`` pairs
    whose ``<stem>.jffs2`` files live in ``slices_dir``. jefferson extraction and
    merge run on ext4 in the container so JFFS2 soft links survive; the result is
    tarred and copied back. This avoids the NTFS path where a host-side ``cp`` or
    ``copytree`` silently drops the ~230 busybox symlinks (WinError 123).
    """
    env_lines = ["set -e", "pip install -q jefferson 2>/dev/null || pip install -q jefferson"]
    env_lines.append("BASE=/work/rootfs; rm -rf /work/rootfs; mkdir -p $BASE")
    for stem, mount in partition_mounts:
        tree = f"/work/{stem}"
        target = "$BASE" if mount in ("/", "") else f"$BASE/{mount.lstrip('/')}"
        env_lines.append(f"rm -rf {tree}; jefferson -d {tree} -f /in/{stem}.jffs2 >/dev/null")
        env_lines.append(f"mkdir -p {target}; cp -a {tree}/. {target}/")
    # after merge, the mount targets hold real content; re-mounting the empty
    # vendor JFFS2 partition over them would shadow the binaries (RP3 "Kylin:
    # not found"). Comment out any such mount line in the boot scripts.
    for _stem, mount in partition_mounts:
        if mount in ("/", ""):
            continue
        mp = mount.rstrip("/")
        env_lines.append(
            "for s in $(ls $BASE/etc/init.d/* $BASE/etc/*rc* $BASE/etc_ro/init.d/* 2>/dev/null); do "
            f"sed -i -E 's|^([[:space:]]*mount.*{mp}[[:space:]].*)$|#IRIS-shadow-fix: \\1|' \"$s\" 2>/dev/null || true; done"
        )
    if guest_script:
        import base64

        b64 = base64.b64encode(guest_script.encode()).decode()
        env_lines.append("mkdir -p $BASE/firmadyne")
        env_lines.append(f"echo {b64} | base64 -d > $BASE/firmadyne/iris_rules.sh")
        env_lines.append("chmod +x $BASE/firmadyne/iris_rules.sh")
    env_lines.append("tar -czf /work/rootfs.tar.gz -C $BASE .")
    # the /work bind mount is the host scratch dir; drop our intermediate trees
    stems = " ".join(stem for stem, _m in partition_mounts)
    env_lines.append("for d in rootfs " + stems + "; do rm -rf /work/$d; done")
    script = "; ".join(env_lines)

    tarball_path.parent.mkdir(parents=True, exist_ok=True)
    volumes = [(str(slices_dir.resolve()), "/in:ro"), (str(tarball_path.parent.resolve()), "/work")]
    produced = _docker_produce(volumes, image, script, "/work/rootfs.tar.gz", tarball_path)
    if tarball_path.name != "rootfs.tar.gz":
        (tarball_path.parent / "rootfs.tar.gz").unlink(missing_ok=True)
    return produced


def build_parts_mounts(parts_dir: Path) -> list[tuple[str, str]]:
    """Order a TendaW ``-parts`` dir's ``<stem>.jffs2`` slices base-first.

    The slice named ``romfs`` (mount ``/``) must merge before overlays.
    Unknown stems fall back to mounting at ``/opt/<stem>``.
    """
    from iris.extract.tenda import PARTITION_MOUNTS

    stems = sorted(p.stem for p in parts_dir.glob("*.jffs2"))
    pairs = [(s, PARTITION_MOUNTS.get(s, f"/opt/{s}")) for s in stems]
    pairs.sort(key=lambda p: (p[1] != "/", p[1]))
    return pairs


def emulate_firmware(
    rootfs_dir: Path,
    arch: str,
    iid: int,
    scratch_dir: Path,
    host_port: int = 8080,
    timeout_sec: int = 120,
    docker_image: str = "",
    parts_slices_dir: Path | None = None,
    partition_mounts: list[tuple[str, str]] | None = None,
) -> EmulationResult:
    start_time = time.time()
    result = EmulationResult(firmware_path=rootfs_dir, arch=arch)

    config = get_config(arch)
    if config is None:
        result.error = f"Unsupported architecture: {arch}"
        return result

    work_dir = scratch_dir / f"emulate-{iid}"
    work_dir.mkdir(parents=True, exist_ok=True)
    tarball_path = work_dir / f"{iid}.tar.gz"

    container_compose = parts_slices_dir is not None and partition_mounts is not None
    try:
        if not tarball_path.exists():
            if container_compose:
                guest_script = ""
                host_rules_script = rootfs_dir / "firmadyne" / "iris_rules.sh"
                if host_rules_script.is_file():
                    guest_script = host_rules_script.read_text(encoding="utf-8", errors="replace")
                print(
                    f"Composing rootfs from {len(partition_mounts)} JFFS2 slices in-container "
                    f"(symlink-safe, guest_script={bool(guest_script)})..."
                )
                _compose_rootfs_from_slices(parts_slices_dir, partition_mounts, tarball_path, guest_script)
            else:
                print("Creating rootfs tarball...")
                _create_tarball(rootfs_dir, tarball_path)
        print(f"Tarball: {tarball_path.stat().st_size} bytes")
    except RuntimeError as e:
        result.error = str(e)
        result.duration_sec = time.time() - start_time
        return result

    if not docker_image:
        docker_image = _build_baked_image()

    container_name = f"iris-qemu-{iid}"
    _run(["docker", "rm", "-f", container_name])

    print(f"Starting emulation container {container_name}...")
    create_cmd = [
        "docker", "create", "--privileged",
        "-p", f"{host_port}:{host_port}",
        "--name", container_name,
        docker_image, "sleep", "3600",
    ]
    create_result = _run(create_cmd)
    if create_result.returncode != 0:
        result.error = f"Container create failed: {create_result.stderr}"
        result.duration_sec = time.time() - start_time
        return result

    start_result = _run(["docker", "start", container_name])
    if start_result.returncode != 0:
        result.error = f"Container start failed: {start_result.stderr}"
        result.duration_sec = time.time() - start_time
        return result
    result.container_id = container_name

    print("Copying tarball into container...")
    cp_target = f"/work/scratch/{iid}/{iid}.tar.gz"
    _run(["docker", "exec", container_name, "mkdir", "-p", f"/work/scratch/{iid}"])
    cp_result = _run(["docker", "cp", str(tarball_path), f"{container_name}:{cp_target}"], timeout=60)
    if cp_result.returncode != 0:
        result.error = f"docker cp tarball failed: {cp_result.stderr}"
        result.duration_sec = time.time() - start_time
        _run(["docker", "rm", "-f", container_name])
        return result

    print(f"Building QEMU image for IID={iid} arch={arch}...")
    make_result = _run(
        ["docker", "exec", container_name, "bash", "/work/scripts/make_image.sh", str(iid), arch],
        timeout=180,
    )
    if make_result.returncode != 0:
        result.error = f"Image build failed: {make_result.stderr[-500:]}"
        result.duration_sec = time.time() - start_time
        _run(["docker", "rm", "-f", container_name])
        return result
    print(f"Image build output: {make_result.stdout[-200:]}")

    print(f"Starting QEMU (port {host_port} -> guest:80)...")
    qemu_result = _run(
        ["docker", "exec", "-d", container_name, "bash", "/work/scripts/run_qemu.sh", str(iid), arch, str(host_port)],
        timeout=15,
    )
    if qemu_result.returncode != 0:
        result.error = f"QEMU start failed: {qemu_result.stderr}"
        result.duration_sec = time.time() - start_time
        _run(["docker", "rm", "-f", container_name])
        return result

    print(f"Waiting for firmware to boot (timeout {timeout_sec}s)...")
    boot_deadline = time.time() + timeout_sec
    guest_ip = "192.168.1.1"
    socat_updated = False
    while time.time() < boot_deadline:
        time.sleep(5)
        elapsed = int(time.time() - start_time)

        if not socat_updated:
            log_cmd = ["docker", "exec", container_name, "grep", "-a", "inet_insert_ifa",
                       f"/work/scratch/{iid}/qemu.serial.log"]
            log_res = subprocess.run(log_cmd, capture_output=True, text=True, env=_env(), timeout=10, check=False)
            for line in log_res.stdout.splitlines():
                if "device:lo" not in line and "ifa:0x" in line:
                    import re
                    m = re.search(r"ifa:0x([0-9a-f]+)", line)
                    if m:
                        raw = int(m.group(1), 16)
                        if arch in ("mipsel", "armel"):
                            ip = f"{raw & 0xFF}.{(raw >> 8) & 0xFF}.{(raw >> 16) & 0xFF}.{(raw >> 24) & 0xFF}"
                        else:
                            ip = f"{(raw >> 24) & 0xFF}.{(raw >> 16) & 0xFF}.{(raw >> 8) & 0xFF}.{raw & 0xFF}"
                        if ip != guest_ip and not ip.startswith("127."):
                            guest_ip = ip
                            print(f"  Detected guest IP: {guest_ip}")
                            _run(["docker", "exec", container_name, "pkill", "-f", "socat.*TCP"], timeout=5)
                            _run(["docker", "exec", "-d", container_name, "socat",
                                  f"TCP-LISTEN:{host_port},reuseaddr,fork", f"TCP:{guest_ip}:80"], timeout=5)
                            socat_updated = True
                            break

        check_result = subprocess.run(
            ["docker", "exec", container_name, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "3", f"http://127.0.0.1:{host_port}"],
            capture_output=True, text=True, env=_env(), timeout=10, check=False,
        )
        http_code = check_result.stdout.strip()
        if http_code and http_code != "000":
            result.web_ok = True
            result.web_url = f"http://localhost:{host_port}"
            result.success = True
            print(f"  Web reachable at {result.web_url} (HTTP {http_code}) after {elapsed}s")
            break
        print(f"  [{elapsed}s] waiting... (HTTP {http_code})")

    log_result = _run(["docker", "cp", f"{container_name}:/work/scratch/{iid}/qemu.serial.log", str(work_dir / "qemu.serial.log")])
    if log_result.returncode == 0:
        serial_path = work_dir / "qemu.serial.log"
        if serial_path.exists():
            result.serial_log = serial_path.read_text(errors="replace")[-2000:]

    result.duration_sec = time.time() - start_time
    return result


def stop_emulation(iid: int) -> bool:
    env = _env()
    result = subprocess.run(["docker", "rm", "-f", f"iris-qemu-{iid}"], capture_output=True, text=True, env=env, timeout=15, check=False)
    return result.returncode == 0