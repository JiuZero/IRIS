# ARM64 Arch Mapping Fix - Complete Report

## Issue Description

When using `iris emulate run firmware.bin` with automatic architecture detection, the command failed with:

```
preflight: unsupported-arch: 'aarch64' has no QEMU config 
(supported: armel, arm64, mipseb, mipsel)
```

## Root Cause Analysis

The ELF census extraction from firmware binaries returns standard architecture labels like `aarch64`, but QEMU configuration uses alternative names like `arm64`. There was no translation layer between these two naming schemes.

| Component | Returns/Uses |
|-----------|-------------|
| L1 Extraction (`rootfs_extract._census_elfs`) | `{"aarch64": N}` |
| QEMU Config (`qemu_config.py`) | `"arm64"` key |
| Preflight Check | Compares raw `aarch64` vs supported `arm64` → FAIL |

---

## Solution Implemented

### 1. Architecture Mapping in `auto.py`

Added explicit mapping dictionary to translate ELF census names to QEMU kernel labels:

```python
_ARCH_MAPPINGS = {
    "mipsel": "mipsel",
    "mipseb": "mipseb",
    "armel": "armel",
    "aarch64": "arm64",  # ELF standard name → QEMU kernel label
}

def _pick_arch_from_counter(counter):
    """Map ELF census arch → runnable kernel label."""
    known = {a: n for a, n in counter.items() if not a.startswith("unk(")}
    if not known:
        return ""
    dominant = max(known, key=known.get)
    return _ARCH_MAPPINGS.get(dominant, "")
```

### 2. CLI Integration in `cli.py`

Applied the same mapping during preflight checks and final arch selection:

```python
# Map ELF census names to QEMU kernel labels
arch_map = {"mipsel": "mipsel", "mipseb": "mipseb", "armel": "armel", "aarch64": "arm64"}
checked_arch = arch_map.get(inferred_arch, inferred_arch)
problem = preflight_arch(rootfs, checked_arch) if not force else ""
```

---

## Test Results

### Before Fix ❌

```bash
$ iris emulate run US_TES7002V1.0re_v1.0.0.86_en+cn_TD.bin
extracting rootfs from US_TES7002V1.0re_v1.0.0.86_en+cn_TD.bin ...
preflight: unsupported-arch: 'aarch64' has no QEMU config
  (override with --force)
```

### After Fix ✅

```bash
$ iris emulate run US_TES7002V1.0re_v1.0.0.86_en+cn_TD.bin
extracting rootfs from US_TES7002V1.0re_v1.0.0.86_en+cn_TD.bin ...
L3 rules matched: dev-extended-nodes, vendor-watchdog-monitor
emulating US_TES7002V1.0re_v1.0.0.86_en+cn_TD.bin arch=arm64
Creating rootfs tarball...
Tarball: 31052594 bytes
Starting emulation container iris-qemu-5255...
Copying tarball into container...
Building QEMU image for IID=5255 arch=arm64...
==== Image built: /work/scratch/5255/image.raw ====
Starting QEMU (port 8092 -> guest:80)...
```

### Stability Verification

Container `iris-qemu-5255` uptime: **> 3 minutes** (still booting at 245s)

**Serial log analysis:**
- Boot count: **1** (initial boot only) ✓
- Reboot attempts: **0** (no watchdog triggers!) ✓
- Monitor die messages: **0** (watchdog silenced) ✓

Expected boot time: ~330 seconds (based on previous arm64 channel tests)

---

## Files Changed

| File | Changes | Purpose |
|------|---------|---------|
| `src/iris/emulate/auto.py` | Added `_ARCH_MAPPINGS` dict + updated `_pick_arch_from_counter()` | Translate ELF names to QEMU labels |
| `src/iris/cli.py` | Applied arch mapping in preflight check and final arch selection | Ensure binary passes validation |

---

## Usage Examples

### Automatic Architecture Detection

```bash
# Fully automated: extract → detect arch → apply rules → emulate
iris emulate run firmware.bin

# Auto port selection
iris emulate run firmware.bin --port 0

# Override architecture manually
iris emulate run firmware.bin --arch arm64
```

### Supported Mappings

| ELF Census Name | QEMU Kernel Label | Architecture |
|-----------------|-------------------|--------------|
| `mipsel` | `mipsel` | MIPS little-endian |
| `mipseb` | `mipseb` | MIPS big-endian |
| `armel` | `armel` | ARM EABI (32-bit) |
| `aarch64` | `arm64` | ARM 64-bit (AArch64) |

---

## Related Fixes

This change works in conjunction with:
- ✅ `vendor-watchdog-monitor.yaml` rule (silences gp8/swg watchdog)
- ✅ `iris_net_fix.sh` improvements (web probe prioritizes process checks)
- ✅ Binary file guard in `engine.py` (prevents ELF corruption)

---

*Report generated at: 2026-09-27*
*Tested with: US_TES7002V1.0re_v1.0.0.86_en+cn_TD.bin (GPON OLT, aarch64)*
