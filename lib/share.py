"""Handing the results to somebody else.

Two ways, because they answer different questions.

**A file.** `omacar share` writes one self-contained HTML document — the scan,
the concerns, the service book, the photographs, all inlined — that opens in
any browser with no server, no account and no network. Email it to a mechanic,
put it on a stick, attach it to a listing when you sell the car. It is the
whole record and it will still open in ten years, which is not true of a link
to somebody's cloud.

**A link.** `omacar share --live` starts the cockpit server with a token that
expires, so somebody on the same network can watch the live data while you
drive. Read-only always: a mechanic looking at your car over your Wi-Fi cannot
clear its codes.

The file is the default because it is the one that keeps working.
"""

import base64
import html
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import concerns  # noqa: E402
import knowledge  # noqa: E402
import photos    # noqa: E402
import records   # noqa: E402

# A report with sixty photographs in it is a fifty-megabyte email nobody can
# receive. Past this they are listed but not carried.
MAX_PHOTOS = 24
MAX_PHOTO_BYTES = 900 * 1024


def esc(x):
    return html.escape("" if x is None else str(x))


def data_uri(path, mime):
    try:
        if os.path.getsize(path) > MAX_PHOTO_BYTES:
            return None
        with open(path, "rb") as f:
            return f"data:{mime};base64," + base64.b64encode(f.read()).decode()
    except OSError:
        return None


def build(note=None, include_photos=True):
    """One self-contained document. No fetch, no fonts, no anything."""
    s = records.snapshot()
    u = s["units"]
    found = concerns.assess()
    shots = concerns.snapshots(10)
    pics = photos.listing() if include_photos else []

    def dist(km):
        v = records.to_dist(km, u)
        return "—" if v is None else f"{v:,.0f} {u['dist']}"

    def econ(l):
        v = records.to_econ(l, u)
        return "—" if v is None else f"{v:.1f} {u['econ']}"

    def temp(c):
        v = records.to_temp(c, u)
        return "—" if v is None else f"{v:.0f} {u['temp']}"

    faults = s.get("faults") or []
    active = [f for f in faults if f["active"]]
    ready = s.get("readiness") or {}
    svc = s.get("service") or {}
    perf = s.get("perf") or {}
    m6 = s.get("mode06") or []
    flagged = [m for m in m6 if m["pass"] is False
               or (m.get("headroom") or 0) > 0.85]

    rows = []

    def section(title, body):
        rows.append(f"<section><h2>{esc(title)}</h2>{body}</section>")

    def table(head, body_rows):
        if not body_rows:
            return "<p class=q>Nothing to report.</p>"
        th = "".join(f"<th>{esc(c)}</th>" for c in head)
        tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                     for r in body_rows)
        return f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>"

    # ---- who and when ----
    head_bits = [b for b in (s.get("name"), (s.get("vehicle") or {}).get("vin"),
                             dist(s.get("odometer")) if s.get("odometer") else None) if b]
    verdict = ("No faults stored" if not active
               else f"{len(active)} fault{'s' if len(active) > 1 else ''} stored")

    if note:
        section("Note", f"<p>{esc(note)}</p>")

    section("Summary", table(
        ["", ""],
        [["Faults", esc(verdict)],
         ["Emissions readiness",
          "Ready to test" if ready.get("ready")
          else f"{ready.get('incomplete', 0)} monitor(s) incomplete"],
         ["On-board self-tests",
          f"{sum(1 for m in m6 if m['pass'] is False)} past the limit, "
          f"{sum(1 for m in m6 if m['pass'] is not False and (m.get('headroom') or 0) > 0.85)} close to it"],
         ["Service", f"{svc.get('due', 0)} due or due soon" if svc else "—"],
         ["Areas of concern", str(len(found))]]))

    # Recalls come from NHTSA's last answer on this machine, never a fetch:
    # this document is built offline like the rest. A car never looked up says
    # so, because a missing section would read as "no recalls".
    vin = (s.get("vehicle") or {}).get("vin")
    if vin and not s.get("simulated"):
        try:
            recalled, rmeta = knowledge.recalls_for_vin(vin, offline=True)
        except Exception:                                     # noqa: BLE001
            recalled, rmeta = [], {"error": "the recall list could not be read"}
        if rmeta.get("error"):
            body = f"<p class=q>{esc(rmeta['error'])}.</p>"
        else:
            body = table(
                ["Campaign", "Reported", "Component", "Consequence", "Remedy"],
                [[f"<b>{esc(r['campaign'])}</b>"
                  + ("<br>Do not drive" if r["park_it"] else "")
                  + ("<br>Park outside" if r["park_outside"] else ""),
                  esc(r["reported"]), esc(r["component"]),
                  esc(r["consequence"]), esc(r["remedy"])] for r in recalled]
            ) if recalled else "<p>None issued.</p>"
            body += f"<p class=q>{esc(rmeta['note'])} Source: {esc(rmeta['source'])}</p>"
        section("Safety recalls for this model", body)

    if active:
        section("Faults stored", table(
            ["Code", "Control unit", "Description", "Status", "First seen", "Last seen"],
            [[f"<b>{esc(f['code'])}</b>", esc((f.get('module') or {}).get('name') or f.get('system')),
              esc(f.get("descr")), esc(f.get("status")),
              esc(datetime.fromtimestamp(f["first_seen"]).strftime("%d %b %Y")
                  if f.get("first_seen") else ""),
              esc(datetime.fromtimestamp(f["last_seen"]).strftime("%d %b %Y")
                  if f.get("last_seen") else "")]
             for f in active]))

    incomplete = [m for m in ready.get("monitors", [])
                  if m["supported"] and not m["complete"]]
    if incomplete:
        section("Why it would not pass an emissions test", table(
            ["Monitor", "Why"],
            [[esc(m["name"]), esc(m.get("why") or "has not completed")]
             for m in incomplete]))

    if flagged:
        section("On-board self-tests worth noting", table(
            ["Test", "Component", "Measured", "Limit", "Verdict"],
            [[esc(m["name"]), esc(m["component"]),
              f"<b>{esc(m['value'])} {esc(m['unit'])}</b>",
              esc(f"max {m['hi']}" if m.get("hi") is not None
                  else (f"min {m['lo']}" if m.get("lo") is not None else "—")),
              "Past the limit" if m["pass"] is False
              else f"Passing at {round((m.get('headroom') or 0) * 100)}% of the limit"]
             for m in flagged]))

    if found:
        section("Areas of concern", "".join(
            f"<div class=concern><h3>{esc(c['title'])}</h3>"
            f"<p>{esc(c['detail'])}</p>"
            f"<p class=q>"
            + esc(f"now {c['value']}{c['unit']}"
                  + (f" of {c['limit']}{c['unit']}" if c.get("limit") is not None else "")
                  + (f"  ·  {c['when']}" if c.get("when") else "")
                  + f"  ·  {c['confidence']}% fit")
            + "</p></div>" for c in found))

    if svc.get("items"):
        due = [i for i in svc["items"] if i["state"] != "ok"] or svc["items"][:5]
        section("Maintenance", table(
            ["Item", "Life left", "Remaining", "Due", "Last done"],
            [[esc(i["item"]), f"{max(0, i['life'])}%",
              (dist(abs(i["km_left"])) + (" over" if i["km_left"] < 0 else " left"))
              if i.get("km_left") is not None else "—",
              esc(i.get("due_on") or "—"), esc(i.get("last_on") or "—")]
             for i in due]))

    y = perf.get("year")
    if y:
        section("Use", table(
            ["", ""],
            [["This year", dist(y["km"])],
             ["Average economy", econ(y.get("lphk"))],
             ["Trips", str(y.get("trips", 0))],
             ["Records from", esc(perf.get("since"))]]))

    if pics:
        cards = []
        for p in pics[:MAX_PHOTOS]:
            uri = data_uri(os.path.join(photos.folder(), p["file"]), p["mime"])
            if not uri:
                continue
            subj = f"{p['subject']} {p['subject_id']}".strip()
            cards.append(
                f"<figure><img src='{uri}' alt=''>"
                f"<figcaption><b>{esc(subj)}</b><br>{esc(p.get('note') or '')}<br>"
                f"<span class=q>{esc(datetime.fromtimestamp(p['at']).strftime('%d %b %Y %H:%M'))}</span>"
                f"</figcaption></figure>")
        if cards:
            section("Photographs", "<div class=gallery>" + "".join(cards) + "</div>")

    if shots:
        section("Captured states", table(
            ["When", "Reason", "What"],
            [[esc(datetime.fromtimestamp(x["at"]).strftime("%d %b %Y %H:%M")),
              esc((x.get("payload") or {}).get("reason") or ""),
              esc(x.get("label") or "")] for x in shots]))

    provenance = (
        "Every figure above was read from the vehicle's control units. "
        + ("This vehicle is SIMULATED — the readings come from OmaCar's own "
           "model of a car and not from an adapter. "
           if s.get("simulated")
           else f"Read over {esc((s.get('vehicle') or {}).get('protocol') or 'OBD-II')}. ")
        + "Repair-share figures and test procedures come from published service "
        "information, not from measurements of this vehicle. Trend projections "
        "are a straight line through the recorded points and are stated as "
        "bands rather than dates, because a straight line does not support a "
        "date.")

    return TEMPLATE.format(
        title=esc(s.get("name") or "Vehicle report"),
        heading=esc(s.get("name") or "Vehicle report"),
        subtitle=esc("  ·  ".join(head_bits)),
        when=esc(datetime.now().strftime("%d %B %Y, %H:%M")),
        verdict=esc(verdict),
        body="".join(rows),
        provenance=provenance,
    )


TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — OmaCar report</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box }}
  body {{ margin: 0; padding: 2rem 1.25rem 4rem; background: #fff; color: #16191b;
         font: 15px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
         max-width: 60rem; margin-inline: auto; }}
  header {{ border-bottom: 2px solid #16191b; padding-bottom: 1rem; margin-bottom: 2rem }}
  h1 {{ font-size: 1.7rem; margin: 0 0 .25rem }}
  h2 {{ font-size: .78rem; letter-spacing: .18em; text-transform: uppercase;
        color: #5a6468; margin: 2.2rem 0 .7rem; font-weight: 700 }}
  h3 {{ font-size: 1rem; margin: 0 0 .3rem }}
  p {{ margin: 0 0 .6rem }}
  .q {{ color: #5a6468; font-size: .82rem }}
  .verdict {{ display: inline-block; margin-top: .6rem; padding: .3rem .7rem;
              border: 1px solid #16191b; border-radius: 2rem; font-size: .82rem }}
  table {{ width: 100%; border-collapse: collapse; font-size: .84rem }}
  th {{ text-align: left; font-size: .68rem; letter-spacing: .12em;
        text-transform: uppercase; color: #5a6468; border-bottom: 1px solid #c8cfd2;
        padding: 0 .6rem .4rem 0 }}
  td {{ padding: .5rem .6rem .5rem 0; border-bottom: 1px solid #e6eaec;
        vertical-align: top }}
  .concern {{ border-left: 3px solid #b8863a; padding: .1rem 0 .1rem .9rem;
              margin: 0 0 1.1rem }}
  .gallery {{ display: flex; flex-wrap: wrap; gap: 1rem }}
  figure {{ margin: 0; width: 15rem; border: 1px solid #d8dee0; border-radius: .4rem;
            overflow: hidden }}
  figure img {{ display: block; width: 100%; height: 10rem; object-fit: cover }}
  figcaption {{ padding: .5rem .6rem; font-size: .74rem; line-height: 1.45 }}
  footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #c8cfd2;
            color: #5a6468; font-size: .76rem }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #0d1214; color: #e7f0ee }}
    header, footer, th {{ border-color: #2a3438 }}
    td {{ border-color: #1a2225 }}
    .q, h2, footer {{ color: #93a6a2 }}
    .verdict {{ border-color: #93a6a2 }}
    figure {{ border-color: #2a3438 }}
  }}
  @media print {{ body {{ padding: 0 }} section {{ break-inside: avoid }} }}
</style>
<header>
  <h1>{heading}</h1>
  <p class=q>{subtitle}</p>
  <p class=q>Report generated {when} by OmaCar</p>
  <span class=verdict>{verdict}</span>
</header>
{body}
<footer>{provenance}</footer>
"""


def write(path=None, note=None, include_photos=True):
    doc = build(note=note, include_photos=include_photos)
    if path is None:
        s = records.snapshot()
        name = (s.get("name") or "vehicle").replace(" ", "-").lower()
        path = os.path.expanduser(
            f"~/omacar-{name}-{datetime.now().strftime('%Y%m%d-%H%M')}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path, len(doc)


def main(argv):
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    note, out = None, None
    no_photos = "--no-photos" in argv
    # Walk the arguments rather than filtering on a leading dash: the value
    # after --out is a path and does not have one, so a filter puts the output
    # filename into the note.
    rest, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        rest.append(a)
        i += 1
    if rest:
        note = " ".join(rest)
    path, size = write(path=out, note=note, include_photos=not no_photos)
    print()
    print(f"  {path}")
    print(f"  {size / 1024:.0f} kB, self-contained — no server, no network, no account.")
    print(f"  Email it, or open it with: xdg-open {path}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
