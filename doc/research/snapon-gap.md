# What replacing a professional scan tool actually takes

<!-- Researched and independently fact-checked on 2026-09-09. Written by one
pass and then attacked by a second whose only job was to find claims stated
confidently that were wrong, marketing copy repeated as fact, and stale prices
or model names. Its corrections are left inline and marked. Anything it could
not confirm says so rather than being quietly dropped. -->

Not substantially right — the skeleton holds, but the security model is a version behind, several citations don't support the claims attached to them, and four load-bearing numbers are invented or unverifiable. Rewritten below with corrections inline.

---

# Can an open-source Linux tool replace a Snap-on scanner? An honest technical ceiling

**Verification status of this document:** protocol/project claims were re-checked against primary sources this session (GitHub repos, Wikipedia, vendor sites) and are cited. Claims about AutoAuth, NASTF, OEM subscription pricing, and FCA model years **could not be verified** — those domains fail DNS or return 403 from this sandbox, and web search was exhausted. Every one of them is marked `[UNCONFIRMED]` in place. The original draft opened with "I have verified the key facts I need" and then admitted in a footnote that it hadn't; those unverified items are load-bearing to the conclusion, so they are flagged where they are used, not buried at the end.

Short version: the tool has climbed the part of the mountain that is standardized and legally open. Almost everything above it is either proprietary data (huge but buildable) or cryptographically/contractually gated (largely closed). A community project can become an excellent *generic + enthusiast-depth* scanner. It cannot become a Snap-on Zeus/TRITON-class shop tool across the fleet, and the reasons are structural, not effort-related.

(Product names check out: Snap-on currently lists ZEUS, TRITON, APOLLO, VERUS and SOLUS platforms — [snapon.com/diagnostics](https://www.snapon.com/diagnostics). No pricing is published there, so don't quote Snap-on prices you haven't sourced.)

A framing distinction that runs through all five answers:
- **Read-side depth** — enhanced live data, all-module DTCs, freeze frame, Mode 06. Mostly open.
- **Write-side / bidirectional** — clear codes, actuator tests, adaptations, service resets, coding. Gated by SecurityAccess/Authentication + gateways.
- **Programming** — reflash, VIN/immobilizer/key, SCN/online coding. Gated by OEM servers + credentials. Effectively closed.

**[ADDITION — the original missed a fourth thing Snap-on sells.]** A large share of what a shop pays for is not protocol access at all: it's the *repair-information layer* — guided troubleshooting, known-good live-data values, wiring diagrams, TSB integration, and fix-frequency databases (Snap-on's SureTrack being the obvious example). That content is licensed, not reverse-engineerable, and no protocol work closes it. Any honest parity plan has to say out loud that it is not competing for that layer.

---

## 1. The genuinely hard capability gaps, ranked by difficulty to close (hardest first)

**Tier A — effectively closed (do not plan to ship these)**

1. **Module programming / reflashing, VIN writing, immobilizer & key programming, SCN/online coding.** Needs signed OEM firmware images, OEM online calibration/coding servers, and — for security functions — a per-technician credential (NASTF Secure Data Release Model / Vehicle Security Professional in the US). `[UNCONFIRMED: nastf.org returned 403 from this sandbox; SDRM/VSP is asserted from domain knowledge, not verified here.]` `[ADDITION: the EU analogue is the SERMI accreditation scheme under Regulation (EU) 2018/858 — also UNCONFIRMED this session, but if the plan has any EU ambition it needs a SERMI line, and the original had none.]`

   **[CORRECTION to the original's framing.]** The original wrote "do not plan to ship these… it does not close." Too absolute in one direction and misleading in another. Independent reflashing of emissions modules is *legally available* to independents — that is the entire point of the J2534 mandate. What is closed is **your tool doing it**. The accurate statement: your tool can at best be the *pipe* (a J2534-class device driver) while a paid OEM application does the flashing. Don't say "impossible"; say "not ours to own."

2. **Gateway architecture and modern ECU authentication.** **[CORRECTION — the original merged three unrelated mechanisms and got the technical detail wrong.]**

   - **The original's claim that everything hinges on UDS 0x27 SecurityAccess is a version behind.** ISO 14229-1:2020 added **service 0x29 Authentication** as an explicit alternative to SecurityAccess, using PKI certificate exchange, proof-of-ownership and ephemeral key agreement. This is confirmed: it is documented on the [UDS Wikipedia page](https://en.wikipedia.org/wiki/Unified_Diagnostic_Services) (described as "an alternative for SecurityAccess service"), fully implemented in [python-udsoncan](https://udsoncan.readthedocs.io/en/latest/udsoncan/services.html) with certificate-validation parameters, and implemented in [Scapy's automotive contrib](https://github.com/secdev/scapy/blob/master/scapy/contrib/automotive/uds.py) as `UDS_AUTH` with `verifyCertificateUnidirectional` and `requestChallengeForAuthentication`. Where 0x29 is deployed, "reverse-engineer the seed/key algorithm" is not a strategy — there is no algorithm to extract, only an OEM-issued certificate you will not be given. **This is a harder wall than the one the original described, and the plan should name it.**
   - **A gateway is not an authentication service.** FCA/Stellantis SGW is a network-level firewall between the OBD connector and the vehicle buses. It is orthogonal to 0x27/0x29 on the ECU behind it. The original's list "(FCA/Stellantis SGW via AutoAuth, GM Global B, Mercedes DoIP/XENTRY)" as examples of seed/key crypto conflates a firewall, a platform, and a *transport*. **DoIP (ISO 13400) is a transport, not an auth scheme** — Mercedes's gate is XENTRY plus online SCN, not DoIP itself.
   - **[CORRECTION] "Without passing 0x27 you cannot enter the sessions that make actuator tests, adaptations, and clearing certain modules work" is wrong as stated.** The extended diagnostic session (0x10 0x03) is routinely entered with no SecurityAccess at all, and a large number of actuator tests (0x2F IOControl) and service routines (0x31) run in extended session unprotected. SecurityAccess/Authentication is typically required for the *programming* session, writes to protected DIDs, and a subset of routines. This distinction matters commercially: it is precisely the gap between "no bidirectional at all" and "a useful amount of bidirectional on pre-authentication vehicles," which is the whole Tier-1 business case in §5.
   - **[CORRECTION] "post-~2018 vehicles" is too broad.** FCA's SGW is roughly MY2018+ `[UNCONFIRMED — could not verify model-year scope this session]`; it is not an industry-wide 2018 cutover. Plenty of 2019–2024 Toyota, Honda and Hyundai/Kia vehicles still permit OBD reads and meaningful bidirectional work without gateway authentication. Scope this per-make or the roadmap will be wrong in both directions.

**Tier B — open in principle, combinatorially enormous**

3. **Guided functional / bidirectional test coverage across the fleet.** The real product is not "can it send an IOControl" but "does it know that on *this* model/engine, DPF regen is a specific RoutineControl routine in a specific session with these preconditions and this abort logic." **[CORRECTION: the original stated this as "routine 0x0F0C after 0x27 level 3" — that is an invented illustrative value presented in the register of a verified fact. Do not carry a fabricated routine ID into a plan document; the point survives without it.]** ISO 22901 ODX is the interchange format for this data. Buildable by reverse engineering and crowd-sourcing, but the surface is effectively unbounded.

4. **Enhanced DID dictionaries & coding/adaptation maps.** Reading 0x22 is trivial; knowing what a given DID *means* and what write values are safe is the labor. **[CORRECTION: the original used "DID 0xF190" as its example of an opaque OEM-specific identifier. 0xF190 is the standardized VIN Data Identifier in ISO 14229-1's identifier table — one of the few DIDs you never have to reverse-engineer. The example undercuts the point it was making. Its second example, 0x2001, does sit in the vehicle-manufacturer-specific range and is a fine illustration.]** `[The ISO 14229-1 DID table is paywalled and was not fetched this session; the F190=VIN assignment is asserted from the standard.]`

5. **ADAS and component calibration procedures.** Even with the right commands, many need physical targets, alignment rigs, and OEM-specified procedures. Software is necessary but not sufficient. Unchanged — this was right.

**Tier C — moderate, genuinely closeable**

6. **DoIP / Ethernet diagnostics (ISO 13400).** Transport is standardized and implementable; what rides on it is gated by Tiers A/B. **[FLAG: the original's "getting the stack working is a few weeks" is an engineering estimate stated as fact. Present it as an estimate, and note it assumes DoIP discovery/activation only — TLS-secured DoIP variants are a different job.]** Worth noting that [caring caribou](https://github.com/CaringCaribou/caringcaribou) already ships DoIP discovery, so the reconnaissance half is off-the-shelf.

7. **Multi-protocol vehicle detection and per-vehicle CAN/ECU mapping.** Labor-heavy but bounded and crowd-sourceable. Unchanged.

**Tier D — easiest, largely a hardware purchase**

8. **Legacy physical layers**: SAE J1850 PWM (Ford, 41.6 kbit/s) and VPW (GM, 10.4/41.6 kbit/s), ISO 9141-2 (10.4 kbit/s K-line), ISO 14230-4 KWP2000, and GM's J2411 single-wire CAN on pin 1 — all confirmed on the [OBD-II Wikipedia page](https://en.wikipedia.org/wiki/On-board_diagnostics), which also confirms CAN (ISO 15765) has been mandatory on US vehicles since 2008. **[NUANCE the original skipped: "buy an STN/J2534 interface that supports those layers" is not one purchase. Most J2534 boxes do not do GM single-wire CAN — that needs J2534-2 SW-CAN support or GM-specific hardware. Budget for two or three adapters, not one.]**

The inversion the original drew is correct and worth keeping: the gaps a technician notices first (won't talk to an old truck; can't do a specific reset) are the easy ones. The gaps that actually stop you replacing the Snap-on — programming, gateway/authentication, fleet-wide guided coverage, and the repair-information layer — are the invisible ones.

---

## 2. What "manufacturer protocol coverage" actually means

**(a) The transport/service protocols — OPEN.** ISO 14229-1 UDS (0x10 session, 0x14 clear, 0x19 ReadDTCInformation, 0x22/0x2E read/write DID, 0x27 SecurityAccess, **0x29 Authentication**, 0x2F IOControl, 0x31 RoutineControl, 0x34/0x36/0x37 download); ISO 15765-2 ISO-TP; ISO 15765-4 OBD-over-CAN; ISO 14230 KWP2000; ISO 13400 DoIP; SAE J1979 / ISO 15031-5 generic OBD. Nothing here is licensed-secret.

**[ADDITION the original omitted, and it matters for a 2026 plan:** generic OBD itself is migrating to UDS-based OBD — SAE J1979-2 (OBDonUDS) and J1979-3 (ZEVonUDS for electrified vehicles). A tool built only around classic J1979 modes will start aging out on new vehicles. `[UNCONFIRMED this session — asserted from domain knowledge; verify the J1979-2/-3 status before it goes in a roadmap.]`**]**

**(b) The per-ECU content layer — MOSTLY CLOSED as source, REVERSE-ENGINEERABLE piecemeal.** OEMs hold this as ODX (ISO 22901) or proprietary formats. **[CORRECTION: the original wrote "Mercedes CBF, VW ODX-F/CBF." CBF (Caesar/Basis files) is a Mercedes format; attributing CBF to VW appears to be an error — VW's ODIS uses ODX/PDX datasets. Verify before repeating.]**

The legal boundary here is documented, not speculative: [OpenVehicleDiag's README](https://github.com/rnd-ash/OpenVehicleDiag) still carries the line **"SMRParser — REMOVED DUE TO DMCA TAKEDOWN NOTICE."** Confirmed this session. That is the single clearest signal of where the closed boundary bites, and the original was right to lead with it.

Also confirmed, and worth stating precisely: [odxtools](https://github.com/mercedes-benz/odxtools) is genuinely open-sourced by Mercedes-Benz under MIT and actively maintained — **and it ships no OEM databases at all**, only the format tooling plus a fictional `somersault.pdx` example. The container is open; the contents are not. That is the whole story of (b) in one repository.

**(c) Per-make authentication — MIXED, trending CLOSED.** Older seed/key algorithms are known or extractable. Newer ones use proper cryptography, move the secret server-side, or replace 0x27 with certificate-based 0x29 (see §1). The Wikipedia UDS article's wording is "Both the client and server compute a key value based on the seed (using a secret algorithm)" — **[CORRECTION: the original attributed the phrase "a secret algorithm" to ISO 14229 itself. That is Wikipedia's phrasing, not a quotation from the standard. The substance is right; the attribution isn't.]** Plan for: you will pass authentication on a *minority* of modern modules, and on 0x29 vehicles, effectively none.

**(d) Gateway unlocking — CLOSED by design, with a paid legitimate path for some.**
- **FCA/Stellantis SGW.** The gateway blocks write traffic through the OBD port; the legitimate aftermarket route is AutoAuth. `[UNCONFIRMED — autoauthda.com and autoauthda.com fail DNS from this sandbox and search budget is exhausted. **The original's "about $50/yr" figure is removed: it is unverified and should not appear in a plan document as a number.** Model-year scope, OEM participation, and current fee all need first-party confirmation.]`
  **[CORRECTION to the original's conclusion here.]** "Not something an anonymous open-source client can legitimately join" overstates it in one direction and understates the real barrier. An *anonymous* client, no — but AutoAuth has a tool-manufacturer onboarding path, so a project with a legal entity behind it could in principle apply. The barrier is commercial and contractual (registration, agreements, per-tool identity, liability), which is a barrier an open-source project of one person cannot clear — but say *that*, because "impossible" is falsifiable and "no legal entity, no shop registration, no liability cover" is not.
  The SGW bypass cable is real and widely sold, but it removes a security control and is a user hack, not a shippable feature. `[UNCONFIRMED whether bypasses still work on the newest Stellantis platforms.]`
- **GM Global B.** Authenticated diagnostics, locked modules, programming via SPS2 online. **[CORRECTION: the original wrote "GM Global B / Global A (newer trucks, C8, EVs)." Global A is the *older* generation (roughly 2010s); Global B is the newer one. Listing them together as "newer" is wrong.]**
- **Mercedes.** Newer platforms use DoIP with XENTRY and online SCN coding computed on Mercedes servers. Closed — but note the gate is XENTRY + SCN, not DoIP.

**[CORRECTION: the original's "'Coverage' as Snap-on means it is 80% closed data + gated auth" — that 80% is invented. There is no source for it. Say "predominantly," or produce a count.]** The defensible version: a community can build (a) fully, a growing but always-partial slice of (b) and (c) for a chosen short list of makes, and essentially none of (d)'s auth for modern vehicles legitimately.

---

## 3. The J2534 pass-thru angle

**What J2534 is.** SAE J2534 standardizes a pass-thru reprogramming interface. **[CORRECTION — citation failure.]** The original cited the [SAE J2534 Wikipedia page](https://en.wikipedia.org/wiki/SAE_J2534) for "a Windows DLL exposing the PassThru API, registered in the Windows registry," for the J2534-1/-2 split, and for the EPA mandate. **That page is a stub and says none of those things.** All it says is that "SAE International standardized the J-2534 universal requirements in 2004, requiring all manufacturers to allow vehicles sold in the United States of America and Europe to accept powertrain reprogramming." (That sentence is itself loose — SAE is a standards body, not a regulator; the compulsion came from EPA rulemaking referencing the standard.) The DLL/registry detail is correct from the specification, but it needs a real citation, and the original was passing off domain knowledge as sourced.

**Does supporting J2534 give a Linux tool access to OEM software? No — but the original's reason is wrong.**
- The original argued Linux is blocked because "the DLL model is Windows-registry-bound." **[CORRECTION: Linux J2534 implementations demonstrably exist** — [NikolaKozina/j2534](https://github.com/NikolaKozina/j2534) ("J2534 driver for linux"), [rnd-ash/MacchinaM2-J2534-Rust](https://github.com/rnd-ash/MacchinaM2-J2534-Rust) (cross-platform, now archived), [thalesac/godiag-linux](https://github.com/thalesac/godiag-linux) for the GODIAG GD101, and [aldoguzman97/sm2can](https://github.com/aldoguzman97/sm2can) for Scanmatik SM2 Pro on Linux/macOS. All found on GitHub this session.**]** The actual blocker is simpler and unarguable: **Ford FDRS, GM GDS2/SPS2, wiTECH, XENTRY, ODIS and Techstream are Windows applications.** You can't host a Windows binary on Linux without Windows. The conclusion stands; fix the reasoning, because the wrong reason invites a technically-literate reader to dismiss the right conclusion.
- **J2534 is a pipe, not a content grant.** Even on Windows you need the per-OEM subscription to download calibrations and run the app. **[CORRECTION: the original's "typically tens to a few hundred dollars per OEM per interval" is unverified and probably understates annual terms — several OEM annual subscriptions run into four figures. `[UNCONFIRMED — could not check any OEM's published pricing this session.]` Either price two or three OEMs from their own service-information sites before this goes in a plan, or write "short-term passes are cheap, annual terms vary by an order of magnitude across OEMs."]**
- The device must also be on the OEM's validated-interface list for programming. `[UNCONFIRMED this session; consistent with domain knowledge.]`

**The Linux story.** [SocketCAN](https://github.com/hartkopp/can-isotp) is the natural interface — and the ISO-TP module is confirmed **in the mainline Linux kernel since 5.10**, per Hartkopp's own README. [comma.ai panda](https://github.com/commaai/panda) is confirmed MIT-licensed, CAN and CAN FD, STM32H725, Python userspace library, Linux udev rules, actively developed. **[ADDITION the original omitted and that will bite an implementer: panda's release firmware enforces openpilot's safety models and deliberately restricts what can be transmitted — sending arbitrary diagnostic frames requires custom-compiled firmware. Budget for that, and for the fact that it is a supported-but-unsupported path.]**

Practical stance (unchanged, and correct): support J2534 as a hardware backend, but do **not** market it as "unlocks OEM software."

---

## 4. Open-source projects already in this space, and where they stopped

**Diagnostic platforms:**
- **[OpenVehicleDiag](https://github.com/rnd-ash/OpenVehicleDiag)** (rnd-ash) — Rust GUI platform; J2534-2 and SocketCAN, UDS/KWP2000, Macchina M2 hardware. Confirmed: last release **v1.0.5, 15 May 2021**, 274 commits, 20 open issues, no activity since; SMRParser removed under DMCA. `[The "university final-year project" characterization and the surviving CBFParser component are from the original and were not re-verified; the README's DMCA line and release date were.]`
- **[ecu_diagnostics](https://github.com/rnd-ash/ecu_diagnostics)** (rnd-ash) — the Rust crate underneath. Confirmed: UDS, KWP2000, OBD-II; hardware backends **Passthru (SAE J2534), SocketCAN, SLCAN, PCAN-USB**, with **D-PDU (ISO 22900-2) explicitly listed as "TBA"** (i.e. not implemented — the original said "TBA" too but a reader could take it as shipped). GPL-3.0, 284 commits. `[The original's "VW-TP2 transport" is not visible on the repo page — unconfirmed.]`
- **[DDT4All](https://github.com/cedricp/ddt4all)** — **[ADDITION: this is the single most relevant precedent and the original omitted it entirely.]** GPL-3.0-or-later, actively maintained (v3.1.3, 1,425 commits, CI across multiple OSes), targets Renault/Dacia (Clio, Mégane, Laguna, Zoe plugins), and — critically — **ships a converted ECU database (`ecu.zip`, XML+ZIP)** plus a custom parameter-screen designer, auto ECU scan, DTC read/clear and manual requests. It is the working existence proof of the Tier-1 strategy in §5: one make, dealer-adjacent depth, community-maintained, still alive years later, having taken the same OEM-database route that got OpenVehicleDiag a takedown. Any parity plan should study why one survived and the other didn't. Note its own warning: "serious damage is possible on a live vehicle."

**Protocol/transport libraries (mature, this is the open foundation):**
- **[python-udsoncan](https://github.com/pylessard/python-udsoncan)** — confirmed MIT, ISO 14229, Python 3.7+, 727 stars, and it implements the 0x29 Authentication service including certificate/proof-of-ownership/ephemeral-key parameters. No OEM data or seed/key algorithms bundled — the seed→key transformation is left to you.
- **[python-can](https://github.com/hardbyte/python-can)**, **[cantools](https://github.com/cantools/cantools)** — as described in the original, not re-verified this session.
- **[python-OBD](https://github.com/brendan-w/python-OBD)** — generic OBD-II over ELM327 only, 635 commits. `[Maintenance status could not be determined from the repo page; the original called these libraries "essentially done," which for this one may be indistinguishable from unmaintained. Check before depending on it.]`
- **can-isotp** — in mainline since Linux 5.10 (confirmed above).

**Reverse-engineering & security tooling:**
- **[caring caribou](https://github.com/CaringCaribou/caringcaribou)** — GPL-3.0, 945 stars. Confirmed modules: UDS discovery, **DoIP discovery**, XCP discovery, dump/listener/send, CAN fuzzer (random/brute-force/mutation), UDS fuzzing, memory read, ECU reset, DID dumping, and **automated security-seed collection with seed-randomness evaluation**. **[NUANCE: it collects and analyses seeds; it does not brute-force key algorithms. The original's "seed/key probing" reads as more capable than it is.]**
- **SavvyCAN / GVRET** (collin80) and **Scapy's automotive layer** — Scapy's UDS/DoIP implementation was confirmed in passing above; SavvyCAN was not re-verified.

**Data and signals:**
- **[opendbc](https://github.com/commaai/opendbc)** — **[CORRECTION: the original's description is out of date.]** It is no longer just DBC files; it is now "a Python API for your car" (DBC repo + CAN library + car control library), MIT-licensed, covering gas/brake/steering and things like EV charge status and door lock/unlock. What matters for this plan is unchanged and now explicit in its own docs: **diagnostics and DTCs are out of scope.** It is an ADAS-interface project, not a diagnostic database.

**[ADDITION: one closed-source counterexample worth naming.]** FORScan (Ford/Mazda) is freeware, not open source, and reaches near-dealer depth on one manufacturer family. Together with DDT4All it establishes the realistic shape of success: *one make, deep, one or two maintainers, years of work* — not breadth. `[FORScan not verified this session.]`

**[CORRECTION to the original's summary line.]** "No community has ever gotten past a handful of makes in real depth" — the *breadth* claim is right and is the important one. But "a handful of makes in real depth" is exactly what DDT4All and FORScan achieved, and the original's framing ("stayed a personal/enthusiast effort… or got a takedown") writes off the one outcome the plan should actually be aiming at.

---

## 5. What's realistically achievable in one year

**Tier 0 — Solidify what exists (months 0–3). Achievable, high confidence.**
Harden generic OBD-II (J1979/ISO 15031, all modes including Mode 06), UDS 0x19 DTC catalogs and 0x22 reads across sessions, passive CAN, safety-gated clear/functional tests. Add SocketCAN + ELM327/STN + one J2534 backend + panda (with the custom-firmware caveat above). Ship VIN decode → protocol/bus auto-detection. Add a J1979-2/OBDonUDS path if that standard checks out. Outcome: a genuinely good generic multi-brand reader on Linux.

**Tier 1 — Enthusiast depth on 2–4 chosen makes (months 3–9). Achievable if scoped narrowly.**
Pick makes with the friendliest security and best community data. **[CORRECTION to the original's shortlist: it named "VAG, some BMW, some pre-SGW FCA, older Toyota" and omitted Renault — the one make where an actively maintained, GPL-licensed, database-carrying open-source tool already exists to build on or learn from. Rank candidates by whether a community dataset already exists, not by folklore about which makes are "friendly."]** Build ODX-style community databases for enhanced live data, all-module DTCs, and read-only adaptation/coding display. Add the common bidirectional/service functions that run in extended session with no SecurityAccess (more of them than the original implied — see §1.2): DPF regen, EPB service, oil/service reset, throttle relearn, ABS bleed where ungated.

**Tier 2 — Selective bidirectional + coding-write on a short list (months 9–12). Partially achievable, fragile.**
Where a specific make's 0x27 for specific modules is known, enable writes behind hard safety gates and explicit at-your-own-risk labeling. Expect a per-module, per-model-year patchwork. **[ADDITION: on any ECU that has moved to 0x29 certificate authentication, this tier is not fragile — it is absent. Segment the target list by 0x27 vs 0x29 before committing to the quarter.]** Legal exposure rises here; the OpenVehicleDiag takedown is the precedent.

**What will NOT happen in a year:**
- **Module flashing/reprogramming, SCN/online coding, VIN & immobilizer/key programming.** No — as *your tool's* function. (It can be the pipe; see §1.1.)
- **Legitimate FCA SGW / GM Global B / Mercedes gateway auth.** No, on commercial and contractual grounds rather than pure impossibility.
- **Fleet-wide guided diagnostics.** No.
- **ADAS calibration.** No.
- **Running OEM Windows diagnostic apps from Linux.** No — because they are Windows binaries, not because J2534 can't exist on Linux.
- **[ADDITION] The repair-information layer** (guided troubleshooting, known-good values, wiring diagrams, fix databases). No, and it's licensed content, so no amount of protocol work touches it.

**The honest ceiling:** in one year you can build the best open-source generic + selectively-deep, read-strong, cautiously-bidirectional Linux scan tool that exists — a real alternative to consumer/prosumer tools and a useful second tool for an independent tech on the makes you cover. **[CORRECTION: the original's "$100–$400 consumer/prosumer tools" price band and its "Snap-on is 90% closed data" and "the standardized 80% / gated 20%" figures are all unsourced inventions. Drop the percentages or replace them with a real count of gated vs. open functions on a sample of vehicles — that's a measurement you can actually make, and it would be the most persuasive artifact in the whole plan.]** What you cannot build is a Snap-on replacement, because Snap-on's value is predominantly the closed coverage catalog, the gateway/authentication access, the programming/credential infrastructure, and the repair-information library — gated by cryptography, OEM servers, contracts and law, not by how good your code is.

---

### Sources

**Verified this session:**
- OpenVehicleDiag — "SMRParser: REMOVED DUE TO DMCA TAKEDOWN NOTICE"; v1.0.5, 15 May 2021: https://github.com/rnd-ash/OpenVehicleDiag
- ecu_diagnostics — UDS/KWP2000/OBD; Passthru/SocketCAN/SLCAN/PCAN-USB; D-PDU listed TBA; GPL-3.0: https://github.com/rnd-ash/ecu_diagnostics
- UDS — service 0x29 Authentication as alternative to SecurityAccess; "secret algorithm" is Wikipedia's phrasing: https://en.wikipedia.org/wiki/Unified_Diagnostic_Services
- python-udsoncan — MIT, ISO 14229, implements 0x29 with certificate parameters: https://github.com/pylessard/python-udsoncan · https://udsoncan.readthedocs.io/en/latest/udsoncan/services.html
- Scapy automotive UDS — `UDS_AUTH` (0x29) with certificate/challenge handling: https://github.com/secdev/scapy/blob/master/scapy/contrib/automotive/uds.py
- OBD-II physical layers, Mode 06, J2411 SW-CAN on pin 1, CAN mandatory in US since 2008: https://en.wikipedia.org/wiki/On-board_diagnostics
- comma.ai panda — MIT, CAN FD, STM32H725, Python lib, Linux udev, release-firmware safety restrictions: https://github.com/commaai/panda
- opendbc — now a car-control library, MIT, ADAS-scoped, diagnostics/DTCs out of scope: https://github.com/commaai/opendbc
- odxtools — Mercedes-Benz, MIT, active, tooling only, no OEM databases: https://github.com/mercedes-benz/odxtools
- DDT4All — GPL-3.0-or-later, active (v3.1.3), Renault plugins, ships converted ECU database: https://github.com/cedricp/ddt4all
- caring caribou — GPL-3.0, UDS/DoIP/XCP discovery, fuzzing, seed collection and randomness evaluation: https://github.com/CaringCaribou/caringcaribou
- can-isotp — "part of the mainline Linux kernel since version 5.10": https://github.com/hartkopp/can-isotp
- Linux J2534 implementations exist: https://github.com/NikolaKozina/j2534 · https://github.com/rnd-ash/MacchinaM2-J2534-Rust · https://github.com/thalesac/godiag-linux · https://github.com/aldoguzman97/sm2can
- Snap-on platform names (ZEUS, TRITON, APOLLO, VERUS, SOLUS); no public pricing: https://www.snapon.com/diagnostics
- Automotive right-to-repair: MA law 2012, 2014 MOU applying from MY2018, REPAIR Act not enacted: https://en.wikipedia.org/wiki/Right_to_repair

**Cited by the original but does NOT support the claims attached to it:**
- SAE J2534 Wikipedia — a stub; contains nothing about Windows DLLs, the registry, J2534-1 vs -2, or EPA rulemaking: https://en.wikipedia.org/wiki/SAE_J2534

**Could not be verified this session (DNS failure / 403 / search budget exhausted) — do not quote without checking:**
- AutoAuth: pricing (the original's "~$50/yr" is removed), OEM/model-year scope, tool-manufacturer onboarding — https://www.autoauthda.com
- NASTF SDRM / Vehicle Security Professional — https://nastf.org (403)
- EU SERMI scheme and Regulation (EU) 2018/858 security-information access
- FCA/Stellantis SGW model-year scope and current bypass viability
- OEM J2534 subscription pricing for any manufacturer
- SAE J1979-2 (OBDonUDS) / J1979-3 (ZEVonUDS) status
- Whether VW's ODIS uses CBF (the original claimed it; likely a Mercedes-only format)
- SavvyCAN/GVRET, python-can, cantools, FORScan
