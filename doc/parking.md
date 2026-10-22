# Where did I park?

When the car is turned off, OmaCar saves the spot in the half minute before the
tablet sleeps (doc/ignition.md): the last GPS fix, one still from the rear and
one from the front camera, and, in a garage, the pressure for a level estimate.
The last ten parks are kept. Code: `lib/parkspot.py`; screen: Navigation ->
Parked; Home card: "Parking". CLI: `omacar parked status|list|run|sim|forget`.
Unit: `omacar-parkspot.service` (user unit, stdlib python, not installed by
anything yet; enable with `systemctl --user enable --now omacar-parkspot`).

## When it fires
`decide_capture` (pure, tested on a fake clock). The charger is the ignition
signal. A park is saved 4 s after the charger goes off, only if:
the charger was seen ON first; the car showed no speed after the charger went
off (a cable out on the road is a fault, not a park); it is still inside the
24 s window (the tablet sleeps at 30 s); and no park was saved in the last 5
minutes. A charger that cannot be read never captures.

## The stills, without disturbing the recorder
The recorder (`omacar-cams`) already writes each camera's latest JPEG for the
Cameras screen (`cams.live_path`). The park copies that file: a read, nothing
asked of the recorder, which is never stopped or slowed. If the picture is over
10 s old, the last frame of the newest clip is read with `ffmpeg -sseof`
(read-only, `nice`, 8 s cap). If both fail there is no still and the screen says
"No rear photo". `parkspot.py` contains no systemctl, kill or signal (tested).

## The level estimate
`omacar-parkspot run` also keeps outdoor pressure samples (env outdoor node)
while the car moves over 15 km/h. At a park the street reference is the median
of those samples from 25 min to 90 s earlier (the last 90 s may be the ramp). Then
`h = 44330 * (1 - (p / p_ref)^0.1903)` metres, one level = 3 m, nothing claimed
under 1.8 m ("street level"). Shown as "level ≈ P3 (estimate)", with up/down in
words. Good to about +-1 level; weather change over a long drive makes it worse.
No node, or no moving samples: no level, never a zero.

## Privacy
Parks (`parks.jsonl`, `parks/<id>-front|rear.jpg` under the state dir) stay on
the tablet. The phone notification (`notify.send("parked", ...)`) is
"Parked at 14:32 · photo saved": no coordinates, no address (tested, including
through the real outbox). A paired client fetches details from
`GET /api/parking/last` (and `/api/parking` for the ten, photos at
`/api/parking/photo/<id>/<front|rear>`). The demo server answers none of these.
`omacar parked forget` deletes everything.

## Simulator
`omacar parked sim [n]` writes up to three parks with a fake fix and generated
stills, all `simulated: true`; every screen says "Simulated". It sends no
notification.

## Not yet verified on the car
GPS accuracy is not saved (gpsd's epx/epy are not yet carried by
`location.parse_tpv`); the live-picture path, the pressure reference and the
unit have been tested only with fakes and the simulator.
