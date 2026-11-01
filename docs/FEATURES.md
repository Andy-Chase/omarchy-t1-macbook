# Optional pages and controls

Core controls need no online data: Escape, workspaces, F-keys, brightness/keyboard-light/volume sliders, playback/mute, nightlight, battery and power details, and a timer with 5/10/15/25-minute presets, pause/resume/clear and blue overtime.

Select optional pages at installation with `--features` and reactivate after changes. Their buttons close up unused space. For example:

```bash
sudo python3 tools/manage.py install --component desktop --user "$USER" --features files,piano,news
```

| Name | Behaviour and dependency |
| --- | --- |
| `files` | Single tap opens Home/Documents/Downloads/Desktop/Pictures/Music/Videos. Uses XDG folder settings and the default file manager |
| `piano` | 25 keys, octave shifts, sustain and eight short synthesised demos. Uses the user's PipeWire output through `pw-cat` |
| `news` | AI/The Verge and Global/BBC World RSS feeds. Cached headlines, category selection, swipe previous/next, tap article and hold to return to categories |
| `weather` | Seven-day and hourly forecasts. Uses the existing Omarchy shared-weather IPC extension where available; optional explicit-location fallback described below |
| `markets` | Delayed/cached gold, silver, ASX 200, S&P 500, Bitcoin and AUD pairs. Depends on external provider availability |
| `chatgpt` | Focuses the installed native desktop app or launches `/usr/bin/chatgpt`; it doesn't send chat text. Without that app, leave the feature off |

Disabled optional pages can't invoke their backend commands through the bridge. Piano audio and news/market requests don't start merely because their helper modules are installed. Weather's worker isn't started when that feature is disabled.

## Weather without the local shell extension

The original laptop has a customised shared weather IPC endpoint. A fresh Omarchy install may not have it. To use weather independently, explicitly supply latitude and longitude:

```bash
sudo python3 tools/manage.py install --component desktop --user "$USER" --features weather --weather-location=52.52,13.41
```

Those are example coordinates, not automatic location detection. Substitute the desired location. The fallback sends these coordinates to [Open-Meteo](https://open-meteo.com/) only when the shared source is unavailable. It refreshes at most every 15 minutes after a successful fetch, with bounded response size/timeouts and local caching. It doesn't add a second weather request loop when the shared source works. Temperature is Celsius for the fallback; shared-source units retain their original behaviour. See the [provider documentation](https://open-meteo.com/en/docs) and usage terms.

This new fallback has offline parsing/packet tests; live provider access and physical forecast presentation still need verification. Failed or missing data shows unavailable/cached states rather than invented values.

## Behaviour carried forward

The timer doesn't survive renderer restart and doesn't schedule automatic breaks or audible alerts. Piano demo selection persists; manual keys are disabled during demos, which stop on leaving/locking. News keeps Escape and battery available. Media controls intentionally remain usable while locked, while workspace/app/folder actions are blocked. Unknown lock state disables desktop actions.

The eight piano demos are Für Elise, Ode to Joy, Twinkle Twinkle, Greensleeves, The Entertainer, Turkish March, Pachelbel Canon and Bach Prelude in C. The earlier joke sound feature isn't included.
