"""Authoritative inputs for plain-English drafts of the seven local workbenches.

Model text is data, never code.  These contracts deliberately describe the
existing bounded editors rather than a general-purpose optimization language.
"""

from __future__ import annotations

import copy
import json
import math
import re
from fractions import Fraction

MAX_DRAFT_BYTES = 65_536


def _number(minimum, maximum, *, integer=False, cents=False):
    result = {"type": "integer" if integer else "number", "minimum": minimum, "maximum": maximum}
    if cents:
        result["multipleOf"] = 0.01
    return result


def _text(maximum=40, *, pattern=None):
    result = {"type": "string", "minLength": 1, "maxLength": maximum}
    if pattern is not None:
        result["pattern"] = pattern
    return result


def _identifier(prefix):
    return _text(4, pattern="^" + prefix + "[1-9][0-9]{0,2}$")


def _array(items, minimum, maximum=None):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": minimum if maximum is None else maximum}


def _object(properties, *, optional=()):
    return {"type": "object", "properties": properties,
            "required": [key for key in properties if key not in optional], "additionalProperties": False}


_BOOLEAN = {"type": "boolean"}
_AVAILABILITY = _array(_BOOLEAN, 3)
_POSITION = {"type": "array", "prefixItems": [_number(-180, 180), _number(-85, 85)],
             "items": False, "minItems": 2, "maxItems": 2}
_PAIR_PATTERN = r"^U[1-9][0-9]{0,2}\|U[1-9][0-9]{0,2}$"

_CONTRACTS = {
    "facility": {
        "title": "Facility placement",
        "guide": (
            "Choose 1–10 candidate hubs and exactly three demand zones, all with supplied [longitude, latitude] coordinates. "
            "IDs H1…H999 identify hubs; D1…D999 identify zones, with unique IDs. Opening budget and each opening cost are CAD/day; "
            "capacity and zone weight are whole visits/day. minutes is the maximum OUTBOUND driving time for a covered zone. "
            "Road cost uses outbound plus return trips for every covered visit at hourlyCost CAD/hour. maxSites limits opened hubs. "
            "Each covered zone is wholly assigned to one enabled hub; demand cannot be split. With requireFullCoverage=false "
            "(the default), maximize covered visits first, then minimize daily opening and road cost. true requires every positive-demand "
            "zone to be covered, otherwise the solver reports infeasible. Clarify hard full coverage versus best effort if the request is ambiguous. "
            "Never invent coordinates, travel durations, road matrices or route geometry. For named places without supplied coordinates, "
            "ask for coordinates or clarification. The server obtains authoritative road times after import. Do not include durations, "
            "snapshots, routing URLs or server-derived fields. This editor has no facility construction timing, split demand, delivery time windows, or distance objective."
        ),
        "schema": _object({
            "budget": _number(0, 10000), "maxSites": _number(1, 10, integer=True),
            "minutes": _number(0.1, 120), "hourlyCost": _number(0, 200),
            "requireFullCoverage": {"type": "boolean", "default": False},
            "sites": _array(_object({"id": _identifier("H"), "name": _text(60), "enabled": _BOOLEAN,
                                      "cost": _number(0, 10000), "capacity": _number(0, 1000, integer=True), "position": _POSITION}), 1, 10),
            "demand": _array(_object({"id": _identifier("D"), "name": _text(60),
                                       "weight": _number(0, 1000, integer=True), "position": _POSITION}), 3),
        }, optional=("requireFullCoverage",)),
    },
    "workforce": {
        "title": "Workforce scheduling",
        "guide": (
            "Schedule 1–8 named workers (W1…W999). There are exactly three fixed eight-hour shifts, in this order: "
            "Early 06:00–14:00, Day 10:00–18:00, Late 14:00–22:00. coverage is three exact whole-person staffing requirements. "
            "available is three booleans in the same order; rate is USD/hour. Minimize total wages. maxHours is either 8 or 16 per worker/day. "
            "Overlapping shifts cannot share a worker; Early and Late can be assigned together at maxHours=16. "
            "This is one day, with no additional rest, skills, overtime premium, contractual minimum hours, or fairness rules. "
            "Do not translate a multi-day, other-shift-duration, or skill-qualified request into this model silently."
        ),
        "schema": _object({"coverage": _array(_number(0, 3, integer=True), 3), "maxHours": {"type": "integer", "enum": [8, 16]},
                           "workers": _array(_object({"id": _identifier("W"), "name": _text(),
                                                       "rate": _number(1, 100), "available": _AVAILABILITY}), 1, 8)}),
    },
    "production": {
        "title": "Production scheduling",
        "guide": (
            "Schedule 1–4 named jobs (J1…J999), each with 1–2 ordered, non-preemptive operations. "
            "Only M1=Cutting and M2=Assembly exist; machine assignment is fixed for each operation, not chosen by the optimizer. "
            "Each machine processes one operation at a time. release and duration are integer hours from shift start. "
            "horizon is the hard planning-window end in hours. Minimize makespan (last job completion); an optimum beyond horizon is infeasible. "
            "Job operations may use the same machine. No setup times, due-date penalties, alternative machines, machine outages, "
            "precedence between jobs or multi-machine operations are supported. Ask rather than dropping such requirements."
        ),
        "schema": _object({"horizon": _number(1, 32, integer=True),
                           "jobs": _array(_object({"id": _identifier("J"), "name": _text(), "release": _number(0, 8, integer=True),
                                                    "operations": _array(_object({"machine": {"type": "string", "enum": ["M1", "M2"]},
                                                                                   "duration": _number(1, 8, integer=True)}), 1, 2)}), 1, 4)}),
    },
    "timetable": {
        "title": "Timetabling",
        "guide": (
            "Place 1–6 named classes (C1…C999) in 1–3 named rooms (R1…R999). Each class meets exactly once. "
            "The three fixed periods are 09:00–11:00, 11:00–13:00, 14:00–16:00, indexed 0,1,2. "
            "slotCount offers only the first 1–3 periods. available is a three-boolean mask; preferred is one period index. "
            "teacher and cohort are arbitrary plain names: equal names identify the same resource and cannot overlap. "
            "Rooms cannot overlap; open rooms must have enough whole seats for students. Minimize the sum of absolute period-index "
            "deviations from preferred, then unused seats in booked rooms. It is valid to prefer a period outside slotCount, at a penalty. "
            "No multi-day weeks, repeated meetings, variable class lengths, equipment, travel between rooms, or multiple teachers/cohorts per class."
        ),
        "schema": _object({"slotCount": _number(1, 3, integer=True),
                           "rooms": _array(_object({"id": _identifier("R"), "name": _text(), "capacity": _number(1, 60, integer=True),
                                                     "open": _BOOLEAN}), 1, 3),
                           "courses": _array(_object({"id": _identifier("C"), "name": _text(), "teacher": _text(), "cohort": _text(),
                                                       "students": _number(1, 60, integer=True), "preferred": _number(0, 2, integer=True),
                                                       "available": _AVAILABILITY}), 1, 6)}),
    },
    "energy": {
        "title": "Energy scheduling",
        "guide": (
            "Optimize one battery over 1–12 consecutive fixed THREE-HOUR intervals starting at hour zero (8 intervals = 24h). "
            "For interval i use the exact label 00–03, 03–06, …, 33–36, with an en dash. load and solar are total interval kWh, "
            "not kW; price and feedIn are CAD/kWh. capacity, initial and reserve are integer kWh (0–40), with reserve<=initial<=capacity. "
            "power and gridLimit are kW limits on interval-average charge/discharge and grid imports; efficiency is ROUND-TRIP percent. "
            "cycling is CAD per kWh added to or removed from battery state. Minimize imported electricity cost minus export revenue plus cycling cost. "
            "The ending charge MUST equal initial. Battery states use 1kWh resolution; solar surplus and battery discharge may be exported. "
            "Zero battery capacity or power is allowed. No hourly/half-hourly intervals, demand charges, intrainterval peaks, degradation capacity, "
            "export limit, multiple batteries or arbitrary final-charge targets. Never relabel different time steps as three-hour data."
        ),
        "schema": _object({"capacity": _number(0, 40, integer=True), "initial": _number(0, 40, integer=True),
                           "reserve": _number(0, 40, integer=True), "power": _number(0, 12), "gridLimit": _number(0, 20),
                           "efficiency": _number(50, 100), "feedIn": _number(0, 2), "cycling": _number(0, 1),
                           "intervals": _array(_object({"label": _text(5, pattern=r"^[0-9]{2}–[0-9]{2}$"),
                                                         "load": _number(0, 100), "solar": _number(0, 100), "price": _number(0, 2)}), 1, 12)}),
    },
    "projects": {
        "title": "Project selection",
        "guide": (
            "Choose among 1–14 projects (P1…P999) under one USD budget. budget and cost have at most two decimal places. "
            "value is a whole benefit score, not revenue or a percentage; maximize total value, then minimize spending. "
            "required is a hard funding requirement. requires is '' or one DIFFERENT existing project ID, and means funding implication "
            "(funding this project also requires funding that one). Mutual funding dependencies are allowed; this is not a timing model. "
            "No multiple prerequisites per project, exclusion groups, recurring annual budgets, project deadlines, resource calendars, or benefit interactions."
        ),
        "schema": _object({"budget": _number(0, 10000000, cents=True),
                           "projects": _array(_object({"id": _identifier("P"), "name": _text(60), "cost": _number(0, 10000000, cents=True),
                                                        "value": _number(0, 1000, integer=True), "required": _BOOLEAN,
                                                        "requires": {"type": "string", "maxLength": 4, "pattern": r"^(?:|P[1-9][0-9]{0,2})$"}}), 1, 14)}),
    },
    "teams": {
        "title": "Team formation",
        "guide": (
            "Partition 2–10 named people (U1…U999) into 2 or 3 nonempty teams; team sizes differ by at most one. "
            "skills has exactly three integer levels 0–3, ordered Engineering, Design, Presentation. minimum_skill is the minimum "
            "SUM in EACH of those skills for EVERY team. compatibility maps lexical-ID-sorted pair keys such as U1|U2 to integer affinity "
            "0–10 (larger is better); for U10 and U2 use U10|U2. Scores must reference two distinct supplied people. "
            "Omitted pairs default to 5, the existing solver default; state this assumption if filling sparse ratings. "
            "Maximize within-team pair affinity, then weakest skill coverage, then minimize skill imbalance. "
            "There are no hard together/apart constraints, leaders, roles, team-specific requirements, or exact custom team sizes. "
            "A compatibility score of zero is a SOFT preference, never a ban; ask or report unsupported for hard pair rules."
        ),
        "schema": _object({"people": _array(_object({"id": _identifier("U"), "name": _text(),
                                                      "skills": _array(_number(0, 3, integer=True), 3)}), 2, 10),
                           "compatibility": {"type": "object", "patternProperties": {_PAIR_PATTERN: _number(0, 10, integer=True)},
                                             "additionalProperties": False},
                           "team_count": {"type": "integer", "enum": [2, 3]}, "minimum_skill": _number(0, 15, integer=True)}),
    },
}


def model_contracts():
    """Return model-facing semantic guides and JSON Schemas for all domains."""
    return copy.deepcopy(_CONTRACTS)


def domain_contract(domain):
    """Return one guide/schema pair, rejecting unknown domain IDs."""
    if not isinstance(domain, str) or domain not in _CONTRACTS:
        raise ValueError("Choose a supported domain: " + ", ".join(_CONTRACTS) + ".")
    return copy.deepcopy(_CONTRACTS[domain])


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _plain(value, path):
    _require(not any(ord(char) < 32 or ord(char) == 127 or char in "<>" or "\u202a" <= char <= "\u202e"
                     or "\u2066" <= char <= "\u2069" or 0xD800 <= ord(char) <= 0xDFFF for char in value),
             path + " must be plain text without markup or control characters.")
    return value.strip()


def _normalize(value, schema, path):
    kind = schema["type"]
    if kind == "object":
        _require(isinstance(value, dict) and all(isinstance(key, str) for key in value), path + " must be an object.")
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        missing = set(schema.get("required", ())) - set(value)
        _require(not missing, path + " is missing: " + ", ".join(sorted(missing)) + ".")
        result = {}
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is None:
                child_schema = next((definition for pattern, definition in patterns.items() if re.fullmatch(pattern, key)), None)
            _require(child_schema is not None, path + " has an unsupported field: " + key + ".")
            result[key] = _normalize(item, child_schema, path + "." + key)
        for key, definition in properties.items():
            if key not in result and "default" in definition:
                result[key] = copy.deepcopy(definition["default"])
        return result
    if kind == "array":
        _require(isinstance(value, list), path + " must be an array.")
        _require(schema["minItems"] <= len(value) <= schema["maxItems"],
                 path + f" must contain {schema['minItems']}–{schema['maxItems']} items.")
        return [_normalize(item, schema.get("prefixItems", [])[index] if "prefixItems" in schema else schema["items"],
                           path + f"[{index}]") for index, item in enumerate(value)]
    if kind == "string":
        _require(isinstance(value, str), path + " must be text.")
        value = _plain(value, path)
        # Existing JavaScript editors enforce String.length (UTF-16 units).
        length = len(value.encode("utf-16-le")) // 2
        _require(schema.get("minLength", 0) <= length <= schema.get("maxLength", 1000), path + " has an invalid text length.")
        if "pattern" in schema:
            _require(re.fullmatch(schema["pattern"], value) is not None, path + " has an unsupported ID or label.")
    elif kind == "boolean":
        _require(type(value) is bool, path + " must be true or false.")
    elif kind in ("number", "integer"):
        _require(type(value) in (int, float), path + " must be a number, not text or a boolean.")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        _require(finite, path + " must be finite.")
        _require(kind != "integer" or value == int(value), path + " must be a whole number.")
        _require(schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf), path + " is outside its supported range.")
        if "multipleOf" in schema:
            _require(Fraction(str(value)) % Fraction(str(schema["multipleOf"])) == 0, path + " must have at most two decimal places.")
        value = int(value) if value == int(value) else float(value)
    if "enum" in schema:
        _require(value in schema["enum"], path + " is not a supported choice.")
    return value


def _unique(items, path):
    ids = [item["id"] for item in items]
    _require(len(ids) == len(set(ids)), path + " must have unique IDs.")


def normalize_draft(domain, inputs):
    """Return detached, canonical editor inputs or a graceful ValueError.

    This validates representability, not feasibility: impossible staffing,
    budgets, rooms, and grid limits remain editable drafts for the real solver.
    """
    contract = domain_contract(domain)
    result = _normalize(inputs, contract["schema"], domain)
    for collection in ("sites", "demand", "workers", "jobs", "rooms", "courses", "projects", "people"):
        if collection in result:
            _unique(result[collection], domain + "." + collection)
    if domain == "facility":
        _require(result["maxSites"] <= len(result["sites"]), "facility.maxSites exceeds the candidate hub count.")
        for node in [*result["sites"], *result["demand"]]:
            rounded = [round(coordinate, 6) for coordinate in node["position"]]
            node["position"] = [int(coordinate) if coordinate == int(coordinate) else coordinate for coordinate in rounded]
    elif domain == "energy":
        _require(result["reserve"] <= result["initial"] <= result["capacity"],
                 "Energy requires reserve <= initial <= capacity.")
        for index, interval in enumerate(result["intervals"]):
            expected = f"{index * 3:02d}–{(index + 1) * 3:02d}"
            _require(interval["label"] == expected, "energy.intervals must be ordered three-hour labels starting with 00–03.")
    elif domain == "projects":
        ids = {project["id"] for project in result["projects"]}
        for project in result["projects"]:
            _require(not project["requires"] or project["requires"] in ids and project["requires"] != project["id"],
                     project["id"] + " must reference a different existing prerequisite, or ''.")
    elif domain == "teams":
        _require(result["team_count"] <= len(result["people"]), "Each team needs at least one person.")
        ids = sorted(person["id"] for person in result["people"])
        for key in result["compatibility"]:
            first, second = key.split("|")
            _require(first in ids and second in ids and first < second,
                     "Compatibility keys must name two existing people in lexical ID order, for example U1|U2.")
        result["compatibility"] = {first + "|" + second: result["compatibility"].get(first + "|" + second, 5)
                                   for index, first in enumerate(ids) for second in ids[index + 1:]}
    try:
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Draft inputs must be valid JSON text.") from None
    _require(len(encoded) <= MAX_DRAFT_BYTES, "Draft inputs exceed 64 KB.")
    return result
