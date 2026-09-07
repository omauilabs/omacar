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
