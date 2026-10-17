# A child left in the car

When the car is switched off and the car seat looks occupied, OmaCar asks
"Is your child still in the car?" on the tablet, tells the owner's outbox,
chimes, escalates if the cabin is getting hot, and does not let the tablet
sleep until somebody answers.

The code is `lib/childalert.py` (the decisions and the loop),
`share/js/childwatch.js` (the camera), `share/js/childalert.js` (the card,
the top-bar lamp and the chime) and `share/js/views/childalert.js`
(Cabin → Child seat). The unit is `omacar-child-alert.service`.
`omacar tablet setup` and `omacar ignition install` both enable it.

## What it does

During a drive, a camera that faces the child seat is watched for a face, two
frames a second, with MediaPipe's Face Landmarker. It runs in the page and
reads the recorder's live picture, as drowsy mode does. A face seen once
arms the alert. While a drive is under way with the seat armed, the top bar
shows a third console readout, **Child seat · Occupied**. The readout makes
no sound and does not pulse.

When the car is switched off, which means the charger is off and the car is
not moving, an armed seat starts this sequence:

| When | What |
|---|---|
| 0 s | A full-screen card: **Is your child still in the car?** It has two buttons: **I have my child** (clears the alert) and **Remind me in 2 min**. |
| 0 s | The ignition loop is held awake with the reason `child alert`. **Sleep now** cannot override it, and a low battery does not end it. |
| 30 s | `notify.send("child-left", …, "critical")` writes to the owner's outbox. It is sent again every 10 minutes until someone answers. |
| 60 s | A chime, then again every 20 s, until someone answers. |
| Hot | The cabin sensor reads the threshold or above (default 30 °C), or has risen 3 °C in 10 minutes. The card turns red and says how hot it is. An urgent message is sent, and another for every further 5 °C. The chime then plays every 10 s. |

**Remind me in 2 min** hides the card for two minutes and shows a countdown
above the dock. The card then returns with the chime at once. The outbox
message still goes at 30 s, because a snooze is not an answer.

**The chime is the one exception to OmaCar's no-sound rule.** The comment in
`share/js/childalert.js` says so. It plays only while the alert's own card
is on screen, and only after 60 s with no answer. The owner can turn it off
in Cabin → Child seat.

The alert never fires while the car is on or moving. It fires once for each
switch-off. It fires again in the same switch-off only if the camera sees a
face after somebody answered (see the latch rule below).

## The latch rule

    occupied = seen_recently OR (seen_during_drive AND not cleared)

- **seen_recently**: a face in the last 2 minutes.
- **seen_during_drive**: a face at any point since the last clear, whether
  during the drive or after it, or the owner's **Child aboard** tap.
- **cleared**: an owner's tap after the last sighting. The tap is **I have my
  child** on the card or **Car seat is empty** in Cabin → Child seat.

A face seen within 20 s after a clear is taken to be the child being lifted
out, and does not re-arm the alert. A face seen later does, and starts a new
alert.

**Unknown is never "empty".** The alert proceeds on the drive's latch in all
of these cases:

- the camera is unplugged;
- the camera reports "dark" (mean brightness under 28/255);
- the detector failed to load or failed on a frame;
- the page stopped reporting.

The card names the camera's state, so the owner knows why it is asking.

**Why the camera cannot clear it.** A face detector that cannot see a
sleeping child's turned face would also "see" an empty seat. So the camera
can arm the alert but never disarm it. Only a tap clears it. The brief left
the choice of an automatic "N minutes of empty seat in good light" clear
open. It is deliberately not built, because it would turn the detector's
known blind spot into a false "all clear". If a person or seat-occupancy
detector is added later, revisit this.

**The latch survives a crash, a restart and a reboot.** The loop's whole state
is written to `child-alert.json` every second and restored on start. If the
loop dies mid-alert, the ignition loop keeps reading its last word and stays
awake until a "clear" tap. The API writes that tap to a file the ignition
loop reads directly.

### When the car counts as "switched off"

The charger is off and the car is not moving. **Moving** means a speed in the
last 2 minutes *with* the car's computers still answering (`lib/ignition.py
car()`). This decision has two consequences:

- a charger lost on the road, or at a red light, with the computers
  answering, is not a switch-off;
- once the ignition is off the computers go quiet. The alert then starts
  within about 15 s, the time the last answer takes to age out, and is not
  held back by the speed from before the driver pulled in.

A charger the loop cannot read never starts an alert, because a switch-off
cannot be seen. In that case the top-bar lamp turns amber and says so. An
alert that is already running keeps running.

## Settings

The settings live in `~/.config/omacar/child-alert.json` and are edited in
Cabin → Child seat. They are parked only: greyed while the car moves, and
the server also refuses them while the car is moving (409).

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Off: nothing is watched and nothing holds the tablet. |
| `chime` | `true` | The 60 s chime. |
| `heat_c` | `30` | The cabin temperature that escalates, from 25 to 40. A value out of range falls back to the default, never the loosest value. |
| `camera` | `null` | Which recorder camera (`front`, `rear`, `cabin`) faces the seat. |

**There is no toddler camera yet.** The recorder has three roles: front, rear
and cabin. The fourth camera facing the child seat is the roadmap's
`safe.child-cam`, which is not built. Until it is, the owner chooses which
existing camera faces the seat. With `camera: null` the feature is still
on, but it acts only on the **Child aboard** tap. That tap stays available
while moving, because it only ever makes an alert more likely.

## What is and is not verified

**Verified by tests.** `test/childalert_test.py` has 64 checks and
`test/js/childalert.test.js` has 21. The ignition veto adds 5 checks to
`test/ignition_test.py`. Together they verify:

- the occupancy rule and the latch;
- the alert's timing (0, 30 and 60 s, the 10-minute repeats and the snooze);
- the heat escalation and that its keys change with each 5 °C band;
- never while on or moving, and once per switch-off;
- the ignition veto: Sleep now and a low battery cannot pass it, and it
  holds even after the child-alert loop dies;
- the camera's failure states;
- the card and the lamp, as pure functions.

**Seen in a sandbox, simulated.** `omacar child-alert sim` drives the real
`decide()` with a scripted world. The card says "Simulated", nothing is sent
to the outbox, and the simulation never holds the real tablet. The
screenshots are in `omacar-notes/screenshots-2026-10-17/features/child-alert-*.png`.

**Not verified: the big unknown.** Nobody has yet tested whether the Face
Landmarker sees a **sleeping child, head turned or under a blanket, in a dark
car**. It almost certainly will not at night with the C920, which has an
IR-cut filter. The roadmap already says the seat camera must be 940 nm
infrared. The design does not rely on a sighting at switch-off: one face at
any point in the drive is enough. But a child put in the car asleep, and
never seen, is not caught by the camera at all. That is what **Child aboard**
is for.

**Not verified: phone delivery.** The alert reaches the owner's
**outbox** (`omacar notify list`, `GET /api/notify`). Nothing pushes it to a
phone until omarchy-mobile gains a subscriber for it (see `lib/notify.py`).
The card says this rather than "sent to your phone".

**Not verified: on the tablet.** The CPU cost of a second MediaPipe
instance at 2 fps next to drowsy mode at 12 fps has not been measured.
Whether the AUX volume pin makes the chime audible with the car off depends
on how the speakers are powered: with the ignition off, the car's amplifier
is probably off too, and only the tablet's own speaker remains.

## Test procedure: a doll or teddy, in daylight

Do this parked, in daylight, with the tablet in the car.

1. In Cabin → Child seat, set **Camera facing the seat** to the camera
   pointing at the seat. Confirm the readout says **Watching**, not "No
   picture" or "Face detection not installed". If it says the latter, run
   `omacar assets fetch`.
2. Put a doll with a clear, front-facing face in the seat. Within a few
   seconds the screen should say **Occupied**, armed by **The camera**. If it
   does not, a teddy's face may not count as a face to the detector. Use a
   doll with a human face, or a printed photo of one, and write down which
   worked.
3. Start the car, then check the top bar shows **Child seat · Occupied**.
4. Turn the doll face-down, or cover it. The seat must **stay** Occupied:
   this is the latch.
5. Switch the car off. The full-screen card should appear within about
   15 s. `omacar ignition status` should show `held (child alert)`.
6. Wait. At 30 s, `omacar notify list` shows a `critical` "Is your child
   still in the car?". At 60 s the chime plays. Note whether it can be heard
   from outside the closed car.
7. Tap **Remind me in 2 min**. The card should give way to a countdown above
   the dock. When the countdown ends, the card and the chime should return.
8. Tap **I have my child**. The card should go, and the ignition countdown
   should start from the full 30 s.
9. Repeat step 5 with the camera unplugged before switching off. The alert
   must still fire, and the card must say the camera is not reporting.
10. In the dark: repeat steps 2 to 5 at night, and write down what the camera
    reported. This is the measurement this document is missing.
