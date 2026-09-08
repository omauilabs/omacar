# The tablet in the car

Running OmaCar natively — Omarchy on x86, the adapter in the port, no second
computer anywhere. This is the setup notes for that, and the honest list of
what is still missing.

## What to buy

It has to be **x86**. Omarchy is Arch and Hyprland; ARM Android tablets have
locked bootloaders and no mainline kernel, and no amount of wanting changes
that. That rules out every cheap Samsung and Lenovo tablet on the shelf.

The thing that decides this is not the processor. **It is heat.** A parked car
interior reaches 70 °C in summer, which is past the safe storage range of a
consumer lithium battery. A Surface left on a dashboard in July is a swollen
battery and, occasionally, worse. So the real question is whether the device is
rated for a vehicle, and only one category is.

### Rated for a vehicle

| | |
|---|---|
| **Dell Latitude 7230 Rugged Extreme** (or a used 7220) | 11.6", 1000 nit sunlight-readable, glove and wet touch, **−29 °C to 63 °C**, IP65, MIL-STD-810H. Dell sells a proper vehicle dock with 12 V passthrough. Plain Intel, so Linux is uneventful. This is the correct answer. |
| **Panasonic Toughbook FZ-G2** (or a used FZ-G1) | 10.1", same category. Vehicle docking is Panasonic's whole business and the mounts are everywhere. A used FZ-G1 is the cheap way into this class. |
| **Getac F110 / UX10** | Same category again, worth checking against the other two on price. |

The reason these are worth it is not the drop rating. It is that **you can run
them with the battery out**, on the vehicle dock's 12 V, which removes the heat
hazard completely and lets the tablet live in the car permanently. That is the
whole trick.

### Cheap, with a condition

**Microsoft Surface Go 3 or Go 4** — around a couple of hundred used, 10.5",
genuinely nice hardware. It needs the [`linux-surface`](https://github.com/linux-surface/linux-surface)
kernel for touch, sensors and cameras; the project is mature and has an Arch
repository. It is **not** rated for a car, so it comes inside with you when you
park. If that is a routine you will actually keep, it is a lot of tablet for
the money.

### Power

A car's 12 V collapses during cranking, and a tablet that browns out every time
you start the engine is one you will unplug within a week. Use a DC-DC supply
with ignition sense and a delayed shutdown — a Mini-Box M4-ATX, or the vehicle
adapter that came with the rugged dock. A phone charger in the lighter socket
is not this.

### Cellular

You do not need it for OmaCar. Everything except the advisor is local: the
loopback server, the database, the whole app. Only `omacar ai` reaches the
network, and tethering from a phone covers it.

If you want it built in, prefer a **USB LTE modem** over an embedded WWAN card:
ModemManager handles them well, and Verizon in particular certifies devices by
IMEI, so an uncertified embedded card may simply refuse to activate while a
carrier-blessed hotspot or a phone hotspot will not.

## Setting it up

    omacar tablet setup

Watchdog at login, fullscreen drive mode at login, daemon started when the
adapter is plugged in, and the app switching to drive mode by itself when the
car connects. `omacar tablet` shows the state; `omacar tablet off` reverses it.

Wire the adapter rather than using Bluetooth. A USB cable from the OBD port to
the tablet is one fewer thing to drop out on a bad road, and the udev rule
gives it a stable `/dev/obd` so it does not matter what else is plugged in.

## Sleep, which is the one that looks like a broken tablet

A tablet on a dash mount has no lid position that means "I am finished", and the
power button is the only control a driver can reach. Both of them suspend the
machine by default, and systemd will suspend it on idle as well.

**When that happens in a car it does not look like sleep.** It looks like a dead
tablet. The screen flashes when the power button is pressed and will not come
up, the network has been gone for ten minutes, and whatever was recording was
not recording. That is what it did on the way home from work on 8 September,
during a drive that was supposed to be capturing all three drive modes.

`share/js/awake.js` holds a wake lock so the dashboard does not blank while the
car is moving. That is the right fix for a screensaver and it does nothing about
this: a wake lock cannot stop the power button.

```
omacar tablet awake
```

It ignores the power button, the suspend key and the lid, and masks the four
sleep targets, so nothing suspends the machine. One sudo, and reversible with
`omacar tablet sleep`. `omacar tablet` reports which state it is in, because a
machine that will suspend gives no warning until it does.

**Do not do this to a laptop you carry around.** It will stay awake in a bag and
flatten its battery. It is for the machine that lives in the car.

**If it is already asleep and will not wake:** hold the power button for ten to
twenty seconds until it powers off, then press it again. There is no software
route back in — that is the point of the problem.

## What Omarchy does not give you yet on a tablet

Being straight about the gaps, because they are the difference between this
working and nearly working:

* **No on-screen keyboard.** Hyprland does not ship one. `wvkbd` works and is
  small. You need it the first time you have to type a Wi-Fi password with no
  keyboard attached.
* **No autologin.** Boot goes to a login prompt. `greetd` with
  `initial_session` gets you from power-on to drive mode with nothing to type,
  which is what you want in a car.
* **Rotation and touch gestures** need configuring by hand — `hyprctl` monitor
  transform, and libinput settings for tap and drag.
* **Screen brightness** at night. There is no ambient sensor path wired up;
  `omarchy brightness` from the menu is the manual answer.

None of these are OmaCar's to fix, but all of them are yours to hit on day one.
