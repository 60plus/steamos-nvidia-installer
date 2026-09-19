import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('resume',Path(__file__).resolve().parents[1]/'scripts/bluetooth-resume.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def fixture(connected=True, trusted=True, powered=True, audio=True, path='/dev1'):
 return {'/adapter':{m.ADAPTER:{'Address':'adapter','Powered':powered}},path:{m.DEVICE:{'Address':'headset','Adapter':'/adapter','Paired':True,'Trusted':trusted,'Connected':connected,'UUIDs':[m.AUDIO_SINK] if audio else []}}}
class Resume(unittest.TestCase):
 def test_only_connected_trusted_audio(self):
  for args in [{'connected':False},{'trusted':False},{'audio':False}]:
   r=m.Recovery();r.suspend(fixture(**args));self.assertFalse(r.pending)
 def test_survives_adapter_reset_and_path_change(self):
  r=m.Recovery();r.suspend(fixture());r.resume(100)
  self.assertEqual(r.candidates({},105),[])
  self.assertEqual(r.candidates(fixture(False,path='/new'),110),[(('adapter','headset'),'/new')])
 def test_no_connect_if_adapter_off_or_already_connected(self):
  r=m.Recovery();r.suspend(fixture());r.resume(100)
  self.assertEqual(r.candidates(fixture(False,powered=False),105),[])
  self.assertEqual(r.candidates(fixture(True),110),[]);self.assertFalse(r.pending)
 def test_attempt_and_time_limits(self):
  r=m.Recovery();r.suspend(fixture());r.resume(100)
  for t in [105,110,115]:self.assertEqual(len(r.candidates(fixture(False),t)),1)
  self.assertEqual(r.candidates(fixture(False),120),[])
  self.assertEqual(r.candidates({},146),[]);self.assertFalse(r.pending)
 def test_second_sleep_replaces_previous_snapshot(self):
  r=m.Recovery();r.suspend(fixture());g=r.generation;r.suspend(fixture(False))
  self.assertGreater(r.generation,g);r.resume(100);self.assertEqual(r.candidates(fixture(False),105),[])
