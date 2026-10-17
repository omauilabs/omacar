# Where the data comes from

OmaCar is MIT. Some of what it can show you is not, and nothing here is bundled
into this repository — it is fetched when you ask for it, cached under your own
state directory, and every entry carries its source and licence with it into
the profile's provenance.

That is a licence decision rather than a technical one. Copying a CC BY-SA
signal set into an MIT tree would put a share-alike obligation on a file
somebody cloned expecting MIT, and quietly relicensing other people's work is
not a thing to do by accident.

## OBDb — community signal sets

<https://github.com/OBDb> · **CC BY-SA 4.0**

One repository per Make-Model, giving headers, requests and the scale and
offset to turn a reply into a number. Around 740 of them exist, contributed by
people who swept their own cars. `omacar mcp`'s `lookup_signals` reads them,
and everything it returns keeps the repository URL and the licence.

If you contribute back what OmaCar discovers on your car — and the whole
strategy in `doc/ROADMAP.md` says you should — it goes to them under their
licence, not this one.

## NHTSA vPIC — VIN decoding

<https://vpic.nhtsa.dot.gov/> · **US Government, public domain**

Free and keyless. It answers the one question OBD-II cannot: what model this
is. The bus reports a VIN, and the make and year are derivable from the
standard, but the model is not — which is why `lib/survey.py` leaves it blank
rather than guessing, and why this exists.

Explicitly not intended for bulk lookups. OmaCar caches a decode for thirty
days and asks about the car in front of you.

## ISO 14229-1 — the standard identifiers

Cited, not copied. The identification identifiers F180 to F199 are named in
`lib/knowledge.py` because they are a fact about the protocol rather than a
copyrightable table, and because they are the cheapest useful thing to ask a
module nobody has mapped. The standard itself is paywalled at around CHF 221;
nothing here reproduces it.

## KESPL — the Valentine One's Bluetooth protocol

<https://github.com/DeveloprOfThings/KESPL> · **MIT**

The exception to "nothing is bundled": `lib/esp.py` carries numbers, not
files. The LE service and characteristic UUIDs, the ESP device and packet
identifiers, the band, arrow and seven-segment bit values, the layouts of the
alert and display-data packets, and the rule for assembling an alert table were
read from KESPL's Kotlin (`Constants.kt`, `ESPPacketId.kt`, `ESPDevice.kt`,
`bluetooth/EspUUID.kt`, `packet/*`, `packet/data/alert/*`,
`packet/data/displayData/*`) and re-expressed as this project's own Python.
They are the protocol's facts, but they are credited, and the MIT notice that
asks for it is reproduced here:

> MIT License
>
> Copyright (c) 2025 Develpr Of Things
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

Where KESPL disagrees with itself (the Ku band's bit) or says nothing (what a
raw signal level means in bars), `lib/esp.py` says so and does not guess; see
`doc/radar.md`.

**tz1/bluev** (<https://github.com/tz1/bluev>, GPL-3) was read to cross-check
the frame format (SOF, destination and originator, the checksum) and nothing
was taken from it. It is listed here so nobody wonders.

Valentine One and V1connection are Valentine Research, Inc.'s names. OmaCar is
not affiliated with or endorsed by them.

## Deliberately not used

**Ross-Tech VCDS label files.** Copyright, all rights reserved, and the `.clb`
form is deliberately encrypted. The PyVCDS reimplementation refuses to include
them and so does this.

**The DDT2000 database.** Not redistributable. DDT4All is GPL-3 and supports
the same adapters OmaCar does, but its value is that database, and the database
is not ours to ship.

**Anything scraped from a paid tool.** The point of this project is that
coverage can be built in the open, by people who own the cars. Taking it from
somebody who paid engineers to build it would be both wrong and beside the
point.

## Bundled with the app

Two things ARE in this repository, both under licences that allow it:

- **Inter** (`share/fonts/InterVariable.ttf`), by Rasmus Andersson — SIL Open
  Font License 1.1, full text in `share/fonts/OFL.txt`. Copied from Arch's
  `inter-font` 4.1 package. The tablet has only Adwaita installed, and the
  mockups are set in Inter.
- **Icon paths** in `share/js/icons.js` marked "Lucide" — ISC License,
  © Lucide Contributors (https://lucide.dev). Paths only; no Lucide code.

Fetched at install, not in this repository: **MediaPipe tasks-vision 1.0.1**
and the **Face Landmarker** model, by Google, under the Apache License 2.0.
`omacar assets fetch` downloads them into `share/js/vendor/mediapipe/` and
checks them against the pins in `share/assets/manifest.json`. Drowsy mode reads
the cabin camera with them, in the page, with nothing fetched on the road.

Protocol knowledge ported (no code copied verbatim) from two MIT-licensed
projects, for the ready-made cabin monitors in `lib/envble.py`:

- **qingping-ble**, © Bluetooth-Devices contributors (itself from Ernst79's
  bleparser): the Qingping Air Monitor Lite advertisement layout.
  https://github.com/Bluetooth-Devices/qingping-ble
- **Aranet4-Python**, © Anrijs Jargans and contributors: the Aranet4
  advertisement layout and scaling. https://github.com/Anrijs/Aranet4-Python
