# Drive day — Marina to Los Banos, Thursday and Friday

Four legs, roughly ninety minutes each. This is the one resource this project
cannot buy, so everything below is here to stop a leg being wasted.

Written 9 September after an adversarial read of the whole capture path found
nine defects, every one of which cost at least a leg. They are fixed; this is
what is left for a person to do.

---

## Tonight, on the tablet

The tablet has to be on and on the tailnet for any of this. In order:

```
cd ~/Projects/omacar && git pull
./install.sh                       # picks up the new units
systemctl --user daemon-reload
systemctl --user enable --now omacar-drivelog.service

omacar preflight                   # and this is the one that matters
```

`omacar preflight` is the whole of the rest of this section. It asks the six
questions somebody would otherwise run six commands for, in a driveway, in the
dark, and it exits 0 only when nothing on this machine will stop a recording.
It fixes nothing — every line names the command that would.

It needs no car, so the right time to run it is the night before, indoors.

`omacar tablet status` now leads with a **recording the bus** row. Until
tonight there was no such row, which is how a tablet with a perfect dashboard
and no recorder reported itself completely ready.

## Then ten minutes in the driveway, engine idling

This exercises almost everything that cannot be tested without a car.

1. Plug the adapter in. `omacar drive status` should move from `waiting` to
   `capturing, leg 1` within about fifteen seconds.
2. Watch it for two minutes. The frames-and-elapsed line must **advance**. It
   updates every fifteen seconds now; before tonight it froze for the whole leg
   and the screen said the supervisor was dead.
3. `omacar drive off` — hand the port back.
4. One `omacar listen capture --seconds 30 --save --note econ`, then check the
   saved file has a `raw` key. It will: `--save` keeps the frames now. It did
   not on 8 September, and that is why that drive answered nothing.
5. `omacar drive on`.

If step 1 never leaves `waiting`, the engine gate is not seeing RPM. If it says
`not running`, the unit did not enable and the whole trip is gauges only.

## On the road — the recorder

Nothing to do. It watches `live.json`, records in twenty-minute legs, hands the
port back for ninety seconds between them so the gauges catch up, and ends a
leg when the frames stop, which is the ignition going off.

**In the first five minutes of leg one**, have the passenger run
`omacar drive status` once. It must say `capturing` with a leg number and an
advancing frame count. That is the whole check.

## On the road — the drive-mode byte

This is the open question: which byte moves when the ECON / SPORT / NORMAL
switch moves. Every capture on the tablet so far is one mode, so nothing can be
compared.

**Do not use `omacar listen marks` for this.** Use three separate captures.
Marks asks the driver to type a label at the moment of the switch, and the
seconds between the hand leaving the switch and the label landing are the part
that used to poison the answer. Three captures have no window boundaries at
all.

Parked, or at steady cruise with a passenger doing the typing:

```
omacar drive off                                        # the port is one thing

omacar listen capture --seconds 30 --save --note econ    # hold ECON, untouched
omacar listen capture --seconds 30 --save --note sport   # hold SPORT
omacar listen capture --seconds 30 --save --note normal  # hold NORMAL

omacar drive on
```

Then compare them. Adoption needs the captures named, so list them first:

```
omacar listen list                                      # the three names
omacar listen adopt 20260910-0812 20260910-0815 20260910-0818
```

A bare `omacar listen adopt` refuses and says why: it compares positions, so it
needs two or more captures.

Hold each position untouched for the whole thirty seconds. A switch moved
mid-capture is a capture that proves nothing.

**The port is one thing.** A twenty-minute leg holds it, and a capture started
during one fails after twelve seconds with "the daemon is holding the port".
`omacar drive off` first, always, and `omacar drive on` after.

## What can go wrong that nobody will notice

These are the failures that look like success, which is why they are listed:

- **`omacar drive on` does not start anything.** It clears a marker. If nothing
  is supervising it now says so and exits 1; before tonight it said "it will
  record again when the engine is running" regardless.
- **A stale status line.** `omacar drive status` exits 1 and prints in red when
  nothing has been written for two minutes. Trust it now — it was wrong for
  most of every leg until tonight, and an alarm that cries wolf once is an
  alarm nobody reads again.
- **A silent bus.** The status screen says "nothing heard yet" after two
  minutes of an open adapter with no frames. That is a real answer: the port is
  open and the car is not talking on any setting we know.

## Afterwards

```
omacar listen list                    what was captured
omacar drive status                   legs and frames for the run
omacar listen adopt NAME NAME NAME    compare them, propose a candidate
```

Adopt records a **candidate**, never more. Nothing a machine does can raise a
finding above what the evidence supports; only a person checking it against
something real makes it validated.

One known limitation, deliberately not fixed before the trip: adopting twice
writes over the first adoption. The captures are the evidence and adopt re-runs
in seconds, so run it once at the end rather than after each day.
