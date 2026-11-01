#!/usr/bin/python
# SPDX-License-Identifier: GPL-2.0-only
"""The only process that talks to Hyprland; runs as the desktop user."""
import argparse
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import struct
import subprocess
import time
import importlib.util
from weather import Weather
from media_buttons import MediaButtons
from nightlight import Nightlight
from battery_details import BatteryDetails
from wifi_details import WifiDetails
from markets import Markets
from piano import Piano
from file_shortcuts import FileShortcuts
from news import News
from settings import enabled_features

_media_spec=importlib.util.spec_from_file_location('touchbar_media',Path(__file__).with_name('media-controls.py'))
_media_module=importlib.util.module_from_spec(_media_spec);_media_spec.loader.exec_module(_media_module)
MediaControls=_media_module.MediaControls
app_processes=[]

def launch_chatgpt():
    """Focus only the actual desktop app class, or launch its fixed executable."""
    clients=json.loads(subprocess.check_output(['/usr/bin/hyprctl','-j','clients'],text=True,timeout=2))
    for window in clients:
        if str(window.get('class','')).lower()!='chatgpt':continue
        address=str(window.get('address',''))
        if not re.fullmatch(r'0x[0-9a-fA-F]+',address):continue
        result=subprocess.run(['/usr/bin/hyprctl','dispatch',f'hl.dsp.focus({{ window = "address:{address}" }})'],capture_output=True,text=True,timeout=2)
        if result.returncode:raise RuntimeError('ChatGPT focus failed')
        return 'focused'
    process=subprocess.Popen(['/usr/bin/uwsm-app','--','/usr/bin/chatgpt'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    app_processes.append(process)
    return 'launched'

def query():
    def get(command):
        return json.loads(subprocess.check_output(['/usr/bin/hyprctl','-j',command],text=True,timeout=2))
    workspaces=get('workspaces');active=get('activeworkspace')['id']
    occupied=sum(1<<w['id'] for w in workspaces if 1<=w['id']<=10 and w['windows']>0)
    present=sum(1<<w['id'] for w in workspaces if 1<=w['id']<=10)
    return active if 1<=active<=10 else 0,occupied,present

def screen_locked():
    """Omarchy owns the session lock; logind alone does not reflect it."""
    try:
        result=subprocess.run(['/usr/bin/omarchy','shell','lock','isLocked'],capture_output=True,text=True,timeout=1.5)
        value=result.stdout.strip()
        if result.returncode or value not in ('true','false'):return True
        if value=='true':return True
        session=os.environ.get('XDG_SESSION_ID','')
        if session:
            result=subprocess.run(['/usr/bin/loginctl','show-session',session,'-p','LockedHint','--value'],capture_output=True,text=True,timeout=1)
            if result.returncode or result.stdout.strip() not in ('yes','no'):return True
            if result.stdout.strip()=='yes':return True
        return False
    except (OSError,subprocess.TimeoutExpired):return True

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--socket',required=True);parser.add_argument('--snapshot',action='store_true');parser.add_argument('--persistent',action='store_true')
    args=parser.parse_args()
    if args.snapshot:
        print(json.dumps(dict(zip(('active','occupied_mask','present_mask'),query()))));return
    uid=os.getuid();assert uid>0
    runtime=Path(os.environ['XDG_RUNTIME_DIR']);path=Path(args.socket)
    assert runtime==Path(f'/run/user/{uid}') and path.parent==runtime and path.name.startswith('omarchy-touchbar-selector-')
    if args.persistent and path.exists():
        assert path.is_socket() and path.lstat().st_uid==uid
        path.unlink()
    assert not path.exists()
    # The user manager can start us before UWSM imports Hyprland's environment.
    # Leave no renderer socket behind until compositor/session setup succeeds;
    # systemd retries with the updated environment instead of resetting the T1.
    signature=os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')
    if not signature:raise RuntimeError('Hyprland environment not ready; waiting for service restart')
    sel=selectors.DefaultSelector()
    event=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);event.settimeout(2)
    event.connect(str(runtime/'hypr'/signature/'.socket2.sock'))
    event.setblocking(False);sel.register(event,selectors.EVENT_READ,'hypr')
    client=None;buffer=b'';stopped=False;deadline=float('inf') if args.persistent else time.monotonic()+720
    last_heartbeat=0;last_action=0;state=query();locked=screen_locked();last_sent=None
    media=MediaControls();last_media=0;last_levels=None;last_app_action=0
    buttons=MediaButtons();last_buttons=None;last_button_action=0
    nightlight=Nightlight();last_nightlight=None;last_nightlight_action=0
    battery_details=BatteryDetails();last_battery_details=None;last_profile_action=0
    wifi_details=WifiDetails();last_wifi_details=None
    markets=Markets();last_markets=None
    files=FileShortcuts(app_processes);last_files_action=0
    news=News(app_processes);last_news=None
    piano=Piano();last_piano=None;last_piano_demo=None;last_piano_settings=None
    features=enabled_features()
    weather=Weather(start="weather" in features);last_weather=None
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    def disconnect():
        nonlocal client,stopped
        wifi_details.show(False)
        news.show(False)
        markets.show(False)
        piano.close()
        sel.unregister(client);client.close();client=None
        if not args.persistent:stopped=True
    server=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    old=os.umask(0o077)
    try:server.bind(str(path))
    finally:os.umask(old)
    server.listen(1);server.setblocking(False);sel.register(server,selectors.EVENT_READ,'server')
    print('BRIDGE_READY '+str(path),flush=True)
    try:
        while not stopped and time.monotonic()<deadline:
            app_processes[:]=[p for p in app_processes if p.poll() is None]
            for key,_ in sel.select(.02 if piano.demo_playing else .2):
                if key.data=='server':
                    connection,_=server.accept()
                    pid,peer_uid,gid=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                    if peer_uid not in (0,uid) or client is not None:
                        connection.close();continue
                    client=connection;client.setblocking(False);sel.register(client,selectors.EVENT_READ,'client')
                    if not args.persistent:deadline=time.monotonic()+720
                    last_heartbeat=0;last_media=0;last_levels=None;last_weather=None;last_buttons=None;last_nightlight=None;last_battery_details=None;last_wifi_details=None
                    last_piano=None;last_piano_demo=None;last_piano_settings=None;piano.close()
                    last_news=None;news.show(False)
                    last_markets=None;markets.show(False)
                    wifi_details.show(False)
                    print('BRIDGE_CONNECTED peer_uid='+str(peer_uid),flush=True)
                elif key.data=='hypr':
                    data=event.recv(65536)
                    if not data:raise RuntimeError('Compositor event connection closed')
                    buffer+=data
                    lines=buffer.split(b'\n');buffer=lines.pop()
                    if len(buffer)>65536:raise RuntimeError('Oversized compositor event')
                    relevant=(b'workspace',b'focusedmon',b'createworkspace',b'destroyworkspace',b'openwindow',b'closewindow',b'movewindow',b'monitor',b'configreloaded')
                    if any(line.split(b'>>',1)[0].startswith(relevant) for line in lines):state=query();last_heartbeat=0
                else:
                    try:data=client.recv(128)
                    except ConnectionResetError:
                        disconnect();break
                    if not data:disconnect();break
                    message=data.decode('ascii',errors='strict')
                    optional={'NEWS ':'news','PIANO ':'piano','WEATHER ':'weather','MARKETS ':'markets','FILES ':'files','APP CHATGPT':'chatgpt'}
                    if any(message.startswith(prefix) and feature not in features for prefix,feature in optional.items()):continue
                    if message.startswith('NEWS '):
                        try:
                            news.apply(message,locked=locked or screen_locked())
                        except (OSError,ValueError,subprocess.SubprocessError) as exc:
                            print('NEWS_ERROR '+str(exc),flush=True)
                        continue
                    if message.startswith('PIANO '):
                        piano.apply(message,locked=locked or screen_locked())
                        if not message.startswith('PIANO NOTES '):last_piano=None;last_piano_demo=None;last_piano_settings=None
                        continue
                    if message=='WEATHER REFRESH':
                        weather.refresh();continue
                    if message=='BATTERY REFRESH':
                        battery_details.refresh();continue
                    if message in ('WIFI OPEN','WIFI CLOSE'):
                        wifi_details.show(message=='WIFI OPEN' and not locked and not screen_locked());continue
                    if message in ('MARKETS OPEN','MARKETS CLOSE'):
                        markets.show(message=='MARKETS OPEN' and not locked and not screen_locked());continue
                    if message=='POWER PROFILE NEXT':
                        if time.monotonic()-last_profile_action<.3 or locked or screen_locked():continue
                        last_profile_action=time.monotonic()
                        battery_details.cycle_profile();continue
                    if message=='BUTTON NIGHTLIGHT':
                        if time.monotonic()-last_nightlight_action<.3 or locked or screen_locked():continue
                        last_nightlight_action=time.monotonic()
                        nightlight.toggle()
                        continue
                    if message in ('BUTTON MIC_MUTE','BUTTON PLAYPAUSE','BUTTON SPEAKER_MUTE'):
                        if time.monotonic()-last_button_action<.3:continue
                        last_button_action=time.monotonic()
                        try:
                            if message=='BUTTON SPEAKER_MUTE':
                                if media.apply(message):print('MEDIA_'+message,flush=True)
                                last_levels=None;last_media=0
                            elif buttons.apply(message):print('MEDIA_'+message,flush=True)
                        except (OSError,subprocess.SubprocessError,RuntimeError) as exc:
                            print('MEDIA_BUTTON_ERROR '+str(exc),flush=True)
                        continue
                    if message.startswith('FILES '):
                        if time.monotonic()-last_files_action<.5 or locked or screen_locked():continue
                        last_files_action=time.monotonic()
                        try:
                            if files.apply(message):print('FILES_OPENED',flush=True)
                        except (OSError,ValueError,subprocess.SubprocessError,RuntimeError) as exc:
                            print('FILES_ERROR '+str(exc),flush=True)
                        continue
                    if message=='APP CHATGPT':
                        if time.monotonic()-last_app_action<1 or locked or screen_locked():continue
                        last_app_action=time.monotonic()
                        try:print('APP_ACTION CHATGPT '+launch_chatgpt(),flush=True)
                        except (OSError,ValueError,subprocess.SubprocessError,RuntimeError) as exc:print('APP_ERROR '+str(exc),flush=True)
                        continue
                    if message.startswith(('SET ','STEP ')):
                        try:
                            if media.apply(message):
                                print('MEDIA_ACTION '+message,flush=True);last_levels=None
                        except (OSError,ValueError,subprocess.SubprocessError,RuntimeError) as exc:
                            print('MEDIA_ERROR '+str(exc),flush=True);last_levels=None
                        continue
                    match=re.fullmatch(r'WS ([1-9]|10)',message)
                    if not match or time.monotonic()-last_action<.12:continue
                    if locked or screen_locked():
                        locked=True;last_heartbeat=0;continue
                    target=int(match[1]);last_action=time.monotonic()
                    result=subprocess.run(['/usr/bin/hyprctl','dispatch',f'hl.dsp.focus({{ workspace = "{target}" }})'],capture_output=True,text=True,timeout=2)
                    print(f'WORKSPACE_ACTION target={target} exit={result.returncode}',flush=True)
                    if result.returncode:raise RuntimeError('Workspace dispatcher failed')
                    state=query();last_heartbeat=0
            if client and time.monotonic()-last_heartbeat>=1:
                locked=screen_locked()
                if locked:wifi_details.show(False)
                if locked:markets.show(False)
                if locked:piano.close()
                if locked:news.show(False)
                packet=f'STATE {state[0]} {state[1]} {state[2]} {int(locked)}'
                try:client.send(packet.encode('ascii'))
                except (BrokenPipeError,ConnectionResetError):disconnect();continue
                last_heartbeat=time.monotonic()
                if packet!=last_sent:print(packet,flush=True);last_sent=packet
            if client and time.monotonic()-last_media>=1:
                levels='LEVELS '+' '.join(map(str,media.levels()))
                if levels!=last_levels:
                    try:client.send(levels.encode('ascii'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    last_levels=levels
                last_media=time.monotonic()
                forecast=tuple(weather.packets())
                if forecast!=last_weather:
                    try:
                        for weather_packet in forecast:client.send(weather_packet.encode('ascii'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    except BlockingIOError:continue
                    last_weather=forecast
                    print('WEATHER_STATE status='+forecast[0].split()[1]+' hourly_days='+str(len(forecast)-1),flush=True)
            if client:
                news_packet=news.packet()
                if news_packet!=last_news:
                    try:client.send(news_packet.encode('utf-8'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    except BlockingIOError:continue
                    last_news=news_packet
                settings_packet=piano.settings_packet()
                if settings_packet!=last_piano_settings:
                    try:client.send(settings_packet.encode('ascii'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    except BlockingIOError:continue
                    last_piano_settings=settings_packet
                piano_packet=piano.packet()
                if piano_packet!=last_piano:
                    try:client.send(piano_packet.encode('ascii'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    except BlockingIOError:continue
                    last_piano=piano_packet
                demo_packet=piano.demo_packet()
                if demo_packet!=last_piano_demo:
                    try:client.send(demo_packet.encode('ascii'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    except BlockingIOError:continue
                    last_piano_demo=demo_packet
                market_packets=markets.packets()
                if market_packets!=last_markets:
                    try:
                        for market_packet in market_packets:client.send(market_packet.encode('ascii'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    except BlockingIOError:continue
                    last_markets=market_packets
                wifi_packet=wifi_details.packet()
                if wifi_packet!=last_wifi_details:
                    try:client.send(wifi_packet.encode('utf-8'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    last_wifi_details=wifi_packet
                battery_packet=battery_details.packet()
                if battery_packet!=last_battery_details:
                    try:client.send(battery_packet.encode('ascii'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    last_battery_details=battery_packet
                nightlight_packet=nightlight.packet()
                if nightlight_packet!=last_nightlight:
                    try:client.send(nightlight_packet.encode('ascii'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    last_nightlight=nightlight_packet
                auxiliary=buttons.packet()
                if auxiliary!=last_buttons:
                    try:client.send(auxiliary.encode('ascii'))
                    except (BrokenPipeError,ConnectionResetError):disconnect();continue
                    last_buttons=auxiliary
                    print(auxiliary,flush=True)
    finally:
        news.close()
        piano.close()
        if client:client.close()
        server.close();event.close();sel.close();path.unlink(missing_ok=True)
        print('BRIDGE_FINISHED',flush=True)

if __name__=='__main__':main()
