"""Exercise pitch, envelopes, protocol validation and the actual stream worker."""
import array
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from piano import BLOCK, Piano, RATE, Synth

synth = Synth()
assert not any(array.array('h', synth.render(0)))
samples = array.array('h')
for _ in range(100):
    samples.frombytes(synth.render(1))
segment = list(samples[2400:7200])
scores = {lag: sum(segment[i] * segment[i + lag] for i in range(len(segment) - lag)) / (len(segment) - lag)
          for lag in range(170, 195)}
frequency = RATE / max(scores, key=scores.get)
assert abs(frequency - 261.626) < 3
for _ in range(15):
    synth.render(0)
assert not synth.voices

piano = Piano()
for message in ('PIANO NOTES -1', 'PIANO NOTES 33554432', 'PIANO NOTES 1 extra', 'PIANO NOTES x'):
    assert not piano.apply(message)
assert piano.apply('PIANO OPEN', locked=True) and piano.status == 0

# Keep the real worker/pipe, substituting only the unavailable sound server.
real_popen = subprocess.Popen
with tempfile.TemporaryDirectory() as directory:
    recording = Path(directory) / 'stream.raw'

    def sink(*args, **kwargs):
        return real_popen([sys.executable, '-u', '-c',
                          'import sys; f=open(sys.argv[1],"wb",buffering=0)\n'
                          'while True:\n b=sys.stdin.buffer.read1(4096)\n'
                          ' if not b: break\n f.write(b)', str(recording)], **kwargs)

    with patch('piano.subprocess.Popen', side_effect=sink):
        piano.open()
        assert piano.apply('PIANO NOTES 145')  # C-major chord.
        time.sleep(.15)
        assert piano.status == 1
        piano.apply('PIANO CLOSE')
        assert piano.process is None and piano.thread is None
        pcm = array.array('h', recording.read_bytes())
        assert len(pcm) >= BLOCK and max(map(abs, pcm)) > 1000

        piano.open()
        piano.notes(1)
        # Expire the watchdog without adding a long-running test.
        piano.updated = time.monotonic() - 2
        time.sleep(.12)
        piano.close()
        pcm = array.array('h', recording.read_bytes())
        assert not any(pcm[-BLOCK:])

        piano.open()
        piano.process.kill()
        time.sleep(.12)
        assert piano.status == -1
        piano.close()

print(f'PIANO_CHECK_OK middle_C={frequency:.2f}Hz; release, chord stream, watchdog, playback failure and cleanup passed')
