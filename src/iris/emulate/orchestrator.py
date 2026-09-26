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

from iris.emulate.qemu_config import get_config


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


def emulate_firmware(
    rootfs_dir: Path,
    arch: str,
    iid: int,
    scratch_dir: Path,
    host_port: int = 8080,
    timeout_sec: int = 120,
    docker_image: str = "",
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

    try:
        if not tarball_path.exists():
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
        "-p", f"{host_port}:8080",
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
        ["docker", "exec", "-d", container_name, "bash", "/work/scripts/run_qemu.sh", str(iid), arch, "8080"],
        timeout=15,
    )
    if qemu_result.returncode != 0:
        result.error = f"QEMU start failed: {qemu_result.stderr}"
        result.duration_sec = time.time() - start_time
        _run(["docker", "rm", "-f", container_name])
        return result

    print(f"Waiting for firmware to boot (timeout {timeout_sec}s)...")
    boot_deadline = time.time() + timeout_sec
    while time.time() < boot_deadline:
        time.sleep(5)
        elapsed = int(time.time() - start_time)
        check_result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "3", f"http://localhost:{host_port}"],
            capture_output=True, text=True, timeout=10, check=False,
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