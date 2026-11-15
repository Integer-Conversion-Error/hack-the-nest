import importlib.util
import json
import struct
import threading
import unittest
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from unittest.mock import patch

from ev_demo import demo
from pygame_renderer import state_at, render_png, scene_layout, PygameUnavailable
from server import ThreadingHTTPServer, Handler


class ReplayTests(unittest.TestCase):
    def test_states_at_start_midpoint_and_end(self):
        sim = demo('EV', {})['simulation']
        for van in sim['vans']:
            self.assertEqual(state_at(van, 0, sim['depot'])[0], sim['depot'])
            drive = next(e for e in van['events'] if e['type']=='drive')
            position, battery = state_at(van, (drive['start_min']+drive['end_min'])/2, sim['depot'])
            self.assertAlmostEqual(battery, (drive['battery_start_kwh']+drive['battery_end_kwh'])/2)
            self.assertEqual(state_at(van, 240, sim['depot'])[0], sim['depot'])

    def test_missing_dependency_gives_setup_instruction(self):
        with patch.dict('sys.modules', {'pygame':None}):
            with self.assertRaisesRegex(PygameUnavailable, 'pip install pygame'):
                render_png(demo('EV', {})['simulation'], 0)

    def test_hit_targets_include_entities_and_controls_within_frame(self):
        scene = scene_layout(demo('EV', {})['simulation'], 60)
        kinds = {t['kind'] for t in scene['targets']}
        self.assertTrue({'van','delivery','charger','play','restart','back','forward','speed','clear','scrub'} <= kinds)
        for target in scene['targets']:
            x,y,w,h = target['rect']
            self.assertGreaterEqual(x,0)
            self.assertGreaterEqual(y,0)
            self.assertLessEqual(x+w,scene['width'])
            self.assertLessEqual(y+h,scene['height'])


@unittest.skipUnless(importlib.util.find_spec('pygame'), 'Pygame not installed in this interpreter')
class FrameTests(unittest.TestCase):
    def test_real_png_frames_change_with_time(self):
        sim = demo('EV', {})['simulation']
        first, later = render_png(sim, 0), render_png(sim, 60)
        self.assertTrue(first.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(struct.unpack('>II',first[16:24]), (1680, 1240))
        self.assertNotEqual(first,later)
        self.assertNotEqual(render_png(sim,60,playing=False),render_png(sim,60,playing=True))

    def test_resolution_tiers_preserve_logical_scene(self):
        sim = demo('EV', {})['simulation']
        scene = scene_layout(sim,60)
        for resolution in (1,2,3):
            frame=render_png(sim,60,resolution=resolution)
            self.assertEqual(struct.unpack('>II',frame[16:24]),(840*resolution,620*resolution))
            self.assertEqual(scene_layout(sim,60),scene)
        with self.assertRaises(ValueError):
            render_png(sim,60,resolution=1000)

    def test_http_frame_uses_stored_run_and_replay_clock(self):
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            payload=json.dumps({'prompt':'EV example','mode':'demo'}).encode()
            with urlopen(Request(base+'/api/run',payload,{'Content-Type':'application/json'})) as response:
                run=json.load(response)
            url=base+'/api/pygame-frame?run_id='+run['run_id']+'&minute=60&selected=V1'
            with urlopen(url) as response:
                self.assertEqual(response.headers['Content-Type'],'image/png')
                scene=json.loads(response.headers['X-Pygame-Scene'])
                self.assertEqual(scene['minute'],60)
                self.assertEqual(scene['width'],840)
                self.assertEqual(scene['pixel_width'],1680)
                self.assertTrue(any(t['kind']=='scrub' for t in scene['targets']))
                self.assertTrue(response.read().startswith(b'\x89PNG'))
            for suffix, code in [('run_id=missing',404),('run_id='+run['run_id']+'&minute=nan',400),('run_id='+run['run_id']+'&resolution=9999',400)]:
                with self.assertRaises(HTTPError) as error:
                    urlopen(base+'/api/pygame-frame?'+suffix)
                self.assertEqual(error.exception.code,code)
                error.exception.close()
            with urlopen(url+'&resolution=3') as response:
                self.assertEqual(struct.unpack('>II',response.read()[16:24]),(2520,1860))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__=='__main__':
    unittest.main()
