# Validation record and physical checklist

Prepared 1 November 2026 for the 0.1.0 release candidate. Results here distinguish the earlier working reference implementation from the new portable candidate.

## Completed candidate checks

- C userspace build with `-std=c11 -O2 -Wall -Wextra -Werror` against Cairo 1.18.6 and libdrm 2.4.134.
- Renderer logic self-test and touch layout checks for all 64 optional-feature combinations.
- Python suites for file launch restrictions, RSS parsing/cache/navigation, real Python-to-C news packets, installer state transitions, destination guards, interrupted-operation recovery and explicit-location weather parsing.
- Piano synthesis, all eight demo arrangements, persistence, playback failure/cleanup, octave and sustain checks. Audio is verified through test streams, not a fresh speaker audition.
- Direct CLI clean staging install of basic and desktop components, followed by uninstall using each staged offline recovery command. Host systemd/DKMS/network/boot tools weren't invoked.
- Build of `apple-ibridge`, `apple-ib-tb`, `apple-ib-als`, `appletbdrm` and `omarchy-t1-raw` against **7.2.8-9-omarchy** headers. The legacy source emits two upstream warnings (missing prototype and an unhandled enum member); builds succeed. The userspace build is warning-free with warnings treated as errors.
- Read-only diagnostic collection and software-rendered core/full/news/piano previews. Previews use synthetic state and exclude user-owned image assets.
- Pacman identifies **omarchy 4.0.6-1**. Inspection of `omarchy-version` confirms it reads the package version; the `4.0.0.alpha` source file isn't the authoritative installed-package result.

## Desktop test confirmation

On 1 November 2026 the user supplied the complete result of `make test-full` from the regular desktop terminal: all **46 Python tests**, renderer logic/layout checks, the three piano suites and the full socket renderer check passed, ending in **SELF_TEST_OK**. All packaged code was checked unchanged against the prior candidate. See `desktop-test-result.json` for the recorded source/binary hashes. The intentional piano failure-path messages aren't failed tests.

The first live installation attempt then stopped before file mutation: the write-destination guard rejected the normal `/sys/class/dmi/id` symlink during a hardware read. Hardware probes now use a separate resolver that permits aliases contained in sysfs while retaining the original write guard. All 16 installer tests passed after the fix, including two new regression cases. Read-only probes against the actual laptop passed for both T1/model and BCM43602/subsystem identity. Renderer/runtime code was unchanged from the passing desktop test at that point; the subsequent migration completed file installation and service readiness. The installed renderer, bridge, supervisor and configuration loader were independently matched to the candidate. See `activation-result.json`. The owner then confirmed all requested initial physical checks passed: display/touch, Escape/workspaces, all three sliders, lock/unlock, piano and news. The subsequent camera check exposed only a media-controller node and no capture device; capture is unavailable in this session. The owner subsequently confirmed logout/login, full shutdown/power-on and existing Wi-Fi reconnection. The latest boot still reports the UVC error; further lifecycle checks remain pending. See `CAMERA.md`.

## Boot follow-up and bridge correction

After another owner-supplied camera enumeration, the current journal confirms a new boot. Capture still exposes only a media-controller node. The Touch Bar starts automatically and reaches renderer readiness after a retry: the first bridge process lacked `HYPRLAND_INSTANCE_SIGNATURE`, published a socket and exited; the renderer then failed to connect and the supervisor restored/retried the T1. The source now delays publishing its renderer socket until compositor connection and initial session setup succeed. All **52 Python tests** passed, including four regressions for missing environment, unavailable compositor, failed initial state and ready-session publication/cleanup. The owner subsequently installed and activated this bridge correction. Its installed SHA256 matches the source, and the new service invocation reached bridge/renderer readiness without a renderer failure. The owner then confirmed the corrected bridge returns after logout/login and full shutdown/power-on, with existing Wi-Fi reconnecting. The latest boot defers bridge startup until Hyprland is ready, then reaches renderer readiness without a renderer failure or supervisor hardware reset. Renderer/supervisor/kernel sources are unchanged. This confirms the selected existing Wi-Fi configuration reconnects; it does not validate the portable Wi-Fi installer or all access points.

## Still blocked or unverified

The authoring environment permits creating a local socket pair but rejects its send operation with **EPERM**. The full renderer self-test reports exit **77** there. The separate unrestricted desktop result above now establishes a full protocol pass.

The process has **NoNewPrivs=1**, and system installation directories aren't writable. The user subsequently completed desktop file installation and service activation using the already working modules. Fresh DKMS installation, Wi-Fi/boot changes and hardware lifecycle tests remain unverified. No public repository, post or email has been created/sent. The prepared CI workflow hasn't run on GitHub yet.

## Desktop validation sequence

1. **Completed:** the regular desktop `make test-full` passed, including `SELF_TEST_OK`.
2. Inspect `python3 tools/doctor.py` and the installation plan. Use the Migration guide for the existing reference setup, preserving the already working modules for initial testing.
3. **Completed for existing-module migration:** installation and service readiness passed; installed code matched the candidate at migration. The subsequent bridge startup fix is installed, activation passed, and owner-confirmed logout/login and full shutdown/power-on passed. Initial physical operation is now owner-confirmed; camera capture is unavailable in the current session; suspend/resume, rollback and independent installation remain pending.
4. Confirm the checks below individually. Record exact release/commit, kernel, result and any recovery required. Save work before lifecycle tests.

| Check | Earlier reference implementation | Portable candidate |
| --- | --- | --- |
| Upright display and aligned touch | Physically confirmed | Owner-confirmed passed |
| Escape and workspace switching | Physically confirmed | Owner-confirmed passed |
| Brightness, volume, keyboard-light sliders | Physically confirmed | Owner-confirmed passed |
| Lock label and restoration after unlock | Physically confirmed after correction | Owner-confirmed passed |
| Newly configurable optional pages | Not applicable | Piano/news physically passed; other pages pending |
| Logout/login and cold boot | No complete final-build record | Owner-confirmed passed with the installed bridge correction; latest boot reaches renderer readiness without a renderer failure or hardware reset. |
| Existing Wi-Fi after full shutdown/power-on | Earlier personalised workaround | Owner-confirmed reconnect; portable Wi-Fi installer remains untested |
| Camera after T1 reconfiguration | Early UVC issue recorded | Capture unavailable: media-controller node only; zero format descriptors; same UVC error persists in the confirmed full-power-on boot. |
| Sleep/hibernate recovery | Unresolved/untested cases | Pending |
| Original-control recovery | Startup restored illumination; same-session return could stay dark | Pending |
| New Wi-Fi installer, UKI inclusion and uninstall | Earlier personalised procedure tested | Pending |
| Independent clean installation | Not established | Pending |

## Privacy and feedback

Use `python3 tools/doctor.py` as the default public diagnostic. Don't upload installation manifests, personalised board files, raw EFI variables, Apple firmware/FDRData or complete journals. If additional logs are needed, redact private network/device identifiers and unrelated personal data first.

The software previews in `previews/` show rendered layouts, not photographs of the physical Touch Bar. A short phone video will be more useful for demonstrating hardware operation after candidate validation.
