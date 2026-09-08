# The CarPlay session

For the morning the Carlinkit adapter goes in. Written 8 September 2026, after
a night of building the path it exercises.

**What is already proven, without hardware.** The framing, the HTTP stream,
both decoders and the canvas. A recording travels the identical path — same
records, same route, same decoder, same pixels — and `test/phone_test.py` runs
it in a real browser on every test run. So if the screen is black tomorrow,
the adapter driver is the only new thing in the picture, and everything else
has an alibi.

**What has never been tried.** `lib/carlink.py` talking to a real adapter.
Every offset in it is sourced from a reading of the protocol; none of it has
been confirmed by a device. Expect the first attempt to fail somewhere, and
expect the failure to be legible, because the whole file is arranged to say
where.

---

## Before you leave the desk

On the Surface, which needs to be on the network for this part:

```
cd ~/Projects/omacar && git pull
omacar hotplug install          # the udev rule; the adapter is unreadable without it
omacar phone                    # should say: rule installed, driver unproven
```

Then plug the adapter in and run `omacar phone` again. Three lines, and you
want the first two green:

```
dongle       Carlinkit (CPC200 family)
permission   the browser can open it
driver       written, never run against an adapter
```

If permission is red, the rule went in after the adapter did. Unplug it, plug
it back in, ask again. `uaccess` is granted at plug time and not retroactively.

**Prove the screen before you prove the adapter.** `omacar phone replay` makes
a recording if there is not one, plays it through the real path, and prints the
URL. Open the Phone screen. You should see a test pattern behind an amber frame
reading REPLAY — a recording, not a phone. If you see that, everything except
the adapter works on this machine, which is the whole point of doing it in this
order.

Note which decoder it used. The status line says so when it is the fallback:
*decoding through the video element, because WebCodecs would not*. Either is a
picture; the fallback costs a little latency and some warmth.

---

## In the car

1. Adapter into the tablet, phone into the adapter, engine on.
2. Open the Phone screen. It starts by itself — the app asks the server what is
   plugged in and picks the adapter over a recording over the mock.
3. The badge tells you where you are:

   | Badge | Meaning |
   |---|---|
   | *UNPROVEN — no picture yet* | The adapter is open and the driver has not yet produced a frame. Expected for the first few seconds. |
   | nothing | A frame decoded. The driver is proven, and the badge takes itself off. |
   | *REPLAY — a recording, not a phone* | You are looking at the canned clip. Not the adapter. |
   | *THIS BROWSER CANNOT DECODE H.264* | The frames arrived and neither decoder would show them. Not the adapter's fault, and the line under it says how many frames. |

4. Touch it. Touches go back as fractions of the screen, so if every tap lands
   in the bottom-right corner, that is the scaling and not the touchscreen.

---

## If it stays black

Ask `omacar phone` and then the status route, in that order:

```
omacar phone
curl -s 127.0.0.1:7560/api/phone | python3 -m json.tool
```

`frames` and `units` are the two numbers that matter. They separate four
failures that look identical on screen:

| frames | units | What it is |
|---|---|---|
| 0 | 0 | The adapter is not sending video. Check `phone` in the status — it names what plugged in. If it is null, the handshake did not take. |
| >0 | 0 | Video messages are arriving and no whole picture has been cut from them. The access-unit boundary rule is wrong for this adapter. |
| >0 | >0 | Pictures are leaving the server. The problem is in the browser, and the screen will say which decoder gave up. |

**The thing most likely to burn the morning** is the USB reset. The adapter has
to be port-reset before it will stream, and it disappears from the bus for one
to three seconds afterwards and comes back as a different device number. The
driver does this and waits; if it reports *the adapter did not come back after
its reset*, unplug it and start again rather than retrying in place.

**Three fields to print if you get that far.** The video sub-header carries
`flags`, a declared `length` and one field with no name, and every
implementation parses all three and reads none. The first `geometry` event
reports them next to the number of bytes actually carried. Write down what they
hold — it is the only chance to learn what they mean, and it costs nothing.

---

## What to bring home

- Whether a picture appeared at all, and how long after plugging in.
- Which decoder carried it.
- The `geometry` event's four numbers.
- Whether touch landed where you touched.
- Anything `omacar phone` said that was not true.
