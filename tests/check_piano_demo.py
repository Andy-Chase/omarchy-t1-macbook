"""Verify demo timing, persistence, input blocking and audio-worker integration."""
import tempfile
from pathlib import Path
from unittest.mock import patch
import array
import subprocess
import sys
import time
from piano import BLOCK, DEMOS, MAX_MASK, DemoPlayer, Piano, LayeredScore

for index, song in enumerate(DEMOS):
    player = DemoPlayer(index)
    masks = []
    while not player.finished:
        masks.append(player.next_mask())
    assert masks[0] and 0 in masks
    if index >= 4: assert max(mask.bit_count() for mask in masks) >= 3
    assert all(0 <= mask <= MAX_MASK for mask in masks)
    assert 10 < player.end / 48000 < 45
    assert player.next_mask() == 0

# A repeated accompaniment pitch must not end a sustained melody note.
score = LayeredScore(((72,2),), ((.5,(72,),.5),))
with patch('piano.DEMOS', (('overlap','Overlap',120,score),)):
    player = DemoPlayer(0)
    for _ in range(140):
        overlap_mask = player.next_mask()
    assert overlap_mask == 1 << 12

real_popen = subprocess.Popen
with tempfile.TemporaryDirectory() as directory:
    preference = Path(directory) / 'settings' / 'demo'
    piano = Piano(preference)
    assert piano.demo_index == 0
    for index in range(8):
        assert piano.select_demo(index)
        assert Piano(preference).demo_index == index
    assert not piano.select_demo(8)
    piano.select_demo(3)
    preference.write_text('unrecognised\n')
    assert Piano(preference).demo_index == 0
    assert not piano.apply('PIANO DEMO SELECT 8')
    assert not piano.apply('PIANO DEMO SELECT -1')
    assert piano.apply('PIANO DEMO SELECT 0', locked=True)
    assert piano.demo_index == 3
    pcm_path = Path(directory) / 'recording.pcm'

    def sink(*args, **kwargs):
        return real_popen([sys.executable, '-u', '-c',
            'import sys; f=open(sys.argv[1],"wb",buffering=0)\n'
            'while True:\n b=sys.stdin.buffer.read1(4096)\n'
            ' if not b: break\n f.write(b)', str(pcm_path)], **kwargs)

    with patch('piano.subprocess.Popen', side_effect=sink):
        piano.open()
        piano.apply('PIANO DEMO TOGGLE')
        assert piano.demo_playing
        piano.notes(1 << 24)
        assert piano.mask == 0  # Manual input can't override the demo.
        time.sleep(.15)
        assert piano.demo_mask and piano.demo_packet().startswith('PIANO DEMO 3 1 ')
        piano.apply('PIANO DEMO TOGGLE')
        assert not piano.demo_playing and not piano.demo_mask
        piano.apply('PIANO DEMO TOGGLE')
        assert piano.demo_playing
        piano.apply('PIANO DEMO STOP')
        assert not piano.demo_playing
        piano.apply('PIANO DEMO TOGGLE', locked=True)
        assert not piano.demo_playing
        piano.apply('PIANO DEMO TOGGLE')
        with piano.demo_lock:
            piano.demo.frame = piano.demo.end - BLOCK
        time.sleep(.04)
        assert not piano.demo_playing  # Natural completion restores keys.
        piano.notes(1 << 7)
        assert piano.mask == 1 << 7
        piano.apply('PIANO DEMO TOGGLE')
        piano.close()
        assert not piano.demo_playing and piano.process is None and piano.thread is None
        assert max(map(abs, array.array('h', pcm_path.read_bytes()))) > 1000
        piano.open()
        piano.apply('PIANO DEMO TOGGLE')
        piano.process.kill()
        time.sleep(.12)
        assert piano.status == -1 and not piano.demo_playing
        piano.close()
print('PIANO_DEMO_CHECK_OK: all eight scores, natural completion, tap-to-stop, key blocking, lock, persistence and cleanup')
