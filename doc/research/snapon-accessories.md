# Which Snap-on accessories can be reused, and which cannot

<!-- Researched and independently fact-checked on 2026-09-09. Written by one
pass and then attacked by a second whose only job was to find claims stated
confidently that were wrong, marketing copy repeated as fact, and stale prices
or model names. Its corrections are left inline and marked. Anything it could
not confirm says so rather than being quietly dropped. -->

# Snap-on diagnostic accessories vs. third-party / open-source tools
*(rewritten after an independent verification pass — see the audit note below)*

## Audit note

**Substantially right on the technical spine.** I re-fetched Snap-on's manuals, accessory sheets, EULA, Terms and Conditions, the Pass Thru Pro IV product page, the BK8000 manual, the BK5600 announcement, the eCFR text, sigrok and the GitHub API, and independently re-verified essentially every quoted passage and part number. They check out verbatim. The Bluetooth/USB architecture story, the banana-plug carry-over story, the low-amp-probe scale factors and the §1201 analysis all survive.

**But nine things needed fixing**, marked **[CORRECTION]** inline below. The two that matter most:

1. **The Pass Thru *Assistant* is a remote service, not a reusable J2534 box.** The original answer folded it into the "reusable exception." The flyer it cited says the opposite.
2. **The Pass Thru Pro IV's J2534 credentials were quoted from marketing copy that the same page's spec table contradicts.**

Also fixed: a claim the original marked "unverified" that its own cited document already answers (ZEUS+ M4+ is USB), a stale OEM attribution (Perceptron), two borescope model numbers that are accessories rather than products, an over-generalized borescope table row, "legacy" battery testers that are current, and an invented acronym.

**One limitation carries over unchanged and you should weight it heavily:** my general web-search budget was also exhausted for this session. I verified everything against primary documents I fetched directly, but I could **not** run the forum / Reddit / Hackaday / FCC sweep either. So "nobody has reverse-engineered this" still rests on exactly two negative sources — GitHub repository search and sigrok's hardware list — and remains **unproven**, not established.

---

**Bottom line up front:** almost nothing "smart" that Snap-on sells carries over. The wireless VCI, the scope module, the borescopes, the TPMS tools and the battery testers are all proprietary, and no public reverse-engineering of any of them turned up in the (narrow) negative-evidence sweep I could run. What *does* carry over is the passive copper — leads, clips, probes, current clamps, OBD-II/OBD-I adapters — plus one genuine standards-based exception: Snap-on's **Pass Thru Pro IV**.

---

## 1. The wireless vehicle interface / dongle

### What it actually is

**[CORRECTION — naming]** Snap-on's accessory sheets call the current wireless VCI a **"Compact Scan Module."** The acronym **"CSM" appears in no Snap-on document I could find — drop it.** The TRITON *manual* never uses "Compact Scan Module" at all; its spec table just says "Scan Module." Treating EESM306B, EESM3060B and EESM317A as one product called "the CSM" is a synthesis, not Snap-on's own taxonomy.

Verified model numbers (I re-fetched and re-extracted both documents):

| Part | Where documented |
|---|---|
| **EESM306B**, listed as "COMPACT SCAN MODULE EESM306B", kit `EAK0355L10B` | [ZEUS+ accessories PDF](https://www.snapon.com/Files/Diagnostics-US-2020/Platforms/ZEUS1/ZEUSPlusAccessories.pdf) — confirmed verbatim |
| "Compact Scan Module" (no P/N given), `6-25222A` Scan Module USB Interface Cable | [TRITON accessories PDF](https://www.snapon.com/Files/Diagnostics-US-2020/Platforms/TRITONM3000/TRITON-Accessories-PDF.pdf) — confirmed |
| **EESM3060B** and **EESM317A** | [TRITON online manual, Scan Module spec table](https://www.snapon.com/DiagnosticsManuals/TRITON%20NA/Content/M3000/TRITON.htm) — confirmed |

Platform models and vehicle-link type, all re-verified against Snap-on's current product pages (MSRPs added, since they were absent and are relevant to "was it worth it"):

- **Wireless scan module:** ZEUS+ **EEMS348** ($11,792), TRITON **EEMS349** ($5,995), APOLLO GEN 4 **EESC357A** ($4,495).
- **Integrated scan module, no separate dongle** — each page's spec line reads "Scan Module: Integrated": TRITON-D10 **EEMS344** ($6,550), APOLLO+ **EESC338** ($4,995), SOLUS+ **EESC337** ($3,599).

Legacy: **VERDICT (Model D7** — confirmed, the manual's figures are captioned "Model D7"**)** and **VERUS Edge** used a larger Scan Module with a proprietary data-cable connector at the module end and OBD-II at the vehicle end.

**[CORRECTION — coverage gap]** Snap-on's [current-products page](https://www.snapon.com/EN/US/Diagnostics/Diagnostics-Tools/Current-Products) also lists **MT2600**, **PRO-LINK+** and **PRO-LINK Edge** (heavy-duty, Class 1–8), and **Diagnostic Thermal Imager+**. None appear in this report. If you are publishing this as a survey of the range, say that the heavy-duty line and MT2600 were not examined.

### Link type — verified, quoted

From the TRITON manual's Scan Module specification table (re-fetched; exact):

> **Communications: Wireless Bluetooth® 2.1 Technology**
> **Operating Range: Approx. 50 feet (15.24 m)**
> **USB Power: Max. 5V @ 0.9A**
> DC Operating Power — EESM3060B: 8-32 VDC, Max. 10W; EESM317A: 8-32 VDC, Max. 6.5W

Legacy modules, same architecture, older radio — both re-verified in the PDFs:

- VERDICT Scan Module — "Communications: **Wireless Bluetooth® 2.0 Technology**", "USB Power 5V @ 500mA", "Data Cable Connector Power 8V to 32V, Maximum 12 Watts" ([VERDICT user guide](https://www.snapon.com/DiagnosticsManuals/PDF/NA/UG/VERDICT.pdf))
- VERUS Edge Scan Module — "Communications: **Wireless Bluetooth® 2.1 Technology**" ([VERUS Edge user guide](https://www.snapon.com/DiagnosticsManuals/PDF/NA/UG/VERUSEDGE.pdf))

So: Bluetooth Classic (BR/EDR), not BLE, not Wi-Fi, not proprietary RF. The tablets *also* have Wi-Fi (the VERUS Edge display-unit table lists "Wi-Fi standard (802.11 b/g/n)" *and* "Bluetooth 2.1" as separate lines), but that is the tablet's network radio for the internet-side features — not the vehicle link. **Mild caveat the original overstated:** "not BLE" is inferred from a version number in a spec table, not from a radio teardown. Bluetooth 2.1 predates BLE, so the inference is sound *if the spec table is current*; a dual-mode chip could still be behind it. Nobody has looked.

Every scan module also has a USB jack and Snap-on sells the cable: `EAX0068L02A` "Wireless Scan Module USB Cable" (ZEUS+), `6-25222A` USB-C→USB micro-B (TRITON). Both confirmed on the accessory sheets. The wired fallback exists.

### Is the protocol documented?

**No.** Snap-on publishes the radio standard and nothing above it. No SDK, no API document, no protocol spec, and no J2534 or RP1210 conformance claim for the scan module on any current product page or manual index. The Bluetooth *profile* is not stated anywhere I could find — **UNCONFIRMED**; SPP/RFCOMM is a plausible guess for BR/EDR 2.1 and nothing more. What travels over that link is Snap-on-proprietary framing of vehicle bus traffic; the module holds the transceivers, so it is the "smart" half. (The DoIP claim checks out indirectly: the TRITON sheet names `EAX0072L01A` an "**OBDII / DoIP Extension Cable**.")

### Has anyone reverse-engineered it?

**No evidence that anyone has — on a thin evidence base I could not widen.** I re-ran the searches myself: GitHub repository search returns **zero** repositories for `snap-on scan module` and zero for `EESM306 OR EESM317 OR EETA113D`. [sigrok's supported-hardware list](https://sigrok.org/wiki/Supported_hardware) contains **zero** occurrences of "snap-on" or "snapon". The one Snap-on-adjacent repo, [JamesPreston1993/vehicle-document-parser](https://github.com/JamesPreston1993/vehicle-document-parser), exists and is what it was described as — "A program to collect vehicle documents on Snap-on Verus and Verdict devices and display in CSV format", **0 stars**, file-format work, not protocol work.

**Weight this correctly.** GitHub *repository* search indexes names, descriptions and topics — not code. Two negative sources are not a literature review. Anyone with working search should redo this before treating "nobody has done it" as settled.

**[CORRECTION — the comparison ecosystem is thinner than stated.]** I checked all three: [NikolaKozina/j2534](https://github.com/NikolaKozina/j2534) is real (73 stars) and its README confirms it is "written specifically for the Tactrix Openport 2.0" — so the original's caveat was right. [rnd-ash/OpenVehicleDiag](https://github.com/rnd-ash/OpenVehicleDiag) is healthy (1.0k stars). But [rnd-ash/MacchinaM2-J2534-Rust](https://github.com/rnd-ash/MacchinaM2-J2534-Rust) is **archived** (24 stars). "The open J2534 ecosystem is healthy" is carried by one project, not three.

### The genuine exception: Snap-on's Pass Thru devices

**[CORRECTION — marketing copy repeated as fact.]** The original quoted this line from [the Pass Thru Pro IV page](https://www.snapon.com/EN/US/Diagnostics/Products/Pass-Thru-Pro-IV):

> "Compatible with SAE J2534-1 and J2534-2 including 04.04 and v500 API reprogramming standards for every automaker."

That line is real — but it is the *marketing* block. Further down **the same page**, the specification table says something materially narrower:

> "**Compliant to SAE J2534 (Feb 2002) and SAE J2534-1 (Dec 2004)**"
> "PC Interface: USB connector"
> "Windows® 10 (32 and 64-bit) / Windows® 7 (32 and 64-bit) / Windows® 8, 8.1 (32 and 64-bit)"
> MSRP: $1,980.00

Report the spec line, not the marketing line. J2534-**2** support is claimed only in ad copy that the spec table does not back.

**[CORRECTION — the Windows ceiling.]** Snap-on's supported-OS list stops at **Windows 10**. There is no Windows 11 entry. Windows 10 reached end of support in October 2025. "Reusable on Windows today" needs that asterisk in bold.

**[CORRECTION — Pass Thru Assistant is NOT a reusable J2534 device.]** The original's verdict said "Pass Thru Pro IV (EETA113D) **and the Pass Thru Assistant family**: reusable in principle." The [PTA+ flyer](https://www.snapon.com/Files/Diagnostics-US-2020/Platforms/Pass-Thru-Assistant/PTAFlyer_2026.pdf) it cited draws the opposite line, in two adjacent columns:

> **Pass Thru Assistant** — "J2534 flash programming services performed **remotely** by OE trained technicians with guaranteed results."
> **Pass Thru Pro** — "Functions as a stand-alone J2534 VCI **when used with a PC and OE purchased subscription**."

The Assistant is a *remote service* delivered on a locked appliance ("Windows 10 OS, 5" touchscreen, 256 GB SSD to host all OE applications"). It is not a box you repurpose. Only **Pass Thru Pro** is the stand-alone J2534 VCI — and note the flyer's own qualifier, which the original dropped: the stand-alone use case is scoped to OEM reprogramming subscriptions. (Third-party *diagnostic* software targeting the J2534 API is not subscription-bound; OEM reflashing is.)

### Verdict — wireless VCI

> **Proprietary, do not bother.** Bluetooth 2.1 is a standard transport, so a Linux box *can* see and connect to the module — but everything above RFCOMM is undocumented Snap-on framing with no public reverse-engineering to build on. Budget this as a from-zero protocol RE project, not an integration task. A $30 ELM327-class or a J2534 adapter gets you further in an afternoon.
>
> **Exception — Pass Thru Pro IV (EETA113D) only: reusable in principle**, because SAE J2534 is a published standard and Snap-on's spec table claims J2534 / J2534-1 compliance. Usable *on Windows 10 or older* today; on Linux, only via Wine + vendor DLL, or unwritten driver work. **The Pass Thru Assistant / PTA+ is a remote programming service, not a device to carry over.**

---

## 2. The lab scope

### Where the scope lives (all re-verified on the product pages)

- **ZEUS+ (EEMS348)** — "4-channel", "3MHz", "Up to 6 million samples/sec", "0-100mV to 0-400V, 50kV secondary ignition", scan module "Wireless". Accessory sheet names a **"SCOPE MODULE M4+."**
- **TRITON (EEMS349)** — **2-channel**, 3MHz, up to 6 MS/s, 0-100mV to 0-400V, scan module "Wireless". **TRITON-D10 (EEMS344)** — same class of scope, scan module "Integrated".
- **VERUS Edge** — **Scope Module M4**, a physically removable module that docks in the back of the tablet.
- **VERDICT (D7)** — the **M2 Scope/Meter**, an entirely separate handheld with its own colour display, rotary switch and 4×1.2 V Ni-MH battery pack, which the manual says "can be used as a stand-alone meter, or configured to transmit data to the D7 Diagnostic Tool."

### The interfaces — this is the important part

**VERUS Edge M4 Scope Module** (all four quotes re-extracted verbatim from the PDF):

> "1— Auxiliary (DB9) Connector; 2— Channel 4 Jack; 3— Channel 3 Jack; 4— Channel 2 Jack; 5— Channel 1 Jack; 6— Common (Ground) Jack; 7— USB Jack (not shown - used for remote operation)"
>
> "If needed, the Scope Module (M4) can be removed from the Diagnostic Tool, then connected to the Diagnostic Tool with a USB cable… Use the Type A/B USB cable, which is provided with your kit." *(Plus a detail the original missed: "When used remotely, the M4 must be connected to the lower (black) USB jack on the diagnostic tool.")*
>
> "The Scope Module operates on USB power (**5V @ 500mA**) supplied by the Diagnostic Tool."
>
> "When the M4 Scope Module is connected either by USB cable or in it's docking station on the rear of the diagnostic tool, **it automatically pairs with the diagnostic tool using the USB connection**." *(§8.2)*

So the M4 is a **plain USB peripheral**. Snap-on's UI calls the USB attachment "pairing," which is where confusion creeps in, but the manual is unambiguous.

**VERDICT M2 Scope/Meter** is the opposite — a genuine **Bluetooth** instrument. Confirmed: VERDICT's Paired Devices flow searches for it and it "is typically listed as 'VERDICT M2-' plus six digits of the unit's serial number"; working range "about 30 feet (9.14 m)". It is also listed as optional pairable equipment in the VERUS Edge manual (§14.1.4), with the note that an M4 must be undocked first.

**[CORRECTION — this was already answered by a document the original cited.]** The original marked "whether the ZEUS+ M4+ is USB" as an unverified inference. It isn't. The [ZEUS+ accessories PDF](https://www.snapon.com/Files/Diagnostics-US-2020/Platforms/ZEUS1/ZEUSPlusAccessories.pdf) lists, under *Included with ZEUS+ purchase*, **"M4 USB Cable (9ft) — 6-10622A"**, as a line item distinct from "Wireless Scan Module USB Cable — EAX0068L02A". The M4+ ships with a dedicated USB cable. Treat "M4+ is USB-attached" as **documented**, not inferred. (What remains unverified is only whether its *descriptor set and protocol* match the older M4 — which nobody knows either way.)

### Could other software drive it?

The M4/M4+ being USB is the most promising thing in this report — and it still isn't enough. There is no published descriptor set, no vendor protocol document, no sigrok driver (verified: zero Snap-on entries), and no GitHub project (verified: zero repos). A USB transport means `lsusb`, usbmon and fast iteration, and a scope's command surface (arm, set timebase/scale, stream samples) is far smaller than a multi-OEM diagnostic stack. But nobody has done it, so it's a project, not a capability.

### Verdict — lab scope

> **Scope modules themselves: proprietary, do not bother** — unless someone adopts the M4/M4+ as a deliberate RE project. It is USB, bus-powered at 5 V/500 mA, and works detached from the tablet, which makes it the single most attackable Snap-on device on this list. Nobody has attacked it. Do not tell people "it's USB, so it'll work" — USB is a transport, not a protocol.
>
> **VERDICT M2 specifically: reusable as a standalone instrument** — the manual says so outright. You keep a working 2-channel scope/meter; you just don't get it into your software.
>
> **The scope *leads and probes*: fully reusable — see §5.** This is where the real carry-over value is.

---

## 3. Borescopes / inspection cameras (BK series)

**Denied — for the models where it's documented.** Snap-on's own manual settles the BK8000 case. The rest is extrapolation and should be labelled as such.

### What the range is

Recovered from the Wayback CDX index of `shop.snapon.com` (the live shop is a JS shell that returns byte-identical HTML for a nonexistent SKU, so it is useless for this — I checked):

- **BK3000** family — "Video Inspection Scope" (BK3000, -55, -55A, -GM, -GR, -OR, -YW)
- **BK5600** — "True Digital Video Inspection Scope" (BK5600-CF, BK5600DUAL55, BK5600DUAL85)
- **BK6500** — "Video Still Recording Digital Borescope" (BK6500, BK6500DUAL55)
- **BK7000** — "High-Definition Borescope with 5.5 mm Dual Imager" (most recent snapshot Feb 2026 — apparently the current flagship)
- **BK8500** — "Advanced Wireless Video Scope" (BK8500, BK8500DUAL55)
- Imagers/accessories sold separately: BK8000-1/-55/-56/-UV, BK5500-1/-7/-9/-10/-15, BK5600-12, BK-IMG-F-38, BKIMGD8.5-3M, BK6000-2/-10/-11/-13

**[CORRECTION — two of the listed "models" are accessory prefixes.]** The original listed "**BK6000** / BK6500" and "**BK8000** / BK8500" as display-unit models. In the archived catalogue, only **BK6500** and **BK8500** appear as scopes; `BK6000-xx` and `BK8000-xx` appear only as accessories and imagers. BK8000 was a real display unit historically (the 2011 manual is for it) but is not in the current catalogue. Fix the range list.

**[CORRECTION — Perceptron is a stale attribution.]** The manual notice is genuine and I re-verified it character-for-character: **"Perceptron is a registered trademark of Perceptron, Inc. © 2011 Perceptron, Inc. … Perceptron, Inc., 47827 Halyard Dr., Plymouth, MI 48170."** But that is a **2011** document. `perceptron.com` today redirects to `isravision.com`, whose Perceptron page states: **"In 2020, Perceptron joined the Atlas Copco Group."** Perceptron is now industrial metrology under ISRA VISION / Atlas Copco. Present-tense "Snap-on rebrands Perceptron inspection scopes" is **not supportable for the BK7000 or BK8500**. Say: *the BK8000 generation was a rebranded Perceptron; who builds the current units is* **unconfirmed**.

### The actual interfaces (BK8000, all quotes re-extracted from [the manual](https://public.snapon.com/UserManuals/ShopTechTools/BK8000_Manual_10-27-11_Multilingual%20low%20res.pdf))

Display unit *and* imager handle each carry:
> "**Wireless interface: Wi-Fi - 802.11n**" · "Screen Resolution: 480 x 272 RGB with LED backlight" · "**Video Out: NTSC/PAL**"

Imager cable connector:
> "BK8000-1  36" long, 8.5mm diameter, **9-pin dual view imager**"

USB behaviour — the decisive quote:
> "The BK8000 can be connected to a PC to transfer image and video files using the supplied USB cable… **The BK8000 will appear as an external drive to the user** allowing pictures and videos to be transferred to and from the BK8000. Note that if a Micro SD card is inserted, only those files on the Micro SD card will be available."

Plus a detail the original omitted that reinforces the point: "The view unit LCD screen will shut off while attached to the computer," and "the BK8000 will not charge through the USB connector." The USB port is mass storage for file retrieval. There is no UVC anywhere in the document.

The corded BK5600 shares the architecture — the [product announcement](https://public.snapon.com/a_prodannouncements_us/BK5600_NPA.pdf) confirms "SD card storage", "Screen Resolution 480 x 272", imager "Resolution 640 x 480" and "**Video Out: NTSC/PAL**". *(It does not mention USB at all.)*

### What *is* reusable

The **composite NTSC/PAL video output**, cable `BK5500-8` "Composite Video Out Cable". Confirmed mechanism, quoted:

> "To use this feature, a standard **3.5mm jack plug to RCA plug cable** is required… **Inserting the 3.5mm jack plug disables the BK8000 LCD display and touchscreen.**"

Any cheap USB composite capture dongle presents that to Linux as a normal V4L2 device. Live 480-line analog video into ffmpeg/OBS — genuinely useful, genuinely universal, genuinely 1990s in quality.

The **imager cables are not reusable** — proprietary 9-pin connector into a Snap-on handle. The BK8000/BK8500 Wi-Fi link between imager handle and viewer is undocumented; whether it exposes anything RTSP/MJPEG-shaped is **UNCONFIRMED** (no teardown found; GitHub returns zero repos). A reasonable Wireshark target that nobody has published.

### Verdict — borescopes

> **Reusable but only as dumb hardware — via the composite video output, not via USB.** Capture the analog NTSC/PAL feed into a cheap dongle and you have live video in Linux at standard-definition quality.
>
> **Explicitly: the BK8000 is NOT UVC. Do not tell people they can plug one in and get a `/dev/video0`.** They get a removable drive full of JPEGs and AVIs.
>
> **[CORRECTION — scope of that denial.]** The evidence covers **BK8000** (full manual) and, for NTSC/PAL out, **BK5600** (announcement). It does **not** cover BK3000, BK5500, BK6500 or BK7000, whose USB behaviour and video-out presence are **UNCONFIRMED**. The BK7000's manual is not published at any guessable path on `public.snapon.com` (404s). The original's summary table asserted composite-out and USB-mass-storage across the whole range; it shouldn't. The BK3000 in particular is an entry-level unit that may have no video out at all.

---

## 4. TPMS tools, battery/charging testers, other modules

### TPMS

Confirmed from archived catalogue URLs: **TPMS2, TPMS3, TPMS4, TPMS5** ("Tire Pressure Sensor System Tool Kit", snapshot Dec 2024 — apparently current). Accessories confirm the architecture: `TPMS3-1` OBD-II Cable, `TPMS3-2` OBD-II Connector, `TPMS3-4` USB Cable, `TPMS3-3` Magnet, `TPMS3BOX` Sensor Bench Tester, `TPMS3PRG` Programmable Sensor Add-on, plus update SKUs `TPMS2U`, `TPMS3U`, and `TPMS4U` — whose catalogue URL is literally `TPMS4-Software-License/TPMS4U`.

From the [TPMS3 press release](https://web.archive.org/web/2015id_/http://www1.snapon.com/display/231/ToolNews/PressReleases/2011/Snap_on_Must_Have_Tool_April_TPMS3_04_26_11_final.pdf) — re-verified verbatim:
> "Reprogram sensor ID into ECU with included OBDII connector for Asian vehicles" · "Tests wireless Key FOBs for proper operation and signal strength" · "**Updateable via USB and PC**"

The key structural fact holds: **these do not talk to the scan tool.** I re-grepped the manuals. VERDICT: zero occurrences of "TPMS". VANTAGE Legend: two. VERUS Edge: 24, and every one is *software content* — "TPMS Indicator Reset Procedures", "TPMS Relearn Procedures", "View and Clear TPMS Related Codes", "Perform TPMS Related Functional Tests" — reached through the normal scanner path. There is no TPMS tool in any Paired Devices list. The handheld is an island.

**[Marked as general knowledge, not sourced]** "315/433 MHz sensor interrogation" is the standard industry frequency pair, not something any Snap-on document I fetched states. Don't attribute it to a source.

**UNCONFIRMED:** who OEMs TPMS5. (The dealer-catalogue ATEQ association is Snap-on Business Solutions running someone else's catalogue, not evidence about Snap-on-branded TPMS5. Do not repeat it as fact.)

### Battery / charging system testers

Confirmed from archived catalogue URLs, with Snap-on's own category names: **EECS150** "Basic Battery System Tester" (also listed as "12 V Basic"), **EECS350** "Enhanced Battery System Tester", **EECS550A** "Wireless Battery System Tester", **EECS750A** "Advanced Battery, Starting and Charging System Tester", plus `EECS-PRINTER` "Wireless Printer (Blue-Point)" and `EECS550-CC` carry case.

**[CORRECTION]** The original called "EECS304/305/306/309" **legacy**. The archive shows **EECS304C** sold as "MicroVAT™ Elite Tester" and **EECS306C** as "D-TAC™ Elite", both with a full 2025 accessory catalogue (`EECS306C-2` amp clamp, `-3` cable drop cables, `-4` multimeter probes, `-5` battery test cables, `-6` post adaptors, `-CVR` cover). These are live product lines, not legacy.

Same structural story regardless: standalone instruments, absent from every Paired Devices list in every manual read. **UNCONFIRMED:** the radio type and protocol of EECS550A/EECS750A — only pre-2015 tester PDFs remain reachable. Do not assume Bluetooth.

### Other modules that *do* talk to the tablet

Exactly two families, both above: the **Scan Module** (Bluetooth) and the **Scope Module / M2 Scope-Meter** (USB for M4/M4+, Bluetooth for M2).

Thermal imagers are separate products. The [platform legal page](https://www.snapon.com/DiagnosticsManuals/NA/Content/Landing%20Sites%20NA/Legal/Legal.htm) does disclose, verbatim: "This device contains third-party software licensed by **FLIR Systems** - Commercial Vision Systems", attached to the **"Thermal Imager ELITE"**. That tells you the imaging core is a licensed FLIR part and nothing usable about a host interface. **[CORRECTION]** The original named "**DTI1**" as a thermal imager model — that string appears on none of the three Snap-on pages I checked, which name only "Diagnostic Thermal Imager+", "Diagnostic Thermal Laser" and "Thermal Imager ELITE". **Drop DTI1 as unverified.**

### Verdict — TPMS, battery testers, other modules

> **Proprietary, do not bother — but note they're also self-sufficient.** These never integrated with the tablet, so "carrying them over" isn't the question: they work exactly as well after you switch scan tools. You lose nothing and gain nothing.
>
> The one real loss is **updates**: TPMS coverage and battery-tester databases arrive via paid, Windows-only, licence-gated updaters — `TPMS4U` is sold under a URL that says "Software License". An un-updatable TPMS tool degrades every model year. That's the thing that actually bites.

---

## 5. Probes, leads, back-probe kits, breakout boxes, adapters

The good news section, and it holds up completely. Every part number below was re-verified against the ZEUS+ and/or TRITON accessory sheets.

### The scope leads are standard banana

Both quotes re-extracted verbatim:

> "**The Scope Module uses standard safety banana plugs that are compatible with many accessories.**" — VERUS Edge §8.3.2
>
> "**The Scope Multimeter uses standard safety banana plugs that are compatible with many accessories.**" — VERDICT §9.1.2

Channel jacks are colour-coded 4 mm safety banana throughout — VERUS Edge and ZEUS+: Ch1 yellow, Ch2 green, Ch3 blue, Ch4 red, GND black.

Genuinely just connectors, reusable with any scope or DMM with 4 mm inputs:

- **Channel leads** — ZEUS+ LSPI leads `6-03022A` (yellow Ch1), `6-03122A` (green Ch2), `6-03222A` (blue Ch3), `6-03322A` (red Ch4). Extended Scope Leads `EETA108B`.
- **Alligator clips** — `2-06433A1` black, `A2` red, `A3` blue, `A4` yellow, `A5` green.
- **Test probes** — `2-09542A1` black, `2-09542A2` red.
- **Ground leads** `EAX0066L41A`, **jumper lead set** `EAX0066L43A`.

### The current clamp — fully reusable, with published scale factors

The **Precision Low Amps Probe (`EETA308D`)** is listed on both the ZEUS+ and TRITON sheets. The manuals publish its transfer function — identical text in VERUS Edge §8 *and* VANTAGE Legend, re-verified in both:

> Low Amp Probe — "20A scale (100mV/Amp)", "40A scale (10mV/Amp)", "60A scale (10mV/Amp)" · "Connect the positive (+) Amp Probe lead to the yellow jack on the diagnostic tool for values on Ch.1, or to the green jack for values on Ch. 2. Connect the negative (–) lead to GND (black jack)."
> Footnoted limit: "Do not use the Low Amp Probe to measure current on conductors at a potential greater than **46VAC peak or 70VDC**."
> Range: "the optional Low Amp Current Probe measures current from **10 mA to 60 Amps**."

And VERDICT confirms the principle: the amperage sensed is "converted to a voltage signal for processing, but displayed as amperage on the screen."

Banana plugs in, known mV/A out. Plug it into *any* scope, set your own scaling, done. Best carry-over item Snap-on sells.

**Two caveats the original should have stated:** (a) those "scales" are **switch positions on the probe body** — the probe's own range switch is load-bearing, and you must know which position you're in to apply the right factor; (b) the manuals attribute these numbers to a generic "Low Amp Probe", never to part number **EETA308D** specifically. Tying the two together is a safe inference, not a quoted fact.

### Ignition and secondary accessories — reusable, passive

`EETA309A05A` Secondary Coil Adapter Lead, `EETM306A02` Secondary Ignition Clip-on Wire Adapter, `EAK0294B09A` Ignition Scope Lead Kit, and the full COP range — all re-verified on both sheets: `EETM306A03` Ford COP-1, `A04` Chrysler COP-2, `A08` Acura/Honda/Isuzu COP-4, `A09` Volvo/BMW COP-5, `A10` Mercedes COP-6, `A11` Mercedes Dual COP-7, `A12` BMW COP-8, `A13` Lexus COP-9, `A14` Chrysler/Jeep/Lexus/Toyota COP-11 — plus pickups `EAC0056L00A` (1-flag), `EAC0056L02A` (3-flag, omitted from the original), `EAC0056L03A` (stick), and CIC pickups `EETM306A05`/`A06`. Capacitive/inductive transducers feeding the coil adapter lead. No electronics to license.

### Things that look passive but aren't quite

- **Inductive RPM Pickup** — quoted verbatim: "connects to the scope auxiliary connector of the Diagnostic Tool with a **DB9F plug**." Reusable only if someone publishes the pinout.
- **Pressure transducers** `EEPV302AL` (100 PSI), `EEPV302AT` (500 PSI), `EEPV302AH` (5000 PSI), cable `EAX0024B10A`, extension `EAX0024B30A`, plus `EEMS324PSA` Pressure Adapter on the TRITON sheet. The manual states the aux connector "is used for connection of the optional RPM inductive pickup and the pressure transducer split lead adapter" — so they need excitation from that port. Physically dumb, electrically dependent on an undocumented pinout. Bench-supply hack territory. **Pinout UNCONFIRMED — no published pinout found for the Snap-on DB9 aux connector.**
- **OBD-I adapter kit `EAK0301B10B`** — confirmed on the TRITON sheet as "OBD-I ADAPTER KIT". *(ZEUS+ sells the same function as `EAK0351L01A`, "OBD-I DOM & ASIAN ADAPTER KIT" — the kit SKU differs by platform.)* Contents: Multi-1 `EAA0355L90B`, Ford-4 `L70A`, Ford-1B `L20C`, GM-1 `L10A`, Nissan-1 `L40A`, Nissan-2 `L58A`, Toyota-1 `L50A`, Toyota-2 `L52A`, Kia-2 `L92A`, Chrysler-1 `L30A`, Chrysler-2 `L31A`, Multi-2 `L42A`, Jeep-1 `L49A`, Hyundai-2 `L51A`, Mitsubishi-1 `L55A`, plus DA-6 `EAX0068L27A`. *(Snap-on's own sheets disagree on Honda-1: `EAA0355L77A` on ZEUS+, `EAA0355L53A` on TRITON — their inconsistency, not a transcription error.)* Pure wiring adapters, but they mate to Snap-on's data cable, so reuse means owning that cable and knowing its pinout. And Snap-on's troubleshooting lists "a circuit fault in the data cable, **Personality Key**, or adapter" — verified in both VERDICT and VERUS Edge. The personality keys are not bare passthroughs.
- **European "software keys"** — `MT2500S17/S20/S21/S27/S33/S34`, `EAP0268L35A` (S51), `EAP0268L70A` (S64), `EAP0293L00A` (S74), kit `EAK0301B06B`. Licence/personality cartridges. Zero carry-over value.
- **Smart Vehicle Interface Adapter Kits** — `EAK0347L20A` BMW, **`L21A` DLC Adapter** (omitted from the original), `L22A` Harley-Davidson, `L23A` Indian/Victory, `L24A` Ducati, `L25A` Suzuki, `L26A` Honda, `L27A`/`L28A`/`L29A` Kawasaki 1-3, `L30A` Yamaha, `L35A` ISO-1. Motorcycle DLC adapters; "Smart" is a product-line label. **Whether any contain active components is UNCONFIRMED.**

### Power and misc cables — reusable as generic cables

`EAX0072L01A` "OBDII / DoIP Extension Cable", `EAX0066L10B` lighter-to-OBD power cable, `EAX0066L04B` 12V power cable/battery clip, `EAX0066L07A` power-sports DC cable, `6-23222A` USB-C to USB-C, `6-25222A` USB-C to micro-B, `6-10622A` M4 USB cable. All verified. Ordinary cables at Snap-on prices.

### Verdict — passive accessories

> **Reusable — genuinely, and this is where the money you already spent survives.**
>
> - **Fully reusable, no caveats:** all channel leads, alligator clips, test probes, extended scope leads (`EETA108B`), ground leads, jumper leads. Snap-on says in its own manual they're standard safety banana.
> - **Fully reusable, best item on the list:** Precision Low Amps Probe `EETA308D` — banana output, published scale factors (100 mV/A on the 20 A range, 10 mV/A on 40 A and 60 A), 10 mA–60 A, works with any scope. Mind the range switch and the 46 VAC pk / 70 VDC limit.
> - **Reusable, passive:** COP adapters, secondary coil lead and clip-on adapter, CIC and flag pickups.
> - **Reusable but only after someone reverses a pinout:** RPM inductive pickup (DB9F), pressure transducers (need aux-port excitation), OBD-I adapters and personality keys (need the Snap-on data cable and its pinout).
> - **Not reusable:** European software keys — licences in a plastic shell.

---

## Legal and contractual landscape

Not legal advice — a description of the terrain, with sources, so a community can ask better questions of someone qualified. **This section survived verification intact; I found no errors in it.**

### The EULA is the sharpest edge, and it is narrower than it looks

[North American Diagnostics EULA](https://www.snapon.com/Files/EULA/8-13963A03RevFStdNAEULAClean.pdf) (index of regional versions at [eula.snapon.com/diagnostics](https://eula.snapon.com/diagnostics)) — all quotes re-extracted verbatim:

> "**PROHIBITED USES YOU MAY NOT:** … (ii) modify, merge, translate, decompile, **reverse engineer**, disassemble, decode, or otherwise alter or attempt to derive the source code of the Software; … (vii) provide, disclose, divulge or make available to, or permit use of the Software by any third party without Snap-on's prior written consent."

Two things about its scope:

1. **It binds "the Software," not the hardware.** The grant runs to "the person or business entity who originally acquired the Software Products ('Software')"; "Snap-on retains title and ownership of the Software, and it is being licensed to you and not sold." Observing what a Bluetooth module puts on the air, or a USB scope module on the wire, is not obviously "decompiling the Software" — but if you do it *by running Snap-on's software on Snap-on's tablet*, you are a licensee doing it, and the line blurs. The cleanest posture is black-box observation of the *accessory*, driven by code you wrote, with the vendor application never involved.
2. **Governing law is aggressive:** "governed by the laws of the State of Wisconsin… YOU CONSENT TO EXCLUSIVE JURISDICTION AND VENUE IN THE FEDERAL COURTS SITTING IN MILWAUKEE COUNTY, WISCONSIN, UNLESS NO FEDERAL JURISDICTION EXISTS, IN WHICH CASE… ANY STATE COURT LOCATED IN MILWAUKEE COUNTY" — plus a waiver of personal-jurisdiction and forum non conveniens defences. (Snap-on itself is at 2801 80th Street, Kenosha, WI.)

The separate [website Terms and Conditions](https://www.snapon.com/Terms-and-Conditions) add binding arbitration — verified verbatim: "arbitration shall take place according to the Commercial Rules of the American Arbitration Association. **The arbitration will be held in Chicago, Illinois**," with claims to be filed "within **one (1) year** following the occurrence first giving rise to the claim." Note the scope: it covers "any controversy or dispute between you and Snap-on **concerning this Site or the Materials**." That is narrower than it looks — it reaches mirroring manuals, not hardware interop.

The EULA also carries an **OEM Data** clause: OEMs "may require you to register your use of the OEM Data or accept separate terms of use or other agreements or conditions." A second layer of agreements sits underneath Snap-on's own.

### DMCA §1201: probably not the obstacle people fear here

[17 U.S.C. §1201(f)](https://www.law.cornell.edu/uscode/text/17/1201) contains an explicit reverse-engineering-for-interoperability provision permitting a lawful user to circumvent "for the sole purpose of identifying and analyzing those elements of the program that are necessary to achieve interoperability of an independently created computer program", with subsection (3) allowing the information to "be made available to others… solely for the purpose of enabling interoperability."

The threshold question is whether a **technological protection measure controlling access to a copyrighted work** exists at all. Sniffing an unencrypted Bluetooth or USB link and writing a fresh client is ordinarily not circumvention of anything. If a given accessory turns out to use signed firmware or an authentication handshake, the analysis changes, and §1201(f) is where the conversation goes.

**I independently re-checked the exemption numbering against the current eCFR text, because this is where the original was most likely to be wrong. It is correct.** [37 CFR §201.40](https://www.ecfr.gov/current/title-37/chapter-II/subchapter-A/part-201/section-201.40), current classes:

- **(b)(13)** — "computer programs that are contained in and control the functioning of a lawfully acquired **motorized land vehicle** or marine vessel… when circumvention is a necessary step to allow the diagnosis, repair, or lawful modification of a vehicle or vessel function." That is about **the car**, not your scan tool.
- **(b)(14)** — same vehicle programs, for owners/lessees "to access, store, and share operational data, including diagnostic and telematics data."
- **(b)(15)** — repair of "a lawfully acquired device that is **primarily designed for use by consumers**." A professional Snap-on scan tool is a hard sell as consumer-primary. Do not assume this covers your tablet.
- **(b)(16)** retail-level commercial food preparation; **(b)(17)** medical devices; **(b)(18)** good-faith security research. Confirmed: **there is no general commercial/industrial-equipment repair class.**

Both (13) and (14) carry the same verbatim warning: "Eligibility for this exemption is not a safe harbor from, or defense to, liability under other applicable laws, including without limitation regulations promulgated by the Department of Transportation or the Environmental Protection Agency."

### The honest distinction

- **Technically possible** — for the passive accessories, trivially. For the M4/M4+ scope module and the scan module, possible in principle, unattempted in public, and a substantial project.
- **Practically available** — for the passive accessories, today, with no work. For everything else, *nothing exists*. Not a driver, not a protocol note, not a teardown. "Technically possible" here means "someone could start", not "someone could use."
- **Legally advisable** — different again, and turning on facts I can't establish: whether any TPM exists on a given device, whether you are acting as a Snap-on licensee while doing the work, US vs EU. **UNCONFIRMED:** the EU position (Software Directive 2009/24/EC Art. 6) — named from memory in the original, and I did not fetch it either. Anyone planning to publish protocol work, ship code, or sell a bridge product should talk to counsel first.

**Still unconfirmed on the legal side:** whether Snap-on has ever asserted these clauses against an interoperability project; whether any accessory firmware is signed or its link encrypted; the current EU position. No case law cited from memory, in either pass.

---

## Summary table

| Category | Interface | Documented? | Anyone made it work elsewhere? | Verdict |
|---|---|---|---|---|
| Compact Scan Module (EESM306B / EESM3060B / EESM317A) | **Bluetooth 2.1 BR/EDR**, ~50 ft, + USB port | Radio yes, protocol **no** | **None found** — zero GitHub repos, zero sigrok support *(two negative sources only)* | **Proprietary, do not bother** |
| Legacy Scan Module (VERDICT / VERUS Edge) | Bluetooth 2.0 / 2.1, + USB jack, 5V @ 500mA | Radio only | None found | **Proprietary, do not bother** |
| **Pass Thru Pro IV (EETA113D)** | **SAE J2534 / J2534-1** *(per spec table; J2534-2 is marketing-only)*, USB, Windows DLL, **Win 10 max**, MSRP $1,980 | **Yes — published standard** | J2534 ecosystem exists; no Linux driver found for *this* box | **Reusable — the real exception**, on Win10-or-older; Linux = work |
| **Pass Thru Assistant / PTA+** | **Remote programming service** on a locked Win10 appliance | n/a | n/a | **[CORRECTED] Not a device to carry over** |
| Scope Module M4 / M4+ | **USB**, 5 V @ 500 mA, docks or via A/B cable (`6-10622A` ships with ZEUS+); 4× banana + DB9 aux | Connectors + transport yes, protocol **no** | None found | **Proprietary, do not bother** — but the most attackable target here |
| M2 Scope/Meter (VERDICT) | **Bluetooth**, ~30 ft; standalone instrument with own display/battery | Manual says standalone | n/a | **Reusable as dumb hardware** — works standalone, just not in your software |
| Borescope **BK8000** (legacy) | 9-pin imager; **Wi-Fi 802.11n** handle↔viewer; USB = **mass storage**; **3.5 mm composite NTSC/PAL out** | Yes, fully | None found | **Reusable only via composite capture. NOT UVC** |
| Borescope **BK5600** | SD storage; **NTSC/PAL out**; 640×480 imager, 480×272 viewer | Partly | None found | **Reusable via composite capture** |
| Borescopes BK3000 / BK5500 / BK6500 / BK7000 / BK8500 | — | **No** | — | **UNCONFIRMED** — do not extend the BK8000 findings to these |
| TPMS2 / 3 / 4 / 5 | Standalone; RF to sensors; OBD-II relearn cable; USB→PC updates | Partly | None found | **Proprietary, do not bother** — keeps working standalone; updates die |
| Battery/charging testers (EECS150/350/550A/750A, MicroVAT Elite, D-TAC Elite) | Standalone; EECS550A "wireless" (**type UNCONFIRMED**) | No | None found | **Proprietary, do not bother** — keeps working standalone |
| Scope leads, clips, probes, ground/jumper leads | **Standard 4 mm safety banana** (Snap-on's own words) | **Yes** | n/a — just works | **Reusable** |
| Low Amps Probe EETA308D | Banana out; **100 mV/A @20 A, 10 mV/A @40 A & 60 A**; 10 mA–60 A; 46 VAC pk / 70 VDC limit | **Yes — scale factors published** *(against generic "Low Amp Probe", not the P/N)* | n/a | **Reusable — best carry-over item** |
| COP adapters, secondary coil lead, CIC/flag pickups | Passive pickups on banana-terminated lead | Adequately | n/a | **Reusable** |
| RPM inductive pickup, pressure transducers | **DB9F aux connector**, needs excitation | **No pinout published** | None found | **Reusable only after pinout RE** |
| OBD-I adapters + personality keys | Passive-ish wiring; Snap-on data-cable connector; keys contain circuitry | No | None found | **Reusable only with the Snap-on cable + its pinout** |
| European "software keys" (MT2500Sxx etc.) | Licence cartridges | n/a | n/a | **Proprietary, do not bother** |
| Smart Vehicle Interface Adapter Kits (motorcycle) | Marque-specific DLC wiring; **active components UNCONFIRMED** | No | None found | **Probably reusable as dumb hardware — unconfirmed** |
| MT2600, PRO-LINK+, PRO-LINK Edge, Diagnostic Thermal Imager+ | — | — | — | **Not examined** |

---

## Things that remain unverified — don't let these get repeated as fact

1. **Forum / blog / Hackaday coverage of any of this.** Both research passes had web search unavailable. "Nobody has done it" rests entirely on GitHub *repository* search (which indexes names and descriptions, not code) and sigrok's hardware list. This is the weakest load-bearing claim in the whole report and the first thing anyone with working search should redo.
2. **The Bluetooth profile** used by the scan modules. BR/EDR 2.1 is documented; SPP/RFCOMM is a guess.
3. **FCC filings** for any Snap-on device — not attempted in either pass. Internal photos and test reports would settle radio and chipset questions quickly.
4. **BK7000 specifics** — the current HD flagship. Its manual is not published at any guessable path (404 on `public.snapon.com`). The UVC denial is solid for BK8000, partial for BK5600, and **an extrapolation for everything else in the BK range**.
5. **Who builds the current borescopes.** The Perceptron attribution is documented for the 2011 BK8000 and stale thereafter — Perceptron joined Atlas Copco in 2020 and is now ISRA VISION industrial metrology.
6. **EECS550A / EECS750A radio type and protocol.** Only pre-2015 tester documents reachable.
7. **Who OEMs TPMS5**, and whether it shares a platform with Bartec or ATEQ.
8. **Whether any Snap-on accessory uses signed firmware, link encryption, or an authentication handshake** — the single fact that would flip the §1201 analysis. No evidence either way.
9. **Whether the Snap-on DB9 aux pinout has ever been published.**
10. **Whether the M4+ shares the M4's USB protocol** — the *transport* is now documented; the protocol above it is not, for either module.
11. **EU legal position** (Software Directive 2009/24/EC Art. 6) — named from memory, text not fetched in either pass.

## Sources

- [Snap-on Diagnostics — current products](https://www.snapon.com/EN/US/Diagnostics/Diagnostics-Tools/Current-Products) · [ZEUS+ (EEMS348)](https://www.snapon.com/EN/US/Diagnostics/Products/ZEUS-Plus) · [TRITON (EEMS349)](https://www.snapon.com/EN/US/Diagnostics/Products/TRITON) · [TRITON-D10 (EEMS344)](https://www.snapon.com/EN/US/Diagnostics/Products/TRITON-D10) · [APOLLO GEN 4 (EESC357A)](https://www.snapon.com/EN/US/Diagnostics/Products/APOLLO) · [APOLLO+ (EESC338)](https://www.snapon.com/EN/US/Diagnostics/Products/APOLLOPlus) · [SOLUS+ (EESC337)](https://www.snapon.com/EN/US/Diagnostics/Products/SOLUS-Plus)
- [TRITON online manual — Scan Module spec table](https://www.snapon.com/DiagnosticsManuals/TRITON%20NA/Content/M3000/TRITON.htm) · [TRITON accessories PDF](https://www.snapon.com/Files/Diagnostics-US-2020/Platforms/TRITONM3000/TRITON-Accessories-PDF.pdf) · [ZEUS+ accessories PDF](https://www.snapon.com/Files/Diagnostics-US-2020/Platforms/ZEUS1/ZEUSPlusAccessories.pdf)
- [VERUS Edge user guide](https://www.snapon.com/DiagnosticsManuals/PDF/NA/UG/VERUSEDGE.pdf) · [VERDICT user guide](https://www.snapon.com/DiagnosticsManuals/PDF/NA/UG/VERDICT.pdf) · [VANTAGE Legend](https://www.snapon.com/DiagnosticsManuals/PDF/NA/UG/VANTAGELEGEND.pdf) · [MODIS Edge](https://www.snapon.com/DiagnosticsManuals/PDF/NA/UG/MODISEDGE.pdf) · [APOLLO GEN 4 brochure](https://www.snapon.com/Files/Diagnostics-US-2020/Platforms/APOLLO-S3000/10764-03-APOLLO-GEN-4-Launch_Brochure_r6-1.pdf)
- [Pass Thru Pro IV (EETA113D)](https://www.snapon.com/EN/US/Diagnostics/Products/Pass-Thru-Pro-IV) · [Pass Thru Assistant+ flyer](https://www.snapon.com/Files/Diagnostics-US-2020/Platforms/Pass-Thru-Assistant/PTAFlyer_2026.pdf)
- [BK8000 borescope manual](https://public.snapon.com/UserManuals/ShopTechTools/BK8000_Manual_10-27-11_Multilingual%20low%20res.pdf) · [BK5600 product announcement](https://public.snapon.com/a_prodannouncements_us/BK5600_NPA.pdf) · [ISRA VISION / Perceptron](https://www.isravision.com/en-en/company/about-us/perceptron) ("In 2020, Perceptron joined the Atlas Copco Group")
- [TPMS3 press release](https://web.archive.org/web/2015id_/http://www1.snapon.com/display/231/ToolNews/PressReleases/2011/Snap_on_Must_Have_Tool_April_TPMS3_04_26_11_final.pdf) · product enumeration via the [Wayback CDX API](https://web.archive.org/cdx/search/cdx?url=shop.snapon.com&matchType=domain) (`shop.snapon.com` is a JS shell — it returns byte-identical HTML for nonexistent SKUs, so the live site cannot confirm a part number)
- [Snap-on Diagnostics EULA (NA, 8-13963A03 Rev F)](https://www.snapon.com/Files/EULA/8-13963A03RevFStdNAEULAClean.pdf) · [EULA index](https://eula.snapon.com/diagnostics) · [Snap-on legal information page](https://www.snapon.com/DiagnosticsManuals/NA/Content/Landing%20Sites%20NA/Legal/Legal.htm) · [Snap-on Terms and Conditions](https://www.snapon.com/Terms-and-Conditions)
- [17 U.S.C. §1201 (Cornell LII)](https://www.law.cornell.edu/uscode/text/17/1201) · [37 CFR §201.40 (eCFR — class numbering re-verified against live text)](https://www.ecfr.gov/current/title-37/chapter-II/subchapter-A/part-201/section-201.40) · [2024 §1201 final rule](https://www.federalregister.gov/documents/2024/10/28/2024-24563/exemption-to-prohibition-on-circumvention-of-copyright-protection-systems-for-access-control) · [Copyright Office §1201 proceedings](https://www.copyright.gov/1201/)
- Negative evidence (re-run independently): [sigrok supported hardware](https://sigrok.org/wiki/Supported_hardware) — zero "snap-on"/"snapon" occurrences; GitHub repository search via `api.github.com/search/repositories` — `total_count: 0` for both `snap-on scan module` and `EESM306 OR EESM317 OR EETA113D`. Only Snap-on-adjacent repo: [JamesPreston1993/vehicle-document-parser](https://github.com/JamesPreston1993/vehicle-document-parser) (0 stars). Open J2534 context: [NikolaKozina/j2534](https://github.com/NikolaKozina/j2534) (73 stars, Tactrix Openport 2.0-specific per its README), [rnd-ash/OpenVehicleDiag](https://github.com/rnd-ash/OpenVehicleDiag) (1.0k stars), [rnd-ash/MacchinaM2-J2534-Rust](https://github.com/rnd-ash/MacchinaM2-J2534-Rust) (24 stars, **archived**).
