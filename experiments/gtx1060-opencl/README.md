# GTX 1060 OpenCL circuit probe

This experiment executes the EV demo's quantum circuits on the connected
GeForce GTX 1060 3 GB using Qrack, Mesa Rusticl, and the existing Nouveau kernel
driver. It includes a GPU-only Bell-state check, a bit-order check, and both
recorded EV QAOA circuits. Each EV circuit uses 2,048 freshly sampled shots.
The angles come from the verified recordings; this does not run a new QAOA
optimization or connect the GPU to the dashboard or generated agent workers.

Qrack's CPU, hybrid, tensor-network, and stabilizer engines are disabled for
the circuit executions. NumPy independently checks the GPU statevectors, and
the exact classical solver independently checks the recovered schedule costs.
The probe requires one OpenCL platform exposing one NVIDIA GPU and fails when
that requirement is not met. It never substitutes a CPU simulator.

## Reproduce on the tested host

The tested platform is Ubuntu 26.04, Linux 7.0.0-43-generic, Python 3.12, and
the Nouveau driver. The host already has the OpenCL loader, LLVM 21, Clang 21,
and a C compiler. Preparation checks the extracted Rusticl library's remaining
system dependencies. Other distributions, GPUs, and Mesa/LLVM versions need
their own validation.

From the repository root:

```sh
# This state directory stays outside the checkout.
export GPU_PROBE_STATE_DIR=/tmp/fleet-gpu-probe-reproduction
PYTHON=python3.12 sh experiments/gtx1060-opencl/prepare.sh
sh experiments/gtx1060-opencl/run.sh --output "$GPU_PROBE_STATE_DIR/results.json"
```

Without `GPU_PROBE_STATE_DIR`, the scripts use
`${XDG_CACHE_HOME:-$HOME/.cache}/quantumloop/gtx1060-opencl`. Existing environments
under the former `fleet-lab` directory are reused automatically. Preparation downloads
Ubuntu userspace packages from the configured APT repositories, extracts them
into that directory, records their exact versions, creates an isolated Python
environment, and compiles a small path-redirection helper. It does not install
system packages, replace a kernel driver, change boot configuration, or reboot.
The Python dependencies are pinned in [requirements.txt](requirements.txt).

Rusticl expects its libclc and Clang resource files at system paths. The
temporary helper redirects those specific lookups into the extracted package
directory. The runner scopes that helper, OpenCL selection, and caches to its
child process, caps the probe's runtime and Qrack allocation, and disables core
dumps. Do not export its `LD_PRELOAD` setting into a normal application session.
Only source code is checked in; downloaded packages, shared libraries, Python
environments, and caches stay outside the repository.

The use of PyQrack's low-level initializer is deliberate: its high-level
constructor enables CPU/GPU hybrid execution. The probe guards the tested
PyQrack version because that initializer is an internal interface. It also
forces the wheel's bundled native library and single-precision ABI, checks
which library loaded, and records its hash in the evidence.

## Evidence and limits

[The captured GPU results](results/2026-11-18-gtx1060.json) include the actual
OpenCL device and driver, dependency versions, measured counts, statevector
errors, and the classical references. Both the 35% and 20% recordings recover
the exact classical costs of approximately $15.78 and $58.37 respectively.
Shot counts can differ between runs. The JSON records the probe source hash;
the [userspace package manifest](results/2026-11-18-userspace-packages.tsv)
records the extracted Ubuntu package versions used for this capture.

This validates small circuit execution and numerical agreement. It does not
establish a GPU speedup, maximum circuit size, or a supported production
deployment. There is no visible UI change; the JSON evidence is the direct
verification artifact. Existing app instances and model configuration are
unaffected.

Quantum Rings' current GPU backend requires a newer NVIDIA architecture and
at least 4 GB of VRAM, so this experiment uses an alternate simulator. An app
integration would need a Qrack adapter and corresponding generated-worker
instructions, device metadata, and cache invalidation.

References: [Qrack](https://github.com/unitaryfoundation/qrack),
[Mesa Rusticl](https://docs.mesa3d.org/rusticl.html),
[Quantum Rings GPU requirements](https://www.quantumrings.com/doc/install/system_requirements.html).
