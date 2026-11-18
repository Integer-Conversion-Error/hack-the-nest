"""Bounded PNG circuit evidence captured from a worker or rebuilt from its parameters."""

import base64
import binascii
import hashlib
import io
import json
import math
import os
import struct
import zlib
from functools import lru_cache
from threading import Lock

PNG_PREFIX = "data:image/png;base64,"
MAX_IMAGE_BYTES = 2_000_000
MAX_IMAGE_PIXELS = 12_000_000
MAX_GATES = 512
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_RENDER_LOCK = Lock()


def _valid_png(data):
    if not isinstance(data, bytes) or not 45 <= len(data) <= MAX_IMAGE_BYTES or not data.startswith(_PNG_SIGNATURE):
        return False
    position, header, compressed, ended = 8, None, [], False
    while position + 12 <= len(data):
        length = struct.unpack_from(">I", data, position)[0]
        end = position + 12 + length
        if end > len(data):
            return False
        kind = data[position + 4:position + 8]
        body = data[position + 8:end - 4]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != struct.unpack_from(">I", data, end - 4)[0]:
            return False
        if header is None:
            if kind != b"IHDR" or length != 13:
                return False
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IHDR":
            return False
        if kind == b"IDAT":
            compressed.append(body)
        if kind == b"IEND":
            ended = length == 0 and end == len(data)
            break
        position = end
    if not header or not compressed or not ended:
        return False
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if (not channels or depth not in depths[color] or width < 1 or height < 1
            or width * height > MAX_IMAGE_PIXELS or compression or filtering or interlace):
        return False
    expected = (((width * channels * depth + 7) // 8) + 1) * height
    if expected > 64_000_000:
        return False
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(b"".join(compressed), expected + 1)
        return len(decoded) == expected and decoder.eof and not decoder.unused_data
    except zlib.error:
        return False


def valid_png_image(value):
    """Only inline PNGs are accepted; no remote URLs or arbitrary file references."""
    if not isinstance(value, str) or not value.startswith(PNG_PREFIX):
        return False
    encoded = value[len(PNG_PREFIX):]
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        return False
    try:
        return _valid_png(base64.b64decode(encoded, validate=True))
    except (ValueError, binascii.Error):
        return False


def _image(data):
    return PNG_PREFIX + base64.b64encode(data).decode("ascii") if _valid_png(data) else None


def captured_circuit(workspace, output, *, saved=False):
    """Capture this attempt's fixed PNG, optionally checking saved default evidence."""
    quantum = output.get("quantum") if isinstance(output, dict) else None
    diagram = quantum.get("circuit_diagram") if isinstance(quantum, dict) else None
    if not isinstance(diagram, dict) or diagram.get("file") != "circuit.png":
        return None
    try:
        path = workspace.path / "circuit.png"
        if path.resolve() != path:
            return None
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if saved:
            report_path = workspace.path / "test.json"
            if report_path.resolve() != report_path:
                return None
            report = json.loads(report_path.read_bytes())
            default = report["runs"][0]
            if (default.get("controls") != {} or default.get("exit_code") != 0 or default.get("timed_out") is not False
                    or default.get("issues") != []
                    or json.dumps(default.get("results"), sort_keys=True) != json.dumps(output, sort_keys=True)
                    or report.get("circuit_digest") != hashlib.sha256(data).hexdigest()):
                return None
        image = _image(data)
    except (OSError, ValueError, KeyError, IndexError, TypeError, RuntimeError):
        return None
    if image is None:
        return None
    return {
        "title": "Executed quantum circuit", "image": image,
        "description": "Captured from this verified solver run.",
    }


class _TraceCircuit:
    def __init__(self, width):
        self.width, self.operations = width, []

    def _add(self, gate, qubits, angle=None):
        if len(self.operations) >= MAX_GATES:
            raise ValueError("Circuit diagram exceeds the gate limit.")
        self.operations.append((gate, tuple(qubits), angle))

    def h(self, qubit):
        self._add("H", [qubit])

    def rz(self, angle, qubit):
        self._add("Rz", [qubit], float(angle))

    def rx(self, angle, qubit):
        self._add("Rx", [qubit], float(angle))

    def cx(self, control, target):
        self._add("CX", [control, target])

    def measure_all(self):
        for qubit in range(self.width):
            self._add("M", [qubit])


class _TraceBackend:
    def circuit(self, width):
        return _TraceCircuit(width)


def _curated_trace(charge, gammas, betas, mapping):
    # Use the solver's own circuit builder, not an illustrative circuit template.
    from backend.ev_dispatch.dispatch_model import generate_feasible_options
    from backend.ev_dispatch.qaoa import build_qaoa_circuit
    from backend.ev_dispatch.qubo_ising import build_qubo, qubo_to_ising

    options, _ = generate_feasible_options(charge)
    expected = tuple((f"q{option.qubit_index}", option.option_id) for option in options)
    if mapping != expected:
        raise ValueError("Circuit mapping does not match the curated problem.")
    qubo, offset = build_qubo(options)
    _, h, interactions = qubo_to_ising(qubo, offset)
    circuit = build_qaoa_circuit(_TraceBackend(), len(options), h, interactions, list(gammas), list(betas))
    return circuit


def _render_trace(circuit, mapping):
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame

    # Place commuting gates in the same column while reserving the complete span
    # of a controlled gate, so every shared-wire gate retains its actual order.
    columns, last = [], [-1] * circuit.width
    for operation in circuit.operations:
        gate, qubits, _ = operation
        span = range(min(qubits), max(qubits) + 1)
        column = max(last[index] for index in span) + 1
        while len(columns) <= column:
            columns.append([])
        columns[column].append(operation)
        for index in span:
            last[index] = column
    per_row, cell, left, top, pitch = 12, 82, 155, 48, 48
    rows = max(1, math.ceil(len(columns) / per_row))
    row_height = circuit.width * pitch + 40
    width, height = left + per_row * cell + 30, rows * row_height + 28
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Circuit diagram exceeds the image limit.")
    with _RENDER_LOCK:
        pygame.font.init()
        font = pygame.font.Font(None, 21)
        small = pygame.font.Font(None, 17)
        surface = pygame.Surface((width, height))
        surface.fill("#ffffff")
        for row in range(rows):
            y0 = row * row_height + top
            caption = font.render(f"Circuit segment {row + 1} / {rows}", True, "#536168")
            surface.blit(caption, (14, y0 - 35))
            for qubit, (_, label) in enumerate(mapping):
                y = y0 + qubit * pitch
                surface.blit(small.render(f"q{qubit}  {label}", True, "#202b2e"), (12, y - 7))
                pygame.draw.line(surface, "#aab6b0", (left - 10, y), (width - 16, y), 2)
            for offset, operations in enumerate(columns[row * per_row:(row + 1) * per_row]):
                x = left + offset * cell + cell // 2
                for gate, qubits, angle in operations:
                    y = y0 + qubits[-1] * pitch
                    if gate == "CX":
                        control_y = y0 + qubits[0] * pitch
                        pygame.draw.line(surface, "#1e5147", (x, control_y), (x, y), 2)
                        pygame.draw.circle(surface, "#1e5147", (x, control_y), 5)
                        pygame.draw.circle(surface, "#1e5147", (x, y), 11, 2)
                        pygame.draw.line(surface, "#1e5147", (x - 8, y), (x + 8, y), 2)
                        pygame.draw.line(surface, "#1e5147", (x, y - 8), (x, y + 8), 2)
                    else:
                        box = pygame.Rect(x - 34, y - 18, 68, 36)
                        pygame.draw.rect(surface, "#e7efeb" if gate != "M" else "#edf0f5", box, border_radius=3)
                        pygame.draw.rect(surface, "#1e5147", box, 1, border_radius=3)
                        label = font.render(gate, True, "#202b2e")
                        surface.blit(label, label.get_rect(center=(x, y - 6 if angle is not None else y)))
                        if angle is not None:
                            label = small.render(f"{angle:.4g}", True, "#536168")
                            surface.blit(label, label.get_rect(center=(x, y + 9)))
        buffer = io.BytesIO()
        pygame.image.save(surface, buffer, "circuit.png")
        return buffer.getvalue()


@lru_cache(maxsize=16)
def _reconstructed_image(charge, gammas, betas, mapping):
    return _image(_render_trace(_curated_trace(charge, gammas, betas, mapping), mapping))


def reconstructed_circuit(output, provenance):
    """Rebuild only a recognized curated circuit from that result's own parameters."""
    try:
        quantum = output["quantum"]
        charge = output["controls"]["van_2_initial_charge_pct"]
        gammas, betas = tuple(quantum["optimal_gammas"]), tuple(quantum["optimal_betas"])
        width, layers = quantum["qubits"], quantum["p_layers"]
        if (type(width) is not int or not 1 <= width <= 32 or type(layers) is not int or not 1 <= layers <= 8
                or len(gammas) != layers or len(betas) != layers
                or any(type(angle) not in (int, float) or not math.isfinite(angle) for angle in (*gammas, *betas))):
            return None
        mapping = tuple((f"q{index}", quantum["qubit_mapping"][f"q{index}"]) for index in range(width))
        if len(quantum["qubit_mapping"]) != width:
            return None
        image = _reconstructed_image(charge, gammas, betas, mapping)
    except (ImportError, OSError, ValueError, KeyError, TypeError, OverflowError, RuntimeError):
        return None
    if image is None:
        return None
    return {
        "title": "QAOA quantum circuit", "image": image,
        "description": "Reconstructed from recorded parameters." if provenance == "recorded"
                       else "Reconstructed from this solver run's parameters.",
    }
