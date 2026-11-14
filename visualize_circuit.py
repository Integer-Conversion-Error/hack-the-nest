import os

from dotenv import load_dotenv

load_dotenv()  # reads QR_KEY / QR_ACCOUNT from the local .env file (git-ignored)

import matplotlib

matplotlib.use("Agg")  # save images to files without opening a window
import matplotlib.pyplot as plt
from qiskit import QuantumCircuit, transpile
from qiskit.visualization import plot_histogram
from QuantumRingsLib import QuantumRingsProvider
from quantumrings.toolkit.qiskit import QrBackendV2

token = os.environ.get("QR_KEY", "")
account = os.environ.get("QR_ACCOUNT", "")
if not token or not account:
    raise SystemExit("Missing QR_KEY or QR_ACCOUNT in .env.")

provider = QuantumRingsProvider(token=token, name=account)
backend = QrBackendV2(provider)

# 3-qubit entangled (GHZ) state
qc = QuantumCircuit(3)
qc.h(0)
qc.cx(0, 1)
qc.cx(1, 2)
qc.measure_all()

shots = 1000
job = backend.run(transpile(qc, backend), shots=shots)
counts = job.result().get_counts()
print(counts)

os.makedirs("output", exist_ok=True)

# 1. Circuit diagram
qc.draw("mpl").savefig("output/circuit.png", dpi=150, bbox_inches="tight")

# 2. Measurement results, with every possible outcome shown so the empty ones stand out
all_outcomes = {format(i, "03b"): counts.get(format(i, "03b"), 0) for i in range(8)}
fig = plot_histogram(
    all_outcomes,
    title=f"3-qubit entangled state on Quantum Rings ({shots} shots)",
    figsize=(9, 5),
    color="#4C72B0",
)
fig.savefig("output/results.png", dpi=150, bbox_inches="tight")
plt.close("all")

print("Saved output/circuit.png and output/results.png")
