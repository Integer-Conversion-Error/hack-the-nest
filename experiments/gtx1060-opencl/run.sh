#!/bin/sh
# All library overrides apply only to the bounded probe process.
set -eu

experiment_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repository_dir=$(CDPATH= cd -- "$experiment_dir/../.." && pwd -P)
state_dir=${GPU_PROBE_STATE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/fleet-lab/gtx1060-opencl}
if [ ! -x "$state_dir/venv/bin/python" ] || [ ! -f "$state_dir/redirect.so" ]; then
    printf 'Prepare the isolated state first: sh %s/prepare.sh\n' "$experiment_dir" >&2
    exit 1
fi
state_dir=$(CDPATH= cd -- "$state_dir" && pwd -P)
ulimit -c 0

exec env \
    GPU_PROBE_EXTRACT_ROOT="$state_dir/root" \
    OCL_ICD_VENDORS="$state_dir/root/etc/OpenCL/vendors" \
    LD_LIBRARY_PATH="$state_dir/root/usr/lib/x86_64-linux-gnu" \
    LD_PRELOAD="$state_dir/redirect.so" \
    RUSTICL_ENABLE=nouveau \
    MESA_SHADER_CACHE_DIR="$state_dir/cache" \
    XDG_CACHE_HOME="$state_dir/cache" \
    QRACK_OCL_PATH="$state_dir/qrack-cache" \
    QRACK_OCL_DEFAULT_DEVICE=0 \
    QRACK_QPAGER_DEVICES=0 \
    QRACK_QUNITMULTI_DEVICES=0 \
    QRACK_MAX_ALLOC_MB=128 \
    PYTHONPATH="$repository_dir/quantum-playground:$repository_dir" \
    timeout --kill-after=2 60 "$state_dir/venv/bin/python" -u "$experiment_dir/probe.py" "$@"
