"""Home's layout: which cards, in what order, at what size, per orientation.

The cards themselves are share/data/home-cards.json, which the browser reads
too, so the two can never disagree about what a card is or how big it can be.
This file only stores the owner's arrangement, validated, the way the drive
layout is: a hand-edited file must not be able to put an unknown card or an
impossible size on a screen used at speed.

Stored in $XDG_CONFIG_HOME/omarchy/omacar-home.json, next to omacar-drive.json:
    {"landscape": {"cards": [["dial", "m"], ...], "hidden": ["nav"]},
     "portrait":  {...}}

`hidden` is how a DEFAULT card stays removed: a default card that is neither
placed nor hidden came into the catalogue after the file was saved, and is
appended rather than silently missing. Cards that are not in the default are
simply offered by the editor's Add sheet.
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGUE = os.path.join(ROOT, "share", "data", "home-cards.json")
HOME_CFG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")), "omarchy", "omacar-home.json")
ORIENTS = ("landscape", "portrait")
SIGNAL_CARDS = ("coolant", "volts", "fuel", "charge", "intake", "econ_now")


def catalogue(path=CATALOGUE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _clean(entry, cat, default, append_missing):
    cards = cat["cards"]
    placed = entry.get("cards") if isinstance(entry, dict) else None
    hidden = entry.get("hidden") if isinstance(entry, dict) else None
    seen, out = set(), []
    for item in placed if isinstance(placed, list) else default:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            continue
        cid, size = item
        if not isinstance(cid, str) or cid not in cards or cid in seen:
            continue
        sizes = list(cards[cid]["sizes"])
        out.append([cid, size if size in sizes else sizes[0]])
        seen.add(cid)
    gone = [c for c in (hidden if isinstance(hidden, list) else [])
            if isinstance(c, str) and c in cards and c not in seen]
    defaults = [c for c, _ in default]
    if append_missing:
        for cid, size in default:
            if cid not in seen and cid not in gone:
                out.append([cid, size])
                seen.add(cid)
    else:
        # Saving: a default card the owner left out has been removed.
        gone += [c for c in defaults if c not in seen and c not in gone]
    return {"cards": out, "hidden": sorted(set(gone))}


def _default(cat, migrate):
    base = {o: [list(x) for x in cat["default"][o]] for o in ORIENTS}
    if not migrate:
        return base
    # THE DRIVE SCREEN'S TILE CHOICE CARRIES OVER ONCE: only while Home has
    # never been saved, and only if the drive layout was ever saved at all.
    try:
        import api
        if not os.path.exists(api.DRIVE_CFG):
            return base
        tiles = [t for t in api.drive_layout().get("tiles", [])
                 if t in SIGNAL_CARDS and t in cat["cards"]]
    except Exception:                                         # noqa: BLE001
        return base
    if not tiles:
        return base
    for o in ORIENTS:
        slots = [i for i, (cid, _s) in enumerate(base[o]) if cid in SIGNAL_CARDS]
        rest = [base[o][i][0] for i in slots if base[o][i][0] not in tiles]
        for i, cid in zip(slots, (tiles + rest)[:len(slots)]):
            base[o][i] = [cid, base[o][i][1]]
    return base


def home_layout():
    cat = catalogue()
    try:
        with open(HOME_CFG, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = None
    default = _default(cat, migrate=doc is None)
    doc = doc if isinstance(doc, dict) else {}
    return {o: _clean(doc.get(o), cat, default[o], append_missing=True) for o in ORIENTS}


def save_home_layout(data):
    if not isinstance(data, dict):
        raise ValueError("a Home layout is an object with landscape and portrait")
    if data.get("action") == "reset":
        try:
            os.remove(HOME_CFG)
        except FileNotFoundError:
            pass
        return home_layout()
    cat = catalogue()
    cur = home_layout()
    doc = {o: _clean(data.get(o, cur[o]), cat, [list(x) for x in cat["default"][o]],
                     append_missing=False) for o in ORIENTS}
    doc["_comment"] = ("Home's arrangement. Edit in the app: Home → Customize layout. "
                       "Card ids and sizes are in share/data/home-cards.json.")
    os.makedirs(os.path.dirname(HOME_CFG), exist_ok=True)
    tmp = HOME_CFG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, HOME_CFG)
    return home_layout()
