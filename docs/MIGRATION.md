# Migrating an existing custom setup

The source used for this candidate was reconstructed from a working MacBookPro13,2 installation. Its old working legacy and graphics modules can remain in place during initial candidate testing. No module/provider replacement is needed just to test the portable userspace changes.

First run from the extracted source directory:

```bash
make
make test-full
make modules
python3 tools/doctor.py
python3 tools/manage.py plan --component desktop --user "$USER" --features chatgpt,weather,markets,piano,files,news
```

If the full self-test passes and the plan is correct, the candidate commands are:

```bash
sudo python3 tools/manage.py install --component desktop --user "$USER" --features chatgpt,weather,markets,piano,files,news --adopt-existing
sudo python3 tools/manage.py activate --component desktop --reuse-modules
```

`--reuse-modules` uses the already installed modules for the running kernel; it doesn't alter their DKMS registrations. It should only be used with a reviewed, working T1 module set. The new source/module builds aren't proof that an arbitrary pre-existing module provider is suitable.

The new `omarchy-t1-session.service` user unit uses the shared root-owned bridge. Activation disables the previous `omarchy-touchbar-session.service` through the selected user’s service manager, retaining its files for rollback. Old user helper files and preferences are retained. Existing system/user unit definitions, renderer and root helpers overwritten by the installer are backed up for uninstall. Optional pages are explicitly selected so the reference UI can be retained; a new install defaults to core controls.

After activation, confirm visibility, Escape, workspaces, sliders and lock/unlock before testing optional pages. Test logout/login, camera, restart, cold boot and sleep separately. Don't perform a sleep/reboot just to test software compilation.

To undo migration:

```bash
sudo omarchy-t1-desktop uninstall
```

The prior files and recorded service enablement/activity are restored where available. You may still need a complete shutdown/power-on to restore the visible firmware strip. The portable candidate has now been installed and activated on the reference laptop with reused modules. Installed code hashes matched the candidate at migration, and initial physical controls passed. A later source correction for an observed bridge startup race is installed and activation passed; logout/login and full shutdown/power-on are owner-confirmed passed; rollback remains unverified.

For an existing managed desktop installation, rerun the desktop install and activation commands above to apply source updates. The first pre-adoption backup is retained across upgrades. A locally edited managed file blocks replacement; review the reported difference rather than overriding it.
