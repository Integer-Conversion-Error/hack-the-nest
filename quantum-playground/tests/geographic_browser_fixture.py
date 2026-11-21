"""Emit independently checked synthetic geographic browser data; never call a provider."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2])]
from fleet_handoff import normalize_handoff
from pipeline_live import project_result
from scenario import effective_scenario, scenario_prompt
from test_geographic import raw_geo_result, routing_fixture
from test_geographic_pipeline import synthetic_road_geometry

data = json.load(sys.stdin)
source = data['source']
control = {'key': 'driving_cost_per_km', 'type': 'number', 'label': 'Driving cost',
           'default': source['costs']['driving_per_km'], 'minimum': 0, 'maximum': 2, 'step': 0.1, 'rerun_mode': 'rebuild'}
values = data.get('values') or {'driving_cost_per_km': control['default']}
routing = routing_fixture(source)
effective = effective_scenario(source, [control], values)
output = raw_geo_result(effective, routing)
output['controls'] = values
identity = {'run_id': data['plan'], 'plan_id': data['plan'], 'attempt_id': data['attempt']}
artifact = {'controls': [control], 'problem': {'title': source['title'], 'summary': 'Synthetic road fixture; no quantum execution.'}}
prompt = scenario_prompt(source)
with patch('road_replay.road_geometry', side_effect=synthetic_road_geometry):
    handoff = project_result(artifact, output, prompt, identity, source_scenario=source,
                             routing_artifact=routing, allow_geometry_fetch=True)
view = normalize_handoff(handoff, 'live', prompt, expected_values=values)
view.update(view_id=data['attempt'], scenario_context=handoff['scenario_context'])
print(json.dumps(view))
