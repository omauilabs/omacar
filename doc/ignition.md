# The tablet follows the car

The owner's request of 2026-10-17: power on when the car starts, power down
30 seconds after the car is parked. Until now the tablet ran all the time and
drained its battery.

## The signal

The Surface Connect car charger plugs into an inverter that only has power
while the car is on. So the charger is the ignition:

| Charger | Meaning |
|---|---|
| online | the car is on |
| off for 30 s | the car is parked: sleep |
| back | the car started: wake |

`lib/ignition.py` reads `/sys/class/power_supply` once a second. Any `Mains`
or USB supply that is online counts as power. If it cannot read a charger at
all, it never starts a countdown.

## Why it can refuse to sleep

On 8 September 2026 a suspend mid-drive took the tablet down for the rest of
the trip (`lib/tablet.py` has the story). From the charger's side, a blown
inverter fuse or a cable knocked loose looks exactly like "car off". So the
car can veto the countdown:

| Hold | Why | On screen |
|---|---|---|
| any speed in the last 2 min | a charger fault on the road | "Charger lost while moving", no buttons |
| the car answering in the last 30 s, including while the recorder borrows the adapter | the ignition is still on | "Waiting for the car to switch off", no buttons |
| Stay on was tapped | someone is using it parked | "Staying on", with Sleep now |
| a child alert is running ([child-alert.md](child-alert.md)) | the car seat looked occupied at switch-off and nobody has answered | the child alert's own card; Sleep now is not offered and cannot pass it, and a low battery does not end it |

When a hold lifts, the countdown starts again from 30 s; it does not pick up
where it left off. Stay on ends when the charger comes back or the battery
reaches 10%. Neither tap can sleep a car that is moving or answering.
`test/ignition_test.py` checks every rule against a fake clock.

The countdown card makes no sound, because a child sleeps near the tablet at
night.

## What sleeping does

1. The camera recorder and the bus logger (`omacar-cams`,
   `omacar-drivelog`) are stopped, so their files are closed, not cut off.
2. logind is asked to suspend, through `lib/power.py`'s checks and its log
   (`~/.local/share/omacar/power.log`).
3. On waking with the charger back, the screen is switched on and the
   recorders that were running are started again. On waking without the
   charger (a stray wakeup), it counts down again and goes back to sleep.

Every cycle is written to `~/.local/state/omacar/ignition-cycles.jsonl` with
the time it slept, the time it woke, the charger state and the kernel's wake
IRQ. `omacar ignition log` prints them.

## Setting it up (once, on the tablet)

```
omacar ignition install
```

This needs sudo and does four things:

- Unmasks `suspend.target` and `sleep.target` only. `tablet awake` stays in
  force otherwise: logind still ignores the power key, the lid and idle, so
  the ignition loop is the only thing that asks for sleep.
- Installs the polkit rule from `omacar power allow`.
- Adds a udev rule that marks the Mains supply as a wakeup source where the
  kernel exposes one.
- Enables `omacar-ignition.service`.

`omacar ignition uninstall` reverses it.

## The open question: does the charger wake it?

Waking when the car starts depends on whether this Surface Pro 7+, on stock
Arch, treats Surface Connect power returning as a wakeup from suspend. This
has **not been measured**. Run `omacar ignition probe` to see what the machine
offers: the charger's wakeup flag, `mem_sleep` (s2idle or deep), ACPI wake
devices, and swap for hibernate.

### The driveway test

1. Car running, tablet on the charger. Run `omacar ignition status`. It
   should show `phase on`.
2. Switch the car off. The card counts down from 30 and the tablet sleeps.
3. Wait two minutes, then start the car.
   - **It wakes by itself:** done. `omacar ignition log` shows the cycle and
     the wake IRQ.
   - **It stays asleep:** press the power button. Then `omacar ignition log`
     shows the cycle with `charger on`. The fallbacks are below.
4. Turn the ignition off while the engine is running a stationary check (for
   example, pull the inverter's plug with the car on). The card should say
   "Waiting for the car to switch off" and not sleep.

### If the charger does not wake it

These are in order of preference. None has been built yet.

- **Enable the xHCI wakeup and plug a USB device into the switched
  inverter**, such as a powered hub. USB wake from s2idle is widely
  supported; the hub appearing when the car starts would be the wake event.
- **Hibernate after a long park** (`"action": "suspend-then-hibernate"` in
  `~/.config/omacar/ignition.json`, which needs swap and a resume hook). This
  saves the battery over a long park, but the power button is then needed
  to come back.

## Settings

`~/.config/omacar/ignition.json` (every key is optional):

```json
{ "enabled": true, "grace_s": 30, "action": "suspend", "battery_floor": 10 }
```

`action` is one of `suspend`, `hibernate`, `suspend-then-hibernate` or
`poweroff`. `grace_s` is clamped to between 5 and 600 seconds.
