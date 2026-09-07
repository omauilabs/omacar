# The first drive

The Surface, the OBDLink EX, the CR-Z, and then the Fit. Every line below is
either a command the tablet runs or a thing to look at, in the order they
happen. Nothing here needs the Surface to be on the tailnet.

Two facts frame the day. The CR-Z speaks 29-bit CAN and has been read before
by this software, on another machine, so its record exists and can be carried
over. The Fit speaks 11-bit CAN and has never been read by anything here; it
will get its own record the moment its VIN arrives, and be named from the
free government decoder a few seconds later if the hotspot is up.

## At the kitchen table, before the car

On the Surface, with Wi-Fi:

    git clone https://github.com/omauilabs/omacar ~/Projects/omacar
    cd ~/Projects/omacar
    ./install.sh
    omacar setup

`omacar setup` builds the Python environment; it needs the network once and
takes a minute. Then:

    omacar doctor

With nothing plugged in it says so and exits 1. That is the correct answer.

The serial port is group `uucp` on Arch. Check once, and log out and back in
if it has to change:

    groups | grep -q uucp || sudo usermod -aG uucp "$USER"

Optional but worth the one sudo, because it gives the adapter a stable
`/dev/obd` and starts the daemon the moment it is plugged in:

    omacar hotplug install

Prove the whole stack without a car:

    omacar bench start actuator
    omacar live RPM SPEED
    omacar bench stop

If the CR-Z's history should be on the tablet today, carry its record over
from the desktop. The record is one file per VIN, and the profile beside it:

    # on the desktop
    scp ~/.local/state/omacar/vehicles/JHMZF1D44FS001835.db \
        ~/.local/state/omacar/profiles/honda-crz-2015.toml  omacar:/tmp/
    # on the Surface
    mkdir -p ~/.local/state/omacar/vehicles ~/.local/state/omacar/profiles
    mv /tmp/JHMZF1D44FS001835.db ~/.local/state/omacar/vehicles/
    mv /tmp/honda-crz-2015.toml ~/.local/state/omacar/profiles/

The daemon opens that file by VIN when the car answers, so nothing has to be
"imported". Skip this and the CR-Z simply starts a fresh record today.

## In the CR-Z

1. Plug the OBDLink EX into the port and the tablet. Ignition on; engine
   running is better for voltage.
2. `omacar doctor` — expect the adapter named, **ISO 15765-4 (CAN 29/500)**,
   and the VIN. If it says the port is not readable, it prints the group
   command; that is the `uucp` step above.
3. `omacar` opens the app. If the daemon is not already running from the udev
   rule, the app has a Connect button and `omacar daemon start` does the
   same. Within about fifteen seconds the vehicle bar shows the car and the
   gauges move.
4. Wait for the first survey — a minute — then `omacar status` and the Faults
   screen. The CR-Z has had zero stored faults; that is what to expect.
5. Writes need arming and the tier:

       omacar write arm
       omacar mode technician

   Both decay on their own: the arm after fifteen minutes, technician stays.
6. **The Tests lab** will say *None of these reach this car yet*, per button.
   That is correct and expected. Actuator identifiers are manufacturer-
   specific and nobody has validated one on this model. The path behind the
   buttons is real and was proven on the bench this morning; what is missing
   is the identifier, and finding one is a car-session job, not a code job.
7. Optional, if there is time and the engine is off:

       omacar learn          which modules answer (about a minute)
       omacar dtc            the 0x19 fault catalogue per module

8. Drive. Drive mode takes over on its own when the car moves, if that is
   set (`omacar tablet` shows it). Everything is filed under the VIN.

## In the Fit

1. Unplug, walk over, plug in, ignition on. The daemon notices the port go
   and come back, re-reads the VIN, and prints *a car we have not seen before*
   in its log. The garage switches by itself; `omacar vehicle` lists both.
2. `omacar doctor` — expect **ISO 15765-4 (CAN 11/500)**.
3. With the hotspot up, the vehicle bar reads *2012 Honda Fit* within a few
   seconds of the first survey; the model comes from NHTSA and the record
   says so. Without a network it reads *2012 Honda* and the model can be
   typed on the garage screen; a typed name is never overwritten.
4. `omacar learn` — this is the 11-bit path that used to crash on the first
   address and now does not. Worth running for that reason alone.
5. The Tests lab says the same thing it said in the CR-Z, for the same
   reason. The OBDb signal set for the Fit is empty, so no learned readings
   appear either; every generic reading does.

## If something is wrong

    omacar doctor                       the adapter, the protocol, the VIN
    omacar daemon status                is one running, on which port
    omacar status                       what the watchdog knows
    journalctl --user -u omacar-daemon  when the udev rule started it
    cat ~/.local/state/omacar/daemon.log

The daemon never writes to the car. The only things that do are behind
`omacar write arm`, and every one of them leaves a line in
`omacar write log`.

## What today is for

- The adapter enumerates on the Surface and the app shows a live value.
- The port lease survives a daemon restart (`omacar daemon stop`, then
  `start`, with the app open).
- The CR-Z's record carries over and its survey matches what it always said.
- The Fit gets a record, a name, and a completed `learn`.
- Anything the screen says that is not true gets written down, verbatim.
