# 0.1.0-rc1 — experimental community source release

Prepared 1 November 2026. This candidate is ready for public-source review; it hasn't been published. It targets **MacBookPro13,2 / A1706 / 2016 13-inch T1**, Omarchy **4.0.6-1**, kernel **7.2.8-9-omarchy**. Other models and kernels need separate evidence.

## Included

Separate basic Touch Bar, custom desktop Touch Bar and BCM43602 Wi-Fi components, with source builds, explicit activation, private original-file backups and per-component offline recovery commands. The custom strip includes workspace controls, Escape/F-keys, sliders, lock awareness and configurable optional pages. Optional pages default off. Kernel and userspace sources, provenance, licences, source checksums and an automated-check workflow are included.

## Established results

- 52 Python behavioural tests passed, including the startup-race regression checks. The owner separately supplied a passing full renderer socket test and piano suites before the bridge correction; the renderer and piano code didn't change for that correction.
- All five kernel modules compiled for the reference kernel. Live desktop migration reused the already working module providers; compiled replacement modules haven't been installed through the fresh DKMS path.
- Owner-confirmed display/touch, Escape/workspaces, three sliders, lock/unlock, piano and news.
- Owner-confirmed Touch Bar return after logout/login and full shutdown/power-on with the corrected bridge. The latest boot waits for Hyprland before exposing the bridge socket and reaches renderer readiness without a renderer failure or hardware-reset retry.
- Owner-confirmed existing Wi-Fi reconnect after full power-on. Earlier short traffic tests passed for the documented 2.4 GHz mode and a separate 5 GHz mode; this isn't a test of the new portable Wi-Fi installer.
- Isolated-root file installation, upgrade, interrupted-operation recovery and uninstall passed. These are file-level results, not proof of live hardware rollback.

## Limits to read before installation

Camera capture is unavailable: only a media-controller node is listed, with zero advertised video formats and the same UVC error in original/custom modes. No camera repair is provided. Suspend/resume, live original-control recovery, fresh DKMS installation, portable Wi-Fi/boot provisioning and independent-owner installation remain unverified. Firmware controls may need full power-off/on to relight after restoration. Both Wi-Fi bands together, some public-network joins and hibernation recovery remain unresolved. Some optional data pages depend on third-party providers.

This is an experimental single-machine release for informed testers. It isn't yet a generally recommended installation for the community. No Apple firmware, private calibration, personal network profiles or personalised board file is distributed.

## What testers can contribute

Use [Installation](INSTALL.md) or [Migration](MIGRATION.md), inspect the plan before activation and retain recovery access. Report exact release/model/kernel and which checks passed, failed or were untested. Start with `python3 tools/doctor.py` and the hardware report template; don't post private firmware, raw EFI variables or complete journals.

The most useful next evidence is an independent installation, live rollback, suspend/resume, camera diagnosis and longer Wi-Fi tests with another access point. Keep these separate so a failure in one component doesn't obscure the working results in another. A short physical demonstration is welcome; the included preview images are synthetic software renders.
