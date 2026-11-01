# Architecture and maintenance boundaries

The basic driver path uses T1 USB configuration 1 and Apple's preset layouts. The custom path uses configuration 2, a T1-compatible DRM display driver, a raw-only digitizer binding and a userspace renderer. The raw binding is restricted to `05ac:8600`, configuration 2, interface 2. The renderer validates its hardware and socket peer before dropping privileges.

| Part | Privilege and responsibility |
| --- | --- |
| Basic handoff helper | Root; loads the three T1 modules and hands off only expected T1 HID interfaces |
| Hardware supervisor | Root; validates the configured account/hardware, switches the T1, opens fixed devices and supervises the renderer |
| Renderer | Starts privileged to obtain handles, then drops groups/GID/UID; draws, reads touch and supplies a restricted virtual keyboard |
| Session bridge | Selected desktop user; fixed command protocol and Hyprland integration, no arbitrary root command dispatch |
| Helper workers | Desktop user; bounded media/data operations outside the input loop |
| Installation manager | Root only for live mutation; private manifests/journals, independent components and no activation during file installation |

The root-owned `/etc/omarchy-t1/desktop.json` selects the user and optional pages. The hardware supervisor validates its ownership, mode, size, account identity and allowed feature values. Runtime code is installed under root-owned paths. The candidate uses a system-provided user unit, `/etc/systemd/user/omarchy-t1-session.service`, enabled only for the chosen user. It doesn't write executable code or unit files into a user-controlled home as root.

The graphics device is assigned a separate seat so Hyprland doesn't treat it as a desktop monitor. Device numbers and the T1 USB bus path aren't assumed to be stable. Backlight devices are discovered under sysfs. The main GPU, USB host, Thunderbolt and ordinary SPI input drivers aren't reset by the hardware service.

The bridge publishes its renderer socket only after the Hyprland event connection and initial session setup succeed. If UWSM hasn't imported the compositor environment yet, the user unit retries without advertising a dead socket. This avoids treating that observed startup condition as a graphics failure; the correction is installed and activation passed; logout/login and full shutdown/power-on are owner-confirmed passed.

The bridge checks Omarchy's lock IPC as well as logind. Unknown/failed lock queries fail closed for desktop actions, and dispatched workspace/app/folder actions recheck the lock. The candidate adds an immediate piano lock recheck. A socket disconnect or stopped renderer releases virtual keys.

File operations validate destinations, refuse symlink parents and journal original records before mutation. Upgrades preserve the first pre-adoption baseline, and locally modified managed files block replacement/removal. Interrupted install/uninstall recovery restores the last committed file state. This doesn't substitute for physical driver recovery or a successful UKI rebuild.

Read-only hardware probes use a separate sysfs resolver: normal DMI/USB/PCI aliases are allowed only when the resolved target stays inside that installation's sysfs tree. An isolated staging root can't use an alias to inspect host sysfs. This exception doesn't apply to write destinations.

Activation is kept separate because DKMS, service state and firmware display state can't be established by file tests. The source includes activation bookkeeping, provider-conflict checks, readiness checks and service-state restoration, but those paths remain unverified on live hardware. Sleep hooks are configured behaviour, not evidence of reliable sleep.

Use a fresh versioned release and build checks for kernel updates. DKMS automatic builds don't guarantee future API compatibility. Offer the small local graphics patch upstream rather than maintaining unrelated fork changes. Keep optional data providers independent of the core controls.
