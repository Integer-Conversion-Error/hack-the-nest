"""Verify orchestration boundaries without making Gemini API calls."""
import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from agent import orchestrate


class AgentTests(unittest.TestCase):
    def invoke(self, generate, backend):
        sdk_types = types.SimpleNamespace(HttpOptions=lambda **kw: kw,
            GenerateContentConfig=lambda **kw: types.SimpleNamespace(**kw),
            AutomaticFunctionCallingConfig=lambda **kw: kw)

        class Client:
            def __init__(self, **kwargs):
                self.models = types.SimpleNamespace(generate_content=generate)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        genai = types.ModuleType('google.genai')
        genai.Client, genai.types = Client, sdk_types
        google = types.ModuleType('google')
        google.genai = genai
        with patch.dict(sys.modules, {'google': google, 'google.genai': genai}), patch.dict(os.environ, {'GEMINI_API_KEY':'unit-test-placeholder'}):
            return orchestrate('EV prompt', {'charging_kw':12}, backend)

    def test_tool_sequence_and_authoritative_overrides(self):
        calls = []
        def backend(path, payload):
            calls.append((path,payload))
            if path=='/api/solve':
                self.assertEqual(payload['parameters']['charging_kw'],12)
                return {'run_id':'r1','simulation':{},'variables':[]}
            return {'feasible':True,'objective_value':4}
        def generate(**kwargs):
            solve, evaluate = kwargs['config'].tools
            self.assertIn('error',evaluate('invented-id'))
            run = solve('EV prompt',json.dumps({'charging_kw':1}))
            evaluate(run['run_id'])
            return types.SimpleNamespace(text='Evaluated explanation')
        result = self.invoke(generate,backend)
        self.assertEqual([c[0] for c in calls],['/api/solve','/api/evaluate'])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['explanation'],'Evaluated explanation')

    def test_backend_evaluation_is_forced_when_agent_skips_it(self):
        calls=[]
        def backend(path,payload):
            calls.append(path)
            return {'run_id':'r1'} if path=='/api/solve' else {'feasible':False,'violations':['reserve']}
        def generate(**kwargs):
            kwargs['config'].tools[0]('EV prompt','{}')
            return types.SimpleNamespace(text='Unsupported success claim')
        result=self.invoke(generate,backend)
        self.assertEqual(calls,['/api/solve','/api/evaluate'])
        self.assertEqual(result['status'],'no_go')
        self.assertNotIn('Unsupported success claim',result['explanation'])

    def test_clarification_does_not_call_backend(self):
        def backend(*args):
            self.fail('Clarification must not execute a fabricated problem')
        result=self.invoke(lambda **kw:types.SimpleNamespace(text='What are the delivery deadlines?'),backend)
        self.assertEqual(result['status'],'needs_input')
        self.assertEqual(result['simulation'],{})


if __name__=='__main__':
    unittest.main()
