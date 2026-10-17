# Arriving home, and the fridge for a pickup

Two features on one seam: a location provider (`lib/location.py`) and a
planner on top of it (`lib/arrival.py`). **There is no GPS receiver on this
tablet yet**, so today the parts that need a position wait, and say so. The
day a u-blox NEO-M9V behind gpsd appears, they start working with no other
change.

## What works today, with no GPS

- **Saved places.** Home and pickups, in `~/.config/omacar/places.json`, set
  with `omacar places set home <lat> <lon> [radius_m]`,
  `omacar places pickup add <name> <lat> <lon>`, or in the app under
  Navigation, Places (parked only; coordinates by hand for now).
- **"Heading to a pickup", with your own ETA.** On Cabin, Fridge (or Places or
  the Home card) tap a saved pickup. With no GPS the card says "No GPS fix, so
  no ETA" and offers **Cool now / In 15 min / 30 min / 1 hour**. The fridge goes
  2 C colder 20 minutes before that time, and back after two hours (or when you
  say Stop). Needs `omacar-arrival.service` running and the fridge connected.
- **Fridge to eco** is one tap and works anywhere the fridge does.

## What is waiting on GPS

- The distance and ETA to home, the "Nearly home" card and prompt, and the
  arrival message to your phone: all need a fix.
- Pickup ETA from the real drive (instead of the minutes you typed), the
  automatic start within 15 minutes of a saved pickup, and "restore when you
  leave the place".
- "Use where I am" in Places.

To turn it on once the receiver is in: `systemctl --user enable --now
omacar-location.service omacar-arrival.service`. `location.py` reads gpsd at
`127.0.0.1:2947` (JSON protocol, TPV messages: lat, lon, speed, track, mode).
It only ever uses a TPV with mode 2 or 3. No gpsd, no fix, or a fix older than
10 s: **no fix**, and nothing is drawn from a guessed position.

## Arriving home

Within 2 km of home and getting closer (or already at the door), one card
appears on Home (add the **Arrival** card in Customize) and a calm strip over
the stage on every other screen. It offers three one-tap keys, allowed while
moving because each is a single tap:

| Key | What it does |
|---|---|
| Open garage | **Disabled, "Garage controller not installed"**, until `lib/garage_door.py` exists. The garage is design only (`doc/design/2026-10-17-garage.md`); no client is built. |
| Fridge to eco | Raises the fridge set point to `fridge_eco_c` (default 5 C, adjustable in Places). |
| House | Runs `house_command` from `places.json`, only if you set one. Nothing by default. |

**Never automatic.** Nothing opens, cools or runs on arrival. "Not now"
puts the card away for the trip. One trip is one message to your phone
(`notify.send("arrival", ...)`, deduped per trip: leaving 3 km and coming back
is a new trip).

`house_command` is read from `places.json` only. The app, the API and the CLI
cannot set it, so a web page cannot make this machine run a command. It is
split into arguments and run without a shell. Example:
`"house_command": "curl -s http://homeassistant.local:8123/api/webhook/arrive"`.

## Pickup pre-cool

Heading to a saved pickup place (you tapped it, or you are within 15 minutes of
one and `auto_pickup` is not false), the fridge is set **2 C colder** 20
minutes before the ETA, bounded by the fridge's own range, and put back when
you leave the place (400 m after having been within 150 m) or two hours after
cooling began. It only puts back if the set point is still what it set: if you
changed it meanwhile, that is left alone. The card reads "Cooling for your
pickup at Target Marina, 37 F by 4:20".

The automatic start requires that the place is nearer than home, that the car's
course points within 45 degrees of it, and that the car is closing on it, so a
drive home that passes a pickup is not a pickup. It still starts without being
asked, and says "Started on its own" with a Stop key. Set `"auto_pickup":
false` to switch it off.

## Files

- `lib/location.py`: gpsd reader, simulator (`omacar location sim [home|<pickup>]`,
  always "Simulated"), haversine, bearing, ETA, places.
- `lib/arrival.py`: the pure planner (`step`, `plan_pickup`, `home_view`), the
  loop (`omacar arrival run`), the API's actions.
- `/api/arrival` (GET, POST `{do: ...}`), `/api/places` (GET, POST).
- `share/js/arrival*.js`, `share/js/views/places.js`, `share/css/arrival.css`.
- `test/arrival_test.py`, `test/js/arrival.test.js`.

## Decisions for the owner

- Eco default 5 C (41 F), just inside the usual food-safe limit; change it in Places.
- The proximity auto-start is **off by default** (the lead's change, 2026-10-17): a pickup you choose cools the fridge; merely driving near a saved pickup does not, unless you set `"auto_pickup": true`. The rest of this feature never acts unasked, and this keeps it that way.
- Where the arrival message goes: `lib/notify.py` records it; nothing pushes
  to the phone until omarchy-mobile reads the outbox (see notify.py).
