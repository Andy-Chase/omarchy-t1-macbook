"""Verify register tuning, sustain release, preferences and demo isolation."""
import array
import tempfile
from pathlib import Path
from unittest.mock import patch
import time
import subprocess
from piano import Piano, Synth, RATE, BLOCK, DemoPlayer

for octave in range(-2,3):
    synth=Synth();samples=array.array('h')
    for _ in range(100):samples.frombytes(synth.render(1,octave))
    target=261.625565*2**octave
    period=RATE/target
    segment=samples[2400:12000]
    lags=range(round(period*.97),round(period*1.03)+1)
    scores={lag:sum(segment[i]*segment[i+lag] for i in range(len(segment)-lag))/(len(segment)-lag) for lag in lags}
    frequency=RATE/max(scores,key=scores.get)
    assert abs(frequency-target)/target<.025,(octave,frequency,target)
    # Pedal holds released notes, then pedal-up releases them.
    for _ in range(30):synth.render(0,octave,True)
    assert synth.voices
    for _ in range(15):synth.render(0,octave,False)
    assert not synth.voices
    print(f'Octave {octave:+}: {frequency:.2f}Hz, target {target:.2f}Hz; sustain release passed')

s=Synth();s.render(1,0,True);s.render(0,1,True);assert not s.voices
with tempfile.TemporaryDirectory() as directory:
    preference=Path(directory)/'piano-demo'
    p=Piano(preference)
    assert p.settings_packet()=='PIANO SETTINGS 0 0'
    assert p.apply('PIANO OCTAVE -1') and p.octave==-1
    assert p.apply('PIANO SUSTAIN TOGGLE') and p.sustain
    loaded=Piano(preference);assert loaded.octave==-1 and loaded.sustain
    for _ in range(8):p.apply('PIANO OCTAVE 1')
    assert p.octave==2
    for _ in range(8):p.apply('PIANO OCTAVE -1')
    assert p.octave==-2
    p.apply('PIANO OCTAVE 1',locked=True);assert p.octave==-2
    p.apply('PIANO SUSTAIN TOGGLE',locked=True);assert p.sustain
    for message in ('PIANO OCTAVE 2','PIANO OCTAVE 0','PIANO OCTAVE -3','PIANO SUSTAIN x'):
        assert not p.apply(message)
    p.demo=DemoPlayer(0)
    p.apply('PIANO OCTAVE 1');assert p.octave==-2
    p.apply('PIANO SUSTAIN TOGGLE');assert p.sustain
    p.stop_demo();assert p.select_demo(7)
    assert Piano(preference).demo_index==7
    p.settings_path.write_text('{"octave":100,"sustain":true}')
    reset=Piano(preference);assert reset.octave==0 and not reset.sustain
    p.settings_path.write_text('invalid-json')
    reset=Piano(preference);assert reset.octave==0 and not reset.sustain
    with patch.object(Path,'replace',side_effect=PermissionError('test write failure')):
        p.apply('PIANO OCTAVE 1');assert p.octave==-2

# Exercise the real audio worker: demos ignore the manual register and pedal.
observed=[]
class SpySynth(Synth):
    def render(self,mask,octave=0,sustain=False):
        observed.append((octave,sustain))
        return super().render(mask,octave,sustain)
real_popen=subprocess.Popen
with tempfile.TemporaryDirectory() as directory:
    p=Piano(Path(directory)/'demo');p.save_settings(-1,True)
    with patch('piano.subprocess.Popen',side_effect=lambda *a,**kw:real_popen(['/usr/bin/cat'],**kw)), patch('piano.Synth',SpySynth):
        p.open();time.sleep(.06)
        assert (-1,True) in observed
        observed.clear();p.apply('PIANO DEMO TOGGLE');time.sleep(.06)
        assert (0,False) in observed
        observed.clear();p.apply('PIANO DEMO STOP');time.sleep(.06)
        assert (-1,True) in observed
        p.close();assert p.process is None and p.thread is None

# Sustaining ten notes must still fit a real-time stream budget.
s=Synth();start=time.perf_counter()
for _ in range(100):s.render((1<<10)-1,0,True)
elapsed=time.perf_counter()-start
assert elapsed<.5,elapsed
print(f'PIANO_EXPRESSION_CHECK_OK; ten notes: {elapsed:.3f}s processing per 0.5s audio')
