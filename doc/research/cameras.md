# Cameras in the car, without taxing the tablet

<!-- Researched and independently fact-checked on 2026-09-08. Every answer
below was written by one pass and then attacked by a second whose only job was
to find claims that were confidently stated and wrong. The corrections it made
are left inline and marked, because which claims turned out to be wrong is
itself worth knowing -- three of the four errors were a specification borrowed
from a neighbouring product. Anything the checker could not confirm is marked
unconfirmed rather than quietly dropped. -->

Verification complete. Returning the rewritten answer.

---

**Verdict: substantially right in its architecture, but with nine factual errors — one miscited source, one wrong claim about go2rtc, a stale/backwards RK3588 encode status, a misread camera count, and a parking-mode power budget that is optimistic by roughly 2×. Corrections are marked inline.**

# In‑car multi‑camera recording for OmaCar — architecture recommendation

**The one‑line answer:** don't encode video on the Surface, and don't encode it on a Pi either. Buy cameras that encode themselves, put a small x86 box in the trunk that only *muxes and decodes*, and give the tablet a single composited stream over one ethernet cable.

---

## 1. Separate box, or can the Surface take it directly?

**Separate box. Not close.** Four reasons, in order of how hard they bite:

**Ports and USB bandwidth.** ✅ **Verified.** The Surface Pro 7 has exactly one USB‑A (USB 3.1 Gen 1, 5 Gbps) and one USB‑C (USB 3.1 Gen 2, 10 Gbps, with DisplayPort 1.4) ([Microsoft specs](https://support.microsoft.com/en-us/surface/surface-pro-7-specs-and-features-8254894d-bb80-77ef-daae-612ea713e310), [SurfaceTip](https://surfacetip.com/surface-pro-7-ports/)). No Thunderbolt — the SP7 predates that; TB4 arrived with the Pro 8, and the Pro 7+ is a different (Tiger Lake) machine. The original answer got this right and did not confuse the three; worth stating explicitly since it's the usual trap.

Three or four cameras means a hub, and USB cameras are isochronous devices that *reserve* bandwidth whether or not they use it. ✅ Only ~80% of a high‑speed microframe may be allocated to periodic (isochronous + interrupt) transfers — 90% for full‑speed — and multiple UVC cameras on one root port routinely fail to open at all, the classic `VIDIOC_STREAMON: No space left on device` ([The Good Penguin](https://www.thegoodpenguin.co.uk/blog/multiple-uvc-cameras-on-linux/), [96Boards](https://www.96boards.org/documentation/consumer/guides/multi-usb-camera.md.html)).

> **⚠️ CORRECTED — the cited source says three, not two.** The original claimed a "practical ceiling of **two** 1080p30 MJPEG streams" per USB 2.0 bus and cited [breq.dev](https://breq.dev/2023/06/21/cameras). That post actually concludes "up to **3** cameras can coexist on a single bus at this resolution and framerate." The architectural conclusion is unchanged — four still doesn't fit, and three is a ceiling, not a comfortable operating point — but the number was a misreading of its own source.

> **⚠️ CORRECTED — the raw‑bitrate figure mixes pixel formats.** 746 Mbps is 1080p30 **YUV420** (12 bpp). UVC cameras stream uncompressed as **YUYV / YUV422** (16 bpp), which is ~995 Mbps — and that is the figure breq.dev actually uses (`1920×1080×16 bits×30 fps ≈ 995.3 Mbit/s ≈ 124.4 MByte/s > 53.2 MByte/s` available on USB 2.0). Use 995 Mbps when arguing about USB; 746 Mbps is the right number only for a 4:2:0 pipeline. Either way it does not fit on USB 2.

*Unconfirmed (unchanged from the original, and correctly flagged there):* whether the SP7's USB‑A and USB‑C hang off the same Ice Lake xHCI root controller. I could not confirm this either. They almost certainly do, which would mean the two ports do **not** give you two independent bandwidth budgets — but treat it as an assumption, not a finding.

**Encoding CPU.** ✅ The Ice Lake iGPU (Gen11) does have Quick Sync with H.264/HEVC encode, and Jellyfin states verbatim: *"Unlike NVIDIA NVENC, there is no concurrent encoding sessions limit on Intel iGPU and ARC dGPU"* ([Jellyfin Intel HWA docs](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/intel/)) — so on paper the Surface could encode several streams.

> **➕ ADDED CAVEAT from the same source.** That Jellyfin page also says Ice Lake specifically is *"losing support for QSV on Linux, since the MediaSDK runtime has been deprecated by Intel."* If OmaCar runs Linux on the Surface, Gen11 QSV is a shrinking target, not a stable one. This strengthens the recommendation rather than weakening it.

> **❓ UNCONFIRMED — the "one MFX engine" claim.** I could not settle Gen11's MFX/VDBOX count. Jellyfin says only that certain higher‑end models "feature a second MFX video engine" and that "all ARC A‑series GPU models come with two," which *implies* baseline iGPUs have one but never states Gen11's number; Notebookcheck ambiguously describes Ice Lake as having "two encoders," which may mean two codec types rather than two engines. Treat "one MFX engine already busy with CarPlay" as a plausible working assumption to be measured, not an established fact.

**Thermals.** This is the decider. ✅ The i5 Surface Pro 7 is fanless (i3 and i5 are passively cooled; only the i7 has a fan).

> **⚠️ CORRECTED — the temperature figures are understated and mostly sourced from dash‑cam marketing.** The original gave "cabin air ~54–63 °C and dashboards 65–82 °C," citing [Nexar](https://www.getnexar.com/blog/dash-cam-summer-heat-protection) and [RedTiger](https://redtigercam.com/blogs/dash-cam/dash-cam-heat-resistant) — both vendor blogs. Published measurements are **higher**: the cited [experimental study](https://pdfs.semanticscholar.org/2184/cbe693e6f556f45e86ac8c058ea0a4c2024b.pdf) and related work report unshaded cabin air reaching **70–75 °C** and dashboard surfaces **93–100 °C**. Most consumer electronics are rated to ~60 °C operating. The argument gets stronger, not weaker: a fanless lithium tablet running a 24/7 encode load on a hot dash isn't a performance problem, it's a **battery swelling problem**.

**Availability.** The recorder has to run when the tablet is asleep, dimmed, or carried into the house. Recording that depends on the UI machine being awake is not a recorder.

**Verdict:** the Surface stays a *client* — it displays, it runs OmaCar's UI, it talks OBD‑II. It never touches a camera.

---

## 2. Which box

| Board | HW H.264 encode | HW decode | AI | Verdict |
|---|---|---|---|---|
| **Raspberry Pi 5** | **None. Removed entirely.** | HEVC only (H.264 decode is software now) | +$70 Hailo‑8L via AI Kit | **Disqualified.** |
| **Raspberry Pi 4** | Yes, but ~1080p30 *total* across the whole chip | H.264 1080p60 | Coral/Hailo external | Enough for **one** camera. Not four. |
| **RK3588** (Rock 5B+, Orange Pi 5 Max) | 8K30 H.264/H.265 | 8K60 HEVC / 8K30 H.264 | **6 TOPS NPU built in** | Strongest silicon. Fussiest software. |
| **Intel N100 mini PC** | Gen12 QSV H.264/HEVC | Gen12 QSV, everything | OpenVINO on iGPU, or M.2 Hailo | **My pick.** |

✅ **Verified.** The Pi 5 has no hardware video encoder of any kind, and BCM2712 does HEVC decode only — H.264 decode is now software ([Raspberry Pi forums](https://forums.raspberrypi.com/viewtopic.php?t=357870), [HN discussion](https://news.ycombinator.com/item?id=38068801)). For a project whose stated premise is "video must not tax the CPU," the Pi 5 is the single worst board you could choose. Say that out loud, because it's the default assumption everyone starts with.

✅ **Verified.** RK3588 encodes H.264/H.265 to 8K@30, decodes HEVC/VP9 to 8K@60 and H.264 to 8K@30, and carries a 6 TOPS triple‑core NPU. (I dropped the original's unsourced "~1080p@480fps class" figure — I could not corroborate it.)

**Buy a fanless Intel N100 mini PC with a 12 V DC barrel input.** Reasoning:

> **⚠️ CORRECTED — "every N100 industrial/fanless box runs on a 12 V barrel jack" is overstated.** 12 V barrel is the most common input on *industrial* fanless N100 boxes, but plenty of N100 mini PCs ship with USB‑C PD (12–20 V) or a 19 V brick instead, and some take PoE. This is a **per‑SKU spec to verify before buying**, not a property of the platform. The argument still holds for the models that do take 12 V ([JOVCOR](https://www.amazon.com/JOVCOR-Fanless-Industrial-Computer-Entertainment/dp/B0F9NHZQ18)) — in a car you already have 12 V, and you skip the entire inverter/ATX‑PSU circus.

- ✅ **6 W TDP** (Intel's rated base power for the N100), passively cooled by the chassis — no fan to fill with road dust, and thermal margin for a hot trunk ([analysis](https://minipc-review.com/en/best-fanless-mini-pcs-for-silent-use-2026) — *low‑authority aggregator source, unverified*).
- ✅ **Mainline kernel, no BSP.** `i915` + VA‑API works out of the box. Compare RK3588: Collabora's H.264/H.265 **decoder** support was merged in **Linux 7.0**, announced 27 February 2026 ([CNX Software](https://www.cnx-software.com/2026/02/27/rockchip-rk3588-rk3576-h-264-and-h-265-video-decoders-mainline-linux/)) — decoders only. Hardware **encode** still requires a vendor kernel: Jellyfin states verbatim *"The Rockchip BSP kernel (6.1 or 5.10 LTS) is required"* and *"until there is a mature transcoding solution in the Linux mainline, users need to use RKMPP"* ([Jellyfin Rockchip HWA](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/rockchip/)).

> **⚠️ CORRECTED — the RK3588 mainline‑encode status was both stale and pointed the wrong way.** The original said "mainline HEVC encode is only just landing on 6.19‑rc edge kernels," citing the [Armbian thread](https://forum.armbian.com/topic/57951-rk3588-kernel-patches-for-h265-hardware-encoding-and-hdmirx-edid-fix/). Reading that thread: those are **out‑of‑tree patches on Armbian's edge kernel**, not mainline. They began at 6.19‑rc8 (February 2026), and a May 2026 reply in the same thread states *"There's still no support for `rkvenc` in edge kernels (as of 7.1rc2)."* Mainline is at 7.1 now, so 6.19‑rc is not "just landing" — it is over half a year behind, and rkvenc **still has not landed**. RK3588 hardware encode remains a vendor‑BSP or out‑of‑tree‑patch proposition. You are shipping a public project on 30 September — do not put that on the critical path.

- **x86 means your dev machine and your car machine run the same binaries.** No cross‑compile, no ARM wheels, no `rknn-toolkit` conversion step between you and a working detector.

**When I'd take the RK3588 instead:** if you decide to feed the box *raw* camera streams (CSI/USB) and encode on the box after all, the RK3588's VPU is genuinely in a different class, and the 6 TOPS NPU is free. *Unconfirmed:* Orange Pi 5 Max 16 GB at ~$160–210 and Rock 5B+ at $189–250 ([comparison](https://dev.to/revenueclaw/orange-pi-5-max-vs-rock-5b-the-32gb-sbc-battle-in-2026-3ho0)) — that is a single low‑authority blog and I could not corroborate the prices or the sourcing claim. Take it as build two, not build one.

*Uncertainty flag (unchanged, and fair):* sources disagree on N100 throughput because they measure different things — one says "2 simultaneous 1080p transcodes" ([streamersize](https://streamersize.com/blog/intel-quicksync-for-streaming/)), another reports four 1080p substreams under 25% CPU ([privacysmarthome](https://www.privacysmarthome.com/guides/scrypted-nvr-vs-frigate-intel-n100-vs-pi-5-benchmarks-2026/)). *Both are low‑authority blogs I could not independently verify.* Full transcode vs. decode‑only. In the architecture below you need almost no encode at all, so this ambiguity doesn't bind.

---

## 3. Cameras: PoE, and it isn't close

**Kill CSI/MIPI immediately.** Ribbon cables are ~30 cm stock, a couple of metres with active extenders, and they are unshielded flat cable in a vehicle full of ignition noise. Your rear camera is 5 m away. The Pi 5 has two CSI ports. Dead on all three counts.

**USB UVC: cabin only, one camera.** The bandwidth math above caps you at ~three 1080p MJPEG streams per bus (corrected), USB 2.0 is spec'd to 5 m, and hub reliability under vibration is its own genre of bug report. There is one genuinely good USB option: **UVC cameras with an onboard H.264 encoder** — ELP and Arducam IMX291/IMX323 modules output 1080p30 H.264 at roughly 500 KB/s and consume essentially zero host CPU ([Arducam](https://blog.arducam.com/mjpeg-yuv-rgb-h264-uvc-camera-usb/), [ELP UB0212](https://www.amazon.com/clp/B01E8OX212)). Perfect for a short cabin run. Not for the rear bumper.

**IP/PoE cameras for everything else.** One Cat6 run carries power and data up to 100 m, the camera is IP66/IP67 by default, and — the actual point — **the camera contains the H.264/H.265 encoder.** Encoding moves off your bill of materials entirely. They speak RTSP/ONVIF, so frames are absolutely reachable by software: `ffmpeg` → hardware decode → your detector. Nothing is sealed.

Cabling and power implications in the car:

- **"12 V" in a car is 11.8 V resting, 13.8–14.6 V running, and dips to 6–9 V on crank.** Most cameras want 12 V ±10% (10.8–13.2 V). Do **not** wire cameras to raw battery. Regulate to a stable 12.0 V, or use PoE and let the switch handle it.
- PoE is cleaner: one 12 V‑input 802.3af switch (or a 12→48 V boost plus passive injectors — [Tycon](https://www.tyconsystems.com/categories/poe-injectors/6026428000002821032), [Coolgear](https://www.coolgear.com/product/gigabit-ieee-802-3af-at-poe-injector-12v)) and every camera gets clean, isolated power over the same cable as its data. ✅ Voltage drop over 10 m of Cat6 at 0.5 A is ~0.42 V — under half a volt, as claimed (24 AWG at ~0.084 Ω/m, two pairs in parallel).
- Run cables in existing loom channels, through grommets, never across airbag paths. Exterior cameras behind the windshield frit or under the rear spoiler lip see less direct sun than a dash mount.
- ✅ **Sensor choice matters more than resolution.** Verified: legacy alternating‑exposure HDR merges long and short exposures on *consecutive frames*, producing a double‑image ghost "on any object moving faster than 15 mph relative to the camera," while STARVIS 2 *Clear HDR* derives both exposures from the same exposure window with no temporal offset ([DashCamGear](https://dashcameragear.com/sony-starvis-2-performance-guide/), [RedTiger](https://redtigercam.com/blogs/news/what-is-sony-starvis-2-imx678-sensor-how-can-it-improve-the-recording-experience)). *Caveat: these are enthusiast/vendor sources, not Sony datasheets — the mechanism is sound but the 15 mph figure is theirs, not a measured spec.* Exactly the objects your near‑miss detector cares about.

**Known limitation, flagged:** IP cameras give you H.264/H.265 only, never raw sensor data, and cross‑camera time sync is NTP‑grade — tens of milliseconds. Fine for "did something nearly hit us." Not fine if you later want tight multi‑camera geometric fusion. The escape hatch for that is GMSL2/FPD‑Link III over coax into a Jetson‑class deserializer, which is a different project at a different price.

---

## 4. How streams reach the tablet

**Ethernet. And send the tablet one stream, not four.**

| What | Per stream | ×4 |
|---|---|---|
| Raw YUYV 4:2:2 (what UVC actually sends — *corrected*) | 995 Mbps | 3.98 Gbps |
| Raw YUV420 (4:2:0 pipeline) | 746 Mbps | 2.98 Gbps |
| MJPEG | 30–60 Mbps | 120–240 Mbps |
| **H.264 main stream, dashcam quality** | **4–8 Mbps** | **16–32 Mbps** |
| H.265 main stream | 2–4 Mbps | 8–16 Mbps |
| Substream 640×360 @10 fps (for AI) | 0.3–0.8 Mbps | 1.2–3.2 Mbps |

✅ So the entire system is **under 35 Mbps**. Even 100BASE‑TX would carry it. This is the whole reason the PoE architecture wins: you moved from gigabits to tens of megabits by letting the cameras compress.

The design:

- **Wire:** USB‑C → 2.5GbE adapter on the Surface, short Cat6 to the box. ~30× headroom. Wi‑Fi works for a single live view but is the wrong medium for a metal box on the move — use it as the fallback, not the plan.
- **Recording:** the box stream‑*copies* RTSP to disk (`-c copy`). Zero decode, zero encode, ~1% of one core. ✅ At 24 Mbps aggregate that's 10.8 GB/hour, ~259 GB/day continuous — a 1 TB SSD gives you about four days of loop. (Arithmetic checked.)
- **Live view in OmaCar's UI:** the box composites a 2×2 mosaic and emits **one** 1080p stream. That costs one hardware encode on the box and lets the Surface decode a single stream in QSV essentially for free. Serve it over WebRTC via `go2rtc` for sub‑second latency. Never ask the tablet to decode four streams.
- **Clip review:** NFS or SMB export of the archive so OmaCar can browse and scrub saved events. Storage sharing is right for *review* and wrong for *live*.
- **A note on USB as the link:** an N100 mini PC cannot be a USB device (no UDC), so USB‑gadget networking is off the table for it. If you go RK3588 or Pi, `usb-ncm` over the USB‑C OTG port gives you a virtual ethernet link on one cable — genuinely elegant. Worth knowing it's a reason someone might pick the SBC.

> **⚠️ CORRECTED — the go2rtc claim is wrong, and it was attached to the wrong source.** The original said "its bundled ffmpeg does not use hardware acceleration" and cited [Frigate discussion #18311](https://github.com/blakeblackshear/frigate/discussions/18311). Two problems.
>
> **(a) The citation is wrong.** Discussion #18311 is titled *"hardware acceleration for Rockchip RK3588 mainline kernel support"* — it is about Mesa Teflon, the v4l2‑request API and the Rocket NPU driver on mainline kernels. It says nothing about go2rtc's ffmpeg. (The Sources list at the bottom of the original labels #18311 correctly as the Rockchip discussion; §4 miscited the same link for a different claim.)
>
> **(b) The claim itself is wrong as stated.** go2rtc **does** support hardware acceleration — VAAPI, plus other engines — configured as `ffmpeg:rtsp://source#video=h264#hardware=vaapi`, and shipped in the `alexxit/go2rtc:master-hardware` Docker image ([go2rtc hardware docs](https://github.com/AlexxIT/go2rtc/blob/master/internal/ffmpeg/hardware/README.md)). What is true: **acceleration is disabled by default because it can be unstable**, and the standard image is not the hardware build. So the practical advice survives in corrected form — *keep go2rtc on the copy path, not the transcode path, and if you must transcode through it, use the hardware build and set `#hardware=` explicitly* — but the stated reason was false.

---

## 5. Hardware encoding, explicitly

| Board | Several 1080p in hardware? |
|---|---|
| Raspberry Pi 5 | **No hardware encoder of any kind.** ✅ verified |
| Raspberry Pi 4 | ~1080p30 aggregate. One camera. |
| **RK3588** | Yes — 8K30 H.264/H.265 encode ✅. Needs BSP kernel or out‑of‑tree patches; **not in mainline as of 7.1‑rc2**. |
| **Intel N100 (Gen12 QSV)** | Yes — H.264/HEVC encode, no session cap ✅, mainline VA‑API. |
| Surface Pro 7 (Gen11 QSV) | Yes on paper — but fanless, contending with CarPlay, and Intel has deprecated MediaSDK for Ice Lake on Linux. |

**But notice what the recommended design does with all of this: almost nothing.** With PoE cameras the encoders live in the cameras; the box needs hardware *decode* (for the AI path) and exactly one encode (for the mosaic). That's the architectural win — you didn't buy a bigger encoder, you deleted the requirement.

**AI path:** decode the low‑res substreams in QSV, run detection on the iGPU via OpenVINO. ✅ A Hailo‑8L M.2 module is 13 TOPS at ~$70 and runs YOLOv6n in ~11 ms — both figures confirmed against [Raspberry Pi](https://www.raspberrypi.com/news/raspberry-pi-ai-kit-available-now-at-70/) and [Frigate hardware docs](https://docs.frigate.video/frigate/hardware/). *Check before buying:* most N100 mini PCs have one M‑key slot, occupied by the boot SSD. Confirm a free M.2 slot or plan to boot from eMMC/USB.

✅ **Verified, with the exception restored.** Frigate's docs say verbatim: *"The Coral is no longer recommended for new Frigate installations, **except in deployments with particularly low power requirements** or hardware incapable of utilizing alternative AI accelerators."* The original dropped that exception clause — and low power is exactly your parking‑mode constraint, so the Coral is not as dismissible here as the original implied. Frigate also notes the N100 with OpenVINO "can only run one detector instance" (~15 ms MobileNetV2).

---

## 6. Power in a car

**The rail.** Automotive 12 V is a hostile supply: 11.8 V resting, 13.8–14.6 V charging, dips to 6–9 V during crank, plus load‑dump transients (ISO 7637‑2 / ISO 16750‑2). Never hang a generic buck converter off it.

**The converter.** ✅ Both parts verified against the manufacturer. The [Mini‑Box M4‑ATX](https://www.mini-box.com/M4-ATX): *"6‑30V wide input,"* 250 W (300 W peak), *"survives vehicle engine cranks (down to 6V),"* with ON/OFF motherboard control and programmable `IGN_HIGH` / `IGN_LOW` / `IGN_DBC` timing. The [DCDC‑USB‑200](https://www.mini-box.com/DCDC-USB-200): *"wide range input, 6‑34V,"* *"programmable output 5‑24V,"* 160 W / up to 15 A, *"standby power consumption is well under 1mA,"* with an automotive ignition‑aware mode. The smaller sibling is the better fit for a 12 V mini PC or SBC.

**Ignition cycles.** Wire ACC through the PSU's ignition sense. On ignition‑off the PSU holds power for a configured delay, sends the shutdown pulse, then hard‑cuts after a timeout. On the software side a systemd unit watches that line, closes the current segment, `fsync`s, and calls `poweroff`.

**The detail that actually saves your footage:** record in **short segments** (60 s, via ffmpeg's `segment` muxer) and use **MKV or fragmented MP4**, never plain MP4. Standard MP4 writes its `moov` atom at the end — a hard power cut leaves an unplayable file. Segments plus a streamable container mean the worst case is losing one minute, not the whole drive home.

**Parking mode.** Feed the box from constant 12 V through a **low‑voltage disconnect set at 11.8–12.0 V** ([adjustable modules](https://www.amazon.com/Digital-Battery-Low-Voltage-Protection/dp/B07929Y5SZ), [BatteryMart LVD](https://www.batterymart.com/p-l15c-12-volt-15-amp-low-voltage-disconnect.html)) so the car still starts.

> **⚠️ CORRECTED — the power budget is optimistic by roughly 2×, which halves the runtime.** The original put "an N100 box plus four PoE cameras at roughly 20–35 W ≈ 2.5 A," yielding "about **eight hours**" off ~20 Ah. Reolink's own spec sheet rates the RLC‑520A at **<12 W each**, and real draw with IR illuminators on at night is ~6–7 W per camera. Four cameras (24–28 W at night) + an N100 box writing four streams to SSD (~12–15 W) + PoE conversion and switch losses lands at **~40–50 W**, i.e. **3.3–4.2 A at 12 V**. Against ~20 Ah of safely usable starting‑battery capacity that is **about five hours, not eight** — and parking mode runs at night, when the IR load is highest, so plan for the pessimistic figure. If you want overnight, you need the auxiliary battery, not "maybe." LiFePO4 specifically, not LiPo, for hot‑car safety.

**Fusing and placement.** Add‑a‑circuit fuse tap on an ACC slot and a second on a constant slot, fused for the wire (7.5–10 A), plus the PSU's own fuse. Mount the box **under a seat or in the trunk, never on the dash** — the dash is the 90 °C+ zone (see corrected temperatures in §1).

---

## 7. Parts list, first build

| # | Item | Notes | ~USD |
|---|---|---|---|
| 1 | Fanless N100 mini PC, 12 V DC in, 16 GB / 512 GB | dual LAN preferred; **verify 12 V barrel input and a free M.2 slot — neither is universal** | 150–220 |
| 2 | 1 TB NVMe or 2.5" SSD | high‑TBW / surveillance‑rated — this drive writes ~259 GB/day | 60–90 |
| 3 | 2× exterior PoE camera, front + rear | Reolink RLC‑520A is 5MP, **dome** (not turret), IP67, 802.3af — *and it does take DC 12 V 1 A as an alternative to PoE, per Reolink's spec page* | 130–260 |
| 4 | 1× cabin camera | ELP/Arducam UVC with onboard H.264, short USB run | 40–60 |
| 5 | 5‑port PoE switch, 12 V input | or 12→48 V boost + passive injectors | 50–90 |
| 6 | Mini‑Box DCDC‑USB‑200 (or M4‑ATX) | ignition‑aware, crank ride‑through | 90–130 |
| 7 | Low‑voltage disconnect, 11.8 V | protects the starting battery in parking mode | 15–25 |
| 8 | **Not optional if you want overnight:** 12 V 7–12 Ah LiFePO4 aux battery | see corrected runtime above | 60–110 |
| 9 | USB‑C → 2.5GbE adapter + Cat6 patch | the tablet's only wire | 20–30 |
| 10 | Add‑a‑circuit fuse taps, 12 AWG, grommets, loom | ACC + constant | 30 |
| 11 | Optional: Hailo‑8L M.2 (13 TOPS) | only if an M.2 slot is free | 70 |

✅ Arithmetic checked: **core build ≈ $585–935**; with the aux battery and Hailo, **≈ $715–1,115**.
**RK3588 variant:** swap line 1 for an Orange Pi 5 Max 16 GB, drop line 11 (NPU is on‑die), and budget a weekend for the BSP kernel — noting the corrected encode status in §2.

> **❓ UNCONFIRMED — every price in this table.** I could not verify current street pricing for the Reolink models (the original's "$60–70" and "~$130" figures, cited to [cctvinfo](https://cctvinfo.com/reviews/reolink-rlc-810a)), the N100 boxes, or the Orange Pi 5 Max. Retailer listings exist for all of these; the numbers do not. Treat every figure as indicative and verify at purchase.

---

## Where I'm uncertain

- **Unconfirmed:** whether the SP7's two USB ports share one xHCI root controller. I could not verify this either; it would change the "two independent budgets" assumption.
- **Unconfirmed:** the MFX engine count on Gen11. Jellyfin implies baseline iGPUs have one but never states it for Ice Lake; Notebookcheck's "two encoders" phrasing is ambiguous. Measure before relying on it.
- **Corrected and now settled:** RK3588 hardware encode is **not** in mainline. Decode landed in Linux 7.0 (Feb 2026); `rkvenc` was still absent from edge kernels as of 7.1‑rc2, and the working solution is out‑of‑tree patches or a vendor BSP.
- **Resolved for one SKU:** the RLC‑520A does support 12 V DC barrel input alongside 802.3af. Other Reolink SKUs still vary — check the datasheet per model.
- **Unconfirmed:** all pricing, and the throughput figures from streamersize, privacysmarthome, minipc-review and the dev.to comparison — all low‑authority blogs I could not corroborate.

---

**Sources:**
- [Raspberry Pi forums — RPi5 codec confusion](https://forums.raspberrypi.com/viewtopic.php?t=357870)
- [HN — Raspberry Pi 5 has no hardware video encoding](https://news.ycombinator.com/item?id=38068801)
- [CNX Software — RK3588/RK3576 decoders reach mainline Linux (merged in 7.0)](https://www.cnx-software.com/2026/02/27/rockchip-rk3588-rk3576-h-264-and-h-265-video-decoders-mainline-linux/)
- [Jellyfin — Rockchip VPU hardware acceleration (BSP 6.1/5.10 required)](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/rockchip/)
- [Jellyfin — Intel GPU hardware acceleration (no session cap; ICL losing QSV on Linux)](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/intel/)
- [Armbian — RK3588 H.265 encoding kernel patches (out‑of‑tree, edge kernel)](https://forum.armbian.com/topic/57951-rk3588-kernel-patches-for-h265-hardware-encoding-and-hdmirx-edid-fix/)
- [ffmpeg-rockchip (MPP/RGA)](https://github.com/nyanmisaka/ffmpeg-rockchip)
- [Frigate — recommended hardware (Coral exception; Hailo‑8L 11 ms)](https://docs.frigate.video/frigate/hardware/)
- [Frigate — Rockchip *mainline kernel* hwaccel discussion #18311](https://github.com/blakeblackshear/frigate/discussions/18311) *(not a go2rtc source — see §4 correction)*
- [go2rtc — hardware acceleration README](https://github.com/AlexxIT/go2rtc/blob/master/internal/ffmpeg/hardware/README.md) *(added to replace the miscitation)*
- [Microsoft — Surface Pro 7 specs](https://support.microsoft.com/en-us/surface/surface-pro-7-specs-and-features-8254894d-bb80-77ef-daae-612ea713e310)
- [SurfaceTip — Surface Pro 7 ports](https://surfacetip.com/surface-pro-7-ports/)
- [The Good Penguin — multiple UVC cameras on Linux](https://www.thegoodpenguin.co.uk/blog/multiple-uvc-cameras-on-linux/)
- [The Good Penguin — why USB isochronous bandwidth errors occur](https://www.thegoodpenguin.co.uk/blog/understanding-why-usb-isochronous-bandwidth-errors-occur/)
- [breq.dev — streaming lots of USB cameras at once (three per bus, not two)](https://breq.dev/2023/06/21/cameras)
- [96Boards — multiple USB cameras on one hub](https://www.96boards.org/documentation/consumer/guides/multi-usb-camera.md.html)
- [Arducam — MJPEG/YUV/RGB/H.264 UVC cameras](https://blog.arducam.com/mjpeg-yuv-rgb-h264-uvc-camera-usb/)
- [ELP H.264 1080P USB camera module](https://www.amazon.com/clp/B01E8OX212)
- [Mini-Box M4-ATX](https://www.mini-box.com/M4-ATX) · [DCDC-USB-200](https://www.mini-box.com/DCDC-USB-200)
- [Tycon PoE injectors](https://www.tyconsystems.com/categories/poe-injectors/6026428000002821032) · [Coolgear 12 V PoE injector](https://www.coolgear.com/product/gigabit-ieee-802-3af-at-poe-injector-12v)
- [Interior temperature distribution in parked cars (study PDF)](https://pdfs.semanticscholar.org/2184/cbe693e6f556f45e86ac8c058ea0a4c2024b.pdf)
- [Nexar — dash cams in summer heat](https://www.getnexar.com/blog/dash-cam-summer-heat-protection) · [RedTiger — dash cam heat resistance](https://redtigercam.com/blogs/dash-cam/dash-cam-heat-resistant) *(vendor blogs; figures understated vs. published measurements)*
- [DashCamGear — STARVIS 2 / Clear HDR](https://dashcameragear.com/sony-starvis-2-performance-guide/)
- [Reolink — RLC-520A official specs (<12 W, IP67, 802.3af, DC 12 V 1 A)](https://reolink.com/us/product/rlc-520a/)
- [Raspberry Pi AI Kit at $70](https://www.raspberrypi.com/news/raspberry-pi-ai-kit-available-now-at-70/)
- [rknn-cpp-yolo — YOLOv11 on RK3588](https://github.com/yuunnn-w/rknn-cpp-yolo)
- [Intel — Processor N100 product specifications (6 W base power)](https://www.intel.com/content/www/us/en/products/sku/231803/intel-processor-n100-6m-cache-up-to-3-40-ghz/specifications.html)
- [Orange Pi 5 Max vs Rock 5B+ (2026)](https://dev.to/revenueclaw/orange-pi-5-max-vs-rock-5b-the-32gb-sbc-battle-in-2026-3ho0) *(unverified)*
- [Scrypted vs Frigate: N100 vs Pi 5 benchmarks](https://www.privacysmarthome.com/guides/scrypted-nvr-vs-frigate-intel-n100-vs-pi-5-benchmarks-2026/) *(unverified)*
- [Reolink RLC-810A review (pricing)](https://cctvinfo.com/reviews/reolink-rlc-810a) *(pricing unverified)*
- [Low-voltage disconnect modules](https://www.amazon.com/Digital-Battery-Low-Voltage-Protection/dp/B07929Y5SZ) · [BatteryMart 12 V LVD](https://www.batterymart.com/p-l15c-12-volt-15-amp-low-voltage-disconnect.html)
