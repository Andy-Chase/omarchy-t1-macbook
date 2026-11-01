# Changes

## 0.1.0 release candidate — 1 November 2026

- Delayed desktop-bridge socket publication until compositor/session initialisation succeeds, correcting an observed early-boot environment race; added four regression checks. Installed hash and activation are verified; logout/login and full shutdown/power-on are owner-confirmed passed.
- Corrected read-only hardware detection to follow kernel sysfs aliases, keeping write-destination symlink protection and staged-root isolation intact.
- Consolidated the latest working Files/news Touch Bar source and current helper modules.
- Removed fixed account/UID assumptions in the supervisor and generated user unit.
- Added independent basic, desktop and Wi-Fi file installation plans and activation commands.
- Added backed-up adoption, upgrades, uninstall and interrupted-file-operation recovery.
- Added configurable optional-page layout and protocol gates, covering all 64 feature combinations.
- Added backlight device discovery, a fresh piano lock-state check and an explicit-location weather fallback.
- Added source-only release packaging, read-only diagnostics, offline tests and proposed CI.
- Built all five modules against 7.2.8-9-omarchy. Initial physical controls, lock/unlock, piano and news passed after migration with reused modules; camera capture is unavailable, and lifecycle/fresh-install validation remains pending.
