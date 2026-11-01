# Installation and recovery

This is a release candidate. The portable installer has been exercised in an isolated filesystem, and migration with existing modules has completed file installation and service readiness on the reference laptop. Physical checks and a fresh DKMS installation remain pending. Read the compatibility table and preserve your existing recovery route first.

## Prerequisites

Use the supported MacBookPro13,2 model with normal T1 `05ac:8600` identity, a working ordinary keyboard/trackpad, matching kernel headers and a recoverable installation. A T1 in recovery mode needs separate firmware-startup work using that Mac's own preserved files. This project doesn't copy firmware or change Apple partitions.

Run `python3 tools/doctor.py`, then `make`, `make test-full` and `make modules` as your regular user. The full test must pass on the desktop. A socket-test exit 77 is a blocked test, not success.

## Basic preset controls

```bash
python3 tools/manage.py plan --component basic
sudo python3 tools/manage.py install --component basic
sudo python3 tools/manage.py activate --component basic
```

Activation builds/registers the three T1 modules as `omarchy-t1-basic/0.1.0`, enables the guarded HID handoff and retains in-kernel `applespi`. It doesn't add the old out-of-tree SPI input module. Existing DKMS providers aren't silently forced out; inspect a conflict rather than unloading unrelated input/controller drivers.

Physically confirm basic display/touch and ordinary input before proceeding. If you're retaining an already working legacy driver, don't reinstall it just to satisfy this sequence; see Migration.

## Custom desktop controls

```bash
python3 tools/manage.py plan --component desktop --user "$USER"
sudo python3 tools/manage.py install --component desktop --user "$USER"
sudo python3 tools/manage.py activate --component desktop
```

Core controls are the default. For optional pages, add e.g. `--features files,piano,news` to both plan and install. See Features for all choices. Activation adds the graphics/raw-digitizer DKMS modules as `omarchy-t1-graphics/0.1.0`. It expects the basic T1 fallback and a logged-in graphical desktop for the selected user. Other kernels require a successful local build/review and explicit `--allow-untested-kernel`; that flag doesn't establish compatibility.

The user unit points to a root-owned shared bridge. It doesn't depend on a user-specific source checkout. Privileged operations are limited to the T1 device, graphics/input handles and fixed service operations.

## Existing files, upgrades and staging

An unrecognised pre-existing destination stops installation before changes. `--adopt-existing` is an explicit migration choice that preserves original bytes, modes, ownership and symlink targets in a private manifest. Inspect the plan and Migration guide before using it.

To change optional pages or upgrade this candidate, build the new source, run the same desktop install command with the desired features and reactivate. Local edits to managed files block overwrite/removal so they can be preserved and reviewed. The recorded pre-adoption originals remain the uninstall baseline across upgrades.

For an isolated filesystem test, use `--root /absolute/existing/staging-directory`. It must contain the model/device identity fixtures if you invoke `install`; `plan` doesn't require them. No host service manager, DKMS, network manager or boot-image command is invoked for a staged root, and activation is refused. This is for packaging validation, not hardware simulation.

## Uninstall and recover

For a completed installation:

```bash
sudo omarchy-t1-desktop uninstall
sudo omarchy-t1-basic uninstall
```

Remove desktop before basic. Each installed component includes its own offline management command. Original adopted files are restored. For newly created files, removal returns them to absence. Private manifests are under `/var/lib/omarchy-t1/COMPONENT/` and contain backup data; don't publish them.

If a file transaction was interrupted, its journal remains and blocks a new install:

```bash
sudo omarchy-t1-desktop recover
```

Alternatively use `sudo python3 tools/manage.py recover --component desktop` from the source checkout if the interrupted install didn't yet create its recovery command. Recovery restores the last committed files and manifest; inspect/re-run activation separately afterwards. Keep the journals until recovery succeeds. After a failed Wi-Fi boot-image rebuild, run the appropriate activation/removal again after file recovery so the UKI and disk configuration agree.

**Original controls can remain dark after driver bindings are restored. Save work, shut down fully, then power on.** This candidate never shuts down the laptop automatically. Retain the original `omarchy-touchbar-restore-original` command if your previous setup supplied it; this project doesn't overwrite that historical recovery helper.

File rollback is tested. DKMS/systemd/UKI activation and recovery still need live validation; report them separately from file-level tests.
