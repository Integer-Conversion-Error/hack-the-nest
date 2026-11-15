"""Run one checked EV dispatch scenario and write its result to stdout."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--van-2-initial-charge", type=int, choices=(20, 35), default=35)
    parser.add_argument("--engine", choices=("auto", "local", "quantum-rings"), default="auto")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    if args.env_file is not None and not args.env_file.is_file():
        parser.error("The specified environment file does not exist.")

    try:
        with redirect_stdout(sys.stderr):
            from dotenv import load_dotenv

            env_file = args.env_file or Path(__file__).resolve().parents[2] / ".env"
            load_dotenv(env_file)

            from .runtime import LocalBackend, QuantumRingsBackend, quantum_rings_configured
            from .solver import solve

            configured = quantum_rings_configured()
            if args.engine == "quantum-rings" and not configured:
                parser.error("Quantum Rings requires QR_KEY and QR_ACCOUNT.")
            use_quantum_rings = args.engine == "quantum-rings" or (args.engine == "auto" and configured)
            backend = QuantumRingsBackend() if use_quantum_rings else LocalBackend()
            result = solve(args.van_2_initial_charge, backend)
            payload = json.dumps(result, allow_nan=False)
    except ImportError as error:
        print(f"Missing solver dependency: {error.name}.", file=sys.stderr)
        return 1
    except Exception as error:
        # Provider exceptions can contain credentials. Keep raw exceptions out of subprocess logs.
        print(f"EV dispatch solver failed ({type(error).__name__}).", file=sys.stderr)
        return 1

    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
