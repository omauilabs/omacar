# The tablet's battery, in a hot car

The Surface Pro 7+ lives on the dash and is charged by the car. A lithium cell
that is kept full and warm ages fastest, and a parked car is both. This is what
Linux can and cannot do about that on this machine, checked 2026-10-17.

## What Linux cannot do

**It cannot limit the charge.** There is no sysfs switch for it.

- The Surface Pro 7 generation reports its battery through the Surface
  Aggregator Module (SSAM), not ACPI, using the `surface_battery` driver
  (`drivers/power/supply/surface_battery.c`, in the mainline kernel, so stock
  Arch `linux` has it; linux-surface's own kernels carry the same driver).
  Source: the driver's commit message, "power: supply: Add battery driver for
  Surface Aggregator Module" (Maximilian Luz, 2021), and the linux-surface wiki
  page for the Surface Pro 7.
- That driver exposes: status, present, technology, cycle_count,
  voltage_min_design, voltage_now, capacity, capacity_level, model_name,
  manufacturer, serial_number, and either energy_{full,full_design,now} +
  power_now or charge_{full,full_design,now} + current_now. Its one extra
  attribute is `alarm` (a low-battery trip point), which is not a charge limit.
- It does **not** expose `charge_control_end_threshold`, `charge_control_start_threshold`
  or `charge_behaviour` (the attributes ThinkPads, ASUS and Framework laptops
  use), and it has no `set_property`. Read from the source on master; not
  confirmed by running it on this tablet.
- It does **not** expose a battery temperature (`temp`). Where a battery has no
  temperature the screen says "Not reported". It never draws a zero.
- So no udev rule, no polkit rule and no sudo can make Linux stop charging at
  80 % here. Nothing was built that pretends otherwise.

## What can be done: Surface UEFI "Enable Battery Limit"

Microsoft documents a firmware setting that stops charging at **50 %** of the
maximum capacity, built into Surface Pro 7 and later, "recommended for devices
intended to be always connected to power". If you turn it on while above 50 %,
the battery does not charge again until it falls below 50 %.
Source: <https://learn.microsoft.com/surface/battery-limit>.

To turn it on, by hand, once:

1. Shut the tablet down. Hold Volume Up and press Power.
2. In Surface UEFI choose Boot configuration, then Advanced Options.
3. Turn **Enable Battery Limit** on. Save and exit.

Only the firmware can set it (or Microsoft's SEMM tooling, which needs Surface
for Business and Windows-side tools). Linux has no way to read or write that
variable, so **OmaCar cannot see whether it is on**.

Tradeoff to decide: with the limit on the tablet never holds more than half a
charge. In a car that supplies power whenever the engine runs that costs little;
away from the charger it gets half the runtime. `lib/ignition.py`'s battery
floor (10 %) is unaffected.

## What OmaCar does

`lib/tabletbatt.py` (read-only; it never writes the battery, the charger or
the car):

- **Health**: `energy_full / energy_full_design` (or `charge_*`) from
  `/sys/class/power_supply/BAT*`, one record a day in
  `$XDG_STATE_HOME/omacar/tabletbatt-history.json`. A least-squares line over
  the days projects when it reaches 80 %, the usual "worn" line. It refuses to
  project under 5 readings or 21 days, and calls anything under 90 days
  "rough": the pack's capacity estimate wanders by a point or two as it
  re-learns itself.
- **Heat**: above 45 C while charging, one calm sentence ("Hot: charging is hard
  on the battery right now") shown on the Tablet battery screen and, once an
  hour, as a quiet toast with no sound. The 45 C line is the usual guidance
  for lithium-ion charging, not a Microsoft figure. Because the driver reports
  no temperature, on this tablet it will most likely read "Not reported" and the
  heat rule never fires. It is there for when a kernel or firmware reports one.
- **Does the limit look on?** Inference only. On the last 14 days, a day counts
  if the tablet was on the charger at least 90 samples (about 90 minutes). With at
  least 3 such days: highest charge on all of them at or under 55 % reads
  "Likely on"; any day at 70 % or more reads "Not active"; in between,
  "Unclear". Fewer days: "Can't tell yet". It can be fooled (a tablet that
  is only ever plugged in for short top-ups looks capped without being so, which
  is why short days are ignored).

## Using it

    systemctl --user enable --now omacar-tabletbatt.service   # samples once a minute
    omacar tabletbatt status                                  # what the app is shown
    omacar tabletbatt sim hot                                 # normal | hot | limited

Settings, then **Tablet battery**, opens the screen. Home shows nothing new.
Simulated data is labelled Simulated on screen, is kept in its own history
file, and never raises the heat toast.
