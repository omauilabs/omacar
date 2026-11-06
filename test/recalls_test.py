#!/usr/bin/env python3
"""Recalls by VIN, against canned NHTSA answers: no network, no car.

What has to hold: the list is ordered with do-not-drive entries first, the
VIN never reaches the recall request (only make, model and year do), the
share report reads the cache and never fetches, and nothing anywhere calls
a model-level recall open on this car."""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import knowledge  # noqa: E402

VIN = "JHMZF1D44FS001835"
fails = 0


def head(t):
    print(f"\n  {t}")


def check(what, got, want):
    global fails
    if got == want:
        print(f"    ok  {what}")
    else:
        fails += 1
        print(f"  FAIL  {what}\n          got  {got!r}\n          want {want!r}")


VPIC_ANSWER = {"Results": [{"Make": "HONDA", "Model": "CR-Z", "ModelYear": "2015"}]}
RECALL_ANSWER = {"Count": 3, "results": [
    {"NHTSACampaignNumber": "16V061000", "ReportReceivedDate": "03/02/2016",
     "Component": "AIR BAGS", "parkIt": "False", "parkOutSide": "False",
     "overTheAirUpdate": "False", "Consequence": "c1", "Remedy": "r1"},
    {"NHTSACampaignNumber": "19V500000", "ReportReceivedDate": "27/06/2019",
     "Component": "AIR BAGS", "parkIt": "False", "parkOutSide": "False",
     "overTheAirUpdate": "False", "Consequence": "c2", "Remedy": "r2"},
    {"NHTSACampaignNumber": "15V001000", "ReportReceivedDate": "05/01/2015",
     "Component": "FUEL", "parkIt": "True", "parkOutSide": "False",
     "overTheAirUpdate": "False", "Consequence": "c3", "Remedy": "r3"},
]}

asked = []


def fake_get(url):
    asked.append(url)
    if "vpic" in url:
        return VPIC_ANSWER
    if "recallsByVehicle" in url:
        return RECALL_ANSWER
    raise AssertionError("unexpected fetch: " + url)


knowledge._get = fake_get

with tempfile.TemporaryDirectory() as tmp:
    knowledge.CACHE = tmp

    head("offline, before anything was looked up")
    entries, meta = knowledge.recalls_for_vin(VIN, offline=True)
    check("says it has not been looked up, rather than 'none issued'",
          (entries, "not been" in meta.get("error", "")), ([], True))
    check("and fetched nothing", asked, [])

    head("looked up")
    entries, meta = knowledge.recalls_for_vin(VIN)
    check("do-not-drive first, then newest first",
          [e["campaign"] for e in entries], ["15V001000", "19V500000", "16V061000"])
    check("dates are ISO", entries[1]["reported"], "2019-06-27")
    check("the park flag is a real boolean", entries[0]["park_it"], True)
    recall_urls = [u for u in asked if "recallsByVehicle" in u]
    check("one recall request, by make, model and year",
          [("make=HONDA" in u and "model=CR-Z" in u and "modelYear=2015" in u)
           for u in recall_urls], [True])
    check("the VIN is not in the recall request",
          any(VIN in u for u in recall_urls), False)
    check("the note says the list is per model, not per car",
          "whether a recall is still open on this car" in meta["note"], True)

    head("cached")
    before = len(asked)
    knowledge.recalls_for_vin(VIN)
    check("a second lookup the same day fetches nothing", len(asked), before)
    entries, _ = knowledge.recalls_for_vin(VIN, offline=True)
    check("offline reads what was stored", len(entries), 3)
    check("offline fetched nothing", len(asked), before)

    head("bad input")
    check("a short VIN is refused before any fetch",
          bool(knowledge.recalls_for_vin("JHMZ")[1].get("error")), True)
    check("a missing year is refused",
          bool(knowledge.recalls("HONDA", "CR-Z", "")[1].get("error")), True)

print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  recalls are cited, ordered and never called open\n")
