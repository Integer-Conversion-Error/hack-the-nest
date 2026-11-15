import copy
import json
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from ev_demo import demo, evaluate, variables
from server import Handler, ThreadingHTTPServer


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.run = demo('EV example', {})
        self.params = {v['id']: v['default'] for v in self.run['variables']}

    def test_default_schedule_is_feasible_and_costs_balance(self):
        ev = self.run['evaluation']
        self.assertTrue(ev['feasible'])
        self.assertEqual(ev['delivered'], 8)
        self.assertEqual(ev['objective_value'], round(sum(ev['costs'].values()), 2))
        self.assertFalse(ev['optimality_proven'])
        self.assertEqual(self.run['simulation']['counts'], {})

    def test_street_paths_and_battery_endpoints(self):
        for van in self.run['simulation']['vans']:
            previous = van['initial_battery_kwh']
            for event in van['events']:
                self.assertAlmostEqual(previous, event['battery_start_kwh'])
                if event['type'] == 'drive':
                    path = event['path']
                    for a, b in zip(path, path[1:]):
                        self.assertEqual(abs(a[0]-b[0])+abs(a[1]-b[1]), 1)
                    self.assertAlmostEqual(event['battery_end_kwh'], previous-.5*(len(path)-1)*self.params['consumption'])
                previous = event['battery_end_kwh']

    def test_low_initial_charge_is_not_reported_feasible(self):
        ev = demo('test', {'initial_1': 5})['evaluation']
        self.assertFalse(ev['feasible'])
        self.assertTrue(any('starts below reserve' in v for v in ev['violations']))

    def test_charger_conflict_is_detected(self):
        sim = copy.deepcopy(self.run['simulation'])
        charges = [e for v in sim['vans'] for e in v['events'] if e['type']=='charge']
        charges[1]['charger_id'] = charges[0]['charger_id']
        self.assertTrue(any('conflict' in v for v in evaluate(sim, self.params)['violations']))

    def test_missing_delivery_and_capacity_are_detected(self):
        sim = copy.deepcopy(self.run['simulation'])
        sim['vans'][0]['events'] = [e for e in sim['vans'][0]['events'] if e.get('delivery_id')!='D1']
        sim['vans'][0]['delivery_ids'].append('D8')
        violations = evaluate(sim, self.params)['violations']
        self.assertIn('D1: undelivered by noon', violations)
        self.assertTrue(any('capacity' in v for v in violations))

    def test_late_delivery_and_slow_return(self):
        sim = copy.deepcopy(self.run['simulation'])
        for van in sim['vans']:
            for event in van['events']:
                event['start_min'] += 150
                event['end_min'] += 150
        evaluation = evaluate(sim, self.params)
        self.assertFalse(evaluation['feasible'])
        self.assertTrue(any('late delivery' in v for v in evaluation['violations']))
        self.assertTrue(any('return to depot' in v for v in evaluation['violations']))

    def test_invalid_parameters(self):
        for overrides in ({'battery_kwh':0}, {'charging_kw':float('nan')}, {'initial_1':True}, {'unknown':1}):
            with self.assertRaises(ValueError):
                variables(overrides)

    def test_slider_changes_affect_cost_time_battery_and_charging(self):
        base = self.run
        expensive = demo('test', {'electricity_price': .6})
        self.assertGreater(expensive['evaluation']['costs']['electricity'], base['evaluation']['costs']['electricity'])
        slower = demo('test', {'speed_kmh': 10})
        self.assertGreater(slower['simulation']['vans'][3]['events'][-1]['end_min'], base['simulation']['vans'][3]['events'][-1]['end_min'])
        charged = demo('test', {'initial_1': 80})
        self.assertFalse(any(e['type']=='charge' for e in charged['simulation']['vans'][0]['events']))
        power = demo('test', {'charging_kw': 20})
        def energy(run):
            return next(e['energy_kwh'] for e in run['simulation']['vans'][0]['events'] if e['type']=='charge')
        self.assertGreater(energy(power), energy(base))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def post(self, data):
        return urlopen(Request(self.base+'/api/run',json.dumps(data).encode(),{'Content-Type':'application/json'}))

    def test_assets_and_live_contract_fixture(self):
        for path in ('/', '/app.js', '/fleet.js', '/style.css', '/api/config', '/api/example'):
            with urlopen(self.base+path) as response:
                self.assertEqual(response.status,200)
        with self.post({'prompt':'EV demo','variables':{'charging_kw':12}}) as response:
            run=json.load(response)
            self.assertEqual(run['simulation']['kind'],'ev_fleet')
            self.assertEqual(len(run['simulation']['vans']),4)
            self.assertEqual(run['mode'],'demo')
            self.assertEqual(run['problem']['original_prompt'],'EV demo')

    def test_invalid_request_shapes(self):
        for body in ([], {'prompt':3}, {'prompt':'test','variables':[]}, {'prompt':'test','mode':'invalid'}):
            with self.assertRaises(HTTPError) as error:
                self.post(body)
            self.assertEqual(error.exception.code,400)
            error.exception.close()


if __name__=='__main__':
    unittest.main()
