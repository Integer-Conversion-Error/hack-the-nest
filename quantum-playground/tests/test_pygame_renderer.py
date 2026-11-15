import importlib.util
import io
import json
import struct
import threading
import unittest
from itertools import combinations
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from unittest.mock import patch

from ev_reference import PROMPT, reference_handoff
from pygame_renderer import state_at, render_png, scene_layout, PygameUnavailable
from server import ThreadingHTTPServer, Handler


def parked_fleet(width, height, count):
    sim = reference_handoff({})['result']['simulation']
    sim['grid'] = {'width': width, 'height': height, 'block_km': 1}
    sim['depot'] = [0, 0]
    sim['vans'] = [
        {'id': f'V{index}', 'initial_battery_kwh': 35, 'events': []}
        for index in range(1, count + 1)
    ]
    return sim


class ReplayTests(unittest.TestCase):
    def test_states_at_start_midpoint_and_end(self):
        sim = reference_handoff({})['result']['simulation']
        for van in sim['vans']:
            self.assertEqual(state_at(van, 0, sim['depot'])[0], sim['depot'])
            drive = next(e for e in van['events'] if e['type']=='drive')
            _, battery = state_at(van, (drive['start_min']+drive['end_min'])/2, sim['depot'])
            self.assertAlmostEqual(battery, (drive['battery_start_kwh']+drive['battery_end_kwh'])/2)
            self.assertEqual(state_at(van, sim['duration_min'], sim['depot'])[0], sim['depot'])

    def test_missing_dependency_gives_setup_instruction(self):
        with patch.dict('sys.modules', {'pygame':None}):
            with self.assertRaisesRegex(PygameUnavailable, 'pip install pygame'):
                render_png(reference_handoff({})['result']['simulation'], 0)

    def test_hit_targets_include_entities_and_controls_within_frame(self):
        scene = scene_layout(reference_handoff({})['result']['simulation'], 60)
        kinds = {t['kind'] for t in scene['targets']}
        self.assertTrue({'van','delivery','charger','play','restart','back','forward','speed','clear','scrub'} <= kinds)
        for target in scene['targets']:
            x,y,w,h = target['rect']
            self.assertGreaterEqual(x,0)
            self.assertGreaterEqual(y,0)
            self.assertLessEqual(x+w,scene['width'])
            self.assertLessEqual(y+h,scene['height'])

    def test_layout_wraps_cards_and_scales_map_targets_from_the_simulation(self):
        original = scene_layout(parked_fleet(6, 6, 4), 0)
        original_delivery = next(t for t in original['targets'] if t['kind'] == 'delivery')
        original_controls = {
            t['kind']: t['rect'][1] for t in original['targets'] if 'id' not in t
        }
        for width, height, count in ((7, 15, 5), (12, 12, 6)):
            with self.subTest(width=width, height=height, vans=count):
                scene = scene_layout(parked_fleet(width, height, count), 0)
                self.assertGreater(scene['height'], original['height'])
                controls = {
                    t['kind']: t['rect'][1] for t in scene['targets'] if 'id' not in t
                }
                self.assertEqual(controls.keys(), original_controls.keys())
                for name, y in controls.items():
                    self.assertEqual(y - original_controls[name], scene['height'] - original['height'])
                delivery = next(t for t in scene['targets'] if t['kind'] == 'delivery')
                self.assertLess(delivery['rect'][2], original_delivery['rect'][2])
                self.assertNotEqual(delivery['rect'][:2], original_delivery['rect'][:2])
                truck_targets = []
                card_targets = []
                for index in range(1, count + 1):
                    truck, card = [
                        t['rect'] for t in scene['targets']
                        if t['kind'] == 'van' and t['id'] == f'V{index}'
                    ]
                    truck_targets.append(truck)
                    card_targets.append(card)
                    self.assertLess(truck[1] + truck[3], card[1])
                    self.assertLess(card[1] + card[3], controls['play'])
                for rectangles in (truck_targets, card_targets):
                    for (x1, y1, w1, h1), (x2, y2, w2, h2) in combinations(rectangles, 2):
                        self.assertTrue(
                            x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1
                        )
                self.assertGreater(card_targets[4][1], card_targets[3][1])
                for target in scene['targets']:
                    x, y, w, h = target['rect']
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x + w, scene['width'])
                    self.assertLessEqual(y + h, scene['height'])

    def test_isolated_van_stays_on_its_street_with_other_vans_at_the_depot(self):
        sim = parked_fleet(12, 12, 6)
        sim['vans'][-1]['events'] = [{
            'type': 'wait', 'position': [8, 7], 'start_min': 0, 'end_min': 20,
            'battery_start_kwh': 35, 'battery_end_kwh': 35,
        }]
        scene = scene_layout(sim, 10)
        truck = next(t for t in scene['targets'] if t['kind'] == 'van' and t['id'] == 'V6')
        x, y, width, height = truck['rect']
        self.assertEqual((x + width / 2, y + height / 2), (570, 200))


@unittest.skipUnless(importlib.util.find_spec('pygame'), 'Pygame not installed in this interpreter')
class FrameTests(unittest.TestCase):
    def test_real_png_frames_change_with_time(self):
        sim = reference_handoff({})['result']['simulation']
        first, later = render_png(sim, 0), render_png(sim, 60)
        self.assertTrue(first.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(struct.unpack('>II',first[16:24]), (1680, 1240))
        self.assertNotEqual(first,later)
        self.assertNotEqual(render_png(sim,60,playing=False),render_png(sim,60,playing=True))

    def test_resolution_tiers_preserve_logical_scene(self):
        sim = reference_handoff({})['result']['simulation']
        scene = scene_layout(sim,60)
        for resolution in (1,2,3):
            frame=render_png(sim,60,resolution=resolution)
            self.assertEqual(struct.unpack('>II',frame[16:24]),(840*resolution,620*resolution))
            self.assertEqual(scene_layout(sim,60),scene)
        with self.assertRaises(ValueError):
            render_png(sim,60,resolution=1000)

    def test_six_van_frame_matches_layout_and_draws_distinct_card_accent_colors(self):
        sim = parked_fleet(12, 12, 6)
        scene = scene_layout(sim, 0)
        frame = render_png(sim, 0)
        self.assertEqual(struct.unpack('>II', frame[16:24]), (2 * scene['width'], 2 * scene['height']))
        import pygame
        image = pygame.image.load(io.BytesIO(frame))
        colors = set()
        for index in range(1, 7):
            _, card = [
                t['rect'] for t in scene['targets']
                if t['kind'] == 'van' and t['id'] == f'V{index}'
            ]
            colors.add(image.get_at(((card[0] + 2) * 2, (card[1] + 22) * 2))[:3])
        self.assertEqual(len(colors), 6)

    def test_http_frame_uses_stored_run_and_replay_clock(self):
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            payload = json.dumps({'prompt': PROMPT, 'mode': 'reference'}).encode()
            with urlopen(Request(base+'/api/run',payload,{'Content-Type':'application/json'})) as response:
                run=json.load(response)
            frame_url = base + '/api/pygame-frame?'
            view = {'view_id': run['view_id']}
            url = frame_url + urlencode({**view, 'minute': 60, 'selected': 'V1'})
            with urlopen(url) as response:
                self.assertEqual(response.headers['Content-Type'],'image/png')
                scene=json.loads(response.headers['X-Pygame-Scene'])
                self.assertEqual(scene['minute'],60)
                self.assertEqual(scene['width'],840)
                self.assertEqual(scene['pixel_width'],1680)
                self.assertTrue(any(t['kind']=='scrub' for t in scene['targets']))
                self.assertTrue(response.read().startswith(b'\x89PNG'))
            for query, code in [({'view_id': 'missing'}, 404), ({**view, 'minute': 'nan'}, 400),
                                ({**view, 'resolution': 9999}, 400)]:
                with self.subTest(query=query):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(frame_url + urlencode(query))
                    self.assertEqual(error.exception.code, code)
                    error.exception.close()
            with urlopen(url+'&resolution=3') as response:
                self.assertEqual(struct.unpack('>II',response.read()[16:24]),(2520,1860))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__=='__main__':
    unittest.main()
