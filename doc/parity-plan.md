# Replacing a Snap-on tablet: the plan

[doc/parity.md](parity.md) compares the two as they stand. This is the plan for
closing the distance, and the part of it that will not close.

Everything here rests on three research notes that were written once and then
attacked by a second pass looking for confident-but-wrong claims. It found
plenty. Read them before quoting figures at anybody:
[the tablets](research/snapon-tablets.md),
[the accessories](research/snapon-accessories.md),
[what parity actually takes](research/snapon-gap.md).

## What you are actually competing with

A ZEUS+ is **$11,792** and its software is **$1,235 a year**. A TRITON is
$5,995 at $1,128 a year. The entry MT2600 is $1,895. Those are Snap-on's own
published list prices, and the software figures come from the franchisee pocket
guide rather than from marketing.

Two things about that subscription are worth saying plainly to anybody who asks
why this project exists.

**Falling behind costs more than staying current.** Catching up from one
release behind is $1,190 on a ZEUS. From two or more behind it is **$1,581** —
more than a year's subscription, to buy back the same position.

**The tool does not stop working, but it stops learning.** A lapsed
subscription keeps the vehicles it already knew and gains nothing after. On a
trade where the work is whatever came in this morning, that is a slow decline
rather than a cliff, which is a more effective form of lock-in than a cliff.

## What of theirs you can keep

This is the question the community actually asks, and the honest answers split
four ways.

| | |
|---|---|
| **Probes, leads, back-probe kits, breakout boxes, adapters** | **Keep them.** Passive hardware, genuinely just connectors and wire. This is where the money you already spent survives. |
| **Borescopes and inspection cameras** | **Keep them, as dumb hardware.** Reusable through the composite video output, not over USB. |
| **The wireless vehicle interface** | **Do not bother.** Bluetooth underneath, undocumented Snap-on framing above it, and no public reverse-engineering to build on. A thirty-dollar ELM327 or a J2534 adapter gets you further in an afternoon. |
| **Lab scope modules, TPMS tools, battery testers** | **Do not bother.** Proprietary. Note the TPMS and battery tools are largely self-sufficient anyway — they do not need the tablet to be useful. |

One exception worth knowing: the **Pass Thru Pro IV** is a J2534 device, and
J2534 is a published standard, so it is reusable in principle. The **Pass Thru
Assistant is not** — despite the similar name it is a remote programming
service on a locked appliance, not a box to carry over. The research note has
the two product descriptions side by side.

## What will not close, and saying so is the point

**Module reflashing, VIN writing, immobiliser and key programming.** These need
signed OEM firmware, OEM calibration servers, and a per-technician credential —
NASTF's Secure Data Release Model in the US, SERMI in the EU. The accurate
statement is not "impossible": independent reflashing is legally available, and
that is what the J2534 mandate exists for. It is that **our tool can at best be
the pipe** while a paid OEM application does the work. Not ours to own.

**Gateway authentication on modern vehicles.** ISO 14229-1:2020 added service
0x29 Authentication, a certificate exchange, alongside the older SecurityAccess.
That is a wall, not a puzzle.

**The known-fix database.** Snap-on's is built from millions of real repairs
across its own installed base. No amount of protocol work substitutes for it.
The only honest route is a community that contributes its own, which is a
social problem rather than a technical one, and slower.

A tool that says these four things plainly is more trustworthy than one that
implies it will get there. That is the whole marketing position.

## What can close, in tiers

The research is blunt about the ceiling, and these are its words made concrete
against what OmaCar already has.

**Tier 0 — finish being an excellent generic tool.** Months nought to three,
high confidence. Generic OBD-II across all modes including Mode 06, UDS 0x19
catalogues and 0x22 reads, passive CAN, safety-gated clear and functional
tests. Most of that exists. What is missing is transport breadth: SocketCAN, a
J2534 backend, and VIN-driven protocol detection.

**Tier 1 — enthusiast depth on two to four makes.** Months three to nine,
achievable if scoped narrowly. Community databases for enhanced live data,
all-module fault reads, and read-only adaptation display. Plus the service
functions that run in an extended session with **no** SecurityAccess, of which
there are more than folklore suggests: particulate filter regeneration, brake
service mode, oil and service resets, throttle relearn, and ABS bleed where it
is ungated.

The research corrects one instinct worth correcting: choose makes by whether a
community dataset already exists, not by which are rumoured to be friendly. It
names Renault specifically, where an actively maintained open-source tool with
its own database already exists to learn from.

**Tier 2 — selective bidirectional control and coding.** Beyond a year, and
only where the security model permits.

## What this means for OmaCar next

In order, and each is a thing that can be started this month:

1. **A J2534 backend.** It is the one transport that turns a Linux tool into
   something a professional can use with hardware they already own, and it is a
   published standard rather than a reverse-engineering project.
2. **SocketCAN.** Cheap, native on Linux, and the natural partner to the
   passive listening that is already built.
3. **VIN-driven protocol detection**, so the tool stops asking the operator
   what it can work out.
4. **Pick two makes and build their databases properly**, chosen on where a
   community dataset already exists.
5. **Publish the accessory table above** as the community-facing piece. It is
   immediately useful to somebody holding a bag of Snap-on leads and wondering
   what survives, and it costs nothing to give away.

## The honest summary

OmaCar will not replace a ZEUS for a shop doing warranty reflashes. It can
replace one for an independent who wants deep, honest reads on a handful of
makes, owns their own data, and is tired of paying $1,581 to catch up after
falling two releases behind.

That is a real market and a defensible position, and it is only defensible
while the tool keeps saying which of the two it is.
