# BCM43602 Wi-Fi workaround

This is a partial, model-specific workaround. It uses the stock `brcmfmac` driver and packaged firmware binary, plus separately obtained community board data. It doesn't install the upstream template project's full installer or patched regulatory driver.

## Tested evidence

| Mode | Board flag | Prior measurements on the reference Mac |
| --- | --- | --- |
| 2.4 GHz | `boardflags3=0x00000003` | Channels 1, 6 and 13 on the same router; interface-bound pings and HTTPS passed, including 20/20 zero-loss checks |
| 5 GHz | `boardflags3=0x00000300` | Channel 36, 5180 MHz, 80 MHz; interface-bound pings and HTTPS passed |

These are separate configurations. Both bands appearing in a scan doesn't establish both-band connectivity. Neither the `0xC0000303` candidate nor omitting the 2.4 GHz switch maps solved both bands here. Short ping windows don't establish sustained performance, range or roaming reliability.

Only AU revision 0 was validated in this project. Set the region appropriate to the installation; don't copy a foreign country setting to open channels. The template isn't factory calibration extracted from this individual Mac. Its provenance and exact hardware match matter.

## Preparation

Keep a working fallback connection while validating Wi-Fi. Obtain the pinned template locally as your regular user:

```bash
python3 tools/fetch_wifi_template.py /tmp/t1-wifi-template.txt
```

This downloads one reviewed file from upstream commit `35dd57c5b2b0fc8f76af5c6c2a45ead010692421`, checks its SHA256 and refuses to overwrite an existing destination. The template isn't bundled in this release; see its upstream provenance before redistributing it.

For the tested Australian 2.4 GHz configuration:

```bash
sudo python3 tools/manage.py plan --component wifi --template /tmp/t1-wifi-template.txt --country AU --regrev 0 --band 2.4
sudo python3 tools/manage.py install --component wifi --template /tmp/t1-wifi-template.txt --country AU --regrev 0 --band 2.4
sudo python3 tools/manage.py activate --component wifi
```

The manager verifies MacBookPro13,2 plus the BCM43602 Apple subsystem, derives the address from the local EFI ROM variable, and writes a mode-0600 model-specific file. No device address is printed. It sets a separate `feature_disable=0x82000` drop-in and refuses a conflicting existing policy. It doesn't rewrite saved Wi-Fi profiles or disable PMF globally.

Activation backs up and verifies the current Omarchy UKI and Limine config, then runs `limine-mkinitcpio linux-omarchy`. It doesn't reload the live radio or disconnect the current network. Reboot at a suitable time, then select a compatible network and verify traffic. Existing manual files require reviewed adoption; don't overwrite an unknown calibration file.

Choose `--band 5` for the separately tested 5 GHz configuration. Changing modes requires reinstalling with that choice, rebuilding the boot image through activation and checking after reboot. This candidate doesn't provide seamless live band switching. Keep the original source/backup and fallback connection available.

## Diagnose the connection stage

Separate scanning, association, WPA handshake, DHCP and internet access. In the previous public-network failures, association status 16 preceded a repeated password prompt. That wasn't proof of a wrong password. Later logs recorded successful connections elsewhere, but no public-network repair.

For traffic verification, use the wireless interface explicitly, discovering its name on the target machine. A negotiated link rate isn't a throughput test. A captive portal can block internet checks even after association succeeds. Record band/channel, access point type, packet loss, duration and measured traffic without publishing private network identifiers.

An earlier hibernation produced a Broadcom firmware crash; reboot restored the interface. Resume repair remains unresolved. Avoid claiming the public-network failures and the hibernation crash are one proven fault.

## Removal

```bash
sudo omarchy-t1-wifi uninstall
```

The original board/drop-in files are restored and the boot image is rebuilt if activation was attempted. Reboot when ready. Private backups and original boot-image checksums stay under `/var/lib/omarchy-t1/wifi/`; don't upload them. If removal/rebuild is interrupted, recover its file journal, then complete the appropriate activation/removal so the boot image agrees with the files on disk.

The portable candidate's live Wi-Fi provisioning and rollback still need testing. The table above describes the earlier machine-specific implementation, not a newly verified installer run.
