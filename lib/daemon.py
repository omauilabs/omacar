"""omacar daemon — hold one connection, poll at tiered rates, publish a snapshot.

One long-lived connection. Opening a serial connection costs 5-8 seconds, so
nothing else in OmaCar is allowed to open one: the UI reads the snapshot this
writes, and the bar widget reads the cache.

It waits rather than exits. A car that is not talking yet — the ignition is
off, the cable is not in, the adapter is mid-reset — is the normal first
minute of a session, not a failure, and so is a lead pulled out at the end of
one. Both are the same loop.

    live.json       the current sample, rewritten atomically each fast tick
    telemetry.db    one row per second, for trips and history
"""
import json
import os
import signal
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connect  # noqa: E402
import survey  # noqa: E402
import telemetry  # noqa: E402

import records  # noqa: E402

LIVE = os.path.join(connect.STATE, "live.json")
# Resolved at open time rather than at import: the vehicle can change between
# the two, and it does — that is exactly what survey.prepare() is for.
DB = None
PIDFILE = os.path.join(connect.STATE, "daemon.pid")

# THE "SURVEY NOW" REQUEST FILE.
#
# The daemon owns the serial port -- one process, one /dev/ttyUSB0 -- so the
# API server cannot read the car itself. Before this existed the app's
# "Scan all systems" button called records.snapshot(), which reads the
# DATABASE, and dressed the already-stored result in a 190ms-per-module
# progress animation. The data was real (the daemon had collected it) but the
# button did not do what it said, and it finished fast enough that the person
# pressing it correctly suspected it of faking.
#
# A file is the right mechanism here rather than a socket or a signal: every
# other cross-process handoff in OmaCar is a file in the state directory
# (live.json, bench-pty, daemon.pid), it survives either side restarting, and
# a daemon that is not running simply never consumes it -- which the API can
# see and report honestly instead of pretending.
SURVEY_REQUEST = os.path.join(connect.STATE, "survey-now")

FAST_HZ = 5.0
MID_EVERY = 5          # fast ticks
SLOW_EVERY = 25

# HOW LONG TO WAIT BETWEEN ATTEMPTS AT A CAR THAT IS NOT ANSWERING.
#
# Backoff rather than a fixed interval, because the thing usually being waited
# out is an ignition that is off, and an ELM327 is reset (ATZ) by every open:
# hammering one every two seconds for an hour is pointless and unkind to the
# adapter. The ceiling is deliberately low, though. The moment somebody turns
# the key they expect the gauges, and a wait past ten seconds stops reading as
# "it is coming up" and starts reading as "it is broken".
RETRY_MIN = 2.0
RETRY_MAX = 8.0

SIM_PID = os.path.join(connect.STATE, "sim.pid")


def sim_running():
    """Is the simulator writing live.json?

    `omacar sim start` already refuses while the daemon is up; this is the
    other half of that pair, which never existed. Two processes writing
    live.json five times a second do not merge — the gauge flickers between two
    cars — and the daemon can now be started by three routes (the CLI, the udev
    rule through hotplug.py, and the app's Connect button), so the check belongs
    here, where all three arrive, rather than in any one of them.
    """
    try:
        with open(SIM_PID, encoding="utf-8") as f:
            os.kill(int(f.read().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def open_db():
    db = sqlite3.connect(records.DB)
    db.execute("""CREATE TABLE IF NOT EXISTS samples (
        t REAL PRIMARY KEY, rpm REAL, speed REAL, load REAL, throttle REAL,
        coolant REAL, intake REAL, maf REAL, stft REAL, ltft REAL,
        timing REAL, lphk REAL, eff REAL, soc REAL)""")
    db.execute("CREATE INDEX IF NOT EXISTS samples_t ON samples(t)")
    # Databases written before the hybrid column existed -- which on this car
    # is 34,000 rows of real driving -- must not be thrown away to gain it.
    # ADD COLUMN is the one schema change SQLite makes in place and in constant
    # time, and old rows then read NULL, which is the honest value for a period
    # when nobody was asking the car the question.
    if "soc" not in {r[1] for r in db.execute("PRAGMA table_info(samples)")}:
        db.execute("ALTER TABLE samples ADD COLUMN soc REAL")
    db.commit()
    return db


def publish(payload):
    # STAMP WHOSE SAMPLE THIS IS. The daemon and the simulator write this same
    # file, and a reader has no other way to tell one car's news from another's
    # -- see the note in records.live(). Stamped here rather than at each call
    # site so no publisher can forget; a failure to read the garage leaves the
    # stamp off, which is the old behaviour and is accepted downstream.
    try:
        import garage
        payload = dict(payload, vehicle=garage.current())
    except Exception:                                         # noqa: BLE001
        pass
    tmp = LIVE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, LIVE)


def value_of(result):
    if result is None or result.is_null():
        return None
    v = result.value
    return float(v.magnitude) if hasattr(v, "magnitude") else v


# Startup contention budget. Twelve tries at 2.5s is thirty seconds, which
# comfortably outlasts a dtclog sweep of four modules -- the thing that was
# actually holding the port when this failed. Bounded rather than infinite so
# `omacar daemon start` still reports failure on a car that is genuinely off,
# rather than hanging until somebody notices.
STARTUP_TRIES = 12
STARTUP_BACKOFF = 2.5
STARTUP_LOCK_WAIT = 20.0


def _unwind(_signum, _frame):
    """Turn a stop signal into the exception the loop already knows about.

    `omacar daemon stop` is `kill "$pid"` -- a plain SIGTERM. Python's default
    disposition for SIGTERM terminates the interpreter WITHOUT unwinding the
    stack, so the `finally` at the bottom of main() never ran: live.json was
    left saying connected=true with the daemon already gone, its pid file was
    left behind, and the serial port was released without anybody being told.

    Everything downstream then read a dead file as a live car. The bar panel
    offered "Stop" for fifteen seconds after there was nothing to stop, and
    then "Reconnect" -- which reads as "the link dropped" -- for a daemon the
    owner had deliberately shut down.

    Raising KeyboardInterrupt is the whole fix, because the loop already
    catches it and the finally already does the right thing. Nothing else
    needed to change.
    """
    raise KeyboardInterrupt


def main():
    if sim_running():
        sys.exit("omacar daemon: the simulator is running — stop it first "
                 "(omacar sim stop)")

    os.makedirs(connect.STATE, exist_ok=True)

    # Installed before the port is opened, so a stop arriving during a slow
    # connect still unwinds rather than leaving a half-built daemon behind.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _unwind)
        except (ValueError, OSError):
            # Not the main thread, or a platform without SIGHUP. Losing the
            # handler is worth less than losing the daemon on startup.
            pass

    # lease=False: this process is the one the lease exists to move aside.
    #
    # BUT IT STILL HAS TO WAIT ITS TURN.
    #
    # lease=False skips request_port(), which skips take_port_lock() as well --
    # so the daemon used to open the serial device regardless of who was
    # already talking to it. On 2026-09-03 dtclog was mid-sweep when the daemon
    # started; two readers hit one ELM327, the adapter answered neither
    # properly, and the daemon exited "not connected". The owner was sitting in
    # the car with a perfectly healthy adapter reading 13.7 V.
    #
    # hand_over() has retried twenty times since it was written, because a
    # daemon that gives up its port mid-run and cannot get it back is obviously
    # broken. Startup had exactly the same problem and one attempt. So: wait
    # for whoever holds the exclusive lock to finish, then retry on the same
    # terms the running daemon already uses.
    import obd

    conn = port = kind = None
    for attempt in range(STARTUP_TRIES):
        # Taking the flock and immediately dropping it is how this waits for an
        # in-flight one-off command WITHOUT holding a lock for the daemon's
        # whole life -- which would deadlock every command that needs the port.
        if connect.take_port_lock(timeout=STARTUP_LOCK_WAIT):
            connect.release_port_lock()
        try:
            conn, port, kind = connect.connect(timeout=1.0, fast=True,
                                               lease=False)
        except SystemExit:
            raise
        except Exception:                                     # noqa: BLE001
            conn = None
        if conn is not None and conn.status() == obd.OBDStatus.CAR_CONNECTED:
            break
        if conn is not None:
            try:
                conn.close()
            except Exception:                                 # noqa: BLE001
                pass
            conn = None
        if attempt < STARTUP_TRIES - 1:
            # The adapter needs a moment after another reader has closed it --
            # the same 0.6s courtesy hand_over() already pays, rounded up
            # because a sweep can take longer than a lease to unwind.
            time.sleep(STARTUP_BACKOFF)

    if conn is None or conn.status() != obd.OBDStatus.CAR_CONNECTED:
        status = str(conn.status()) if conn is not None else "no connection"
        publish({"connected": False, "status": status, "port": port})
        sys.exit(f"omacar daemon: not connected ({status}) "
                 f"after {STARTUP_TRIES} attempts")

    # THE PIDFILE GOES DOWN BEFORE THE CAR ANSWERS, NOT AFTER.
    #
    # It used to be written only once the car had replied, which was harmless
    # while a daemon that could not connect exited on the spot. Now that it
    # waits, the pidfile is the only thing that says a daemon exists at all:
    # `omacar daemon start` polls for it for ten seconds and otherwise reports
    # "daemon did not start", the panel matches on that exact string, and the
    # same pidfile is the guard that stops a second press of Connect forking a
    # second daemon onto the same port. Written here, all three keep telling the
    # truth while we wait for the ignition. The finally: block removes it.
    with open(PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    # `omacar daemon stop` sends SIGTERM, which by default ends the process
    # where it stands: the finally: block never runs, so live.json keeps
    # whatever it last said. That did not show while the last thing it said was
    # a sample, because a sample goes stale and records.live() notices — but
    # "waiting for the car" carries no timestamp to go stale, so a stopped
    # daemon would go on politely waiting on screen forever. Turning the signal
    # into the exception the loop already handles gives Ctrl-C and stop one
    # exit path between them.
    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)

    # python-obd stays out of the module header because it lives in the venv,
    # and it is silenced here the way connect.connect() silences it. That used
    # to be enough on its own — we never reached obd before a successful
    # connect. We do now, once per retry, and its logger has a great deal to say
    # about exactly the failure this loop exists to wait out.
    import logging
    import obd
    # Scoped to obd's own tree, not the whole logging module. The blunt form of
    # this was logging.disable(logging.CRITICAL), which is process-wide,
    # permanent, and silences every library in the venv plus anything this
    # daemon ever logs about itself -- in a process that is now meant to run
    # for weeks and to say when it reconnects. propagate=False keeps obd's
    # children from climbing past the level onto the root handler.
    obd.logger.setLevel(logging.CRITICAL)
    obd.logger.propagate = False

    conn = db = None
    port, kind = connect.resolve()
    supported = set()
    # (id, OBDCommand) for every VALIDATED profile entry -- see lib/signals.py.
    # Empty for a car nobody has mapped, which changes nothing.
    sig_cmds = []
    cmds = {"fast": [], "mid": [], "slow": []}
    sample, tick, last_row = {}, 0, 0.0
    # The last complete published sample, echoed back during a hand-off.
    sample_snapshot = {}
    started = time.time()

    # The slow half of the car: codes, readiness, on-board tests, the VIN.
    # None of it changes at gauge rate and all of it costs bus time the gauge
    # would rather have, so it runs once on connect and then rarely. Without
    # it a real adapter feeds the sample stream and nothing else, and the
    # whole diagnostic side of the app has nothing to show.
    last_survey = 0.0

    def slow_pass():
        nonlocal last_survey
        last_survey = time.time()
        try:
            survey.survey(conn, obd)
        except Exception as e:                            # noqa: BLE001
            # A survey that fails must never take the gauge down with it.
            print(f"survey failed: {e}", file=sys.stderr, flush=True)

    def try_connect():
        """One attempt at the whole prologue. True when the gauge can run.

        Opening the port is only the first half of getting started: which car
        this is decides which record we open, and both halves have to happen
        again after every reconnection. They live together in here because
        either can fail on a car that is only half awake, and a VIN read that
        times out should be one more retry rather than the end of the process.
        """
        nonlocal conn, port, kind, supported, cmds, db, sample, sig_cmds
        found, found_kind = connect.resolve()
        if found:
            # Kept up to date on every attempt: an adapter that is unplugged and
            # put back can come up as a different device node, and the port is
            # what we publish for the app to name.
            port, kind = found, found_kind
        try:
            # lease=False: this process is the one the lease exists to move
            # aside, so it must never ask itself to let go.
            c, port, kind = connect.connect(timeout=1.0, fast=True, lease=False)
        except SystemExit as e:
            # connect.connect() exits rather than raises: no adapter at all, or
            # a device node we are not in the group to read. Neither is fatal
            # here — both are things somebody is about to fix by pushing a plug
            # in or logging back in — but the message is the useful half, so it
            # goes into live.json where the app can put it on screen.
            publish({"connected": False, "status": "waiting for the car",
                     "port": port, "note": str(e).splitlines()[0]})
            return False
        except Exception as e:                            # noqa: BLE001
            publish({"connected": False, "status": "waiting for the car",
                     "port": port, "note": f"{type(e).__name__}: {e}"})
            return False

        if c.status() != obd.OBDStatus.CAR_CONNECTED:
            publish({"connected": False, "status": "waiting for the car",
                     "port": port,
                     "note": "the adapter is answering and the car is not. "
                             "Is the ignition on?"})
            try:
                c.close()
            except Exception:                             # noqa: BLE001
                pass
            return False

        conn = c
        try:
            supported = {x.name for x in conn.supported_commands}
            cmds = {tier: [n for n in names if n in supported]
                    for tier, names in (("fast", telemetry.FAST),
                                        ("mid", telemetry.MID),
                                        ("slow", telemetry.SLOW))}
            # Which car is this? The VIN decides which record we open, so it has
            # to be read before anything opens one. Switching the file under a
            # live SQLite handle does not fail loudly — it refuses the next
            # write with "attempt to write a readonly database", which is a
            # memorable way to spend an evening.
            survey.prepare(conn, obd)

            # A VALIDATED IDENTIFIER RIDES THE SAME LINK.
            #
            # Every step of the coverage strategy worked except this one: a
            # profile entry a person had checked against something real was a
            # line in a file, because nothing polled it. It is an OBDCommand on
            # this connection now, with its own header, queried on the slow
            # tier. Any failure here is a car with no learned readings, which is
            # exactly what it was before -- refusing to start over a malformed
            # profile is the one outcome worse than ignoring it.
            sig_cmds = []
            try:
                import profile as profilelib
                import signals
                import garage
                # garage.current() is the active vehicle KEY: the VIN once a
                # survey has read one, or the simulator's key. A simulated car
                # has no profile to consult and must not be matched to a real
                # one's by accident.
                _vin = garage.current()
                if _vin == garage.SIM_KEY:
                    _vin = None
                _slug = profilelib.for_vin(_vin) if _vin else None
                if _slug:
                    _doc, _ = profilelib.load(_slug)
                    sig_cmds = signals.commands(_doc or {})
                    if sig_cmds:
                        print(f"learned readings: {', '.join(i for i, _c in sig_cmds)}",
                              file=sys.stderr, flush=True)
            except Exception as e:                            # noqa: BLE001
                print(f"no learned readings: {e}", file=sys.stderr, flush=True)
                sig_cmds = []
            if db is not None:
                # Reconnecting may mean a different car, so the record is
                # reopened rather than reused — and the old handle is closed
                # rather than dropped, because this loop can run for weeks.
                db.close()
                db = None
            db = open_db()
        except Exception as e:                            # noqa: BLE001
            print(f"could not open the record: {e}", file=sys.stderr, flush=True)
            try:
                conn.close()
            except Exception:                             # noqa: BLE001
                pass
            conn = None
            return False

        # Nothing from the previous connection survives a reconnection. It may
        # be a different adapter or a different car, and a value this ECU never
        # reports would otherwise sit in live.json unchanged for the rest of the
        # session, looking exactly like a reading.
        sample = {}
        return True

    def wait_for_car():
        """Block until the car answers, saying so in live.json while we wait.

        THE DAEMON NO LONGER EXITS WHEN THE CAR IS QUIET.

        It used to. The classic first run — push the cable in, ignition still
        off, start OmaCar — left a dead daemon behind, and turning the key
        afterwards fixed nothing because nothing retried. The panel honestly
        gates "connected" on the age of live.json, so what the person saw was
        the plugin dropping out. Waiting is the truthful behaviour: the adapter
        is there, the car is not talking yet, and that is a sentence we can put
        on the screen instead of an error.
        """
        delay = RETRY_MIN
        after_lease = False
        while True:
            # Somebody else wants the adapter. Honour the lease even with
            # nothing open to give up: the pidfile is already down, so
            # connect.request_port() is waiting on us to publish "yielded" and
            # would otherwise spend its whole timeout and then tell the user the
            # daemon is holding a port it has not even managed to open. This is
            # also why the daemon never takes the advisory lock in connect.py —
            # that lock belongs to the command we are stepping aside for.
            if yielded_until():
                publish({"connected": False, "status": "yielded", "port": port,
                         "note": "a command is using the adapter"})
                after_lease = True
                time.sleep(0.3)
                continue
            if after_lease:
                # LET THE ADAPTER BREATHE. python-obd opens with ATZ, a chip
                # reset that takes a second or two to answer, and the command
                # has only just closed the port. Opening into a teardown gets
                # silence back, which we would then report as a car that will
                # not talk.
                time.sleep(0.6)
                after_lease = False
            if try_connect():
                return
            time.sleep(delay)
            delay = min(RETRY_MAX, delay * 2)

    def yielded_until():
        """Deadline on an active lease, or None when there is no live one.

        A lease past its deadline is treated as absent AND removed: a one-off
        command that crashed must not be able to pause the gauge permanently.
        """
        try:
            with open(connect.PORT_YIELD, encoding="utf-8") as f:
                until = float(f.read().strip())
        except (OSError, ValueError):
            return None
        if time.time() >= until:
            try:
                os.remove(connect.PORT_YIELD)
            except OSError:
                pass
            return None
        return until

    def yield_snapshot():
        """What to publish while the adapter is lent out.

        A HAND-OFF USED TO LOOK EXACTLY LIKE A DEAD DAEMON.

        This published {"connected": false, "status": "yielded"} and nothing
        else -- no `t`, no values. Every reader treats a sample with no
        timestamp as stale and a sample with no values as an offline car, so
        for the ten to twenty seconds of a DTC sweep the whole app decided the
        link was gone: the bar icon went grey, Stop became Connect, a driving
        car became "parked", and the watchdog filed an adapter-lost alert.
        Then it all came back. Every five minutes, for the length of a drive.

        The daemon is alive and the car is still on the other end of the cable,
        so it keeps saying so -- with a current `t`, the last readings it took,
        and a status that admits where the port went.
        """
        out = dict(sample_snapshot)
        out.update({
            "connected": False,
            "status": "yielded",
            "handover": True,
            "port": port,
            "t": time.time(),
            "note": "a command is using the adapter",
        })
        # A FUNCTIONAL TEST KEEPS THE TRACE LIVE. lib/actuate.py reads the
        # car through the borrowed link and leaves its readings in a
        # sidecar; while that sidecar is fresh the snapshot carries them,
        # says a test is running, and stays "connected" -- because it is.
        # Stale, and the daemon's own last readings are the truth again.
        try:
            import actuate as actlib
            with open(actlib.BORROWED, encoding="utf-8") as f:
                b = json.load(f)
            if time.time() - float(b.get("t") or 0) < actlib.BORROWED_FRESH:
                vals = dict(out.get("values") or {})
                vals.update({k: v for k, v in (b.get("values") or {}).items()
                             if v is not None})
                out.update({"values": vals, "connected": True,
                            "status": "test", "test": b.get("test"),
                            "note": "a functional test is using the adapter"})
        except (OSError, ValueError, TypeError):
            pass
        return out

    def hand_over():
        """Close the port, wait for the lease to end, then take it back."""
        nonlocal conn, supported, cmds, db, sig_cmds
        publish(yield_snapshot())
        # Guarded: hand_over can be reached before the first successful
        # connect, and closing None is not a hand-over, it is a traceback.
        if conn is not None:
            try:
                conn.close()
            except Exception:                                 # noqa: BLE001
                pass
        # Keep publishing through the wait. A lease can outlast the staleness
        # window, and a hand-off that goes stale is indistinguishable from a
        # daemon that died holding the port.
        while yielded_until():
            time.sleep(0.3)
            publish(yield_snapshot())
        # Same courtesy in the other direction: the command has just closed the
        # port and the adapter is mid-teardown.
        time.sleep(0.6)
        # Reconnect. survey.prepare() runs again because the VIN decides which
        # record is open, and a different car could have been plugged in while
        # we were not looking.
        for attempt in range(20):
            try:
                conn.close()
            except Exception:                                 # noqa: BLE001
                pass
        # wait_for_car() does the rest: it waits the lease out, gives the
        # adapter its moment, reopens, and works out which car it is talking to
        # this time, because a different one could have been plugged in while we
        # were not looking. It also no longer gives up after twenty attempts — a
        # command that leaves the port in a state we cannot immediately reopen
        # used to end the daemon, and waiting is exactly what the ignition-off
        # case needs anyway.
        wait_for_car()

    try:
        wait_for_car()
        slow_pass()
        db_broken = False

        while True:
            # Someone wants the adapter. Step aside rather than make them
            # believe the cable has failed.
            if yielded_until():
                hand_over()

            # An on-demand survey, asked for by the app. Claimed by removing
            # the file BEFORE the work, so a survey that throws cannot leave a
            # request behind to be retried every tick forever.
            if os.path.exists(SURVEY_REQUEST):
                try:
                    os.remove(SURVEY_REQUEST)
                except OSError:
                    pass
                slow_pass()

            names = list(cmds["fast"])
            if tick % MID_EVERY == 0:
                names += cmds["mid"]
            if tick % SLOW_EVERY == 0:
                names += cmds["slow"]

            try:
                for n in names:
                    sample[n] = value_of(conn.query(getattr(obd.commands, n)))
                if sig_cmds and tick % SLOW_EVERY == 0:
                    for sig_id, cmd in sig_cmds:
                        r = conn.query(cmd, force=True)
                        # A decoder that refused -- a reply too short for its
                        # formula -- is None, and None is published as None:
                        # a learned reading that cannot be read shows a dash,
                        # never a stale number.
                        sample[sig_id] = None if r.is_null() else r.value
            except Exception as e:                            # noqa: BLE001
                # THE LEAD CAME OUT — or the adapter browned out, or the car
                # went to sleep between one query and the next. One serial
                # exception used to end the process, because only
                # KeyboardInterrupt was caught, which made a tug on a USB cable
                # indistinguishable from quitting on purpose. Treat it as the
                # disconnection it is and go back to waiting: the loop that
                # handles an ignition which is not on yet is the same loop that
                # handles an adapter which has just been pushed back in.
                print(f"lost the adapter: {e}", file=sys.stderr, flush=True)
                publish({"connected": False, "status": "lost", "port": port,
                         "note": "the adapter stopped answering"})
                try:
                    conn.close()
                except Exception:                             # noqa: BLE001
                    pass
                wait_for_car()
                continue

            lphk, lph = telemetry.economy(sample.get("MAF"), sample.get("SPEED"))
            eff, basis = telemetry.efficiency(sample)
            now = time.time()

            live_payload = {
                "connected": True, "port": port, "kind": kind,
                "t": now, "uptime": now - started,
                "protocol": conn.protocol_name(),
                "supported": sorted(supported),
                "values": dict(sample),
                "economy_lphk": lphk, "fuel_lph": lph,
                "efficiency": eff, "efficiency_basis": basis,
            }
            sample_snapshot.clear()
            sample_snapshot.update(live_payload)
            publish(live_payload)

            if now - last_row >= 1.0:
                try:
                    # Columns named rather than positional. The bare VALUES form
                    # this replaces was correct only for as long as nobody added a
                    # column, and it would have failed silently by writing the new
                    # value into the wrong field the first time somebody did.
                    db.execute(
                        "INSERT OR REPLACE INTO samples "
                        "(t, rpm, speed, load, throttle, coolant, intake, maf, "
                        "stft, ltft, timing, lphk, eff, soc) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (now, sample.get("RPM"), sample.get("SPEED"),
                         sample.get("ENGINE_LOAD"), sample.get("THROTTLE_POS"),
                         sample.get("COOLANT_TEMP"), sample.get("INTAKE_TEMP"),
                         sample.get("MAF"), sample.get("SHORT_FUEL_TRIM_1"),
                         sample.get("LONG_FUEL_TRIM_1"), sample.get("TIMING_ADVANCE"),
                         lphk, eff, sample.get("HYBRID_BATTERY_REMAINING")))
                    db.commit()
                except sqlite3.Error as e:
                    # The gauge outranks the record. A row that cannot be written --
                    # a full disk, or the readonly-database error try_connect() warns
                    # about -- used to end the process and take the live reading down
                    # with it, which is the wrong way round: the person is watching a
                    # number, not a database. Said once, because at one row a second a
                    # broken record would otherwise write the log file the disk has no
                    # room for.
                    if not db_broken:
                        print(f"cannot write the record: {e}",
                              file=sys.stderr, flush=True)
                        db_broken = True
                else:
                    db_broken = False
                last_row = now

            if now - last_survey >= survey.EVERY:
                slow_pass()

            tick += 1
            time.sleep(1.0 / FAST_HZ)
    except KeyboardInterrupt:
        pass
    finally:
        # This block is why _unwind() below exists. Reaching it is the ONLY
        # thing that tells the rest of the app the car is gone.
        publish({"connected": False, "status": "stopped", "port": port})
        # Both can be None: the daemon is now allowed to spend its whole life
        # waiting for a car that never answers, and it may be stopped there.
        if db is not None:
            db.close()
        if conn is not None:
            try:
                conn.close()
            except Exception:                                 # noqa: BLE001
                pass
        # Only if it is still OURS. `omacar daemon stop` removes the pidfile
        # itself and the app's Connect button can start the next daemon inside
        # the same second, so a departing process that deleted the file
        # unconditionally could delete its successor's — leaving a daemon
        # running that nothing can see, and a second one one click away.
        try:
            with open(PIDFILE, encoding="utf-8") as f:
                ours = f.read().strip() == str(os.getpid())
        except OSError:
            ours = False
        if ours:
            try:
                os.remove(PIDFILE)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
