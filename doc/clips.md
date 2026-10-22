# Incident clips

The minute around a hard stop, a jolt or a tap, kept for good: 30 s before and
30 s after, from every camera that was recording, with the car's own numbers
(speed, RPM, throttle, IMA charge and current) for the same minute. Cameras tab
→ **Clips**.

## Why it is a second process

The recorder (`lib/cams.py`) is the dashcam, and it must never stop or slow. So
`lib/clips.py` is **a separate process** (`omacar-clips.service`) that never
touches a camera, ffmpeg, the loop or the recorder's code. All it does:

1. reads `live.json` (the car's speed, about 5 Hz) and, if the Surface has one,
   its accelerometer (`/sys/bus/iio`, read-only);
2. on a trigger, **hard-links** the recorder's one-minute segments that cover the
   window into `~/Videos/OmaCar/incidents/<id>/`. A hard link is a second name
   for the same bytes. No copy, no re-encode, no extra disk, and the footage is
   protected the instant the link exists;
3. a few seconds after the window closes, joins each camera's segments into one
   file with `ffmpeg -c copy` (no decode, no encode; it runs outside the
   recorder's lock) and writes `telemetry.json`.

It takes the recorder's own lock (`camstore._lock`) only for a directory
listing and the links, so the janitor cannot delete a segment between the two.
If it crashes, systemd restarts it and the recorder does not notice; an incident
left open by a crash is closed with what there is on the next start.

## Protected from pruning

`camstore.janitor()` deletes the oldest loop clips to stay under budget. It lists
`<role>/` and `locked/` only; it never sees `incidents/`. Even if it deleted the
loop's name for a segment, the incident's second name keeps the file. Nothing
automatic deletes an incident. `test/clips_test.py` runs the janitor with a budget
of zero over a folder with an incident in it and checks every incident file
survives byte for byte.

Incidents are **not counted** against the loop's budget (`budget_gb`): the Clips
header shows what they use, and the free space on the disk. At under 1 GB free a
new incident is refused rather than starving the loop (the service says so).

```
~/Videos/OmaCar/incidents/<YYYYmmdd-HHMMSS>-<brake|jolt|manual|sim>/
    incident.json       trigger, time, peak g, speed before/after, per-camera parts
    front.mp4 rear.mp4 cabin.mp4     only the cameras that were recording
    telemetry.json      rows {dt (s from the trigger), speed, rpm, throttle, soc, ima_amps}
~/Videos/OmaCar/exports/<id>/        a real copy, made by Export
```

If ffmpeg is missing or fails, the linked segments stay as `<role>-<segment>.mp4`,
`incident.json` lists them as parts, and the player plays whichever part covers
the moment (the list says "Partial" or the note says why).

## Triggers

All pure, with a fake clock, in `test/clips_test.py`.

| | rule |
|---|---|
| **Hard braking** | deceleration of 0.45 g or more, held 0.3 s or more, from 15 km/h or more, from the car's own speed. Looked at over 0.3 to 1.2 s windows; counts only if **both halves** of the window brake at least half as hard, so one bad sample never fires it. A missing SPEED (a hand-off) or a gap over 0.75 s voids the history: the first sample after a hole is compared with nothing. The simulator's speed is never the car's. |
| **Jolt** | the Surface's accelerometer, if there is one: the magnitude of (x, y, z) moving 1.5 g from its own slow baseline (tilt and mount angle cancel out). Absent: skipped, and `omacar clips status` and the Clips screen say "no accelerometer". |
| **Save clip** | the one-tap key on the Cameras tab, on the Clips list and on Home's Dashcams card. Allowed while moving: one tap. |

One incident per 20 s. A tap inside that window is not lost: it is added to the
incident already being kept ("+ tap"). The list's peak g is the hardest braking
actually measured in the seconds around the trigger, not the first figure over
the bar.

Thresholds can be changed in `~/.config/omacar/clips.json`:
`{"brake_g": 0.45, "jolt_g": 1.5, "brake": true, "jolt": true}`.

## The screen

Cameras → **Clips**. A list of incidents (time, trigger, peak g, speed before
and after, a small speed line) and a player: every camera that was recording,
side by side and in step, over a strip of the car's telemetry with the trigger
marked. Tap or drag the strip to scrub. The cameras share one clock (seconds from
the trigger); each file says where on it it starts, so cameras whose segments
began at different seconds still agree. A camera with no footage at a moment says
so on its tile, never a frozen frame.

- **Export** copies the incident to `~/Videos/OmaCar/exports/<id>/`.
- **Delete** is **parked only**: greyed (never hidden) with the reason drawn under
  it while `store.lockedAsMoving`, a confirmation either way, and refused by the
  server too (`/api/clips/delete` answers 409 at 3 km/h or more).
- A small **Clip saved** toast, silent, on any trigger (a poll of `/api/clips/last`).
  The owner's child sleeps near the tablet: nothing here plays sound.

If the service is not running, **Save clip** still keeps the footage: it falls back
to the recorder's own lock (`camstore.mark`, the same minute on every camera,
without telemetry) and says so.

## Install

```
omacar clips on          # systemctl --user enable --now omacar-clips.service
omacar clips status      # the service, and whether it found an accelerometer
omacar clips list        # incidents on disk, newest first
omacar clips save        # the Save clip key, from a shell
omacar clips sim         # a Simulated incident (test-pattern video, a synthetic stop)
```

`omacar clips sim` writes one incident labelled **Simulated** (and every picture in
the player carries a SIMULATED tag). It uses `ffmpeg`'s `testsrc2` if ffmpeg is
installed, otherwise it makes the telemetry only and says there is no video.

## What was not verified, and what was assumed

- Verified end to end on a Mac with synthetic footage: the service watching a
  scripted `live.json` (a hard stop), linking, joining with `ffmpeg -c copy`,
  telemetry, a Save tap merged into the incident, the janitor not touching it, the
  routes, the player and the toast. **Not** run on the Surface or against the
  real recorder's VAAPI segments.
- Joined files start at a keyframe at or before the window's start: the recorder
  forces one every 2 s (`cams.ffmpeg_args`), and the start time is worked out from
  that (good to a frame or two). The file is one of ~60 s.
- The jolt threshold (1.5 g) and the accelerometer's behaviour on the Surface
  Pro 7+ (a mount that rattles, a tablet lifted by hand) are guesses: nothing here
  has read a real one. Raise `jolt_g`, or set `"jolt": false`, if it fires on
  potholes or when the tablet is handled.
- The recorder's own `BrakeWatch` (a 16 km/h drop in one second) still locks a
  `hard-braking` event in `locked/`; a hard stop now makes both. They are
  independent; retire the old one when the new one has earned trust.
- Incidents are outside the loop's budget and nothing prunes them. A drive-day of
  potholes could fill the disk with jolts; that is why delete exists and why a new
  incident is refused under 1 GB free.
