# Cameras, and how the box talks to the tablet

The plan, decided. The research behind every number is in
[doc/research/cameras.md](research/cameras.md), including the parts the
fact-checker could not confirm.

## The one decision everything else follows from

**The tablet never sees a camera.** It sees one video stream and a directory of
clips, both over ethernet, from a box that does all the work.

That is not a preference. The Surface has one USB-A port and one USB-C port,
and USB cameras reserve bus bandwidth whether or not they are sending anything —
three 1080p streams on one bus is already the ceiling, and the failure is not
graceful. It is `VIDIOC_STREAMON: No space left on device`, or cameras that
simply refuse to open. Meanwhile the tablet is already holding a serial adapter,
hosting a CarPlay video stream and running the diagnostics.

Let the cameras compress, and the whole system drops from gigabits to tens of
megabits:

| | one stream | four |
|---|---|---|
| Raw off a USB camera | 995 Mbps | 3.98 Gbps |
| H.264 from a PoE camera | 4–8 Mbps | 16–32 Mbps |
| A 640×360 substream for the AI | 0.3–0.8 Mbps | 1.2–3.2 Mbps |

Under 35 Mbps for everything. A hundred-megabit link would carry it.

## The box

**A fanless Intel N100 mini PC with a 12 V input.** Not a Raspberry Pi 5 — the
Pi 5 has no hardware video encoder at all, which makes it the worst available
choice for a project whose entire premise is that video must not touch the CPU.
The Pi 4 has one, and it manages about 1080p30 across the whole chip, which is
one camera.

The N100 wins on software rather than on silicon. An RK3588 board encodes more
and has a 6 TOPS neural engine on-die, and it will cost you a weekend on a
vendor kernel. The N100 runs ordinary Linux, does H.264 and HEVC in Quick Sync,
and takes an M.2 accelerator later if the AI work outgrows the iGPU.

**Check two things before buying**, because neither is universal on these:
a real 12 V DC barrel input, and a free M.2 slot.

## How it connects to the tablet

**One cable: USB-C to 2.5 GbE on the Surface, short Cat6 to the box.** That is
about thirty times the headroom the traffic needs, and it leaves the tablet's
USB-A port free for the OBD adapter and the CarPlay dongle on the hub.

Wi-Fi will work for a single live view and is the wrong medium for a metal box
on the move. Keep it as the fallback, not the plan.

Three separate paths run over that one wire, and they are different on purpose:

**Live view — one stream, not four.** The box composites a 2×2 mosaic and emits
a single 1080p stream over WebRTC. That is one hardware encode on the box and
one hardware decode on the tablet. Asking the Surface to decode four streams
would undo the entire point of having a box.

**Recording — the tablet is not involved.** The box stream-copies each camera's
RTSP straight to disk with no decode and no encode, which costs about one per
cent of a core. At 24 Mbps that is 10.8 GB an hour, so a 1 TB SSD holds roughly
four days before it loops.

**Clip review — a file share.** The archive is exported over NFS or SMB so
OmaCar can browse and scrub saved events. Sharing storage is right for review
and wrong for live, which is why it is a third path and not the only one.

> A note if you ever reconsider the board: an N100 mini PC cannot present
> itself as a USB device, so USB-gadget networking is not available to it. An
> RK3588 or a Pi can do `usb-ncm` over its OTG port and give you a virtual
> ethernet link on a single USB cable, which is genuinely tidier. It is the one
> real argument for the SBC.

## Cameras

**PoE, and it is not close.** One cable per camera carrying power and video,
proper outdoor housings, and H.264 done in the camera. USB cameras put the
bandwidth problem back on the host, and long USB runs in a car are their own
kind of misery.

Front and rear exterior, plus one cabin camera. The cabin one can be USB with a
short run into the box, since a single stream is not the problem.

## Power, which is where car projects die

Automotive 12 V rests at 11.8 V, charges at 14.6 V, and drops to 6–9 V while
the starter turns. Never hang a generic buck converter off it.

Use an ignition-aware supply — the Mini-Box DCDC-USB-200 is the right size for
a mini PC — wired so accessory power drives its ignition sense. On ignition-off
it holds power for a set delay, sends a shutdown pulse, and hard-cuts after a
timeout. A systemd unit on the box watches that line, closes the current
segment, flushes it to disk and powers off.

**The detail that actually saves your footage:** record in sixty-second
segments, into MKV or fragmented MP4, never plain MP4. A standard MP4 writes
its index at the end of the file, so a hard power cut leaves an unplayable
recording. Segments plus a streamable container mean the worst case is losing
one minute rather than the drive home.

## What OmaCar has to build

Nothing on the tablet decodes video that OmaCar does not already decode. The
phone screen already reads an H.264 stream and puts it on a canvas, through two
decoder routes, with a fallback for browsers whose WebCodecs does nothing. A
camera view is that same pipeline pointed at a different source.

In order:

1. **A camera source** beside `dongleSource` in `share/js/omaplay/source.js`,
   reading the box's mosaic stream. The decoder underneath it is already built
   and already tested.
2. **A Cameras view**, which is a canvas and a clip list.
3. **The clip list**, reading the file share, with the segments' own timestamps.
4. **Events**, which is where the AI work starts: the box runs detection on the
   low-resolution substreams and writes an event record; OmaCar reads that list
   the way it already reads faults, and correlates it with what the car was
   doing at that second — which is the thing no dashcam can do, because no
   dashcam has the bus.

Step 4 is the reason for all of this. Everything before it is plumbing that
exists to make it possible.

## Order to buy and build

1. Box, SSD, and the 2.5 GbE adapter. Prove the link and a single RTSP stream
   on a desk before anything goes in a car.
2. One PoE camera and the switch. Prove stream-copy recording and segment
   rotation.
3. The ignition-aware supply and the shutdown unit. Prove a clean power cycle
   fifty times on a bench before trusting it to a car.
4. The remaining cameras.
5. The OmaCar side, in the order above.

Roughly $585–935 for the core build, though every price in the research note is
marked unconfirmed and worth re-checking on the day.
