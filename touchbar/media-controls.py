#!/usr/bin/python
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded user-session media controls for the physical Touch Bar sliders."""
import re
from pathlib import Path
import subprocess

class MediaControls:
    def __init__(self):
        displays=sorted(Path('/sys/class/backlight').glob('*'))
        keyboards=sorted(Path('/sys/class/leds').glob('*kbd_backlight*'))
        self.devices={'B':displays[0] if displays else None,'K':keyboards[0] if keyboards else None}

    def run(self,*args):
        return subprocess.run(args,check=True,text=True,capture_output=True,timeout=1).stdout.strip()

    def sink(self):
        sink=self.run('/usr/bin/omarchy','audio','output','sink')
        if not sink or '\n' in sink:raise RuntimeError('Output sink unavailable')
        return sink

    def light(self,kind):
        path=self.devices[kind]
        if path is None:raise FileNotFoundError("Backlight device unavailable")
        return int((path/'brightness').read_text()),int((path/'max_brightness').read_text())

    def levels(self):
        values=[]
        for kind in ('B','K'):
            try:
                current,maximum=self.light(kind);values.append(min(100,max(0,round(current*100/maximum))))
            except (OSError,ValueError,ZeroDivisionError):values.append(-1)
        volume=-1;muted=0
        try:
            sink=self.sink();raw=self.run('/usr/bin/pactl','get-sink-volume',sink)
            match=re.search(r'(\d+)%',raw)
            if match:volume=min(100,int(match[1]))
            muted=int(self.run('/usr/bin/pactl','get-sink-mute',sink).endswith('yes'))
        except (OSError,subprocess.SubprocessError,RuntimeError):pass
        return values[0],volume,values[1],muted

    def apply(self,message):
        if message=='BUTTON SPEAKER_MUTE':
            self.run('/usr/bin/pactl','set-sink-mute',self.sink(),'toggle')
            return True
        match=re.fullmatch(r'(SET|STEP) ([BVK]) (-?\d{1,3})',message)
        if not match:return False
        action,kind,value=match.groups();value=int(value)
        if action=='SET' and not (0<=value<=100):return False
        if action=='STEP' and value not in (-1,1):return False
        if kind=='V':
            sink=self.sink()
            if action=='STEP':
                raw=self.run('/usr/bin/pactl','get-sink-volume',sink);found=re.search(r'(\d+)%',raw)
                if not found:raise RuntimeError('Volume unavailable')
                value=max(0,min(100,int(found[1])+value))
            self.run('/usr/bin/pactl','set-sink-volume',sink,f'{value}%')
            self.run('/usr/bin/pactl','set-sink-mute',sink,'0')
        else:
            current,maximum=self.light(kind);minimum=1 if kind=='B' else 0
            if action=='SET':target=round(max(minimum,value)*maximum/100)
            else:
                target=round((round(current*100/maximum)+value)*maximum/100)
                if target==current:target+=value
            target=max(minimum,min(maximum,target))
            self.run('/usr/bin/brightnessctl','-q','-d',self.devices[kind].name,'set',str(target))
        return True
