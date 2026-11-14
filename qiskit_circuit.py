import os

from dotenv import load_dotenv

load_dotenv()  # reads QR_KEY / QR_ACCOUNT from the local .env file (git-ignored)

from qiskit import QuantumCircuit, transpile
from QuantumRingsLib import QuantumRingsProvider
from quantumrings.toolkit.qiskit import QrBackendV2

token = os.environ.get("QR_KEY", "")
account = os.environ.get("QR_ACCOUNT", "")
if not token or not account:
    raise SystemExit("Missing QR_KEY or QR_ACCOUNT in .env.")

print("Connecting to Quantum Rings via the Qiskit bridge...")
provider = QuantumRingsProvider(token=token, name=account)
backend = QrBackendV2(provider)
print("Connected. Running 3-qubit Qiskit circuit...")

qc = QuantumCircuit(3)
qc.h(0)
qc.cx(0, 1)
qc.cx(1, 2)
qc.measure_all()

job = backend.run(transpile(qc, backend), shots=1000)
print(job.result().get_counts())
