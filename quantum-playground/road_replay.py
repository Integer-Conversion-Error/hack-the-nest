"""Capture selected road geometries without changing approved solver inputs."""
import copy
import hashlib
import json
import math

from road_network import road_geometry


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _schedules(output):
    result = {'simulation': output['simulation']}
    reference = output.get('reference')
    if isinstance(reference, dict) and isinstance(reference.get('simulation'), dict):
        result['classical_simulation'] = reference['simulation']
    return result


def _without_paths(simulation):
    result = copy.deepcopy(simulation)
    for van in result['vans']:
        for event in van['events']:
            if event['type'] == 'drive':
                event.pop('path', None)
    return result


def _point(point):
    try:
        return (isinstance(point, list) and len(point) == 2
                and all(type(n) in (int, float) and math.isfinite(n) for n in point)
                and -180 <= point[0] <= 180 and -85 <= point[1] <= 85)
    except OverflowError:
        return False


def capture_replay(output, routing, *, stored=None, fetch=False):
    """Use an intact saved capture, or fetch only this result's selected edges.

    Saved-run callers leave fetch=False so reopening never changes road data or
    depends on an available routing service. Raw worker geometry is never trusted.
    """
    schedules = _schedules(output)
    result_digest = _digest(output)
    nodes = {node['id']: node['snapped_position'] for node in routing['nodes']}
    if isinstance(stored, dict):
        try:
            metadata = {key: value for key, value in stored.items() if key != 'digest'}
            valid = (type(stored.get('schema_version')) is int and stored['schema_version'] == 1
                     and stored.get('routing_digest') == routing['digest']
                     and stored.get('result_digest') == result_digest
                     and stored.get('digest') == _digest(metadata))
            if valid:
                for key, raw in schedules.items():
                    scene = stored[key]
                    if _without_paths(scene) != _without_paths(raw):
                        valid = False
                        break
                    for van in scene['vans']:
                        for event in van['events']:
                            if event['type'] != 'drive':
                                continue
                            path = event.get('path')
                            if (not isinstance(path, list) or not 2 <= len(path) <= 20000
                                    or not all(_point(point) for point in path)
                                    or path[0] != nodes[event['from_id']]
                                    or path[-1] != nodes[event['to_id']]):
                                valid = False
                if valid:
                    return copy.deepcopy(stored)
        except (KeyError, TypeError, ValueError):
            pass
    if not fetch:
        raise ValueError('The saved road replay is missing or changed. Start a new geographic run to capture its routes.')
    capture = {'schema_version': 1, 'routing_digest': routing['digest'], 'result_digest': result_digest}
    paths = {}
    for key, raw in schedules.items():
        scene = copy.deepcopy(raw)
        for van in scene['vans']:
            for event in van['events']:
                if event['type'] != 'drive':
                    continue
                pair = (event['from_id'], event['to_id'])
                if pair not in paths:
                    path = copy.deepcopy(road_geometry(routing, *pair))
                    if not isinstance(path, list) or not 2 <= len(path) <= 20000 or not all(_point(point) for point in path):
                        raise ValueError('The routing provider returned an invalid road geometry.')
                    # Provider encodings can round endpoints by a few centimetres.
                    for actual, expected in ((path[0], nodes[pair[0]]), (path[-1], nodes[pair[1]])):
                        if any(abs(a - b) > 0.00002 for a, b in zip(actual, expected)):
                            raise ValueError('Road geometry no longer matches the saved locations. Plan a new geographic run.')
                    path[0], path[-1] = nodes[pair[0]], nodes[pair[1]]
                    paths[pair] = path
                event['path'] = copy.deepcopy(paths[pair])
        capture[key] = scene
    capture['digest'] = _digest(capture)
    if len(json.dumps(capture).encode()) > 10_000_000:
        raise ValueError('The selected road geometries are too large to replay. Use a smaller geographic area.')
    return capture
