import os

from dotenv import load_dotenv

load_dotenv()  # reads QR_KEY / QR_ACCOUNT from the local .env file (git-ignored)

from QuantumRingsLib import QuantumRegister, ClassicalRegister, QuantumCircuit
from QuantumRingsLib import QuantumRingsProvider
from QuantumRingsLib import job_monitor

# Set these in the terminal before running (see instructions), so no secrets live in the file.
token = os.environ.get("QR_KEY", "")
account = os.environ.get("QR_ACCOUNT", "")

print(f"Key loaded: {'yes' if token else 'NO'} ({len(token)} characters)")
print(f"Account name: {account!r}")
if not token or not account:
    raise SystemExit("Missing QR_KEY or QR_ACCOUNT. Set them in this terminal first.")

print("Connecting to Quantum Rings...")
provider = QuantumRingsProvider(token=token, name=account)
backend = provider.get_backend("scarlet_quantum_rings")
print("Connected. Running circuit...")

numberofqubits = 50
shots = 100

q = QuantumRegister(numberofqubits, "q")
c = ClassicalRegister(numberofqubits, "c")
qc = QuantumCircuit(q, c)

qc.h(0)
for i in range(qc.num_qubits - 1):
    qc.cnot(i, i + 1)

qc.measure_all()

job = backend.run(qc, shots=shots)
job_monitor(job)

counts = job.result().get_counts()
print(counts)
