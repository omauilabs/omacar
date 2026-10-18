# The back-seat screen

An old phone or tablet, on the car's hotspot or Tailscale, shows a child a few
big friendly cards. It is a separate door into OmaCar, not a smaller key.

    omacar rear serve        the rear server (port 7571; or enable omacar-rear.service)
    omacar rear pair         prints the URL and a QR code (qrencode, if installed)
    omacar rear revoke       the token is deleted; paired screens stop at once
    omacar rear music on|off let the rear screen skip tracks (default off)
    omacar rear status       paired? skip allowed? what the page is being told
    omacar rear sim          a Simulated song that can skip, for a look

## The cards

- **How long?** An ETA only when navigation has written `navigation.json`
  (`{"at": <epoch>, "eta_at": <epoch>}`) in the last two minutes. Without it,
  the time the car has been driving ("we've been driving"), from the sample
  book. With neither, a dash. Never a guess. Navigation does not exist yet, so
  today this is always the time driven.
- **Music.** What the front screen's player says it is playing (the front page
  publishes it; `share/js/rearlink.js`). Next/previous appear only if the owner
  ran `omacar rear music on` AND the player can skip. The radio is a live
  stream and cannot, so today there are no skip keys; the hook is
  `radio.skip(+1|-1)`.
- **Calm time.** Slow bubbles and a breathing circle, three dim palettes, no
  flashing, still under reduced-motion. Holds the screen awake.
- **Temperature.** The cabin sensor, in the owner's units, with a word
  ("just right"). No sensor, no card.
- **Ask the driver.** Four fixed choices (a break, a snack, a drink, the
  bathroom), never typed words. Silent. The front screen shows "Back seat
  wants: a break" with a Got it key; it lapses after 5 minutes. One note per
  2 minutes across all rear screens. Never while an alert is active (radar
  alert, a drowsy or watchdog alert in the last minute, or an alert source that
  cannot be read); a blocked ask does not start the clock.

## What it can never show

Cameras (including the toddler camera), diagnostics, Work, settings,
locations, history, names, the nursery. Enforced on the server:

1. `serve.py --rear` never imports `lib/api.py`; requests are answered by
   `lib/rear.py` or refused.
2. A token of its own (`rear-token`; the cockpit's does not open this door).
   Bearer, `?k=` or the cookie the page sets (HttpOnly, SameSite=Strict).
3. An exact allow-list, `REAR_GET`/`REAR_POST`: the page, three files,
   `GET /api/rear/state`, `POST /api/rear/ask`; `POST /api/rear/music` only
   while music control is on. Everything else, including every file under
   `share/`, is 403 (401 without a valid token).
4. `/api/rear/state` is built field by field; the page is never sent a field
   that is not written there.

`test/rear_test.py` sweeps every route the app has plus every file and holds
all of this against a real `--rear` server.

## Honesty notes

Plain HTTP on a LAN, like the cockpit: the token keeps other devices out, it is
not encryption. Simulated sources say Simulated on the rear screen. A rear
screen that loses the car says so after 12 seconds and dims its cards.
