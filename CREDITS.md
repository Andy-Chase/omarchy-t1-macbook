# Credits and provenance

Community adaptation and Omarchy interface work: **Andy-Chase**, developed with AI assistance. The original Linux drivers and the T1 protocol discoveries below predate this project.

| Source | Contribution used here |
| --- | --- |
| Ronald Tschalär / roadrunner2 and macbook12-spi-driver contributors | Legacy Apple iBridge, Touch Bar and ambient-light drivers |
| marc-git lineage and [Heratiki fork](https://github.com/Heratiki/macbook12-spi-driver/tree/touchbar-driver-hid-driver) | Modern-kernel legacy T1 source, pinned to `1d5cd6f82c2f9ee6e2431ac51237c2a82c397b77` |
| Kerem Karabay / Linux `appletbdrm` | Graphics-driver foundation; original GPL/copyright headers retained |
| [moerdowo/appletbdrm-t1](https://github.com/moerdowo/appletbdrm-t1) | T1 short-response/echo handling and display support; imported base SHA256 `dc05ae87b8d10d1e3049869ae2ef3dc531c962c0b76272df1f03d87f5b2ab4d9` |
| [MacLinux-Touchbar](https://github.com/moerdowo/MacLinux-Touchbar) and [xeeban/macbook-t1-linux](https://github.com/xeeban/macbook-t1-linux) | Existing T1 implementations, protocol and same-model research |
| [Bogdan7c Wi-Fi project](https://github.com/Bogdan7c/macbook-bcm43602-wifi-fix) | Separately obtained community board-data template; commit `35dd57c5b2b0fc8f76af5c6c2a45ead010692421` |
| Omarchy, Arch, Linux, Cairo, libdrm and PipeWire | Desktop, kernel, packaging, rendering and audio infrastructure |

`docs/imported-source.json` records hashes before portability changes. The two graphics files retain their GPL notices. `patches/appletbdrm-t1-kernel-compat.patch` isolates the local display-driver changes from the reviewed imported base. The local legacy Makefile builds only three T1 modules and preserves the kernel's `applespi`.

Piano audio is locally synthesised; the eight demo arrangements use public-domain compositions. No recordings or third-party logo images are shipped. The ChatGPT button falls back to a text/glyph treatment if the user hasn't supplied their own mask.

Weather fallback data: [Open-Meteo](https://open-meteo.com/), with [API documentation and attribution/usage terms](https://open-meteo.com/en/docs). News links point to the original publishers. Market data remains provider-dependent and isn't an execution or trading service.
