// OmaCar — the menu bar wheel and its panel.
//
// The dock card answers "how is the car doing" in three numbers. This answers
// the rest of it: what the engine is doing this second, how the driving has
// gone by day, week, month and year, every code the ECU is holding and when it
// last set it, and the whole service book with what is next due.
//
// Two clocks, deliberately. The live sample is read straight from OmaCar's
// `live.json` once a second and only while the panel is open — it is one small
// file and nothing else is watching it that fast. Everything else comes from
// the rollup cache the dock card already reads, on a much slower timer,
// because a year of driving does not change between blinks.
//
// Units are the cache's business, not this file's: OBD-II is metric on the
// wire, so the record stays metric and carries a units block saying how to
// show it. Every conversion in this panel goes through the helpers below and
// nowhere else.

import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "omacar"
  ipcTarget: "omacar"
  manageIpc: true

  // The bar sizes a widget from these. Without them it is allotted zero width
  // and simply never appears — no error, just an invisible button.
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property string home: Quickshell.env("HOME")

  // DEMO MODE, AND WHY THE REAL CAR STILL WINS.
  //
  // `omacar demo start` gives itself a whole separate state and config tree by
  // redirecting XDG_STATE_HOME and XDG_CONFIG_HOME. This panel cannot be given
  // those: it is QML inside omarchy-shell, which has the desktop's environment,
  // and the only way to change it would be restarting the shell -- redirecting
  // every other plugin along with it.
  //
  // So the panel is not moved, it is POINTED. While the marker file exists it
  // reads the demo's files and wears a DEMO badge; the moment it is gone it is
  // back on your car, with no restart either way and not one real file written.
  //
  // And the demo never wins over a car that is actually there. The real
  // live.json is read on every tick regardless, and the instant the adapter is
  // plugged in and answering, the bar shows YOUR car -- because the one thing a
  // demo must never do is stand between you and the vehicle in front of you.
  readonly property string demoState: home + "/.local/state/omacar-demo/state"
  readonly property string demoMarkerFile: home + "/.local/state/omacar-demo/ACTIVE"
  property bool demoMarker: false
  readonly property bool demoing: root.demoMarker && !root.realLive

  readonly property string stateRoot: root.demoing ? demoState : home + "/.local/state"
  readonly property string cache: stateRoot + "/omarchy/liquid-glass-car.json"
  // ALWAYS the real one. This is how the panel knows to come back.
  readonly property string liveFile: home + "/.local/state/omacar/live.json"
  readonly property string demoLiveFile: demoState + "/omacar/live.json"
  readonly property string alertFile: stateRoot + "/omacar/alerts.json"
  readonly property string dismissFile: stateRoot + "/omacar/dismissed.json"

  // Omarchy's caption token is 10px and its body 12px, sized for a bar where a
  // label is glanced at. This panel is read, so everything scales off those
  // tokens rather than hardcoding — it still follows the theme, it just stops
  // assuming you are hunting for the number.
  property real textScale: 1.12
  readonly property int fMicro:   Math.round(Style.font.caption * textScale)
  readonly property int fCaption: Math.round(Style.font.bodySmall * textScale)
  readonly property int fBody:    Math.round(Style.font.body * textScale)
  readonly property int fTitle:   Math.round(Style.font.title * textScale)
  readonly property int fStat:    Math.round(Style.font.heading * textScale * 1.15)
  readonly property int fHero:    Math.round(Style.font.displayLarge * textScale)

  // White, deliberately, not bar.foreground: see ink() below.
  readonly property color fg: "#FFFFFF"
  function dim(a) { return Qt.rgba(fg.r, fg.g, fg.b, a) }

  // TEXT IS WHITE, AND BARELY DIMMED. THIS IS READ IN A CAR.
  //
  // dim() is the panel's one colour helper and it does two different jobs:
  // it tints backgrounds and borders, where a low alpha is exactly right, and
  // it tints TEXT, where a low alpha is why labels vanished in sunlight. They
  // cannot share a scale, so text gets its own.
  //
  // White rather than the theme foreground: this theme's ink is #a9b1d6, a
  // blue-grey that reads fine on a desk and washes out through a windscreen.
  // The floor keeps hierarchy -- a caption is still quieter than a heading --
  // without letting anything fall to where it cannot be read at a glance.
  function ink(a) { return Qt.rgba(1, 1, 1, Math.max(a, 0.80)) }

  readonly property color cGreen:  "#30D158"
  readonly property color cAmber:  "#FF9F0A"
  readonly property color cRed:    "#FF453A"
  readonly property color cBlue:   "#0A84FF"
  // The notification card: dark blue with light text, fixed rather than
  // theme-derived. These cards carry the one thing in the panel you must be
  // able to read at a glance in a moving car, so they keep their own contrast
  // instead of inheriting whatever a night theme has done to the foreground.
  readonly property color cCard:      "#12294A"
  readonly property color cCardHover: "#193762"
  readonly property color cCardInk:   "#EAF2FF"
  readonly property color cCardSub:   "#9FB8D8"
  readonly property color cCyan:   "#64D2FF"

  property string tab: "now"
  readonly property var tabs: [
    { "id": "now",     "label": "Now" },
    { "id": "drive",   "label": "Drive" },
    { "id": "health",  "label": "Health" },
    { "id": "service", "label": "Service" }
  ]

  // ---- state ---------------------------------------------------------------
  property var car: ({})
  // What the real adapter last said, whatever the panel is currently showing.
  property var realSample: ({})
  property var demoSample: ({})
  readonly property var sample: root.demoing ? root.demoSample : root.realSample
  readonly property bool realLive: (realSample.t || 0) > 0
    && (root.nowSec - realSample.t) <= root.liveStale
    && (realSample.connected === true || realSample.status === "yielded")
  // What the watchdog has raised. Read from its own small file rather than the
  // rollup cache, because an alert has to reach the bar the moment it happens
  // and the rollup runs on a slow timer.
  property var alerts: ({})
  property real nowSec: Date.now() / 1000

  readonly property var vehicle: car.vehicle || ({})
  readonly property var perf: car.perf || null
  readonly property var svc: car.service || null
  readonly property var units: car.units || ({
    "system": "imperial", "dist": "mi", "speed": "mph", "econ": "mpg",
    "vol": "gal", "temp": "°F", "km": 0.621371, "litre": 0.264172,
    "econ_better": "high"
  })

  // The live sample when the panel has one of its own, the cache's copy
  // otherwise — so the panel is right the instant it opens rather than after
  // its first tick.
  readonly property var live: {
    var v = (sample.values || null)
    if (!v) return car.live || ({})
    return {
      "rpm": v.RPM, "speed": v.SPEED, "coolant": v.COOLANT_TEMP,
      "intake": v.INTAKE_TEMP, "ambient": v.AMBIANT_AIR_TEMP,
      "fuel_pct": v.FUEL_LEVEL, "volts": v.CONTROL_MODULE_VOLTAGE,
      "load": v.ENGINE_LOAD, "throttle": v.THROTTLE_POS,
      "ltft": v.LONG_FUEL_TRIM_1, "stft": v.SHORT_FUEL_TRIM_1,
      "timing": v.TIMING_ADVANCE, "run_time": v.RUN_TIME,
      "lphk": sample.economy_lphk, "lph": sample.fuel_lph,
      "basis": sample.efficiency_basis, "protocol": sample.protocol,
      "adapter": sample.kind, "port": sample.port, "trip": sample.trip
    }
  }
  // How old the last published sample may be before it stops counting as live.
  // Must track records.LIVE_STALE in lib/records.py -- the daemon publishes
  // five times a second, so anything past a few seconds means whoever was
  // writing has stopped.
  readonly property int liveStale: 15

  // A FILE ON DISK DOES NOT KNOW THE PROCESS WRITING IT HAS DIED.
  //
  // live.json is written while the daemon is connected and simply left behind
  // when it is not. Unplugging the adapter and killing the daemon leaves
  // {"connected": true, "port": "/dev/ttyUSB0"} on disk forever, and this
  // panel read that as gospel: green bar icon, a Stop button, a "parked"
  // state, all from a sample that was in one observed case 8.6 hours old with
  // no /dev/ttyUSB0 on the machine at all.
  //
  // lib/records.py has had this guard since the beginning; the bug was that
  // the panel reads live.json directly with `cat` and so never went through
  // it. Duplicating the read meant duplicating the rule.
  readonly property bool liveFresh: (sample.t || 0) > 0
    && (root.nowSec - sample.t) <= root.liveStale

  // A HAND-OFF IS NOT A DISCONNECTION.
  //
  // Every few minutes the DTC sweep borrows the serial port, and the daemon
  // steps aside and publishes connected=false with status="yielded" until it
  // has it back. The cable never moved. Read literally, that turned the bar
  // icon grey and swapped Stop for Connect for a few seconds out of every
  // five minutes, all drive long -- the flicker that made the panel look like
  // it kept losing the car.
  //
  // Still gated on freshness: a yield that never ends is a stopped daemon, and
  // liveStale catches it on exactly the same schedule as a plain drop.
  readonly property bool handover: sample.status === "yielded"

  readonly property bool connected: sample.connected !== undefined
    ? ((sample.connected === true || root.handover) && root.liveFresh)
    : car.connected === true

  // How long ago the daemon last said anything, for the panel to own up with.
  readonly property int liveAge: (sample.t || 0) > 0 ? Math.max(0, Math.round(root.nowSec - sample.t)) : -1

  // When the last sample CLAIMED a link but is too old to believe, say which.
  // "offline" on its own cannot tell "nothing ever connected" from "the daemon
  // stopped hours ago and left its claim on disk", and those want different
  // things from the user.
  readonly property string staleNote: (sample.connected === true && !root.liveFresh && root.liveAge > 0) ? "daemon stopped " + root.since(root.liveAge) : ""
  // ONE THRESHOLD IS NOT ENOUGH TO SAY "DRIVING".
  //
  // A single 3 kph line means a car in traffic, or rolling up to a junction,
  // crosses it several times a minute -- and the state dot, the status line
  // and the tooltip all flip with it, once a second, while nothing about the
  // situation has actually changed. Flickering state is read as a broken
  // readout, not as a precise one.
  //
  // So the line moves depending on which side of it we are already on:
  // it takes 3 kph to be called driving and a drop below 1 kph to stop being
  // called it. Schmitt trigger, in other words, and the same trick a
  // speedometer needle uses to sit still at a standstill.
  //
  // Held in its own property and written from the sample rather than computed
  // inside the state_ binding, because a binding that reads and writes the
  // same property is a binding loop; this way the dependency runs one way,
  // live -> wasDriving -> state_.
  property bool wasDriving: false
  onLiveChanged: {
    var d = (root.live.speed || 0) > (root.wasDriving ? 1 : 3)
    if (d !== root.wasDriving) root.wasDriving = d
  }
  readonly property string state_: {
    if (!connected) return "offline"
    var s = live.speed || 0, r = live.rpm || 0
    if (s > (root.wasDriving ? 1 : 3)) return "driving"
    if (r > 200) return "idling"
    return "parked"
  }
  readonly property bool engineOn: state_ === "driving" || state_ === "idling"

  // ---- getting started -----------------------------------------------------
  //
  // Nothing in this panel could START OmaCar. If the daemon was not running
  // the panel simply said "offline" and left you to find the CLI -- which is
  // fine for whoever built it and useless for anyone else. So: a button, in
  // the one place it is obviously a button, and only while there is nothing
  // running to make it redundant.
  property bool daemonStarting: false
  property string startError: ""

  Process {
    id: startDaemon
    command: ["bash", "-lc", "omacar daemon start 2>&1"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.daemonStarting = false
        // The wrapper prints its failure rather than exiting loudly, so the
        // text is the only signal worth reading.
        //
        // It is read rather than pattern-matched down to one guess. The old
        // code mapped every failure onto "is the ignition on?", which was
        // wrong for the most common one by far -- the simulator holding the
        // port, which has nothing to do with the ignition and is fixed by a
        // different action. The wrapper already writes one good sentence per
        // cause, so the honest thing is to show it. Ignition stays as the
        // fallback for a silent failure, because a car that is not awake is
        // the reason a start times out with nothing to say.
        var t = (text || "").trim()
        var line = ""
        var lines = t.split("\n")
        for (var i = lines.length - 1; i >= 0; i--) {
          var candidate = lines[i].trim().replace(/^omacar:\s*/, "")
          if (candidate.length > 0) { line = candidate; break }
        }
        if (line.length === 0) {
          root.startError = ""
        } else if (t.indexOf("daemon running") >= 0) {
          root.startError = ""
        } else if (t.indexOf("did not start") >= 0 && lines.length <= 1) {
          root.startError = "could not start — is the ignition on?"
        } else {
          // Long enough to be a sentence, short enough for the panel's width.
          root.startError = line.length > 96 ? line.substring(0, 95) + "…" : line
        }
      }
    }
  }

  function startOmaCar() {
    if (root.daemonStarting) return
    root.startError = ""
    root.daemonStarting = true
    startDaemon.running = true
  }

  Process {
    id: stopDaemon
    // `omacar daemon stop`, NOT `systemctl --user stop`. The CLI signals the
    // daemon so its cleanup runs and live.json is marked disconnected; SIGTERM
    // from systemd skips that and leaves the panel showing the last readings
    // as though the car were still attached.
    command: ["bash", "-lc", "omacar daemon stop 2>&1"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: { root.daemonStopping = false }
    }
  }

  property bool daemonStopping: false

  function stopOmaCar() {
    if (root.daemonStopping) return
    root.daemonStopping = true
    stopDaemon.running = true
  }

  // ONE BUTTON FOR THE LINK, BECAUSE THERE IS ONLY EVER ONE THING TO DO TO IT.
  //
  // Connect and Stop were two pills, each hidden while the other applied, and
  // between them they could not say the third thing that actually happens:
  // the adapter is pulled or the daemon dies, live.json is left behind still
  // claiming a connection, and the panel goes back to offering "Connect" --
  // which is the right press but the wrong word, because it reads as "nothing
  // was ever plugged in" when the panel knows perfectly well that something
  // was. That state gets its own label now, and the pair became one control
  // in one place, which is also one less thing to find.
  //
  // IT SPEAKS FOR THE REAL DAEMON, NEVER THE DEMO. `connected` follows
  // whichever sample is being shown, so during a demo it went true and the
  // button read "Stop" -- over a demo that has no daemon to stop, next to a
  // Stop that would have killed the real one had it been running. `realLive`
  // is read from the real live.json on every tick regardless of the demo, so
  // the link control is about the cable and nothing else.
  readonly property int realAge: (realSample.t || 0) > 0
    ? Math.max(0, Math.round(root.nowSec - realSample.t)) : -1
  // A claim of a connection that is too old to believe: something was running
  // and is not any more. A clean `omacar daemon stop` does not land here --
  // it publishes connected=false, and that is a Connect, not a Reconnect.
  //
  // That sentence was written as fact before it was true. `omacar daemon stop`
  // sends SIGTERM, and Python terminates on SIGTERM WITHOUT unwinding, so the
  // daemon's `finally` never ran and connected=true was left on disk -- which
  // landed a deliberate shutdown here, in `dropped`, and offered "Reconnect"
  // for a daemon the owner had just stopped on purpose. lib/daemon.py now
  // installs a handler that raises KeyboardInterrupt so the cleanup runs, and
  // the sentence above is true because of that handler, not on its own.
  // Removing it re-breaks this button.
  //
  // "yielded" counts, because a daemon that dies during a DTC sweep leaves
  // exactly that behind and it was every bit as connected a moment earlier.
  readonly property bool realDropped: (realSample.connected === true
                                       || realSample.status === "yielded")
    && !root.realLive && root.realAge > 0

  readonly property string linkState: {
    if (root.daemonStopping) return "stopping"
    if (root.daemonStarting) return "connecting"
    if (root.realLive)       return "connected"
    if (root.realDropped)    return "dropped"
    return "idle"
  }
  readonly property bool linkBusy: root.linkState === "stopping"
                                || root.linkState === "connecting"
  readonly property string linkLabel: {
    // U+F06A5 is nf-md-power_plug, checked present in the bar font rather
    // than assumed. It rides the three labels that are about making a link
    // and not the two that are about ending one.
    switch (root.linkState) {
    case "stopping":   return "Stopping"
    case "connected":  return "Stop"
    case "connecting": return "\u{F06A5}  Connecting"
    case "dropped":    return "\u{F06A5}  Reconnect"
    default:           return "\u{F06A5}  Connect"
    }
  }

  // RECONNECT HAS TO ACTUALLY RECONNECT.
  //
  // It used to call startOmaCar(), and `daemon_start` opens with
  //   if daemon_pid >/dev/null; then echo "daemon already running"; return; fi
  // -- so in the one state the button exists for, it did nothing. `dropped`
  // means the file still claims a connection and has gone quiet, and the
  // commonest cause of that is a daemon that is alive but wedged on the serial
  // port. It IS running, so start correctly refuses, prints a line nobody
  // sees, and the panel goes on saying "Reconnect" for ever. A button that
  // reports success and changes nothing is worse than one that fails.
  //
  // So dropped gets a stop first. Sequential in one shell, because the start
  // must not race the stop for the port.
  Process {
    id: restartDaemon
    command: ["bash", "-lc", "omacar daemon stop; omacar daemon start 2>&1"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.daemonStarting = false
        root.startError = text.indexOf("did not start") >= 0
          ? "could not restart — is the ignition on?" : ""
      }
    }
  }
  function restartOmaCar() {
    if (root.daemonStarting) return
    root.startError = ""
    root.daemonStarting = true
    restartDaemon.running = true
  }

  function toggleLink() {
    if (root.linkBusy) return
    if (root.realLive) { root.stopOmaCar(); return }
    if (root.realDropped) { root.restartOmaCar(); return }
    root.startOmaCar()
  }

  // A BUSY FLAG THAT CANNOT STICK.
  //
  // Both flags are cleared by their Process's onStreamFinished, and if that
  // never arrives -- the shell is killed, `omacar` is not on PATH under
  // `bash -lc`, the collector never sees an end -- the button sits on
  // "Connecting" or "Stopping" with linkBusy true, and toggleLink() returns
  // early for ever. That is the whole failure: not a wrong label, but a
  // control with no way out short of restarting the shell.
  //
  // Twenty seconds is past the worst honest case: daemon_start polls 40 times
  // at 0.25s before giving up, so ten seconds is its own ceiling.
  Timer {
    id: linkWatchdog
    interval: 20000
    running: root.daemonStarting || root.daemonStopping
    repeat: false
    onTriggered: {
      root.daemonStarting = false
      root.daemonStopping = false
      if (root.startError === "") root.startError = "no answer — try again"
    }
  }
  // HOW MANY FAULTS, FROM WHICHEVER PRODUCER IS FEEDING US.
  //
  // Two things write this panel's cache. The rollup script that exists today
  // hands over a pre-counted `issues` integer; the `omacar card` snapshot that
  // is replacing it hands over `active_faults`, the list itself, and no count
  // at all. Reading only one of them means the hero pill goes quietly wrong
  // the day the other one lands -- and a pill that reads "no faults" over a
  // Health tab listing codes is worse than no pill.
  //
  // So: prefer the list when it is there, because a length cannot disagree
  // with the rows underneath it, and fall back to the count when it is not.
  readonly property int issues: car.active_faults
    ? car.active_faults.length : (car.issues || 0)

  // Worth lighting the bar for: something is past due, or a code has set in
  // the last few days. A fault that has been standing since the spring is on
  // the card and in the panel; it does not get to own the menu bar.
  // Dismissals: which notifications this user has already dealt with.
  //
  // The watchdog owns alerts.json and rewrites it whenever anything happens,
  // so the panel must never write there -- a dismissal and a new alert landing
  // together would lose one of them. Dismissals live in their own file that
  // only the panel writes, and the two are combined at read time.
  property var dismissed: ({})
  // Keys written this session but not yet confirmed on disk. Without these a
  // dismissal would reappear for the second or so between the click and the
  // next read of the file.
  property var pendingDismiss: []

  // Identity for an alert, which the feed does not give us. `at` alone is not
  // enough -- two alerts can share a second -- so the text is folded in via
  // FNV-1a. The result is hex, which matters: this key is passed through a
  // shell, and a key that cannot contain a quote cannot become a quoting bug.
  function alertKey(a) {
    var t = (a.title || "") + "\u0000" + (a.body || "")
    var h = 2166136261
    for (var i = 0; i < t.length; i++) {
      h ^= t.charCodeAt(i)
      h = (h * 16777619) >>> 0
    }
    return String(a.at || 0) + "-" + h.toString(16)
  }

  function isDismissed(a) {
    var k = root.alertKey(a)
    if (root.pendingDismiss.indexOf(k) >= 0) return true
    var before = root.dismissed.before || 0
    if (before > 0 && (a.at || 0) <= before) return true
    var ids = root.dismissed.ids || []
    return ids.indexOf(k) >= 0
  }

  readonly property var liveAlerts: {
    var all = root.alerts.alerts || []
    var out = []
    for (var i = 0; i < all.length; i++)
      if (!root.isDismissed(all[i])) out.push(all[i])
    return out
  }

  // Counted here rather than taken from the feed's own rollup, because the
  // rollup does not know what has been dismissed -- and a badge that survives
  // dismissing the thing it counts is a badge people learn to ignore.
  readonly property int criticalAlerts: {
    var n = 0
    for (var i = 0; i < root.liveAlerts.length; i++)
      if (root.liveAlerts[i].urgency === "critical"
          && root.nowSec - (root.liveAlerts[i].at || 0) < 86400) n++
    return n
  }
  readonly property int dayAlerts: {
    var n = 0
    for (var i = 0; i < root.liveAlerts.length; i++) {
      var u = root.liveAlerts[i].urgency
      if ((u === "critical" || u === "normal")
          && root.nowSec - (root.liveAlerts[i].at || 0) < 86400) n++
    }
    return n
  }

  // Whether the notifications page is showing instead of the panel body.
  property bool notifOpen: false
  // Whichever page is showing. The panel, its scrollbar and its flick gesture
  // all size off this -- pointing them at `column` alone left the notifications
  // page unscrollable, because `column` is hidden while it is up.
  readonly property real bodyHeight: root.notifOpen ? notifCol.implicitHeight : column.implicitHeight

  readonly property bool attention: {
    if (root.criticalAlerts > 0) return true
    if (svc && svc.overdue > 0) return true
    var f = car.faults || []
    for (var i = 0; i < f.length; i++)
      if (f[i].active && f[i].ago !== null && f[i].ago < 3 * 86400) return true
    return false
  }
  readonly property real odometer: car.odometer || 0

  // ---- units ---------------------------------------------------------------
  function uDist(km) {
    var n = parseFloat(km)
    return isNaN(n) ? NaN : n * (units.km || 1)
  }
  function uSpeed(kph) { return uDist(kph) }
  function uVol(l) {
    var n = parseFloat(l)
    return isNaN(n) ? NaN : n * (units.litre || 1)
  }
  function uTemp(c) {
    var n = parseFloat(c)
    if (isNaN(n)) return NaN
    return units.system === "imperial" ? n * 9 / 5 + 32 : n
  }
  // A reciprocal, not a scale: a car burning nothing has infinite mpg, so a
  // missing or zero consumption stays missing rather than becoming a very
  // large number.
  function uEcon(lphk) {
    var n = parseFloat(lphk)
    if (isNaN(n) || n <= 0) return NaN
    return units.system === "imperial" ? 235.214583 / n : n
  }

  function grouped(v) {
    var n = Math.round(parseFloat(v))
    if (isNaN(n)) return "—"
    var out = String(Math.abs(n)), i = out.length - 3
    while (i > 0) { out = out.slice(0, i) + "," + out.slice(i); i -= 3 }
    return (n < 0 ? "-" : "") + out
  }
  function distStr(km, withUnit) {
    var n = uDist(km)
    if (isNaN(n)) return "—"
    var t = n >= 1000 ? grouped(n) : (n >= 100 ? String(Math.round(n)) : n.toFixed(1))
    return withUnit === false ? t : t + " " + units.dist
  }
  function econStr(lphk, withUnit) {
    var n = uEcon(lphk)
    if (isNaN(n)) return "—"
    return n.toFixed(1) + (withUnit === false ? "" : " " + units.econ)
  }
  function money(v) {
    var n = parseFloat(v)
    return isNaN(n) ? "" : "$" + (n >= 1000 ? grouped(n) : n.toFixed(2))
  }
  function mins(secs) {
    var m = Math.round((secs || 0) / 60)
    if (m < 60) return m + " min"
    return Math.floor(m / 60) + "h " + (m % 60) + "m"
  }
  function since(secs) {
    if (secs === null || secs === undefined) return ""
    var s = Math.max(0, Math.floor(secs))
    if (s < 90) return "just now"
    if (s < 3600) return Math.floor(s / 60) + "m ago"
    if (s < 172800) return Math.floor(s / 3600) + "h ago"
    return Math.floor(s / 86400) + "d ago"
  }
  readonly property var monthNames: ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  function shortDate(secs) {
    if (!secs) return ""
    var d = new Date(secs * 1000)
    return d.getDate() + " " + monthNames[d.getMonth()]
  }
  function isoDate(iso) {
    if (!iso) return ""
    var p = String(iso).split("-")
    if (p.length < 3) return String(iso)
    return parseInt(p[2], 10) + " " + monthNames[parseInt(p[1], 10) - 1] + " " + p[0]
  }
  function clockOf(secs) {
    var d = new Date(secs * 1000)
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2)
  }

  // Which way is good. Litres per hundred wants to be low and miles per gallon
  // wants to be high, so anything comparing two windows has to ask rather than
  // assume — the same improvement flips sign with the unit.
  function econDelta(now, before) {
    var a = uEcon(now), b = uEcon(before)
    if (isNaN(a) || isNaN(b)) return null
    if (Math.abs(a - b) < 0.15) return { "arrow": "=", "text": "", "good": null }
    var up = a > b
    var good = units.econ_better === "high" ? up : !up
    return { "arrow": up ? "↑" : "↓",
             "text": Math.abs(a - b).toFixed(1), "good": good }
  }
  function deltaColor(d) {
    if (!d || d.good === null) return dim(0.45)
    return d.good ? cGreen : cAmber
  }

  // Remaining service life, on Honda's own scale: 15% is "book it", 5% is
  // "now", nought is past due. Inverted against every other percentage in this
  // desktop, because here a low number is the bad one.
  function lifeColor(life) {
    if (life === null || life === undefined) return dim(0.45)
    if (life <= 0) return cRed
    if (life <= 15) return cAmber
    return cGreen
  }
  function severityColor(sev) {
    return sev === "critical" ? cRed : sev === "warning" ? cAmber : dim(0.5)
  }

  // ---- data ----------------------------------------------------------------
  //
  // FOUR SMALL FILES, READ WITHOUT FORKING ANYTHING.
  //
  // These four were read with `bash -c 'cat "$1" || echo {}'`. The `||` is
  // what makes it expensive: it stops bash from exec-replacing itself, so
  // every read cost two forks and two execs. Twenty reads a minute with the
  // panel shut, ninety-six with it open, and a single click on the bar icon
  // could issue eight process creations in one turn of the event loop --
  // against a shell process holding a GL context and a large QML heap, whose
  // page tables are copied on every one of them. That is the only thing in
  // this plugin that got worse the longer the session ran, and it is what the
  // menu bar stalling on a click actually was.
  //
  // FileView reads the file on Quickshell's own I/O thread and, with
  // watchChanges, is told by the kernel when it changes rather than asking.
  // Three of these four now have no timer at all.
  //
  // Two things about the shim are worth writing down, because both are silent
  // when got wrong. `text` is a FUNCTION here, not a property -- the Process
  // idiom of reading a bare `text` in the handler yields undefined. And
  // `fileChanged` is a notification, not a reload: the content does not come
  // back until reload() is called, which is why every FileView in the shell
  // is written with `onFileChanged: reload()`.
  //
  // printErrors is off throughout. A file that no daemon has written yet is
  // the ordinary state of a fresh install, not something to log once a second.
  function parsedJson(t) {
    var s = String(t || "")
    if (s === "") return null
    var d
    try { d = JSON.parse(s) } catch (e) { return null }
    return (d && typeof d === "object") ? d : null
  }

  FileView {
    id: cacheView
    path: root.cache
    watchChanges: true
    printErrors: false
    onLoaded: root.readCache(text())
    onFileChanged: reload()
    // The `|| echo '{}'` the bash reader used to end with. Without it a file
    // that is deleted, renamed away, or caught zero-length mid-rollup simply
    // never fires onLoaded, and the panel goes on showing a car that is no
    // longer there instead of falling back to "No car".
    onLoadFailed: root.readCache("{}")
  }

  function readCache(t) {
    var d = root.parsedJson(t)
    if (!d) return
    // Compared before assigning: every row in the panel binds to this, so an
    // unconditional write rebuilds the lot for a file that is usually
    // byte-for-byte what we already had.
    if (JSON.stringify(d) !== JSON.stringify(root.car)) root.car = d
  }

  // BELT AND BRACES, ONCE A MINUTE -- FOR ALL THREE WATCHED FILES.
  //
  // All three are written to a temp file and renamed over the old one, so the
  // thing being watched is replaced rather than modified. Quickshell copes
  // with that -- every config file the shell watches is written the same way
  // by an editor -- but the failure if it ever did not would be a panel
  // quietly showing yesterday, which is the one failure worth insuring
  // against. Three reads of three small files a minute is the whole premium.
  //
  // The insurance covers alerts.json most of all, not least. The rollup is
  // refreshed by an explicit Refresh button, so a stalled cache has a way out
  // that a user can find; the alert feed has none, and arriving promptly is
  // the entire point of it. lib/watch.py writes it with os.replace on every
  // single alert, so it is also the file whose inode is destroyed most often.
  Timer {
    interval: 60000
    running: true
    repeat: true
    onTriggered: {
      cacheView.reload()
      alertsView.reload()
      dismissedView.reload()
    }
  }

  FileView {
    id: alertsView
    path: root.alertFile
    watchChanges: true
    printErrors: false
    onLoaded: root.readAlerts(text())
    onFileChanged: reload()
    onLoadFailed: root.readAlerts("{}")
  }

  function readAlerts(t) {
    var d = root.parsedJson(t)
    if (!d) return
    if (JSON.stringify(d) !== JSON.stringify(root.alerts)) root.alerts = d
  }

  FileView {
    id: dismissedView
    path: root.dismissFile
    watchChanges: true
    printErrors: false
    onLoaded: root.readDismissed(text())
    onFileChanged: reload()
    onLoadFailed: root.readDismissed("{}")
  }

  function readDismissed(t) {
    var d = root.parsedJson(t)
    if (!d) return
    // Anything the file now confirms can stop being carried locally.
    var ids = d.ids || []
    var still = []
    for (var i = 0; i < root.pendingDismiss.length; i++)
      if (ids.indexOf(root.pendingDismiss[i]) < 0)
        still.push(root.pendingDismiss[i])
    if (still.length !== root.pendingDismiss.length)
      root.pendingDismiss = still
    if (JSON.stringify(d) !== JSON.stringify(root.dismissed))
      root.dismissed = d
  }

  Process {
    id: writeDismiss
    // python3 rather than jq: jq is not a given on a fresh Omarchy box, and
    // the app already depends on python. The write is to a temp file and then
    // renamed, so a panel closing mid-write cannot leave a truncated file that
    // would silently un-dismiss everything.
    command: ["bash", "-c",
      "python3 - \"$1\" \"$2\" \"$3\" <<\"PYEOF\"\n"
      + "import json, os, sys\n"
      + "p, k, before = sys.argv[1], sys.argv[2], int(sys.argv[3])\n"
      + "try:\n"
      + "    d = json.load(open(p))\n"
      + "except Exception:\n"
      + "    d = {}\n"
      + "if not isinstance(d, dict):\n"
      + "    d = {}\n"
      + "ids = d.get(\"ids\") or []\n"
      + "if k and k not in ids:\n"
      + "    ids.append(k)\n"
      + "if before:\n"
      + "    d[\"before\"] = max(before, int(d.get(\"before\") or 0))\n"
      + "    ids = [x for x in ids if int(x.split(\"-\")[0] or 0) > d[\"before\"]]\n"
      + "d[\"ids\"] = ids[-500:]\n"
      + "os.makedirs(os.path.dirname(p), exist_ok=True)\n"
      + "t = p + \".tmp\"\n"
      + "f = open(t, \"w\")\n"
      + "json.dump(d, f)\n"
      + "f.close()\n"
      + "os.replace(t, p)\n"
      + "PYEOF\n",
      "x", root.dismissFile, "", "0"]
    // Re-read the file we just wrote. The watcher would tell us about it a
    // moment later anyway, but a dismissal that appears to come back for even
    // one frame is the kind of thing people stop trusting a button over.
    onExited: dismissedView.reload()
  }

  function dismissAlert(a) {
    var k = root.alertKey(a)
    // Hide it NOW, and record it. The local list is what makes the card
    // vanish on the click rather than on the next read of the file.
    var pend = root.pendingDismiss.slice()
    if (pend.indexOf(k) < 0)
      pend.push(k)
    root.pendingDismiss = pend
    if (writeDismiss.running)
      return
    writeDismiss.command = writeDismiss.command.slice(0, 3).concat(["x", root.dismissFile, k, "0"])
    writeDismiss.running = true
  }

  function dismissAllAlerts() {
    // A watermark rather than 500 individual keys: everything at or before the
    // newest alert we can currently see goes away, and the file stays small
    // however long the car has been running.
    var all = root.alerts.alerts || []
    var newest = 0
    for (var i = 0; i < all.length; i++)
      newest = Math.max(newest, all[i].at || 0)
    if (newest <= 0)
      return
    var pend = root.pendingDismiss.slice()
    for (var j = 0; j < all.length; j++)
      if (pend.indexOf(root.alertKey(all[j])) < 0)
        pend.push(root.alertKey(all[j]))
    root.pendingDismiss = pend
    if (writeDismiss.running)
      return
    writeDismiss.command = writeDismiss.command.slice(0, 3).concat(["x", root.dismissFile, "", String(newest)])
    writeDismiss.running = true
  }

  // THE ONE FILE THAT IS NOT WATCHED, and deliberately.
  //
  // live.json is rewritten five times a second while the daemon is running.
  // Watching it would hand this panel five wake-ups, five reads and five
  // rounds of binding invalidation a second for a readout no one can follow
  // faster than about one -- so this is the file to throttle rather than the
  // file to subscribe to. The timer below asks for it once a second with the
  // panel open and once every five with it shut.
  FileView {
    id: liveView
    path: root.liveFile
    watchChanges: false
    printErrors: false
    onLoaded: root.readLive(text())
  }

  // The demo marker and the demo's sample in one pass: two files, one process,
  // on the same tick as everything else.
  Process {
    id: loadDemo
    command: ["bash", "-c",
      "if [ -f \"$1\" ]; then echo 1; cat \"$2\" 2>/dev/null || echo '{}';"
      + " else echo 0; echo '{}'; fi",
      "x", root.demoMarkerFile, root.demoLiveFile]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var nl = text.indexOf("\n")
        root.demoMarker = text.slice(0, nl < 0 ? text.length : nl).trim() === "1"
        try { root.demoSample = JSON.parse(text.slice(nl + 1)) }
        catch (e) { root.demoSample = ({}) }
      }
    }
  }

  function readLive(t) {
    var d = root.parsedJson(t)
    if (!d) return
    // The same guard the three readers above have had all along, and this one
    // wanted it most. `live` below is an object LITERAL, so assigning `sample`
    // mints a new identity whether or not a single number moved -- and that
    // invalidates state_, engineOn, connected, the tooltip and every row of
    // the Now tab. With the car parked and the daemon stopped, the file does
    // not change from one read to the next, so nothing downstream should move
    // either.
    if (JSON.stringify(d) !== JSON.stringify(root.sample)) root.sample = d
  }

  // THE PANEL'S ONLY REMAINING CLOCK, and it keeps ticking with the panel
  // shut, which is the point.
  //
  // The cache, the alert feed and the dismissal list are all told when they
  // change now, so their three timers are gone. This one is left because the
  // file it reads changes faster than it is worth hearing about, and because
  // nowSec has to keep moving: freshness is measured against it, so a frozen
  // clock and a frozen sample once meant the icon kept whatever colour it had
  // when the panel closed -- green, forever, over an adapter that had been
  // unplugged for hours.
  //
  // Slower when shut, because reading one small file every five seconds is
  // cheap and being wrong in the bar is not. Changing the interval restarts
  // the timer, which with triggeredOnStart gives a fresh reading the moment
  // the panel opens -- nobody wants a reading from a minute ago while staring
  // at the thing that shows it. That restart used to be expensive, because
  // what it kicked was two processes; now it is one file read.
  Timer {
    interval: root.opened ? 1000 : 5000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: {
      root.nowSec = Date.now() / 1000
          liveView.reload()
          if (!loadDemo.running) loadDemo.running = true
    }
  }

  onOpenedChanged: {
    // Always come back to the panel itself. Reopening onto the notifications
    // page -- most likely emptied, since that is why you were there -- would
    // look like the panel had lost its contents.
    //
    // Nothing is kicked from here any more. The three explicit reads that
    // used to live here were the worst of the fork storm -- up to eight
    // process creations in the one turn of the event loop that also had to
    // build the panel -- and every one of them is now either already current
    // via the watcher or covered by the timer above restarting.
    notifOpen = false
    // One rebuild on a click, though: reading is only ever as good as whoever
    // last wrote the file, and for the panel's whole life that was nobody.
    if (!opened) return
    root.refreshNow()
  }

  Process { id: act }
  function run(args) {
    if (act.running) return
    act.command = args
    act.running = true
  }
  // Rebuilds the rollup this panel reads. `liquid-glass-car` was never a
  // command, so for a long time this refreshed nothing and the cache it reads
  // was never written by anybody -- which is why every vehicle field in this
  // panel was blank.
  //
  // No button calls it any more. Its callers are the panel opening, the timer
  // below while it stays open, and the demo switching trees: three moments
  // where the rollup is known to be behind, which between them are every
  // moment the button was ever pressed for.
  function refreshNow() {
    // The demo's rollup lives in the demo's own tree, and only omacar knows
    // where that is -- the panel asks rather than reimplementing the path.
    run(["bash", "-c", root.demoing
         ? "omacar demo cache >/dev/null 2>&1"
         : "omacar card --quiet >/dev/null 2>&1"])
    recheck.restart()
  }
      // The rollup takes a moment to run. The watcher notices its rename on its
      // own, so this only makes a refresh feel like it did something even when
      // the run produced a byte-identical file.
      Timer { id: recheck; interval: 1200; onTriggered: cacheView.reload() }

  // THE PANEL REFRESHES ITSELF NOW, WHICH IS WHY THERE IS NO REFRESH BUTTON.
  //
  // There was one, and it was the only thing in the panel that ever REBUILT
  // the rollup: opening the panel rebuilt it once and then nothing did, so
  // odometer, service life and the day's distance were frozen at whatever
  // they were when you opened it, however long you sat there. The live sample
  // ticks every second and made that easy to miss -- the numbers that move
  // moved, and the numbers beside them quietly did not.
  //
  // Thirty seconds, and only while the panel is open. The rollup walks the
  // telemetry database, which is not free, and nothing is reading it while
  // the panel is shut; a minute-old service percentage on a screen nobody is
  // looking at is not a problem worth spawning a process for.
  Timer {
    interval: 30000
    running: root.opened
    repeat: true
    onTriggered: root.refreshNow()
  }

  // SWITCHING TREES MUST NOT LEAVE THE OTHER CAR'S NUMBERS ON SCREEN.
  //
  // `car` is a whole rollup read from whichever tree is live, and the read is
  // asynchronous. Stopping the demo repointed `cache` instantly but the panel
  // went on showing the demo's oil life, its service book and its `simulated`
  // flag until the next read landed -- up to ten seconds of invented numbers
  // with the DEMO badge already gone from beside them, because the badge
  // follows a marker file and the numbers follow a file read. That is the one
  // failure a demo is not allowed to have.
  //
  // So the rollup is blanked the instant the source changes. Empty is the
  // honest state: the panel genuinely knows nothing about the other tree yet,
  // and every pill hides itself on an empty label rather than showing the
  // wrong one. The rebuild below fills it back in, and `recheck` re-reads it
  // a beat later -- deliberately not read here, because the `cache` path is a
  // binding on this same property and reading it in the same handler is a
  // race for which tree you get.
  //
  // It fires both ways, including the way that matters most: a real car
  // waking up mid-demo drops `demoing` on its own, and this is what clears
  // the demo's figures off the screen when it does.
  onDemoingChanged: {
    root.car = ({})
    root.refreshNow()
  }

  function openCluster() { run(["bash", "-c", "omacar >/dev/null 2>&1 &"]) }

  // Seeding a year of driving is not instant the first time, so the button
  // dims until the marker turns up rather than looking as though nothing
  // happened.
  property bool demoBusy: false
  function toggleDemo() {
    if (root.demoBusy)
      return
    root.demoBusy = true
    demoAct.command = ["bash", "-c", root.demoing
      ? "omacar demo stop >/dev/null 2>&1"
      : "omacar demo start >/dev/null 2>&1"]
    demoAct.running = true
  }

  Process {
    id: demoAct
    onExited: {
      root.demoBusy = false
      // Only the marker. Re-reading the rollup here read whichever tree the
      // path binding still pointed at -- and it pointed at the old one until
      // this very read told it otherwise, so stopping the demo pulled the
      // demo's cache in one last time. onDemoingChanged owns that now.
      if (!loadDemo.running) loadDemo.running = true
    }
  }

  // ---- the bar button ------------------------------------------------------
  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // A quarter larger than the shell's icon canvas, and derived from it.
    //
    // The wheel is square, so unlike the coupe it already fills whatever
    // canvas it is given and needs no 3.3:1 correction -- the 38px the coupe
    // needed would put a 36px disc in a 26px bar. A quarter over the token
    // resolves to 20px on this theme, which keeps the ~3px of air above and
    // below that stops an icon looking wedged in, lands on whole device pixels
    // at a 1.25 display scale, and follows the theme if it ever resizes its
    // bar. The slot is left alone so neighbouring widgets keep their spacing;
    // an OmaCar button wider than everything beside it was the other half of
    // what made the coupe look wrong.
    opticalSize: Math.round(Style.bar.iconCanvas * 1.25)
    // Lit when the engine is turning, or when something wants attention now.
    // NOT for any standing fault: a code the car has held since June would
    // leave the icon permanently on, and an indicator that is always lit is
    // an indicator nobody reads.
    active: root.engineOn || root.attention

    iconComponent: SteeringWheel {
      // GREEN whenever the link to the car is live.
      //
      // "active" already lights the button for engine-on or an alert, but both
      // of those are transient -- they say something is happening, not that
      // OmaCar is talking to the car at all. Connected is the state you glance
      // at the bar to check, so it gets the colour. cGreen is the same green
      // the "no faults" pill uses, so green means the same thing everywhere.
      tint: root.connected
              ? root.cGreen
              : (button.active && button.useActiveColor ? button.activeColor : button.foreground)
      running: root.engineOn
      // NO LEAN, AND NO ANIMATION. `spin` is left at its default of nought.
      //
      // The 0.42 this carried came from the coupe, where spin is turns per
      // second and feeds an animation. On the wheel it is a static angle in
      // radians, which is 24 degrees -- and with three spokes at -90, 21.6 and
      // 158.4 degrees, a 24 degree offset does not read as a lean, it reads as
      // an icon someone failed to align. Movement is already said twice over
      // here: green for a live link, a filled hub while the engine turns.
    }

    tooltipText: {
      var bits = []
      bits.push(root.car.name || "OmaCar")
      if (root.state_ === "driving" && root.live.speed)
        bits.push(Math.round(root.uSpeed(root.live.speed)) + " " + root.units.speed)
      else bits.push(root.state_)
      if (root.perf && root.perf.day)
        bits.push(root.distStr(root.perf.day.km) + " today")
      if (root.dayAlerts > 0)
        bits.push(root.dayAlerts + " alert(s) today")
      if (root.issues > 0) bits.push(root.issues + " issue(s)")
      if (root.svc && root.svc.next && root.svc.next.life <= 15)
        bits.push(root.svc.next.item + " due")
      if (root.demoing)
        bits.unshift("DEMO — not your car")
      return bits.join("  ·  ") + "\nclick: panel · "
             + (root.realLive ? "" : "middle: connect · ")
             + "right-click: cluster"
    }

    onPressed: function (b) {
      // Middle-click was Refresh, and Refresh is gone -- the panel keeps its
      // own rollup current. Connecting is what is left that is worth doing
      // without opening anything, and it is offered in that direction only: a
      // middle-click that could cut the link to a car you are driving is not
      // a shortcut, it is a hazard. When there is a link, this does nothing.
      if (b === Qt.MiddleButton) {
        if (!root.realLive && !root.linkBusy) root.startOmaCar()
        return
      }
      if (b === Qt.RightButton) { root.openCluster(); return }
      root.toggle()
    }
  }

  // A count on the wheel when the watchdog has raised something today. The bar
  // is the only surface that is always visible, so this is where an alert has
  // to land. Drawn over the button rather than inside it: BarIconButton has no
  // notion of a badge, and subclassing the shell's own component to add one
  // would tie this plugin to a particular version of it.
  Rectangle {
    visible: root.dayAlerts > 0
    anchors.right: button.right
    anchors.top: button.top
    anchors.rightMargin: Style.space(2)
    anchors.topMargin: Style.space(2)
    z: 5
    width: Math.max(Style.space(13), badgeText.implicitWidth + Style.space(5))
    height: Style.space(13)
    radius: height / 2
    color: root.criticalAlerts > 0 ? root.cRed : root.cAmber
    border.width: 1
    border.color: root.bar ? root.bar.background : "transparent"

    Text {
      id: badgeText
      anchors.centerIn: parent
      text: String(root.dayAlerts)
      color: "#12060A"
      font.family: root.bar.fontFamily
      font.pixelSize: Math.max(8, Style.font.caption - 1)
      font.weight: Font.Bold
    }
  }

  // ---- small building blocks ----------------------------------------------

  component SectionLabel: Text {
    color: root.ink(0.82)
    font.family: root.bar.fontFamily
    font.pixelSize: root.fMicro
    font.letterSpacing: 1.9
    font.weight: Font.DemiBold
    topPadding: Math.ceil(root.fMicro * 0.15)
  }

  component Body: Text {
    color: root.fg
    font.family: root.bar.fontFamily
    font.pixelSize: root.fBody
  }

  component Muted: Text {
    color: root.ink(0.88)
    font.family: root.bar.fontFamily
    font.pixelSize: root.fCaption
  }

  // A pill: a count or a state word that has to carry its own colour without
  // borrowing the panel's ink.
  component Pill: Rectangle {
    id: pill
    property string label: ""
    property color tint: root.dim(0.5)
    implicitWidth: pillText.implicitWidth + Style.space(11)
    implicitHeight: Math.round(root.fCaption * 1.85)
    radius: height / 2
    color: Qt.rgba(pill.tint.r, pill.tint.g, pill.tint.b, 0.17)
    visible: pill.label !== ""

    Text {
      id: pillText
      anchors.centerIn: parent
      text: pill.label
      color: pill.tint
      font.family: root.bar.fontFamily
      font.pixelSize: root.fCaption
      font.weight: Font.DemiBold
    }
  }

  // A plain text button. PanelActionButton up the road is an icon button, and
  // "Open cluster" is not an icon.
  component TextButton: Rectangle {
    id: tb
    property string label: ""
    signal pressed()

    implicitWidth: tbText.implicitWidth + Style.space(20)
    implicitHeight: Math.round(root.fBody * 2.2)
    radius: Style.space(6)
    color: tbMouse.containsMouse ? root.dim(0.14) : root.dim(0.07)
    Behavior on color { ColorAnimation { duration: 120 } }

    Text {
      id: tbText
      anchors.centerIn: parent
      text: tb.label
      color: root.fg
      font.family: root.bar.fontFamily
      font.pixelSize: root.fCaption
    }

    MouseArea {
      id: tbMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: tb.pressed()
    }
  }

  // A pill with a word in it. Every action in the hero takes this shape, so
  // the group reads as one set of controls rather than three widgets that
  // happen to sit near each other.
  component PillButton: Rectangle {
    id: pb
    property string label: ""
    // BUSY IS A STATE, NOT A SHADE.
    //
    // This was a `fade` number, and a button dimmed to 0.5 still took the
    // click -- so a second press while the daemon was coming up queued a
    // second `omacar daemon start` behind the first. The dimming and the
    // deafness now come from one property, because they were never two
    // separate decisions.
    property bool busy: false
    signal pressed

    width: pbText.implicitWidth + Style.space(18)
    height: Style.space(26)
    radius: height / 2
    color: pbMouse.containsMouse ? root.dim(0.22) : root.dim(0.10)
    border.width: 1
    border.color: root.dim(0.32)
    opacity: pb.busy ? 0.5 : 1
    Behavior on color {
      ColorAnimation { duration: 120 }
    }

    Text {
      id: pbText
      anchors.centerIn: parent
      text: pb.label
      color: root.fg
      font.family: root.bar.fontFamily
      font.pixelSize: Math.round(Style.font.caption)
      font.weight: Font.Medium
    }

    MouseArea {
      id: pbMouse
      anchors.fill: parent
      hoverEnabled: true
      // Disabled rather than ignored: it also stops the hover tint, so a busy
      // button does not light up under the pointer as though it were waiting
      // to be pressed again.
      enabled: !pb.busy
      cursorShape: Qt.PointingHandCursor
      onClicked: pb.pressed()
    }
  }

  // One figure with its name above it and its qualifier below — the shape
  // every number in this panel takes, so four of them read as a row rather
  // than as four separate facts.
  component Stat: Item {
    id: stat
    property string label: ""
    property string value: "—"
    property string unit: ""
    property string note: ""
    property color tint: root.fg
    property color noteTint: root.dim(0.5)

    implicitHeight: statCol.implicitHeight

    Column {
      id: statCol
      width: parent.width
      spacing: Style.space(2)

      SectionLabel { text: stat.label }

      Row {
        spacing: Style.space(3)
        Text {
          text: stat.value
          color: stat.tint
          font.family: root.bar.fontFamily
          font.pixelSize: root.fStat
          font.weight: Font.DemiBold
        }
        Text {
          visible: stat.unit !== ""
          text: stat.unit
          color: root.ink(0.88)
          font.family: root.bar.fontFamily
          font.pixelSize: root.fCaption
          anchors.baseline: parent.children[0].baseline
        }
      }

      Text {
        visible: stat.note !== ""
        text: stat.note
        color: stat.noteTint
        font.family: root.bar.fontFamily
        font.pixelSize: root.fCaption
      }
    }
  }

  // A labelled row: name on the left, value on the right, aligned into a
  // column you can scan. The whole reason to use rows rather than prose.
  component KV: Item {
    id: kv
    property string k: ""
    property string v: ""
    property color tint: root.fg
    property bool mono: false
    implicitHeight: Math.max(kvK.implicitHeight, kvV.implicitHeight) + Style.space(3)

    Text {
      id: kvK
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      text: kv.k
      color: root.ink(0.88)
      font.family: root.bar.fontFamily
      font.pixelSize: root.fCaption
    }

    Text {
      id: kvV
      anchors.right: parent.right
      anchors.left: kvK.right
      anchors.leftMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      horizontalAlignment: Text.AlignRight
      text: kv.v
      color: kv.tint
      elide: Text.ElideRight
      font.family: root.bar.fontFamily
      font.pixelSize: root.fCaption
    }
  }

  // ---- the panel -----------------------------------------------------------
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keys
    contentWidth: panel.fittedContentWidth(Style.space(468))
    contentHeight: panel.fittedContentHeight(root.bodyHeight)

    PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      // Back out of notifications first: Escape on a sub-page should return
      // you to the panel, not shut the whole thing and leave you on that page
      // the next time you open it.
      onCloseRequested: {
        if (root.notifOpen)
          root.notifOpen = false;
        else
          root.close();
      }

      // The panel is capped to the screen by fittedContentHeight, but a cap
      // without a scroller just clips: on this laptop the content ran off the
      // bottom and the rest was simply unreachable. Same idiom as Omarchy's
      // own audio panel -- clip, a scrollbar only when it is needed, and the
      // flick gesture enabled only when there is somewhere to flick to, so a
      // short panel does not swallow drags on a touchscreen.
      ScrollView {
        id: scrollArea
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: root.bodyHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        Binding {
          target: scrollArea.contentItem
          property: "interactive"
          value: root.bodyHeight > scrollArea.height
        }

        // ---- notifications, on their own page --------------------------
        Column {
          id: notifCol
          width: scrollArea.availableWidth
          spacing: Style.space(8)
          visible: root.notifOpen

          Item {
            width: notifCol.width
            height: Math.max(backBtn.height, clearBtn.height)

            PillButton {
              id: backBtn
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              label: "\u2039  Back"
              onPressed: root.notifOpen = false
            }

            Text {
              anchors.centerIn: parent
              text: "Notifications"
              color: root.fg
              font.family: root.bar.fontFamily
              font.pixelSize: root.fBody
              font.weight: Font.Medium
            }

            PillButton {
              id: clearBtn
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              visible: root.liveAlerts.length > 0
              label: "Dismiss all"
              onPressed: root.dismissAllAlerts()
            }
          }

          Item {
            width: notifCol.width
            height: Style.space(2)
          }

          // Nothing left is a RESULT, not an empty screen -- say so, rather
          // than showing a page that looks like it failed to load.
          Item {
            visible: root.liveAlerts.length === 0
            width: notifCol.width
            height: Style.space(70)

            Column {
              anchors.centerIn: parent
              spacing: Style.space(4)

              Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "All clear"
                color: root.fg
                font.family: root.bar.fontFamily
                font.pixelSize: root.fBody
              }

              Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Nothing waiting for you."
                color: root.dim(0.45)
                font.family: root.bar.fontFamily
                font.pixelSize: root.fCaption
              }
            }
          }

          Repeater {
            model: root.liveAlerts

            Rectangle {
              id: card
              required property var modelData
              width: notifCol.width
              height: cardCol.implicitHeight + Style.space(20)
              radius: Style.space(8)
              color: cardMouse.containsMouse ? root.cCardHover : root.cCard
              // Severity rides the border, so the fill can stay the one blue
              // the user asked for and still tell a warning from a note.
              border.width: 1
              border.color: card.modelData.urgency === "critical" ? Qt.rgba(root.cRed.r, root.cRed.g, root.cRed.b, 0.55) : card.modelData.urgency === "normal" ? Qt.rgba(root.cAmber.r, root.cAmber.g, root.cAmber.b, 0.45) : Qt.rgba(1, 1, 1, 0.10)
              Behavior on color {
                ColorAnimation { duration: 120 }
              }

              MouseArea {
                id: cardMouse
                anchors.fill: parent
                hoverEnabled: true
              }

              Column {
                id: cardCol
                anchors.left: parent.left
                anchors.right: dropBtn.left
                anchors.leftMargin: Style.space(12)
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(3)

                Row {
                  spacing: Style.space(8)

                  Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: Style.space(8)
                    height: width
                    radius: width / 2
                    color: card.modelData.urgency === "critical" ? root.cRed : card.modelData.urgency === "normal" ? root.cAmber : root.cBlue
                  }

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: card.modelData.title || ""
                    color: root.cCardInk
                    font.family: root.bar.fontFamily
                    font.pixelSize: root.fCaption
                    font.weight: Font.Medium
                  }

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.since(root.nowSec - (card.modelData.at || 0))
                    color: root.cCardSub
                    font.family: root.bar.fontFamily
                    font.pixelSize: root.fMicro
                  }
                }

                Text {
                  width: cardCol.width
                  text: card.modelData.body || ""
                  color: root.cCardSub
                  wrapMode: Text.WordWrap
                  leftPadding: Style.space(16)
                  font.family: root.bar.fontFamily
                  font.pixelSize: root.fCaption
                }
              }

              // Dismiss. A hit area far larger than the glyph, because this is
              // reachable from a touchscreen in a car.
              Rectangle {
                id: dropBtn
                anchors.right: parent.right
                anchors.rightMargin: Style.space(6)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(28)
                height: width
                radius: width / 2
                color: dropMouse.containsMouse ? Qt.rgba(1, 1, 1, 0.16) : "transparent"

                Text {
                  anchors.centerIn: parent
                  text: "\u00D7"
                  color: dropMouse.containsMouse ? root.cCardInk : root.cCardSub
                  font.family: root.bar.fontFamily
                  font.pixelSize: root.fBody
                }

                MouseArea {
                  id: dropMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.dismissAlert(card.modelData)
                }
              }
            }
          }
        }

        Column {
          id: column
          width: scrollArea.availableWidth
          spacing: Style.space(12)
          visible: !root.notifOpen

          // ---- who, and what it is doing ----
          Item {
            width: column.width
            // Tall enough for whichever is taller. The car is now bigger than
            // the text beside it, and a container sized only to the text would
            // clip it.
            height: Math.max(heroCol.implicitHeight,
                             heroWheel.height + Style.space(8) + heroBtns.height)

            // THREE ACTIONS, IN ONE ROW, RIGHT-ALIGNED UNDER THE CAR.
            //
            // There were five pills here and two of them were the same
            // decision wearing different labels: Connect and Stop are the one
            // link control (see linkState), so they are one button. Refresh
            // is gone outright -- it was the panel admitting it did not keep
            // itself current, and the timer above means it does.
            //
            // What is left is the whole set: the link, the demo, the app.
            // They sit at the top rather than in the footer because the
            // footer is inside the scroller, and the controls you reach for
            // every time should not be the ones you have to scroll to find.
            //
            // The link button is deliberately the LEFTMOST. In its Stop state
            // it severs the connection to the car, and the right edge is where
            // a thumb rests on a touchscreen in a moving vehicle; the harmless
            // action gets that spot instead.
            Row {
              id: heroBtns
              anchors.top: heroWheel.bottom
              anchors.topMargin: Style.space(8)
              anchors.right: parent.right
              spacing: Style.space(6)

              // A PLUG, not a play triangle. Play means "begin something";
              // this establishes a link to a car over a serial adapter, and a
              // plug is the thing you physically did a moment earlier.
              //
              // It sits in the row rather than in the corner as a circle. The
              // corner argument was that a lone control there could not fight
              // the car for the space; once every other action moved into this
              // row, one round button floating above the pills was simply the
              // odd one out.
              PillButton {
                label: root.linkLabel
                busy: root.linkBusy
                onPressed: root.toggleLink()
              }

              // ONE SWITCH FOR BOTH SURFACES.
              //
              // `omacar demo start` brings up the demo's own simulator, serves
              // it on its own port, opens it in the browser AND drops the
              // marker this panel watches -- so one press moves the bar widget
              // and the app together. Two surfaces disagreeing about which car
              // you are looking at would be worse than no demo at all.
              PillButton {
                label: root.demoing ? "Demo Off" : "Demo"
                busy: root.demoBusy
                onPressed: root.toggleDemo()
              }

              // "Control Center", not "Open cluster". The window this opens is
              // every surface OmaCar has, not just the gauges, and the button
              // should name the place it takes you to.
              PillButton {
                label: "Control Center"
                onPressed: {
                  root.openCluster();
                  root.close();
                }
              }
            }

            // A stripe across the top of the panel, not a subtle tint. The
            // whole risk of a demo is somebody reading invented numbers as
            // their own car's.
            Rectangle {
              visible: root.demoing
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.top: parent.top
              height: Style.space(3)
              radius: height / 2
              color: root.cAmber
            }

            SteeringWheel {
              id: heroWheel
              // ALWAYS drawn, faint when there is no link.
              //
              // It used to be hidden while disconnected, because a round start
              // button occupied this corner and two things could not share it.
              // That button is in the row now, so the corner is free -- and a
              // panel that keeps its shape between states is easier to use
              // than one that reflows every time the adapter drops. Faint,
              // though: a full-strength car would be claiming a live link.
              anchors.top: parent.top
              // UPPER RIGHT, and big.
              //
              // It had no horizontal anchor at all, so it sat at x=0 while
              // heroCol anchored itself to its left -- which put the text at
              // negative x, off the panel.
              anchors.right: parent.right
              // A proportion of the panel rather than a fixed size, so it stays
              // the same relative weight whatever width the bar gives us.
              //
              // 0.16 where the coupe had 0.38. The two numbers describe the
              // same picture: a 3.3:1 coupe at 0.38 of a ~468px panel was 178
              // wide and 54 tall, and it is the HEIGHT the eye weighs. A
              // square wheel at 0.38 would be a 178px disc owning the whole
              // hero; at 0.16 it is 75px, a shade taller than the car it
              // replaces, which is right for the mark the app is named after.
              width: Math.round(parent.width * 0.16)
              // Square, because a wheel is. `aspect` was a Car property and
              // does not exist here -- reading it would give width/undefined
              // and collapse the height binding to NaN.
              height: width
              tint: root.dim(root.engineOn ? 0.85 : root.connected ? 0.55 : 0.16)
              running: root.engineOn
              // NO `spin` AT ALL, deliberately.
              //
              // The two components mean different things by the word. Car.qml
              // turned `spin` into the DURATION of an infinite wheel
              // animation; SteeringWheel.qml passes it straight to
              // ctx.rotate as RADIANS. Carrying the old 0.42 across would not
              // have spun anything -- it would have hung the wheel at a
              // permanent 24-degree tilt. The wheel says "running" with its
              // hub, which is the convention the bar icon already uses, so
              // there is nothing left for rotation to add.
            }

            Column {
              id: heroCol
              // Anchored to BOTH edges, not just the right one.
              //
              // With only a right anchor the column was as wide as its widest
              // child and floated against the Stop button, leaving the left
              // fifth of the panel empty. Spanning the gap puts the vehicle
              // name where a name belongs -- at the left edge -- and gives the
              // children a real width to elide and wrap against, which
              // `width: heroCol.width` was previously resolving circularly.
              anchors.left: parent.left
              // Against the BUTTON ROW, which is the widest thing on the right
              // in both states -- wider than the car above it, and far wider
              // than the lone Start button.
              //
              // Anchoring to the car (or to Start) only cleared the FIRST line.
              // The status line sits level with the button row, so a column
              // sized to the narrow thing above ran straight under "Open
              // cluster" the moment that line had anything long to say. Sizing
              // to the widest element clears every line at once.
              anchors.right: heroBtns.left
              anchors.rightMargin: Style.space(10)
              spacing: Style.space(3)

              SectionLabel { text: "OMACAR" }

              Text {
                // "James' 2015 Honda CR-Z" -- whose, and what. `name` alone
                // said "2015 Honda" until records.vehicle() stopped throwing
                // the model away.
                text: root.car.title || root.car.name || "No car"
                color: root.fg
                font.family: root.bar.fontFamily
                font.pixelSize: root.fTitle
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                width: heroCol.width
              }

              Muted {
                visible: root.startError !== ""
                text: root.startError
                width: heroCol.width
                wrapMode: Text.WordWrap
              }

              Row {
                id: stateRow
                spacing: Style.space(7)
                width: heroCol.width

                Rectangle {
                  id: stateDot
                  width: Style.space(8); height: width; radius: width / 2
                  anchors.verticalCenter: parent.verticalCenter
                  color: root.state_ === "driving" ? root.cGreen
                       : root.state_ === "idling" ? root.cAmber
                       : root.state_ === "parked" ? root.cBlue : root.dim(0.3)
                }

                Muted {
                  anchors.verticalCenter: parent.verticalCenter
                  // Cut at the column edge. With no width this ran on under the
                  // button row -- which only became visible once the line had
                  // something long enough to say.
                  width: stateRow.width - stateDot.width - stateRow.spacing
                  elide: Text.ElideRight
                  text: {
                    var bits = [root.staleNote || root.state_]
                    if (root.vehicle.trim) bits.push(root.vehicle.trim)
                    if (root.odometer) bits.push(root.grouped(root.uDist(root.odometer))
                                                 + " " + root.units.dist)
                    return bits.join("   ·   ")
                  }
                }
              }

              // A Flow, not a Row. The button cluster on the right grew a
              // Demo control, which narrowed this column -- and a Row has no
              // opinion about running out of room, so the pills carried on
              // straight under the buttons. Wrapping is the honest response to
              // a narrower column.
              Flow {
                width: heroCol.width
                spacing: Style.space(6)
                topPadding: Style.space(3)

                // First, and unmissable. A panel showing somebody else's
                // invented car has to say so before it says anything else --
                // "simulated" alone is too easy to read past when the numbers
                // beside it look exactly like a real drive.
                //
                // ONE BADGE, NEVER TWO. This and the cyan "simulated" pill at
                // the end of the Flow were both lit through a demo, saying the
                // same thing twice in two colours at opposite ends of a row --
                // half the reason this panel read as busy. They are mutually
                // exclusive now, and neither exists when neither applies: a
                // Flow does not position an invisible child, so nothing is
                // left behind, not a gap and not a spacer.
                Pill {
                  visible: root.demoing
                  label: "DEMO"
                  tint: root.cAmber
                }

                Pill {
                  label: root.issues > 0
                    ? root.issues + (root.issues === 1 ? " issue" : " issues") : "no faults"
                  tint: root.issues > 0 ? root.cAmber : root.cGreen
                }

                Pill {
                  label: (root.svc && root.svc.next)
                    ? (root.svc.next.short || root.svc.next.item)
                      + "  " + Math.max(0, root.svc.next.life) + "%" : ""
                  tint: root.lifeColor(root.svc && root.svc.next ? root.svc.next.life : null)
                }

                // Not the demo's badge -- that is DEMO, above. This one is for
                // the other way of ending up with invented numbers: the garage
                // is on the built-in simulator rather than a car, in the real
                // tree, with no demo running at all. It is a true thing about
                // the data on screen and it stays.
                //
                // It is suppressed while demoing, because there it was only
                // ever a second word for the badge already showing. And it is
                // driven by the rollup, which is blanked the instant the demo
                // stops (see onDemoingChanged) -- that, not this pill, is why
                // it used to linger after the demo was switched off.
                Pill {
                  visible: !root.demoing && root.car.simulated === true
                  label: "simulated"
                  tint: root.cCyan
                }
              }
            }
          }

          PanelSeparator { foreground: root.fg }

          // ---- tabs ----
          Row {
            id: tabRow
            width: column.width
            spacing: Style.space(4)

            Repeater {
              model: root.tabs

              Rectangle {
                required property var modelData
                readonly property bool sel: root.tab === modelData.id
                width: (tabRow.width - Style.space(4) * (root.tabs.length - 1)) / root.tabs.length
                height: Math.round(root.fBody * 2.3)
                radius: Style.space(6)
                color: sel ? root.dim(0.14) : (tabMouse.containsMouse ? root.dim(0.07) : "transparent")
                Behavior on color { ColorAnimation { duration: 120 } }

                Text {
                  anchors.centerIn: parent
                  text: parent.modelData.label
                  color: parent.sel ? root.fg : root.dim(0.55)
                  font.family: root.bar.fontFamily
                  font.pixelSize: root.fCaption
                  font.weight: parent.sel ? Font.DemiBold : Font.Normal
                }

                MouseArea {
                  id: tabMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.tab = parent.modelData.id
                }
              }
            }
          }

          // ================= NOW =================
          Column {
            width: column.width
            spacing: Style.space(11)
            visible: root.tab === "now"

            // ONE line, not the list.
            //
            // Three alert cards stacked here pushed the speed, the odometer
            // and everything else below the fold -- and this feed is mostly
            // "OmaCar is watching", which nobody opens the panel to read. The
            // count and the newest headline are what the glance is for; the
            // rest is a tap away on its own page.
            Rectangle {
              visible: root.liveAlerts.length > 0
              width: parent.width
              height: Style.space(40)
              radius: Style.space(8)
              color: notifSumMouse.containsMouse ? root.cCardHover : root.cCard
              Behavior on color {
                ColorAnimation { duration: 120 }
              }

              Rectangle {
                id: sumDot
                anchors.left: parent.left
                anchors.leftMargin: Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(8)
                height: width
                radius: width / 2
                color: root.criticalAlerts > 0 ? root.cRed : root.dayAlerts > 0 ? root.cAmber : root.cBlue
              }

              Text {
                id: sumCount
                anchors.left: sumDot.right
                anchors.leftMargin: Style.space(9)
                anchors.verticalCenter: parent.verticalCenter
                text: root.liveAlerts.length + (root.liveAlerts.length === 1 ? " notification" : " notifications")
                color: root.cCardInk
                font.family: root.bar.fontFamily
                font.pixelSize: root.fCaption
                font.weight: Font.Medium
              }

              Text {
                anchors.left: sumCount.right
                anchors.leftMargin: Style.space(9)
                anchors.right: sumChev.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                text: root.liveAlerts.length > 0 ? root.liveAlerts[0].title || "" : ""
                color: root.cCardSub
                elide: Text.ElideRight
                font.family: root.bar.fontFamily
                font.pixelSize: root.fCaption
              }

              Text {
                id: sumChev
                anchors.right: parent.right
                anchors.rightMargin: Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                text: "\u203A"
                color: root.cCardSub
                font.family: root.bar.fontFamily
                font.pixelSize: root.fBody
              }

              MouseArea {
                id: notifSumMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.notifOpen = true
              }
            }

            // The headline is road speed while moving and the odometer while
            // not: a stopped car showing a big zero is a gauge shouting a
            // number nobody asked for.
            Item {
              width: parent.width
              height: nowHero.implicitHeight

              Row {
                id: nowHero
                spacing: Style.space(12)

                Column {
                  spacing: Style.space(1)
                  SectionLabel {
                    text: root.state_ === "driving" ? "ROAD SPEED"
                        : root.engineOn ? "IDLING" : "ODOMETER"
                  }
                  Row {
                    spacing: Style.space(5)
                    Text {
                      text: root.state_ === "driving"
                        ? String(Math.round(root.uSpeed(root.live.speed || 0)))
                        : (root.engineOn ? root.grouped(root.live.rpm || 0)
                           : root.grouped(root.uDist(root.odometer)))
                      color: root.fg
                      font.family: root.bar.fontFamily
                      font.pixelSize: root.fHero
                      font.weight: Font.DemiBold
                    }
                    Text {
                      anchors.bottom: parent.children[0].bottom
                      anchors.bottomMargin: Style.space(4)
                      text: root.state_ === "driving" ? root.units.speed
                          : (root.engineOn ? "rpm" : root.units.dist)
                      color: root.ink(0.88)
                      font.family: root.bar.fontFamily
                      font.pixelSize: root.fCaption
                    }
                  }
                }
              }
            }

            // Engine load, as the one bar that says how hard it is working.
            Column {
              width: parent.width
              spacing: Style.space(4)
              visible: root.engineOn

              // Label left, reading right. Neither child was anchored, so both
              // sat at x=0 and the throttle figure was printed straight over
              // "ENGINE LOAD" -- two strings in one place, which is what the
              // overlap in the panel actually was.
              Item {
                width: parent.width
                height: Math.max(loadLabel.implicitHeight, loadValue.implicitHeight)

                SectionLabel {
                  id: loadLabel
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  text: "ENGINE LOAD"
                }

                Muted {
                  id: loadValue
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  // Never wider than the space the label leaves.
                  width: Math.min(implicitWidth,
                                  parent.width - loadLabel.implicitWidth - Style.space(10))
                  horizontalAlignment: Text.AlignRight
                  elide: Text.ElideRight
                  text: Math.round(root.live.load || 0) + "%"
                    + (root.live.throttle !== undefined
                       ? "   ·   throttle " + Math.round(root.live.throttle) + "%" : "")
                }
              }

              MiniMeter {
                width: parent.width
                foreground: root.fg
                value: (root.live.load || 0) / 100
                tint: (root.live.load || 0) > 80 ? root.cAmber : root.cGreen
              }
            }

            Grid {
              width: parent.width
              columns: 3
              columnSpacing: Style.space(10)
              rowSpacing: Style.space(12)

              // Instantaneous economy while moving; today's average while not.
              // A stopped car has no economy at all — the figure is undefined,
              // not zero — and a dash where a number lives reads as a fault.
              Stat {
                readonly property bool liveEcon: root.state_ === "driving" && root.live.lphk
                width: (parent.width - Style.space(20)) / 3
                label: "ECONOMY"
                value: liveEcon ? root.econStr(root.live.lphk, false)
                  : (root.perf && root.perf.day ? root.econStr(root.perf.day.lphk, false) : "—")
                unit: root.units.econ
                note: liveEcon
                  ? (root.live.basis === "load" ? "load estimate" : "from mass air flow")
                  : "today's average"
              }

              Stat {
                width: (parent.width - Style.space(20)) / 3
                label: "COOLANT"
                value: root.live.coolant !== undefined && root.live.coolant !== null
                  ? String(Math.round(root.uTemp(root.live.coolant))) : "—"
                unit: root.units.temp
                tint: (root.live.coolant || 0) > 105 ? root.cRed : root.fg
                note: (root.live.coolant || 0) < 70 ? "still warming" : "at temperature"
              }

              Stat {
                width: (parent.width - Style.space(20)) / 3
                label: "BATTERY"
                value: root.live.volts ? root.live.volts.toFixed(1) : "—"
                unit: "V"
                tint: (root.live.volts || 14) < 12.2 ? root.cAmber : root.fg
                note: (root.live.volts || 0) > 13.2 ? "charging" : "not charging"
              }

              Stat {
                width: (parent.width - Style.space(20)) / 3
                label: "FUEL"
                value: root.live.fuel_pct !== undefined && root.live.fuel_pct !== null
                  ? String(Math.round(root.live.fuel_pct)) : "—"
                unit: "%"
                tint: (root.live.fuel_pct || 100) < 15 ? root.cAmber : root.fg
                note: root.vehicle.tank_l
                  ? "≈ " + root.uVol(root.vehicle.tank_l * (root.live.fuel_pct || 0) / 100).toFixed(1)
                    + " " + root.units.vol : ""
              }

              Stat {
                width: (parent.width - Style.space(20)) / 3
                label: "FUEL TRIM"
                value: root.live.ltft !== undefined && root.live.ltft !== null
                  ? (root.live.ltft > 0 ? "+" : "") + root.live.ltft.toFixed(1) : "—"
                unit: "%"
                tint: Math.abs(root.live.ltft || 0) > 6 ? root.cAmber : root.fg
                note: "long term, bank 1"
              }

              Stat {
                width: (parent.width - Style.space(20)) / 3
                label: "INTAKE AIR"
                value: root.live.intake !== undefined && root.live.intake !== null
                  ? String(Math.round(root.uTemp(root.live.intake))) : "—"
                unit: root.units.temp
                note: root.live.ambient !== undefined && root.live.ambient !== null
                  ? "ambient " + Math.round(root.uTemp(root.live.ambient)) + root.units.temp : ""
              }
            }

            PanelSeparator { foreground: root.fg }

            Column {
              width: parent.width
              spacing: Style.space(2)

              SectionLabel { text: "CONNECTION" }

              KV {
                width: parent.width
                k: "Adapter"
                v: (root.live.adapter || root.vehicle.adapter || "—")
                   + (root.live.port ? "   ·   " + root.live.port : "")
              }
              KV {
                width: parent.width
                k: "Protocol"
                v: root.live.protocol || root.vehicle.protocol || "—"
              }
              KV {
                width: parent.width
                k: "Reading"
                v: root.connected
                  ? (root.car.stale !== null && root.car.stale !== undefined
                     ? root.car.stale + "s old" : "live")
                  : "no link"
                tint: root.connected ? root.cGreen : root.dim(0.5)
              }
              KV {
                width: parent.width
                visible: root.vehicle.vin !== undefined
                k: "VIN"
                v: root.vehicle.vin || ""
              }
            }
          }

          // ================= DRIVE =================
          Column {
            width: column.width
            spacing: Style.space(12)
            visible: root.tab === "drive"

            Grid {
              width: parent.width
              columns: 2
              columnSpacing: Style.space(12)
              rowSpacing: Style.space(12)

              Repeater {
                model: [
                  { "key": "day",   "label": "TODAY" },
                  { "key": "week",  "label": "LAST 7 DAYS" },
                  { "key": "month", "label": "THIS MONTH" },
                  { "key": "year",  "label": "THIS YEAR" }
                ]

                Stat {
                  required property var modelData
                  readonly property var w: root.perf ? root.perf[modelData.key] : null
                  readonly property var d: (w && w.prev)
                    ? root.econDelta(w.lphk, w.prev.lphk) : null

                  width: (parent.width - Style.space(12)) / 2
                  label: modelData.label
                  value: w ? root.distStr(w.km, false) : "—"
                  unit: root.units.dist
                  note: w ? root.econStr(w.lphk) + (d && d.text
                          ? "   " + d.arrow + d.text : "") : ""
                  noteTint: d ? root.deltaColor(d) : root.dim(0.5)
                }
              }
            }

            Column {
              width: parent.width
              spacing: Style.space(6)
              visible: root.perf && (root.perf.months || []).length > 1

              Item {
                width: parent.width
                height: monthsLabel.implicitHeight
                SectionLabel { id: monthsLabel; text: "TWELVE MONTHS" }
                Row {
                  anchors.right: parent.right
                  spacing: Style.space(9)
                  Row {
                    spacing: Style.space(4)
                    Rectangle {
                      width: Style.space(7); height: Style.space(7); radius: 2
                      color: root.cBlue
                      anchors.verticalCenter: parent.verticalCenter
                    }
                    Muted { text: root.units.dist; anchors.verticalCenter: parent.verticalCenter }
                  }
                  Row {
                    spacing: Style.space(4)
                    Rectangle {
                      width: Style.space(7); height: Style.space(2); radius: 1
                      color: root.cAmber
                      anchors.verticalCenter: parent.verticalCenter
                    }
                    Muted { text: root.units.econ; anchors.verticalCenter: parent.verticalCenter }
                  }
                }
              }

              MonthChart {
                width: parent.width
                height: Style.space(96)
                foreground: root.fg
                accent: root.cBlue
                line: root.cAmber
                labelSize: root.fMicro
                series: {
                  var out = [], m = (root.perf ? root.perf.months : []) || []
                  for (var i = 0; i < m.length; i++) {
                    var mm = parseInt(String(m[i].month).split("-")[1], 10)
                    out.push({
                      "label": root.monthNames[mm - 1].charAt(0),
                      "dist": root.uDist(m[i].km),
                      "econ": m[i].lphk ? root.uEcon(m[i].lphk) : null
                    })
                  }
                  return out
                }
              }
            }

            PanelSeparator { foreground: root.fg }

            Column {
              width: parent.width
              spacing: Style.space(2)

              SectionLabel { text: "THIS YEAR" }

              KV {
                width: parent.width
                k: "Fuel burned"
                v: root.perf && root.perf.year
                  ? root.uVol(root.perf.year.litres).toFixed(0) + " " + root.units.vol
                    + (root.perf.year.cost ? "   ·   " + root.money(root.perf.year.cost) : "")
                  : "—"
              }
              KV {
                width: parent.width
                k: "Trips"
                v: root.perf && root.perf.year
                  ? root.perf.year.trips + " over " + root.perf.year.days + " days" : "—"
              }
              KV {
                width: parent.width
                k: "Engine running"
                v: root.perf && root.perf.year ? root.mins(root.perf.year.engine_s) : "—"
              }
              KV {
                width: parent.width
                k: "Fastest"
                v: root.perf && root.perf.year && root.perf.year.top_kph
                  ? Math.round(root.uSpeed(root.perf.year.top_kph)) + " " + root.units.speed : "—"
              }
              KV {
                width: parent.width
                k: "Records from"
                v: root.perf ? root.isoDate(root.perf.since) : "—"
              }
            }

            PanelSeparator { foreground: root.fg }

            Column {
              width: parent.width
              spacing: Style.space(4)
              visible: (root.car.trips || []).length > 0

              SectionLabel { text: "RECENT TRIPS" }

              Repeater {
                model: (root.car.trips || []).slice(0, 5)

                Item {
                  required property var modelData
                  width: parent.width
                  height: tripRow.implicitHeight + Style.space(6)

                  // Anchored to both edges, and it has to be.
                  //
                  // Every column in this row is sized as a fraction of
                  // `parent.width`, and a Row with no width of its own takes
                  // its width from its children -- so the children were asking
                  // the row how wide it was while the row was asking them.
                  // Qt breaks that with a binding loop warning and whatever
                  // width it had reached; both anchors give the row a real
                  // width to divide up instead.
                  Row {
                    id: tripRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(10)

                    Column {
                      width: (parent.width - Style.space(20)) * 0.46
                      spacing: Style.space(1)
                      Body {
                        text: modelData.label || modelData.kind
                        elide: Text.ElideRight
                        width: parent.width
                      }
                      Muted {
                        text: root.shortDate(modelData.t0) + "  " + root.clockOf(modelData.t0)
                          + "   ·   " + root.mins(modelData.moving_s + modelData.idle_s)
                      }
                    }

                    Body {
                      width: (parent.width - Style.space(20)) * 0.27
                      text: root.distStr(modelData.km)
                      horizontalAlignment: Text.AlignRight
                      anchors.verticalCenter: parent.verticalCenter
                    }

                    Body {
                      width: (parent.width - Style.space(20)) * 0.27
                      text: root.econStr(modelData.lphk)
                      horizontalAlignment: Text.AlignRight
                      anchors.verticalCenter: parent.verticalCenter
                    }
                  }
                }
              }
            }
          }

          // ================= HEALTH =================
          Column {
            width: column.width
            spacing: Style.space(10)
            visible: root.tab === "health"

            Text {
              width: parent.width
              visible: (root.car.faults || []).length === 0 && (root.car.watch || []).length === 0
              text: root.car.have_history
                ? "No trouble codes stored, and nothing in the samples worth flagging."
                : "No diagnostics yet."
              color: root.ink(0.88)
              wrapMode: Text.WordWrap
              font.family: root.bar.fontFamily
              font.pixelSize: root.fCaption
            }

            // Every code the ECU is holding, with the day it first appeared and
            // the day it last did — a code that set once in February is a
            // different problem from one that sets every cold morning.
            Repeater {
              model: root.car.faults || []

              Rectangle {
                required property var modelData
                width: parent.width
                height: faultCol.implicitHeight + Style.space(20)
                radius: Style.space(8)
                color: modelData.active ? root.dim(0.06) : "transparent"
                border.width: 1
                border.color: modelData.active
                  ? Qt.rgba(root.severityColor(modelData.severity).r,
                            root.severityColor(modelData.severity).g,
                            root.severityColor(modelData.severity).b, 0.35)
                  : root.dim(0.10)

                // Both edges, which is also what re-arms the margin below.
                //
                // Every child here is `width: parent.width` and wraps, so a
                // column with no width of its own is a binding loop dragging
                // WordWrap re-layout -- the most expensive thing in Qt Quick --
                // behind it every time the cache lands. And
                // anchors.margins does nothing without an edge to hold it off,
                // which is why these cards had text sitting on their borders.
                Column {
                  id: faultCol
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.margins: Style.space(10)
                  spacing: Style.space(4)

                  Item {
                    width: parent.width
                    height: codeText.implicitHeight

                    Text {
                      id: codeText
                      text: modelData.code
                      color: modelData.active ? root.severityColor(modelData.severity)
                                              : root.dim(0.45)
                      font.family: root.bar.fontFamily
                      font.pixelSize: root.fBody
                      font.weight: Font.DemiBold
                      font.letterSpacing: 0.8
                    }

                    Pill {
                      anchors.right: parent.right
                      anchors.verticalCenter: codeText.verticalCenter
                      label: modelData.status
                      tint: modelData.active ? root.severityColor(modelData.severity)
                                             : root.dim(0.4)
                    }
                  }

                  Body {
                    width: parent.width
                    text: modelData.descr
                    wrapMode: Text.WordWrap
                    color: modelData.active ? root.fg : root.dim(0.55)
                  }

                  Muted {
                    width: parent.width
                    visible: modelData.detail !== undefined && modelData.detail !== ""
                    text: modelData.detail || ""
                    wrapMode: Text.WordWrap
                  }

                  Muted {
                    width: parent.width
                    text: {
                      var bits = []
                      if (modelData.system) bits.push(modelData.system)
                      if (modelData.count) bits.push("seen " + modelData.count + "×")
                      if (modelData.first_seen)
                        bits.push("first " + root.shortDate(modelData.first_seen))
                      if (modelData.last_seen)
                        bits.push("last " + root.shortDate(modelData.last_seen))
                      return bits.join("   ·   ")
                    }
                    color: root.dim(0.4)
                  }

                  // The freeze frame: what the engine was doing at the instant
                  // the code set. It is the difference between "the sensor is
                  // bad" and "the sensor is bad on a cold start".
                  Muted {
                    width: parent.width
                    visible: modelData.freeze !== null && modelData.freeze !== undefined
                    text: {
                      var f = modelData.freeze
                      if (!f) return ""
                      var bits = []
                      if (f.rpm !== undefined) bits.push(root.grouped(f.rpm) + " rpm")
                      if (f.speed !== undefined)
                        bits.push(Math.round(root.uSpeed(f.speed)) + " " + root.units.speed)
                      if (f.coolant !== undefined)
                        bits.push(Math.round(root.uTemp(f.coolant)) + root.units.temp)
                      if (f.load !== undefined) bits.push(f.load + "% load")
                      if (f.ltft !== undefined) bits.push("trim +" + f.ltft + "%")
                      if (f.soc !== undefined) bits.push("IMA " + f.soc + "%")
                      return bits.length ? "Freeze frame:  " + bits.join("   ") : ""
                    }
                    color: root.dim(0.45)
                  }
                }
              }
            }

            // Not codes — things the sample stream says before the car has
            // decided to complain about them.
            Column {
              width: parent.width
              spacing: Style.space(6)
              visible: (root.car.watch || []).length > 0

              SectionLabel { text: "NOTICED IN THE DATA" }

              Repeater {
                model: root.car.watch || []

                Column {
                  required property var modelData
                  width: parent.width
                  spacing: Style.space(2)

                  Row {
                    spacing: Style.space(7)
                    Rectangle {
                      width: Style.space(7); height: width; radius: width / 2
                      anchors.verticalCenter: parent.verticalCenter
                      color: root.severityColor(modelData.severity)
                    }
                    Body {
                      text: modelData.title
                      anchors.verticalCenter: parent.verticalCenter
                    }
                    Muted {
                      visible: modelData.seen !== null && modelData.seen !== undefined
                      text: modelData.seen ? root.shortDate(modelData.seen) : ""
                      anchors.verticalCenter: parent.verticalCenter
                    }
                  }

                  Muted {
                    width: parent.width
                    text: modelData.detail || ""
                    wrapMode: Text.WordWrap
                    leftPadding: Style.space(14)
                  }
                }
              }
            }
          }

          // ================= SERVICE =================
          Column {
            width: column.width
            spacing: Style.space(11)
            visible: root.tab === "service"

            // The one that is nearest, said plainly — this is the question the
            // tab exists to answer, and it should not need reading a table.
            Rectangle {
              width: parent.width
              visible: root.svc !== null && root.svc.next !== undefined
              height: nextCol.implicitHeight + Style.space(22)
              radius: Style.space(9)
              color: root.dim(0.06)
              border.width: 1
              border.color: Qt.rgba(root.lifeColor(root.svc ? root.svc.next.life : null).r,
                                    root.lifeColor(root.svc ? root.svc.next.life : null).g,
                                    root.lifeColor(root.svc ? root.svc.next.life : null).b, 0.38)

              // Both edges, for the same two reasons as faultCol above: the
              // children are all parent.width, and the margin is inert without
              // a horizontal anchor to hold it off.
              Column {
                id: nextCol
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: Style.space(12)
                spacing: Style.space(6)

                SectionLabel { text: "NEXT DUE" }

                Item {
                  width: parent.width
                  height: nextName.implicitHeight

                  Text {
                    id: nextName
                    text: root.svc ? root.svc.next.item : ""
                    color: root.fg
                    font.family: root.bar.fontFamily
                    font.pixelSize: root.fTitle
                    font.weight: Font.DemiBold
                  }

                  Text {
                    anchors.right: parent.right
                    anchors.baseline: nextName.baseline
                    text: root.svc ? Math.max(0, root.svc.next.life) + "%" : ""
                    color: root.lifeColor(root.svc ? root.svc.next.life : null)
                    font.family: root.bar.fontFamily
                    font.pixelSize: root.fTitle
                    font.weight: Font.DemiBold
                  }
                }

                MiniMeter {
                  width: parent.width
                  foreground: root.fg
                  value: root.svc ? Math.max(0, root.svc.next.life) / 100 : 0
                  tint: root.lifeColor(root.svc ? root.svc.next.life : null)
                }

                Muted {
                  width: parent.width
                  wrapMode: Text.WordWrap
                  text: {
                    if (!root.svc) return ""
                    var s = root.svc.next, bits = []
                    if (s.km_left !== null && s.km_left !== undefined)
                      bits.push(s.km_left < 0
                        ? "overdue by " + root.distStr(Math.abs(s.km_left))
                        : "in " + root.distStr(s.km_left))
                    if (s.due_on) bits.push("by " + root.isoDate(s.due_on))
                    if (s.last_on) bits.push("last done " + root.isoDate(s.last_on)
                      + (s.last_km ? " at " + root.grouped(root.uDist(s.last_km))
                         + " " + root.units.dist : ""))
                    return bits.join("   ·   ")
                  }
                }

                Muted {
                  width: parent.width
                  visible: root.svc && root.svc.next.note !== ""
                  text: root.svc ? (root.svc.next.note || "") : ""
                  color: root.dim(0.4)
                  wrapMode: Text.WordWrap
                }
              }
            }

            Column {
              width: parent.width
              spacing: Style.space(8)
              visible: root.svc !== null

              Item {
                width: parent.width
                height: bookLabel.implicitHeight
                SectionLabel { id: bookLabel; text: "THE BOOK" }
                Muted {
                  anchors.right: parent.right
                  text: root.svc
                    ? (root.svc.due > 0 ? root.svc.due + " due or due soon" : "nothing due")
                    : ""
                  color: root.svc && root.svc.due > 0 ? root.cAmber : root.dim(0.45)
                }
              }

              Repeater {
                model: root.svc ? root.svc.items : []

                Column {
                  required property var modelData
                  width: parent.width
                  spacing: Style.space(3)

                  Item {
                    width: parent.width
                    height: itemName.implicitHeight

                    Row {
                      id: itemName
                      spacing: Style.space(6)
                      Body {
                        text: modelData.item
                        anchors.verticalCenter: parent.verticalCenter
                      }
                      // Honda's Maintenance Minder letters and numbers, kept
                      // because the shop asks for the code, not the name.
                      Rectangle {
                        visible: modelData.code !== ""
                        width: Math.round(root.fMicro * 1.9)
                        height: width
                        radius: Style.space(3)
                        color: root.dim(0.12)
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                          anchors.centerIn: parent
                          text: modelData.code
                          color: root.dim(0.6)
                          font.family: root.bar.fontFamily
                          font.pixelSize: root.fMicro
                          font.weight: Font.DemiBold
                        }
                      }
                    }

                    Text {
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      text: Math.max(0, modelData.life) + "%"
                      color: root.lifeColor(modelData.life)
                      font.family: root.bar.fontFamily
                      font.pixelSize: root.fCaption
                      font.weight: Font.DemiBold
                    }
                  }

                  MiniMeter {
                    width: parent.width
                    implicitHeight: Style.space(3)
                    foreground: root.fg
                    value: Math.max(0, modelData.life) / 100
                    tint: root.lifeColor(modelData.life)
                  }

                  Muted {
                    width: parent.width
                    color: root.dim(0.4)
                    elide: Text.ElideRight
                    text: {
                      var bits = []
                      if (modelData.km_left !== null && modelData.km_left !== undefined)
                        bits.push(modelData.km_left < 0
                          ? "over by " + root.distStr(Math.abs(modelData.km_left))
                          : root.distStr(modelData.km_left) + " left")
                      if (modelData.due_on) bits.push("due " + root.isoDate(modelData.due_on))
                      if (modelData.by) bits.push("by " + modelData.by)
                      return bits.join("   ·   ")
                    }
                  }

                  Item { width: 1; height: Style.space(4) }
                }
              }
            }
          }

          PanelSeparator { foreground: root.fg }

          // ---- footer ----
          Item {
            width: column.width
            height: stamp.implicitHeight

            // Just the timestamp now that both buttons have moved to the hero.
            // It had been anchored only vertically, which left it at x=0 --
            // sitting underneath "Open cluster" rather than beside it.
            Muted {
              id: stamp
              anchors.left: parent.left
              color: root.dim(0.35)
              text: root.car.checked ? "updated " + root.since(root.nowSec - root.car.checked) : ""
            }
          }
        }
      }
    }
  }
}
