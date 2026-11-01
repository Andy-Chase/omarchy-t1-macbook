# SPDX-License-Identifier: GPL-2.0-only
"""Local, dependency-free piano synthesis through one desktop PipeWire stream."""
import array
import math
import json
import os
from pathlib import Path
import re
import select
import subprocess
import threading
import time

RATE = 48000
BLOCK = 240
MAX_MASK = (1 << 25) - 1
SINE = tuple(math.sin(2 * math.pi * i / 4096) for i in range(4096))
# Whole-cycle tables: bright hammer attack gradually gives way to the fundamental.
BRIGHT = tuple(sum(weight * math.sin(2 * math.pi * harmonic * i / 4096)
                   for harmonic, weight in enumerate((1,.4,.22,.12,.07,.035), 1)) for i in range(4096))


# Short, locally sequenced arrangements of familiar public-domain melodies.
# Entries are (MIDI note, beats); None is a rest. All fit the visible C4-C6 keys.
DEMOS = (
    ('fur-elise', 'Für Elise', 132,
     ((76,.5),(75,.5),(76,.5),(75,.5),(76,.5),(71,.5),(74,.5),(72,.5),(69,1.5),
      (None,.5),(60,.5),(64,.5),(69,.5),(71,1.5),(None,.5),(64,.5),(68,.5),(71,.5),
      (72,1.5),(None,.5),(64,.5),(76,.5),(75,.5),(76,.5),(75,.5),(76,.5),(71,.5),
      (74,.5),(72,.5),(69,1.5),(None,.5),(60,.5),(64,.5),(69,.5),(71,1.5),
      (None,.5),(64,.5),(72,.5),(71,.5),(69,2))),
    ('ode-to-joy', 'Ode to Joy', 120,
     tuple(zip((64,64,65,67,67,65,64,62,60,60,62,64,64,62,62,
                64,64,65,67,67,65,64,62,60,60,62,64,62,60,60),
               (1,1,1,1,1,1,1,1,1,1,1,1,1.5,.5,2,
                1,1,1,1,1,1,1,1,1,1,1,1,1.5,.5,2)))),
    ('twinkle', 'Twinkle, Twinkle', 112,
     tuple(zip((60,60,67,67,69,69,67,65,65,64,64,62,62,60,
                67,67,65,65,64,64,62,67,67,65,65,64,64,62,
                60,60,67,67,69,69,67,65,65,64,64,62,62,60),
               (1,1,1,1,1,1,2,1,1,1,1,1,1,2,
                1,1,1,1,1,1,2,1,1,1,1,1,1,2,
                1,1,1,1,1,1,2,1,1,1,1,1,1,2)))),
    ('greensleeves', 'Greensleeves', 130,
     ((69,1),(72,2),(74,1),(76,1.5),(77,.5),(76,1),(74,2),(71,1),(67,1.5),
      (69,.5),(71,1),(72,2),(69,1),(69,1.5),(68,.5),(69,1),(71,2),(68,1),
      (64,2),(69,1),(72,2),(74,1),(76,1.5),(77,.5),(76,1),(74,2),(71,1),
      (67,1.5),(69,.5),(71,1),(72,1.5),(71,.5),(69,1),(68,1.5),(66,.5),(68,1),(69,3))),
)


class LayeredScore:
    """Independent melody/accompaniment events, measured in beats."""
    def __init__(self, melody, harmony=()):
        self.events = []
        position = 0
        for note, duration in melody:
            notes = () if note is None else (note,) if isinstance(note, int) else note
            self.events.append((position, duration * .88, notes))
            position += duration
        self.length = position
        for start, notes, duration in harmony:
            if start < self.length:
                self.events.append((start, min(duration, self.length - start), tuple(notes)))


def walking_harmony(chords, bars, beats=4):
    """Alternating root and off-beat chord accompaniment."""
    events = []
    for bar in range(bars):
        root, third, fifth = chords[bar % len(chords)]
        for beat in range(beats):
            notes = (root,) if beat % 2 == 0 else (third, fifth)
            events.append((bar * beats + beat, notes, .72))
    return events


# New demos are compact two-octave arrangements, rather than full performances.
_entertainer = ((62,.25),(63,.25),(64,.5),(72,.5),(64,.5),(72,.5),(64,.5),(72,1),
               (72,.5),(74,.25),(75,.25),(76,.5),(72,.5),(74,.5),(76,1),(71,.5),
               (74,.5),(72,1),(None,.5),(62,.25),(63,.25),(64,.5),(72,.5),
               (64,.5),(72,.5),(64,.5),(72,1),(69,.5),(67,.5),(66,.5),(69,.5),
               (72,.5),(76,1),(74,.5),(72,.5),(69,.5),(74,.5),(72,1.5))
_turkish = ((71,.25),(69,.25),(68,.25),(69,.25),(72,.5),(74,.5),(76,.5),(77,.25),
            (76,.25),(75,.25),(76,.25),(83,.25),(81,.25),(80,.25),(81,.25),
            (83,.25),(81,.25),(80,.25),(81,.25),(84,.5),(81,.5),(84,.5),(81,.5),
            (84,.5),(81,.5),(84,.5),(81,.5),(79,.5),(77,.5),(76,.5),(74,.5),
            (72,.5),(71,.5),(69,1),(None,.5),(71,.25),(72,.25),(74,.5),(76,.5),
            (77,.5),(76,.5),(74,.5),(72,.5),(71,.5),(68,.5),(69,2))
_canon = tuple(zip(
    (76,74,72,71,69,67,69,71,72,71,69,67,65,64,65,62,
     64,67,72,74,76,67,72,76,69,72,77,76,74,72,71,74,
     72,71,72,64,67,64,72,71,69,72,76,77,76,74,76,72,
     71,69,67,65,64,65,67,69,67,65,64,62,60),
    (1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,
     .5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,
     .5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,
     .5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,.5,2)))
_prelude_patterns = ((60,64,67,72,76,67,72,76),(60,62,69,74,77,69,74,77),
                     (59+12,62,67,74,77,67,74,77),(60,64,67,72,76,67,72,76),
                     (60,64,69,76,81,69,76,81),(60,62,66,69,74,66,69,74),
                     (59+12,62,67,74,79,67,74,79),(59+12,60,64,67,72,64,67,72))
_prelude = tuple((note,.25) for pattern in _prelude_patterns for _ in range(2) for note in pattern)
_prelude += (((60,64,67,72),2),)
DEMOS += (
    ('entertainer', 'The Entertainer', 116,
     LayeredScore(_entertainer, walking_harmony(((60,64,67),(60,65,69),(62,67,71),(60,64,67)), 7))),
    ('turkish-march', 'Turkish March', 112,
     LayeredScore(_turkish, walking_harmony(((60,64,69),(62,65,69),(64,68,71),(60,64,69)), 5))),
    ('pachelbel-canon', 'Pachelbel Canon', 94,
     LayeredScore(_canon, walking_harmony(((60,64,67),(67,71,74),(60,64,69),(64,67,71),
                                          (60,65,69),(60,64,67),(60,65,69),(62,67,71)), 10))),
    ('bach-prelude', 'Bach Prelude in C', 96, LayeredScore(_prelude)),
)


class DemoPlayer:
    def __init__(self, index):
        self.events = []
        self.frame = 0
        self.cursor = 0
        score = DEMOS[index][3]
        scale = 60 / DEMOS[index][2] * RATE
        if isinstance(score, LayeredScore):
            events = score.events
            self.end = round(score.length * scale)
        else:
            events = []
            end = 0
            for note, beats in score:
                notes = () if note is None else (note,)
                events.append((end, max(beats * .8, beats - .045 * DEMOS[index][2] / 60), notes))
                end += beats
            self.end = round(end * scale)
        # Reference counts preserve sustained notes when layers share a pitch.
        boundaries = []
        for start, duration, notes in events:
            for note in notes:
                if not 60 <= note <= 84:
                    raise ValueError('Demo note outside the displayed keyboard')
                boundaries.append((round(start * scale), note - 60, 1))
                boundaries.append((round((start + duration) * scale), note - 60, -1))
        boundaries.sort()
        counts = [0] * 25
        self.events = []
        for frame, key, delta in boundaries:
            counts[key] += delta
            mask = sum(1 << i for i, count in enumerate(counts) if count > 0)
            if self.events and self.events[-1][0] == frame:
                self.events[-1] = (frame, mask)
            else:
                self.events.append((frame, mask))
        self.mask = 0

    def next_mask(self):
        while self.cursor < len(self.events) and self.frame >= self.events[self.cursor][0]:
            self.mask = self.events[self.cursor][1]
            self.cursor += 1
        self.frame += BLOCK
        return self.mask

    @property
    def finished(self):
        return self.frame >= self.end


class Synth:
    def __init__(self):
        self.voices = {}
        self.previous = 0
        self.octave = 0

    def render(self, mask, octave=0, sustain=False):
        if octave != self.octave:
            # Register changes never leave the old register ringing.
            self.voices.clear()
            self.previous = 0
            self.octave = octave
        for key in range(25):
            if mask & (1 << key) and not self.previous & (1 << key):
                self.voices[key] = [0, None]
        self.previous = mask
        mixed = [0.0] * BLOCK
        for key, voice in list(self.voices.items()):
            age, release = voice
            if not mask & (1 << key) and not sustain and release is None:
                release = 0
            midi = 60 + key + 12 * octave
            frequency = 440 * 2 ** ((midi - 69) / 12)
            seconds = age / RATE
            # Lower strings decay more slowly; high notes have a shorter, brighter body.
            decay_time = 2.8 * 2 ** ((60 - midi) / 36)
            decay = .75 * math.exp(-seconds / .65) + .25 * math.exp(-seconds / decay_time)
            brightness = math.exp(-seconds * 4)
            phase = (age * frequency / RATE % 1) * 4096
            second = (age * frequency * 1.0015 / RATE % 1) * 4096
            step = frequency * 4096 / RATE
            second_step = step * 1.0015
            for index in range(BLOCK):
                envelope = decay if age >= 192 else age / 192 * decay
                if release is not None:
                    envelope *= (1 - release / 2880) if release < 2880 else 0
                    release += 1
                position = int(phase)
                tone = SINE[position] + brightness * (BRIGHT[position] - SINE[position])
                # A second, very slightly detuned string adds gentle beating.
                sample = .8 * tone + .2 * SINE[int(second)]
                mixed[index] += .16 * envelope * sample
                phase += step
                second += second_step
                if phase >= 4096: phase -= 4096
                if second >= 4096: second -= 4096
                age += 1
            if (release is not None and release >= 2880) or age >= RATE * 15:
                del self.voices[key]
            else:
                self.voices[key] = [age, release]
        samples = array.array('h', (int(30000 * math.tanh(value)) for value in mixed))
        if os.sys.byteorder != 'little': samples.byteswap()
        return samples.tobytes()


class Piano:
    def __init__(self, preference=None):
        self.preference = Path(preference) if preference else Path.home() / '.config/omarchy/touchbar/piano-demo'
        self.settings_path = self.preference.with_name('piano-settings.json')
        self.octave = 0
        self.sustain = False
        try:
            settings = json.loads(self.settings_path.read_text())
            octave, sustain = settings['octave'], settings['sustain']
            if type(octave) is int and -2 <= octave <= 2 and type(sustain) is bool:
                self.octave, self.sustain = octave, sustain
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.demo_lock = threading.Lock()
        self.demo_index = 0
        self.demo = None
        self.demo_mask = 0
        try:
            saved = self.preference.read_text().strip()
            self.demo_index = next((i for i, song in enumerate(DEMOS) if song[0] == saved), 0)
        except OSError:
            pass
        self.process = None
        self.thread = None
        self.stop = threading.Event()
        self.mask = 0
        self.updated = 0
        self.status = 0

    def open(self):
        self.close()
        self.stop = threading.Event()
        try:
            self.process = subprocess.Popen(
                ['/usr/bin/pw-cat', '--playback', '--raw', '--format', 's16',
                 '--rate', str(RATE), '--channels', '1', '--latency', '10ms',
                 '--media-role', 'Music', '--properties', '{ node.name = omarchy-touchbar-piano node.description = "Touch Bar Piano" }', '-'],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, bufsize=0)
            os.set_blocking(self.process.stdin.fileno(), False)
            self.status = 1
            self.thread = threading.Thread(target=self._audio, daemon=True)
            self.thread.start()
        except OSError as exc:
            self.status = -1
            print('PIANO_AUDIO_ERROR ' + str(exc), flush=True)

    def notes(self, mask):
        if self.status == 1 and not self.demo_playing:
            self.mask = mask
            self.updated = time.monotonic()

    @property
    def demo_playing(self):
        with self.demo_lock:
            return self.demo is not None

    def select_demo(self, index):
        if not 0 <= index < len(DEMOS):
            return False
        # Commit preference first; report failure rather than claiming it persisted.
        self.preference.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.preference.with_name(self.preference.name + '.tmp')
        temporary.write_text(DEMOS[index][0] + '\n')
        temporary.replace(self.preference)
        with self.demo_lock:
            self.demo_index = index
            self.demo = None
            self.demo_mask = 0
            self.mask = 0
        return True

    def stop_demo(self):
        with self.demo_lock:
            self.demo = None
            self.demo_mask = 0
            self.mask = 0

    def demo_packet(self):
        with self.demo_lock:
            return f'PIANO DEMO {self.demo_index} {int(self.demo is not None)} {self.demo_mask}'

    def settings_packet(self):
        with self.demo_lock:
            return f'PIANO SETTINGS {self.octave} {int(self.sustain)}'

    def save_settings(self, octave, sustain):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_name(self.settings_path.name + '.tmp')
        temporary.write_text(json.dumps({'octave': octave, 'sustain': sustain}) + '\n')
        temporary.replace(self.settings_path)
        with self.demo_lock:
            if octave != self.octave:
                self.mask = 0
            self.octave, self.sustain = octave, sustain

    def apply(self, message, locked=False):
        shift = re.fullmatch(r'PIANO OCTAVE (-1|1)', message)
        if shift or message == 'PIANO SUSTAIN TOGGLE':
            if not locked and not self.demo_playing:
                octave = max(-2, min(2, self.octave + int(shift[1]))) if shift else self.octave
                sustain = self.sustain if shift else not self.sustain
                try:
                    self.save_settings(octave, sustain)
                except OSError as exc:
                    print('PIANO_PREFERENCE_ERROR ' + str(exc), flush=True)
            return True
        if message == 'PIANO CLOSE':
            self.close()
            return True
        if message == 'PIANO OPEN':
            if not locked:
                self.open()
            return True
        if message == 'PIANO DEMO STOP':
            self.stop_demo()
            return True
        if message == 'PIANO DEMO TOGGLE':
            if not locked and self.status == 1:
                with self.demo_lock:
                    self.mask = 0
                    self.demo_mask = 0
                    self.demo = None if self.demo else DemoPlayer(self.demo_index)
            return True
        selection = re.fullmatch(r'PIANO DEMO SELECT ([0-7])', message)
        if selection:
            if not locked:
                try:
                    self.select_demo(int(selection[1]))
                except OSError as exc:
                    print('PIANO_PREFERENCE_ERROR ' + str(exc), flush=True)
            return True
        match = re.fullmatch(r'PIANO NOTES ([0-9]{1,8})', message)
        if not match or int(match[1]) > MAX_MASK:
            return False
        if not locked:
            self.notes(int(match[1]))
        return True

    def packet(self):
        return 'PIANO STATUS ' + str(self.status)

    def _audio(self):
        synth = Synth()
        process = self.process
        next_block = time.monotonic()
        was_demo = False
        try:
            while not self.stop.is_set():
                self.stop.wait(max(0, next_block - time.monotonic()))
                next_block = max(next_block + BLOCK / RATE, time.monotonic())
                if process.poll() is not None:
                    raise OSError('PipeWire playback exited')
                # Independent watchdog: missing touch/bridge updates cannot sustain notes.
                mask = self.mask if time.monotonic() - self.updated < 1 else 0
                with self.demo_lock:
                    is_demo = self.demo is not None
                    octave, sustain = (0, False) if is_demo else (self.octave, self.sustain)
                    if self.demo is not None:
                        mask = self.demo.next_mask()
                        self.demo_mask = mask
                        if self.demo.finished:
                            self.demo = None
                            self.demo_mask = 0
                            self.mask = 0
                if is_demo != was_demo:
                    synth = Synth()
                    was_demo = is_demo
                block = memoryview(synth.render(mask, octave, sustain))
                while block and not self.stop.is_set():
                    _, writable, _ = select.select([], [process.stdin], [], .1)
                    if writable:
                        try:
                            count = os.write(process.stdin.fileno(), block)
                            block = block[count:]
                        except BlockingIOError:
                            pass
        except (OSError, ValueError) as exc:
            if not self.stop.is_set():
                self.status = -1
                self.stop_demo()
                print('PIANO_AUDIO_ERROR ' + str(exc), flush=True)
        finally:
            if process.poll() is None:
                process.terminate()

    def close(self):
        self.stop_demo()
        self.mask = 0
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=.5)
            self.thread = None
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=.5)
            self.process.stdin.close()
            self.process = None
        self.status = 0
