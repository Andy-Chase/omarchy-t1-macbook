# SPDX-License-Identifier: GPL-2.0-only
#!/usr/bin/python
"""Supervise the fixed, local Touch Bar renderer; never execute session commands."""
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
from config import load_config

BASE=Path('/usr/local/lib/omarchy-touchbar')
RUNTIME=Path('/run/omarchy-touchbar')
CONFIG=load_config()
UID=CONFIG['uid']
SOCKET=Path(f'/run/user/{UID}/omarchy-touchbar-selector-desktop.sock')
STOP=False

def run(*args,check=True):
    result=subprocess.run(args,text=True,capture_output=True,timeout=25)
    if result.stdout.strip():print(result.stdout.strip(),flush=True)
    if result.stderr.strip():print(result.stderr.strip(),flush=True)
    if check and result.returncode:raise RuntimeError(f'{args[0]} failed ({result.returncode})')
    return result

def device():
    if Path('/sys/class/dmi/id/product_name').read_text().strip()!='MacBookPro13,2':raise RuntimeError('Unsupported hardware')
    found=[p.parent for p in Path('/sys/bus/usb/devices').glob('*/idProduct') if p.read_text().strip()=='8600' and (p.parent/'idVendor').read_text().strip()=='05ac']
    if len(found)!=1:raise RuntimeError('Expected exactly one Apple T1 USB device')
    return found[0]

def nodes(usb):
    parent=str(usb.resolve())+'/'
    cards=[p for p in Path('/sys/class/drm').glob('card[0-9]*') if p.name[4:].isdigit() and (p/'device/driver').exists() and (p/'device/driver').resolve().name=='appletbdrm' and str((p/'device').resolve()).startswith(parent)]
    inputs=[]
    for node in Path('/sys/class/hidraw').glob('hidraw*'):
        dev=node/'device'
        if not str(dev.resolve()).startswith(parent):continue
        if (dev/'report_descriptor').read_bytes().startswith(bytes((5,13,9,5,161,1))):
            if not (dev/'driver').exists() or (dev/'driver').resolve().name!='omarchy-t1-digitizer':raise RuntimeError('Digitizer is not bound to the raw-only driver')
            inputs.append('/dev/'+node.name)
    if len(cards)!=1 or len(inputs)!=1:raise RuntimeError('Validated display/digitizer pair is unavailable')
    return '/dev/dri/'+cards[0].name,inputs[0]

def restore():
    if not (RUNTIME/'state.json').exists():return
    usb=device();saved=json.loads((RUNTIME/'state.json').read_text())
    if (usb/'bConfigurationValue').read_text().strip()!='1':
        (usb/'bConfigurationValue').write_text('0');time.sleep(.5)
        (usb/'bConfigurationValue').write_text('1');time.sleep(1)
    for module in ('appletbdrm','omarchy_t1_raw'):
        if Path('/sys/module',module).exists():run('modprobe','-r',module)
    run('/usr/local/sbin/apple-t1-touchbar-bind')
    for name,value in saved['settings'].items():
        for path in Path('/sys/bus/hid/devices').glob('0003:05AC:8600.*/'+name):path.write_text(value)
    (usb/'power/control').write_text(saved['power_control'])
    run('systemctl','--no-block','start','apple-t1-touchbar.service',check=False)
    owners=[p.resolve().name for p in Path('/sys/bus/hid/devices').glob('0003:05AC:8600.*/driver')]
    if len(owners)!=2 or any(p!='apple-ibridge-hid' for p in owners):raise RuntimeError('Original HID ownership not restored')
    (RUNTIME/'state.json').unlink()
    print('ORIGINAL_BINDINGS_RESTORED illumination_requires_power_on_check=1',flush=True)

def prepare():
    usb=device()
    if (usb/'bConfigurationValue').read_text().strip()=='2':return nodes(usb)
    if (usb/'bConfigurationValue').read_text().strip()!='1':raise RuntimeError('Unexpected T1 USB configuration')
    # Fail before detaching the normal driver if a kernel update lacks modules.
    for module in ('appletbdrm','omarchy_t1_raw'):run('modinfo','-F','filename',module)
    settings={}
    for name in ('fnmode','idle_timeout','dim_timeout'):
        for p in Path('/sys/bus/hid/devices').glob('0003:05AC:8600.*/'+name):settings[name]=p.read_text()
    (RUNTIME/'state.json').write_text(json.dumps({'settings':settings,'power_control':(usb/'power/control').read_text()},indent=2)+'\n')
    run('systemctl','stop','apple-t1-touchbar.service')
    run('modprobe','-r','apple_ib_tb','apple_ib_als','apple_ibridge')
    run('modprobe','omarchy_t1_raw');run('modprobe','appletbdrm')
    (usb/'power/control').write_text('on')
    (usb/'bConfigurationValue').write_text('0');time.sleep(.5)
    (usb/'bConfigurationValue').write_text('2')
    run('udevadm','settle','--timeout=15');time.sleep(1)
    result=nodes(usb)
    print('GRAPHICS_READY '+json.dumps({'card':result[0],'digitizer':result[1]}),flush=True)
    return result

def bridge_ready():
    try:
        st=SOCKET.lstat()
        return stat.S_ISSOCK(st.st_mode) and st.st_uid==UID and st.st_mode&0o077==0
    except FileNotFoundError:return False

def status(**fields):
    (RUNTIME/'status.json').write_text(json.dumps(fields,indent=2)+'\n')

def main():
    global STOP
    if os.geteuid()!=0:raise RuntimeError("Hardware supervisor requires root")
    RUNTIME.mkdir(mode=0o700,exist_ok=True)
    device()
    if sys.argv[1:]==['--restore']:restore();return
    if sys.argv[1:]:raise RuntimeError('Unknown argument')
    def stop(*_):
        global STOP
        STOP=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    child=None;failures=0
    print('SUPERVISOR_READY waiting_for_desktop=1',flush=True)
    try:
        while not STOP:
            if not bridge_ready():
                status(state='waiting-for-desktop');time.sleep(1);continue
            try:
                card,digitizer=prepare()
                started=time.monotonic()
                child=subprocess.Popen([str(BASE/'selector'),card,digitizer,str(SOCKET),str(UID),'0','--persistent'],env=dict(os.environ,OMARCHY_T1_FEATURES=','.join(CONFIG['features'])))
                status(state='running',renderer_pid=child.pid,card=card,digitizer=digitizer)
                while child.poll() is None and not STOP:time.sleep(.5)
                if STOP:
                    child.terminate()
                    try:child.wait(timeout=5)
                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
                    break
                code=child.returncode;child=None
                if time.monotonic()-started>30:failures=0
                if bridge_ready() and code:raise RuntimeError(f'Renderer exited {code}')
                print('RENDERER_DISCONNECTED waiting_for_desktop=1',flush=True)
                time.sleep(2)
            except Exception as exc:
                failures+=1;print('SUPERVISOR_ERROR '+str(exc),flush=True)
                status(state='retrying',error=str(exc),attempt=failures)
                # Rebuild USB display state and drain replies after a real error.
                restore()
                if failures>=3:raise RuntimeError('Three consecutive renderer failures; original bindings restored') from exc
                time.sleep(3)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        restore()
        status(state='stopped')

if __name__=='__main__':main()
