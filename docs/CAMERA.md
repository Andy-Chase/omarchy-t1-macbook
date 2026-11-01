# Camera status and validation

## Reference observation — 1 November 2026

The installed desktop candidate's display/touch, controls, lock/unlock, piano and news passed the owner's physical checks. Camera capture did **not** pass: the owner's regular terminal reported only a media-controller node for the iBridge FaceTime HD Camera, with no video capture node:

```text
iBridge: FaceTime HD Camera (Bu (usb-0000:00:14.0-3):
    /dev/media0
```

`/dev/media0` describes the media topology; it isn't a video capture device that can be passed to a V4L2 player. No preview was attempted because no capture node was available.

Read-only inspection found:

- T1 normal identity `05ac:8600`, active USB configuration 2.
- Interface 0 (VideoControl) bound to `uvcvideo`; interface 1 (VideoStreaming) unbound.
- Cached USB descriptors for configurations 1, 2 and 3 all advertise `bNumFormats=0` in the VideoStreaming input header, with no format or frame descriptors.
- The current boot's kernel journal reports `No streaming interface found for terminal 32770` in both configurations 1 and 2, including before the latest candidate migration.

These observations show a failure below the camera-preview application. They don't establish whether firmware state, calibration, hardware or the kernel is responsible, or prove the custom graphics configuration preserves a working camera. The issue was already present in the earlier reference setup. Normal T1 identity alone doesn't establish that every attached function is usable.

For context, upstream [Linux UVC parsing code](https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/c7decec2f2d2ab0366567f9e30c0e1418cece43f/drivers/media/usb/uvc/uvc_driver.c) rejects a stream with no supported format descriptors. A [2017 descriptor submission from the original MacBook camera quirk author](https://www.mail-archive.com/linux-media%40vger.kernel.org/msg111348.html) includes a format and frame descriptors on a working iBridge. Those sources explain the distinction; they aren't a validated repair for this laptop.

## Repeat enumeration after a new boot

The owner repeated the same command and again obtained only `/dev/media0`. A new boot is independently visible in the current journal, with the same UVC error in both USB configurations 1 and 2. The owner later confirmed logout/login and a full shutdown/power-on. The latest boot still reports the same UVC error in original and custom configurations. Camera capture remains unavailable.

The Touch Bar renderer reached readiness automatically after one failed bridge startup and supervisor retry. The bridge initially lacked `HYPRLAND_INSTANCE_SIGNATURE`; its premature socket made the renderer attempt a connection to a dead listener. The source candidate now publishes that socket only after initial compositor/session setup, covered by four regression checks. The owner has installed and activated this fix; its installed hash matches the source, and the fresh service invocation reached renderer readiness without a renderer failure. Logout/login and full shutdown/power-on are now owner-confirmed passed; the latest boot reaches renderer readiness without the earlier hardware-reset retry. It changes no camera code.

## Next checks

1. **Completed:** owner-confirmed full shutdown/power-on, automatic Touch Bar startup and existing Wi-Fi reconnection. Camera capture remains unresolved.
2. Run `v4l2-ctl --list-devices` again in the normal desktop terminal. If a video node appears, identify its capture capabilities using `v4l2-ctl --device /dev/videoN --all` and `--list-formats-ext`, substituting the actual node. Check a local preview with its advertised format; record the result without publishing camera images.
3. If only a media-controller node remains, record camera capture as unavailable after cold boot. Establish when this camera last worked and, if a working macOS installation is already available, whether it can capture there. A comparison in the basic Touch Bar mode needs a separate planned rollback test; the existing journal alone doesn't constitute that comparison.
4. Keep camera recovery distinct from Touch Bar enablement. No camera fix, USB reset, firmware replacement or macOS reinstall is included in this candidate. Preserve machine-specific Apple firmware and calibration privately.

Full shutdown/power-on and the Touch Bar startup correction have passed; camera recovery hasn't been established. Don't advertise camera compatibility until actual capture succeeds, including after the custom Touch Bar starts.
