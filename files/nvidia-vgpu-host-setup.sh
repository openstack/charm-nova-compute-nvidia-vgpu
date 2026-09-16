#!/bin/bash
# NVIDIA vGPU Host Setup (VFIO mode), run by nvidia-vgpu-host-setup.service.
#
# Enables SR-IOV VFs on NVIDIA GPUs and applies the vGPU types recorded by
# the charm in /etc/nvidia-vgpu-state.yaml. The charm never changes VF types
# at runtime; changes are applied here, at boot.

set -eu -o pipefail

SYS_PCI_BASE="${SYS_PCI_BASE:-/sys/bus/pci/devices}"
SRIOV_MANAGE="${SRIOV_MANAGE:-/usr/lib/nvidia/sriov-manage}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
PYTHON="${PYTHON:-/usr/bin/python3}"
FAILURE_FILE="${FAILURE_FILE:-/run/nvidia-vgpu-host-setup-failed}"
MAX_WAIT="${MAX_WAIT:-60}"
: "${VGPU_CHARM_DIR:?VGPU_CHARM_DIR must point to the charm directory}"
export SYS_PCI_BASE FAILURE_FILE

log() {
    echo "[$(date '+%Y-%m-%dT%H:%M:%S')] $*" >&2
}

fail() {
    log "FATAL: $*"
    printf '%s\n' "$*" > "$FAILURE_FILE"
    exit 1
}

trap '[ -s "$FAILURE_FILE" ] || fail "unexpected error on line $LINENO"' ERR

rm -f "$FAILURE_FILE"

sriov_gpus() {
    local device
    for device in "$SYS_PCI_BASE"/*; do
        [ -e "$device/physfn" ] && continue
        [ "$(cat "$device/vendor" 2>/dev/null)" = "0x10de" ] || continue
        case "$(cat "$device/class" 2>/dev/null)" in
            0x0302*) ;;
            *) continue ;;
        esac
        [ "$(cat "$device/sriov_totalvfs" 2>/dev/null || echo 0)" -gt 0 ] \
            || continue
        basename "$device"
    done
}

GPUS=$(sriov_gpus)
if [ -z "$GPUS" ]; then
    log "No SR-IOV capable NVIDIA GPU found, nothing to do."
    exit 0
fi

log "Waiting for nvidia-vgpu-mgr.service..."
wait_count=0
until "$SYSTEMCTL" is-active --quiet nvidia-vgpu-mgr.service; do
    wait_count=$((wait_count + 1))
    [ "$wait_count" -le "$MAX_WAIT" ] || \
        fail "nvidia-vgpu-mgr.service not active after ${MAX_WAIT}s"
    sleep 1
done

[ -x "$SRIOV_MANAGE" ] || fail "$SRIOV_MANAGE not found"
for gpu in $GPUS; do
    if [ "$(cat "$SYS_PCI_BASE/$gpu/sriov_numvfs" 2>/dev/null || echo 0)" \
            -eq 0 ]; then
        log "Enabling VFs on $gpu..."
        "$SRIOV_MANAGE" -e "$gpu" || fail "Failed to enable VFs on $gpu"
    fi
done

log "Applying vGPU types..."
if ! PYTHONPATH="$VGPU_CHARM_DIR/venv:$VGPU_CHARM_DIR/src" \
        "$PYTHON" -m vfio_utils; then
    [ -s "$FAILURE_FILE" ] || \
        printf '%s\n' "vGPU type assignment failed" > "$FAILURE_FILE"
    log "FATAL: $(head -n 1 "$FAILURE_FILE")"
    exit 1
fi

log "vGPU host setup completed."
