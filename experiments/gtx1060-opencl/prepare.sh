#!/bin/sh
# Download and extract userspace packages; never install a system package or driver.
set -eu

experiment_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
state_dir=${GPU_PROBE_STATE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/fleet-lab/gtx1060-opencl}
python_command=${PYTHON:-python3}

for command_name in apt-get dpkg-deb cc ldd timeout "$python_command"; do
    command -v "$command_name" >/dev/null 2>&1 || {
        printf 'Missing prerequisite: %s\n' "$command_name" >&2
        exit 1
    }
done
if [ "$(uname -m)" != x86_64 ]; then
    printf 'This experiment has only been prepared for Linux x86_64.\n' >&2
    exit 1
fi

mkdir -p "$state_dir"
state_dir=$(CDPATH= cd -- "$state_dir" && pwd -P)
download_dir=$(mktemp -d "$state_dir/downloads.XXXXXX")
mkdir -p "$state_dir/root" "$state_dir/cache" "$state_dir/qrack-cache"

# These names match the tested Ubuntu 26.04 / Mesa 26 / LLVM 21 userspace stack.
(
    cd "$download_dir"
    apt-get download mesa-opencl-icd libllvmspirvlib21.1 libclc-21 libclc-21-dev libclang-common-21-dev
)
printf 'package\tversion\tarchitecture\n' > "$state_dir/packages.tsv"
for package in "$download_dir"/*.deb; do
    dpkg-deb --show --showformat='${Package}\t${Version}\t${Architecture}\n' "$package" >> "$state_dir/packages.tsv"
    dpkg-deb --extract "$package" "$state_dir/root"
done

library_dir=$state_dir/root/usr/lib/x86_64-linux-gnu
rusticl_library=$library_dir/libRusticlOpenCL.so.1
if [ ! -f "$rusticl_library" ]; then
    printf 'The downloaded Mesa package does not contain Rusticl.\n' >&2
    exit 1
fi
LD_LIBRARY_PATH="$library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ldd "$rusticl_library" > "$state_dir/rusticl-libraries.txt"
if grep -q 'not found' "$state_dir/rusticl-libraries.txt"; then
    cat "$state_dir/rusticl-libraries.txt" >&2
    printf 'Missing host userspace dependencies. This probe expects matching libLLVM/libclang 21; no system packages were installed.\n' >&2
    exit 1
fi

cc -shared -fPIC -Wall -Wextra -Werror -o "$state_dir/redirect.so" "$experiment_dir/path_redirect.c" -ldl
"$python_command" -m venv "$state_dir/venv"
"$state_dir/venv/bin/python" -m pip install --disable-pip-version-check -r "$experiment_dir/requirements.txt"
printf 'Prepared isolated GPU probe in %s\nRun: GPU_PROBE_STATE_DIR=%s sh %s/run.sh\n' "$state_dir" "$state_dir" "$experiment_dir"
