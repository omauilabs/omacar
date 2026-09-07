# OmaCar

Live OBD-II diagnostics for your car, on your Omarchy desktop.

Developed against a **2015 Honda CR-Z** with a wired **OBDLink** adapter — an
SX, and now an EX — on `/dev/ttyUSB0`. Everything generic works on any car built
since 1996; everything protocol-aware works on the CAN-era cars this has been
run against, and says which is which.

    ./install.sh          CLI, launcher, icon, and Omarchy menu entries
    ./install.sh --bind   also bind Super+Shift+C
    ./test/all.sh         every test — needs neither a car nor an adapter

## No car? Run one

    omacar sim seed       a year of driving, the codes it has thrown, the service book
    omacar sim start      the live loop — 1 Hz samples and live.json, same as the daemon
    omacar sim status

The simulator is a whole 2015 CR-Z in software, writing the same
`telemetry.db` and `live.json` the real daemon writes, so the cluster, the bar
panel and the dock card all read it without knowing the difference. It never
opens a serial port, so it cannot fight the daemon for one — but the two do
share `live.json`, and only one of them may run at a time.

It is not a lie: the vehicle record says `simulated`, and the bar panel and
`omacar sim status` both say so out loud.

A year of one-second rows would be a hundred megabytes to say something a daily
rollup says in a line, so the two halves are generated differently and made to
agree — 365 `days` rollups from a trip-level model, and the last fortnight at
1 Hz synthesised from those same trips, with each trip's fuel scaled to the
figure the rollup already published. The sparkline and the odometer cannot
disagree.

There is a `omacar-sim.service` user unit alongside it for a simulator that
should still be there after a reboot.

## The workshop

    omacar               open the app
    omacar scan          full system scan in the terminal
    omacar ai            the advisor: what is actually wrong with this car

`share/app.html` is a diagnostic platform, not a gauge. Twenty-two screens
under five tabs:

  **Car**     the hub you land on, drive mode, the ambient cluster, the
              overview, the IMA hybrid screen, your phone, and the garage
  **Faults**  every code with what it means, this car's own numbers, ranked
              causes and its freeze frame; the full system scan; and readiness
              with the reason each monitor is stuck, plus Mode 06
  **Data**    several channels on one time axis with a cursor and statistics,
              a scrubber over any recorded drive, and the functional tests
  **Care**    the service book, resets and procedures, what is trending
              somewhere it should not, the drive log, and the paperwork
  **AI**      the advisor — see below

Off the navigation but routable: the printable vehicle report, learn mode, and
the theme editor, each reached from the screen that needs it rather than from a
tab nobody would look in.

No build step, no framework, no dependencies: ES modules served straight off
disk. The thing this replaces is a tablet that stops getting updates when its
vendor decides it should, and a tool you cannot open and repair is the same
trap in a different colour.

**Functional tests** command the car rather than asking it. The cylinder
balance sequence silences each injector in turn at idle and measures how far
the engine speed falls: a cylinder that does not drop the idle when you switch
it off was not contributing to begin with. On the simulated car it finds
cylinder 1 at 55% of the best cylinder's drop, which independently agrees with
the P0301 sitting in that car's own code history. Every test says on its face
that a real vehicle needs a manufacturer protocol — generic OBD-II has Mode 08
in the standard and almost nobody implements it. Durations are capped in the
server, and the command carries its own expiry so a crashed app cannot leave a
cooling fan on.

Two more features are worth calling out because nobody else surfaces them.
**Readiness** says not just which monitors are incomplete but *why* — almost
always another fault standing in the way — which turns "you cannot pass a smog
test" into "fix this one code and you can". **Mode 06** is the ECU showing its
working: every self-test's measured value next to the limit it was judged
against. Every OBD-II car has had it since 1996 and almost no consumer tool
shows it, which is a waste, because a catalyst test passing at 95% of its limit
is a failure with a date on it.

## When the adapter arrives

Everything above works against a real car, not only the simulator. The daemon
runs a slow pass on connect and every few minutes — separate from the gauge,
because none of it changes at gauge rate and all of it costs bus time:

    omacar survey     read it once by hand

  Mode 03 / 07      stored and pending trouble codes, merged into what was
                    already known so a code keeps its first-seen date and
                    gains a count, and one that has gone is marked cleared
                    rather than deleted — the history of what a car has done
                    is most of what makes the next fault diagnosable
  Mode 02           the freeze frame, attached to the code it belongs to
  Mode 01 PID 01    MIL state and every readiness monitor
  Mode 06           the on-board test results
  Mode 09           VIN and calibration

OBD-II will not tell you what car it is in. It reports a VIN and nothing else,
so a tool that shows a make and a year either asked a paid database or worked
it out: `lib/survey.py` reads the manufacturer from the VIN's first three
characters and the year from its tenth, both of which are in the standard and
free, and validates the North American check digit rather than trusting a
misread. The model is genuinely not derivable without a licensed database, so
it is left for you to fill in rather than guessed at.

Two things are not on the car and cannot be:

    omacar odometer set 85700     the reading, once
    omacar service log oil        what has been done to it

**There is no odometer PID.** Service 01 PID 0xA6 exists in the later
standards and almost nothing implements it — python-obd does not even carry a
command for it — so the mileage on your dashboard is unavailable to every
generic scan tool, the expensive ones included. Anything that shows you one
either asked a manufacturer protocol or did what this does: took a reading
from you once and integrated distance from the wheels ever since. That tracks
a trip meter to well inside a percent, it drifts if the daemon was not running
while you drove, and the tool says which figure is which and when it was set
rather than presenting a derived number as if it came off the bus.

**The service record is not on the car either** — Maintenance Minder lives in
the instrument cluster behind a manufacturer protocol. A car with no book gets
a starter schedule with every item showing as due, because eleven green ticks
on a vehicle nobody has told us anything about would be a lie. Log what has
been done and it counts down from there. `omacar service add` and `forget`
adjust the list; the handbook wins over the defaults.

The watchdog now files finished trips into the record as well as announcing
them, so the drive log has something in it on a real car.

Daily figures no longer need a compaction to have run: `records.days()` merges
the stored rollups with a live rollup of recent raw samples, so a car driven
for the first time this morning has a year view that includes this morning.

The daemon runs `survey.prepare()` before it opens anything, and that ordering
is load-bearing: renaming a database out from under an already-open handle does
not fail loudly — SQLite follows the inode and then refuses the next write with
"attempt to write a readonly database", which is a memorable way to spend an
evening.

A real adapter never writes into the simulator's record. If a survey finds a
simulated car in the database it moves the whole thing aside as
`telemetry-simulated.db` and starts clean, because half a fictional CR-Z next
to half a real car is worse than either — it would show somebody trouble codes
their vehicle has never set. `omacar sim seed` builds a fresh one whenever it
is wanted, so nothing is lost.

And a sample nobody is publishing any more stops counting as live after
fifteen seconds. A file on disk does not know the process writing it has died,
and without that check the app shows last Tuesday's road speed as the current
one — which on a dashboard is not a cosmetic problem.

## Sharing it

    omacar share "second opinion on the IMA pack, please"

One self-contained HTML file — the scan, the concerns, the service book and
the photographs, all inlined — that opens in any browser with no server, no
account and no network. Email it to a mechanic, put it on a stick, attach it
to a listing when you sell the car. It will still open in ten years, which is
not true of a link to somebody's cloud. The same file is a button in the app.

For live viewing there is `omacar cockpit`, which is read-only unless told
otherwise: a mechanic watching your car over your Wi-Fi cannot clear its codes.

## Photographs

A perished hose is not on the bus, and a picture of it is half the diagnosis.
Photographs are filed against a code, a concern, a snapshot or a service item,
with a note and the odometer, and they travel in the vehicle's own record.

The camera needs a secure context, so it works on the machine running OmaCar
and in the kiosk, and not on a cockpit display reached over the LAN — that is
a browser rule and every browser enforces it. Rather than a button that cannot
work, the app says so and offers a file picker, which on a phone still opens
the camera.

## More than one car

    omacar vehicle              the garage
    omacar vehicle use <vin>    look at another one

Each vehicle gets its own record, keyed by VIN, and plugging in switches
automatically — the VIN comes back in the first survey, before anything is
written. A tool that only knows the car it saw last is a tool for somebody with
one car, and merging two cars' records is not a merge: it is a driver being
shown trouble codes their vehicle has never set.

## Trends, and what to worry about

    omacar concerns
    omacar snapshot "before the oil change"

A scan tool tells you a self-test is passing. It is passing *today*, and the
useful question is for how much longer. OmaCar keeps the same measurement over
months and draws a line through it — least squares on the raw points, with the
fit quality carried so it can decline to draw a conclusion through a cloud, and
bands rather than dates because a straight line does not support a date.

On the simulated car it finds long-term fuel trim drifting from +2.8% to +8.3%
across the year at 0.42% a month — which is the cause behind two of the codes
that car is holding, arrived at from the trend alone.

A **snapshot** freezes the whole state. The watchdog takes one automatically
whenever a rule fires, because a coolant spike on a climb is gone by the time
anybody opens the app, and an alert without its evidence is a rumour.

## How it compares to a Snap-on tablet

Honestly, in [doc/parity.md](doc/parity.md), including the two places it is
genuinely behind — there is no lab scope, and manufacturer protocols are what
their price actually buys.

## The advisor

    omacar ai                    everything at once, cheapest certain work first
    omacar ai owner              the same, without the jargon
    omacar ai predict            what fails next, and roughly when
    omacar ai code P0135         one code, ranked against THIS car's numbers
    omacar ai ask "will it pass a smog test?"

and, from the app, two more that have no equivalent on a scan tool:

  **Symptom first.** Describe what the car is doing — "it stalls at junctions
  for the first five minutes after a cold start, no warning light" — and it
  ranks the hypotheses, says what in the evidence *fails to rule each one out*,
  names the one observation that separates them, and hands back a recording
  plan: which channels, under what conditions, for how long, and what in that
  recording would confirm or kill each theory. Asked exactly that, it noticed
  the hybrid pack's freeze frames were captured at 54 mph and therefore cannot
  explain a stall at idle, and that assist current is not among the channels
  this car can report — so it marked that hypothesis unconfirmable rather than
  quietly asserting it.

  **Read a recording.** Save a stretch of driving in the data lab, then ask the
  advisor to read it. It gets per-channel statistics rather than the rows: far
  cheaper, and a model reads a shape better than ten thousand numbers.

A five-thousand-dollar scan tool can tell you what other technicians did about
a code on this model. It cannot read *your* freeze frame, *your* Mode 06
margins, *your* fuel trims and *your* year of economy at once and tell you what
is going on with this particular car. That is a reasoning problem, and it is
now a solved one — so it belongs in a free tool rather than behind a
subscription.

It drives the `claude` CLI you already have, headless. No API key, no account
of ours, no per-scan fee, and nothing about the car leaves the machine except
the question you chose to ask. Without the CLI everything else still works and
the app says so rather than pretending.

The failure mode of a language model on a diagnostic tool is a confident
invention — a code that does not exist, a measurement nobody took. Every
defence in `lib/ai.py` is aimed at that: the model gets a structured evidence
bundle and is told it is the only source of fact about this vehicle; it must
answer in JSON against a fixed schema; every finding must cite bundle keys;
**findings citing anything else are dropped in Python before they are ever
shown**, and the app says how many were dropped. Confidence is per finding and
on screen. Answers are cached against a hash of the evidence, so re-asking an
unchanged car is instant and free.

## Where this actually is

One car read end to end, on real hardware, over a real adapter: a 2015 Honda
CR-Z at 189,760 miles, 48,565 one-second samples, and a UDS service 0x19 sweep
that found four modules enumerating 241 fault codes between them — data no
generic scan tool exposes. Zero stored faults on that car, which is why the
interesting screens here are the ones about margins and history rather than the
one about codes.

`doc/ROADMAP.md` carries the countable half, regenerated by `omacar roadmap`
from git and from the test run rather than typed in, because those are the
numbers that go quietly wrong.

### You do not need the adapter, or the car

You do not need the adapter, or the car, to develop this.

    omacar setup          build the Python environment
    omacar bench start    start the ELM327 emulator (a simulated car on a pty)
    omacar doctor         adapter, protocol, supported PIDs, stored faults
    omacar live RPM SPEED stream readings to the terminal
    omacar bench stop

`doctor` against the emulator negotiates **ISO 15765-4 (CAN 11/500)** — the
same protocol Honda has used since 2008, so the bench is a fair rehearsal for
the real car.

Then the cluster itself:

    omacar daemon start   one long-lived connection, tiered polling
    omacar                open the cluster

### The ambient meter

The ring borrows the CR-Z's own language: it glows blue in NORMAL and ECON,
greens as you drive efficiently, and commits to red in SPORT. **Colour is
efficiency; the bright arc is road speed**; a slim inner arc is engine speed.

Efficiency is not a vibe. Where the ECU reports MAF and speed, OmaCar computes
real instantaneous economy — mass air flow over the stoichiometric ratio gives
fuel mass flow, and that over road speed gives L/100km — then reads it against
a band (4.5 good, 9.5 poor) tuned for this car. Only when MAF is unavailable
does it fall back to a load-and-throttle estimate, and **the status bar says
which one it used**. A gauge that looks authoritative while guessing is worse
than one that admits it estimated.

Preview it without a car: `?demo=1`, `?mode=sport`, `?units=metric`.

### One process owns the serial port

Opening an OBD connection costs 5-8 seconds. So exactly one process holds it —
the daemon — and everything else reads what it publishes: the UI polls
`/api/live`, the bar widget reads a cache. Nothing else opens a connection.

    ~/.local/state/omacar/live.json      current sample, rewritten atomically
    ~/.local/state/omacar/telemetry.db   one row per second, for trips

## Environment notes

`python-obd` is not in the Arch repos, so it lives in a venv under
`~/.local/share/omacar/venv`. `ELM327-emulator` additionally needs
`setuptools<81` and `--no-build-isolation`, because its build script still
imports `pkg_resources`, which setuptools 81 removed. `omacar setup` handles
both; that combination is the only one that installs on Python 3.14.

**On Arch the serial group is `uucp`, not `dialout`.** When the SX arrives:

    sudo usermod -aG uucp $USER    # then log out and back in

`omacar doctor` checks this and says so rather than failing with a bare
permission error.

## Living in the car

The point of leaving an adapter plugged in is to be told while something is
going wrong, not after.

    omacar watch start        the watchdog
    omacar watch list         what it has raised
    omacar status             is anything watching, and what does it know

`omacar-watch.service` reads the same `live.json` everything else reads and
raises an Omarchy notification when a rule fires: overheating, a charging
system that stopped charging, a trouble code that set and cleared itself before
you ever saw the light, low fuel, a trim that has drifted, the adapter coming
and going — and a summary when a trip ends, which is the one alert that is not
a warning. Every rule arms above one level and re-arms below a lower one, and
has to hold for several seconds before it counts, because an alerter that fires
twice a minute is one people learn to ignore. Every alert is filed whether or
not a notification reached anyone; the timeline is in the app and in the bar
widget, and the notification is a courtesy.

**Drive mode** (`#drive`) is the only screen that is safe to have on while the
car is moving: four numbers, type you can read at arm's length in daylight, one
full-width way out, and no pointer needed anywhere. An alert takes over the
whole screen, because if the watchdog has something to say at 70 mph it is the
only thing worth looking at. It holds a screen wake-lock, so a tablet on a
dashboard does not blank halfway through a drive.

**Kiosk.** `omacar kiosk` is drive mode fullscreen with no browser chrome, its
own Chromium profile (so it cannot restore yesterday's tabs over the gauge),
and Omarchy's own stay-awake switch held for the duration and put back exactly
as it was on the way out. `omacar-kiosk.service` is installed but deliberately
NOT enabled: a unit that puts a fullscreen gauge over your desktop the moment
you log in is correct on a tablet in a car and wrong on the machine you write
code on. Enable it on the tablet.

**It switches to drive mode on its own.** When the adapter answers — or when
the car actually starts rolling, if you would rather — the app takes itself to
drive mode, and goes back to the workshop when the link drops. You get in, the
car wakes up, and the screen is already the one you want without touching it.
The one rule that makes this bearable rather than infuriating: if you navigate
away from drive mode by hand, it stays away until the adapter reconnects.
Software that keeps dragging you back to a screen you just left is software you
end up fighting.

**Drive mode is yours to arrange.** Twenty readouts to choose from — speed,
engine speed, instantaneous and trip economy, coolant, intake, outside air,
battery, fuel level and computed range, load, throttle, timing, both fuel
trims, air flow, today's distance, odometer, stored faults, next service — up
to eight at a time, one to four across, with the big number and the bottom line
your choice too. The layout is stored on the machine running OmaCar rather than
in a browser, so the arrangement you make at the kitchen table is the one the
tablet in the car shows. The editor is only offered while the car is stopped;
that part is not configurable, because a screen you can rearrange at speed is a
screen you rearrange at speed.

**Hotplug.** `omacar hotplug install` adds a udev rule that gives the adapter a
stable `/dev/obd` (ttyUSB numbering moves the moment you plug in anything else
that presents a serial port) and asks your systemd to start the daemon the
instant it appears. One sudo, once. The watchdog also does the same job without
root, a second or two slower, by noticing a serial port that was not there
before — having both is harmless, because starting a daemon that is already
running is a no-op and that is checked before anything is spawned.

**Compaction.** A car driven a thousand miles a week writes about 0.6 GB of
one-second samples a year, which on a dashboard tablet is both a disk problem
and a speed problem. `omacar prune` — run automatically by the watchdog once a
day, and never while the car is being driven — rolls samples older than 45 days
into the daily figures the year view already uses, then drops them and
reclaims the space. The rollup uses exactly the same integration and fuel
model as the live path, so the year does not develop a seam at the boundary.

## The tablet in the car

Running natively on an x86 tablet with the adapter in the port and no second
computer anywhere — which is the point. **[doc/tablet.md](doc/tablet.md)** has
what to buy, why heat rather than the processor decides it, how to power it
from 12 V without browning out at every crank, and the honest list of what
Omarchy does not yet give you on a tablet.

    omacar tablet setup

One command, and it says what it is about to do before it does it: watchdog at
login, fullscreen drive mode at login, daemon started when the adapter is
plugged in, and the app switching to drive mode by itself when the car
connects. `omacar tablet` shows the current state and `omacar tablet off` puts
it back. Nothing here is turned on by `install.sh` — a tool that quietly makes
your laptop boot into a fullscreen speedometer has done something rude.

## A tablet that cannot run Omarchy

Most cheap tablets are ARM Android devices with locked bootloaders, and no
amount of wanting will put Arch on them. That is fine, because OmaCar is a web
app and every one of them has a browser.

    omacar cockpit              read-only, on the local network
    omacar cockpit --control    ...and allowed to command the car

It prints a URL and a QR code. Point a Samsung tablet, an old iPad, a phone or
a laptop at it and you have the gauge, while the machine that actually runs
OmaCar — a mini PC or a Pi in the glovebox, with no battery to cook in a parked
car in July — stays where it is.

The security model is the whole point of the mode, so it is worth stating
plainly. A token is required on every request including the page itself; every
write is refused unless `--control` was passed deliberately, so a display
cannot clear codes, command an actuator or spend your AI budget; and the token
is **not** a password — it stops the other devices on a car's hotspot from
stumbling in, and anyone who can read your Wi-Fi traffic can read this, because
it is plain HTTP on a LAN. Loopback keeps every power it has always had.

## Wearing Omarchy's clothes

The palette comes from `~/.local/state/omarchy/current/theme/colors.toml`, not
from this app. Change the desktop theme and OmaCar changes with it, light
themes included — the theme supplies the hues and `lib/theme.py` decides the
roles, so a light theme comes out legible rather than inverted. Every semantic
colour is checked for contrast against the surface it will actually sit on and
nudged if it does not clear the floor, because a theme's yellow is chosen to be
readable in a terminal and that is not the same background.

It is in the Omarchy menu (OmaCar → scan, advisor, watchdog, simulator), on the
bar with a badge when the watchdog has raised something, and in the app
launcher. `./install.sh --bind` adds a keybinding; without it, nothing grabs a
chord uninvited.

## Where it goes next

Landed: DTCs in plain English, freeze frames, readiness monitors, Mode 06,
full-system scan and the advisor. Then the write path, UDS discovery, a
shareable profile format with provenance, the IMA hybrid screen, a theme editor
that derives its palette on the server so it cannot produce something
unreadable, SVG gauges, and demo mode.

Still ahead, and named rather than implied:

- **Bidirectional on a real car.** The functional tests work against the
  simulator. On a real vehicle each one needs a controllable identifier this
  project has not discovered yet, and most modules gate service 0x2F behind
  security access whose key algorithm is not public. The screen says which it
  has for *your* car rather than offering a button that does nothing.
- **Coverage beyond one vehicle.** The whole strategy is in doc/ROADMAP.md: a
  full identifier sweep is about seventy minutes per car, but only once per
  model if the result is shared.

## Finding what Honda does not document

Generic OBD-II will not give you IMA state of charge, assist/regen current, or
battery temperature — those live behind manufacturer services, and no
off-the-shelf tool ships a Honda custom-PID set.

    omacar prospect                              sweep service 0x21 (Honda)
    omacar prospect --service 0x22 --range 0000-01FF
    omacar profile                               what has been learned so far

The method: sweep candidate headers against a read-only service, keep whatever
answers, then **re-sample every responder several times and diff the payloads**.
Bytes that never change are almost certainly not the reading you want; variance
is the signal. The output is a draft profile of candidates.

A responder is evidence that an address exists, not knowledge of what it means.
Every drafted entry is `confidence = "candidate"`, and **an unvalidated
candidate must never drive a gauge**. Name it, write its formula, check it
against something you can see — the dash's own SoC bars, or a second tool —
then mark it validated.

### Where the Honda data actually was

The DID sweep above came up empty on this car, and the null is worth recording
because it is a real result rather than a broken tool: **8192 identifiers across
both hybrid modules returned nothing**, on ECUs that answered `22F181` normally
immediately afterwards. They were powered and talking; they simply have nothing
mapped in that range.

Service **0x19 (ReadDTCInformation)** is where the manufacturer content lives,
and no amount of DID sweeping could have found it — 0x19 takes a *subfunction*,
not an identifier. It is a different question to the ECU.

    omacar dtc --parked        every 0x19 subfunction, on every module

Subfunction `0x0A` asks a module to enumerate every fault code it can ever set.
On the test car that returned **49 Honda hybrid-specific codes** — battery cell
faults, cooling fan, current sensors, inverter temperatures. That catalogue is a
map of what the module *measures*.

Try `0x19` before committing hours to `0x22`. It costs about twenty requests.

## Reading, writing, and what is refused

OmaCar reads by default and can write when you arm it. A tool that cannot clear
a code after you have fixed the fault is a viewer, not a diagnostic.

    omacar write status | arm | disarm | list
    omacar learn [--deep]      discover this car's modules and what they monitor
    omacar dtclog              log DTC status through a whole drive

Write mode covers clearing codes (0x04, 0x14), functional tests (0x2F), routines
(0x31), settings (0x2E) and the session/security services those need (0x10,
0x27). It **disarms itself after fifteen minutes**, and every operation states
what it does to the car before anything is sent.

### Safety, enforced rather than documented

- **Enforced at the transport.** `lib/elm.py` decides what may be emitted, so
  nothing further up can route around it. Reads always pass; writes pass only
  while armed.
- **Reprogramming is absent, not disabled.** Services 0x34/0x36/0x37 cannot be
  sent however armed the tool is. They need a manufacturer-signed firmware image
  this tool cannot produce, and a partial transfer leaves a module unable to
  boot. That is a tow truck, not a fault code.
- **Clearing does not happen locally.** The clear request goes to the car first;
  our own records are updated only if a module accepts it. (An earlier version
  got this wrong in the worst way — it updated the database and reported success
  without sending anything, so the codes vanished from the screen while the lamp
  stayed lit.)
- **It refuses to sweep or write to a moving car.** Road speed is checked first;
  if it cannot be read, it stops and asks you to confirm with `--parked` rather
  than assuming.
- **Voltage floors.** Reads stop below 11.8 V, writes below 12.2 V. A write
  interrupted by a brownout leaves a module holding half a change. This exists
  because a 25-minute key-on session tripped an ABS warning with nothing
  watching the battery.
- **Routine identifiers are never discovered by sweeping.** Unlike readable
  values, a routine is a procedure the module *runs* — guessing its number could
  spin a fan, cycle an ABS pump or retract a parking brake. There is no harmless
  miss, so `share/data/resets.json` ships only what somebody has confirmed on a
  real car, and the app says "nothing verified for this vehicle" rather than
  offering guesses. See `doc/RESETS.md`.
- **It refuses to fight the daemon** for the serial port.

## Verify shader changes on the real display

The ring's fragment shader declares `precision highp float`. At `mediump` the
hash collapses on this machine's integrated GPU and the ring renders black —
and **software/headless GL renders it correctly, which hides the bug**. The
headless screenshots used during development therefore cannot prove the shader
is right; open it on the actual monitor after touching the GLSL.

## Two safety rules, baked in from the start

**Clearing a code also clears the readiness monitors**, and a car with unset
monitors fails emissions until it completes a drive cycle. Any clear action
says that before it fires.

**IMA faults touch a high-voltage system.** Read and log them; do not offer
one-click clearing of hybrid codes the way you would for a loose gas cap.

Scaffolded from [omarchy-app-template](../omarchy-app-template).
