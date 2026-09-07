"""What other people have already worked out about a car.

The moat this project is trying to cross is coverage: thousands of
engineer-years spent learning which identifier means what on which model. The
strategy in doc/ROADMAP.md is that a community can cross it, because a sweep
costs seventy minutes per car but only once per model if the result is shared.

Some of that sharing has already happened, in public, under licences that allow
it. This module goes and reads it.

THREE SOURCES, AND WHY THESE THREE.

  OBDb          One repository per Make-Model, a signal set per year range,
                CC BY-SA 4.0. Its schema maps almost exactly onto a request
                this tool can send: a header, a service and payload, and a
                length/scale/offset for turning the reply into a number.
                740 repositories exist. This is the real prize.

  NHTSA vPIC    A free, keyless, US-government VIN decoder. It answers the one
                question OBD-II genuinely cannot: what model is this. The bus
                reports a VIN and nothing else, and the make and year are
                derivable from the standard, but the MODEL is not -- which is
                why lib/survey.py leaves it for a person to fill in rather than
                guessing. This fills it in with a citation instead.

  ISO 14229     The standard identification identifiers, F180 to F19E. Not
                fetched: they are a fact about the protocol rather than about a
                car, so they are written down here, and they are the first
                thing worth asking an unknown module.

NOTHING IS BUNDLED, AND THAT IS A LICENCE DECISION.

OBDb is CC BY-SA 4.0. This repository is MIT. Copying signal sets into the tree
would put a share-alike obligation on a file somebody cloned expecting MIT, and
quietly relicensing other people's work is not a thing to do by accident. So
they are fetched at use, cached under the user's own state directory, and every
entry that comes back carries its source URL and licence into the profile's
provenance -- which the profile format has fields for precisely so that where a
claim came from survives being merged.

A LOOKUP IS A LEAD, NOT A FINDING.

Everything here answers "somebody says this identifier means X on this model".
That is worth a great deal and it is not evidence about the car in front of
you: model years differ, markets differ, and a signal set can be wrong. So a
result lands at confidence `proposed`, below `candidate`, and the way it climbs
is car_request answering.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import records                                                # noqa: E402

CACHE = os.path.join(records.STATE, "knowledge")
TIMEOUT = 12
AGENT = "omacar/1 (+https://github.com/omauilabs/omacar)"

OBDB_RAW = "https://raw.githubusercontent.com/OBDb/{slug}/main/signalsets/v3/default.json"
OBDB_REPO = "https://github.com/OBDb/{slug}"
OBDB_LICENCE = "CC BY-SA 4.0"

VPIC = ("https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/"
        "{vin}?format=json")
VPIC_LICENCE = "US public domain (NHTSA vPIC)"

# ISO 14229-1 identification identifiers. A fact about the protocol, so it is
# written down rather than fetched, and it is the cheapest useful thing to ask
# a module nobody has mapped: about a dozen requests, all reads, and the
# answers tell you what the module calls itself.
ISO_IDENT_DIDS = {
    "F180": "boot software identification",
    "F181": "application software identification",
    "F182": "application data identification",
    "F183": "boot software fingerprint",
    "F184": "application software fingerprint",
    "F186": "active diagnostic session",
    "F187": "vehicle manufacturer spare part number",
    "F188": "vehicle manufacturer ECU software number",
    "F189": "vehicle manufacturer ECU software version",
    "F18A": "system supplier identifier",
    "F18B": "ECU manufacturing date",
    "F18C": "ECU serial number",
    "F190": "VIN",
    "F191": "vehicle manufacturer ECU hardware number",
    "F192": "system supplier ECU hardware number",
    "F193": "system supplier ECU hardware version",
    "F194": "system supplier ECU software number",
    "F195": "system supplier ECU software version",
    "F197": "system name or engine type",
    "F199": "programming date",
}


def _cached(key, fetch, max_age=86400):
    """Fetch once a day. A signal set does not change hourly and a lookup that
    hits the network on every call is one somebody turns off."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, key + ".json")
    try:
        if time.time() - os.path.getmtime(path) < max_age:
            with open(path, encoding="utf-8") as f:
                return json.load(f), True
    except (OSError, ValueError):
        pass
    doc = fetch()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, path)
    return doc, False


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def slug_for(make, model):
    """OBDb names a repository Make-Model, each part title-cased with spaces
    turned into hyphens: 'Honda CR-Z' is Honda-CR-Z."""
    def part(x):
        return "-".join(w for w in str(x or "").replace("-", " ").split())
    return f"{part(make)}-{part(model)}"


def signals(make, model):
    """Everything OBDb publishes for this model, flattened to one request per
    entry so a caller can hand them straight to Elm.request().

    Returns (entries, meta). An empty list is a real answer and a common one:
    740 repositories exist and most models have not been mapped by anybody.
    """
    slug = slug_for(make, model)
    url = OBDB_RAW.format(slug=slug)
    try:
        doc, hit = _cached("obdb-" + slug, lambda: _get(url))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return [], {"source": OBDB_REPO.format(slug=slug),
                        "found": False,
                        "note": f"No OBDb repository for {slug}. That is not a "
                                f"failure -- most models have not been mapped "
                                f"by anybody yet, which is the gap this "
                                f"project exists to close."}
        raise
    out = []
    for cmd in doc.get("commands") or []:
        hdr = cmd.get("hdr")
        for service, payload in (cmd.get("cmd") or {}).items():
            request = f"{service}{payload}".upper()
            for sig in cmd.get("signals") or []:
                fmt = sig.get("fmt") or {}
                out.append({
                    "header": hdr,
                    "request": request,
                    "name": sig.get("name") or sig.get("id"),
                    "id": sig.get("id"),
                    "unit": fmt.get("unit") or "",
                    "bits": fmt.get("len"),
                    "scale": fmt.get("mul"),
                    "offset": fmt.get("add") or fmt.get("min"),
                    "max": fmt.get("max"),
                    "source_url": OBDB_REPO.format(slug=slug),
                    "licence": OBDB_LICENCE,
                })
    return out, {"source": OBDB_REPO.format(slug=slug), "found": True,
                 "licence": OBDB_LICENCE, "cached": hit,
                 "note": "Community data. Somebody says these apply to this "
                         "model; nothing here has been checked against your "
                         "car. Send one with car_request and see."}


def decode_vin(vin):
    """What NHTSA says this VIN is.

    OBD-II reports a VIN and nothing else. The make and the year are derivable
    from the standard -- lib/survey.py does it -- but the MODEL genuinely is
    not, which is why survey leaves it blank rather than guessing. This is the
    free, keyless, government answer.
    """
    vin = (vin or "").strip().upper()
    if len(vin) != 17:
        return {}, {"error": f"a VIN is 17 characters; got {len(vin)}"}
    doc, hit = _cached("vpic-" + vin, lambda: _get(VPIC.format(vin=vin)),
                       max_age=86400 * 30)
    rows = doc.get("Results") or []
    r = rows[0] if rows else {}
    keep = ("Make", "Model", "ModelYear", "Series", "Trim", "BodyClass",
            "EngineCylinders", "DisplacementL", "FuelTypePrimary",
            "ElectrificationLevel", "DriveType", "TransmissionStyle",
            "PlantCountry", "ErrorText")
    out = {k: r[k] for k in keep if r.get(k) not in (None, "", "Not Applicable")}
    return out, {"source": "https://vpic.nhtsa.dot.gov/", "cached": hit,
                 "licence": VPIC_LICENCE}


def ident_dids():
    """The standard identification identifiers, as requests.

    The cheapest useful thing to ask a module nobody has mapped: about a dozen
    reads, and the answers tell you what the module calls itself.
    """
    return [{"request": f"22{did}", "name": what,
             "source_url": "ISO 14229-1", "licence": "standard, cited not copied"}
            for did, what in sorted(ISO_IDENT_DIDS.items())]


def main(argv):
    if not argv:
        print("  usage: knowledge.py signals <make> <model> | vin <VIN> | dids")
        return 1
    if argv[0] == "signals" and len(argv) >= 3:
        entries, meta = signals(argv[1], " ".join(argv[2:]))
        print(f"\n  {meta['source']}")
        print(f"  {len(entries)} signal(s)"
              + (f" · {meta.get('licence')}" if entries else ""))
        print(f"  {meta.get('note','')}\n")
        for e in entries[:40]:
            print(f"    {e['header']:9} {e['request']:10} {e['name']} "
                  f"{('(' + e['unit'] + ')') if e['unit'] else ''}")
        if len(entries) > 40:
            print(f"    … {len(entries) - 40} more")
        print()
        return 0
    if argv[0] == "vin" and len(argv) >= 2:
        out, meta = decode_vin(argv[1])
        if meta.get("error"):
            print("  " + meta["error"])
            return 1
        print(f"\n  {meta['source']}  ({meta['licence']})\n")
        for k, v in out.items():
            print(f"    {k:22} {v}")
        print()
        return 0
    if argv[0] == "dids":
        for d in ident_dids():
            print(f"    {d['request']:8} {d['name']}")
        return 0
    print("  usage: knowledge.py signals <make> <model> | vin <VIN> | dids")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
