# OmaCar updates itself, safely

Until now an update meant SSH and `git pull` on the tablet. This replaces it
with a release that is built beside the running one, proven, switched to
atomically, watched for 90 seconds, and undone if it does not come up. It
never updates a moving car, and it never updates by itself unless the owner
turns that on.

    omacar update status      what runs, what is waiting, what stops it now
    omacar update check       fetch the branch and say what is new (changes nothing)
    omacar update apply       build, test, switch, watch -- only when parked
    omacar update rollback    back to the previous release, one command
    omacar update history     every check, switch and rollback
    omacar update adopt       one-time setup (below)

Settings -> Updates shows the same: the running commit (sha, date, subject),
what is waiting with its PR titles, **Check now**, **Update when parked**,
**Roll back**, and the last result with the gate's steps. The buttons are
greyed, with the reason beside them, while the car is not parked, never
hidden. Home shows one quiet line in its footer when an update waits.

## Layout

    ~/Projects/omacar                 the owner's clone. Never reset, never edited.
    ~/Projects/.omacar-releases/<sha> one git worktree per release (last 3 kept)
    ~/Projects/omacar-current         a symlink to the release that runs

The clone only ever sees `git fetch` (it moves `refs/remotes/origin/<branch>`,
not HEAD or the working tree) and `git worktree add`. If it has uncommitted
changes, including untracked files, the update is refused and says so.

Files git does not carry but the app needs (the fetched MediaPipe bundle,
`share/assets/private/`, `share/models/`) are hard-linked into each new release
from the one that runs.

## Why a stable symlink, not re-running the unit install

The user units are installed with `__ROOT__` replaced by a path. Two ways to
change what they run: reinstall them with the new release's path, or make the
path never change. This uses the second, so `adopt` installs them once with
`__ROOT__` = `~/Projects/omacar-current` and `~/.local/bin/omacar` pointing
through the same link. A switch is then one `rename(2)` and a restart:

* it is atomic: a power cut leaves the old link or the new one, never neither;
* no unit file is rewritten and no `daemon-reload` races the restart;
* a unit that crashes and restarts by itself picks up whatever is current;
* rollback is the identical operation in the other direction.

`omacar update adopt` (once, from a clean clone) builds a release at the
clone's HEAD, repoints only units and `~/.local/bin` links that were already
installed from the clone, installs and enables `omacar-update.timer`, and
restarts nothing. After it, production runs from `omacar-current`; the clone is
for editing, and a change reaches the car by being committed to the branch.

## When it may apply (`may_apply`, pure)

All must hold; anything unreadable counts against:

* **parked**: not moving (speed > 0 in the last 2 min, or the ignition loop holds
  "moving"); the car's computers not answering (ignition on); and either the
  charger is off, or the car's daemon is reporting recently that it is not
  answering. A charger on with no word from the car is refused.
* no recording or camera incident being written (the `.lock` in
  `lib/camstore.py` is held, or an incident is still `pending`);
* no radar alert, no drowsy alert in the last 2 min, no awake/crying child
  state from the nursery document;
* battery >= 30 % or charging;
* the live checkout is clean.

They are checked at the start **and again after the gate**, which takes minutes.
The server enforces them too: the screen's buttons are a courtesy.

## Apply

1. `git fetch` the configured branch; target = `origin/<branch>`.
2. `git worktree add` at `~/Projects/.omacar-releases/<sha>`.
3. The gate, in that worktree, scratch state dirs, per-step timeouts: Python
   syntax of `lib/`, `test/guards_test.py`, `test/demoserve_test.py`, and
   `test/js_test.py` if a Chromium exists. First failure stops it.
   A failure removes the worktree, keeps the old release, and remembers the sha
   as rejected so it is not retried (a newer commit clears it).
4. Re-check the conditions; write `update-pending.json`; switch the symlink.
5. Restart the units that run from the checkout (those installed with the link
   path and active), the kiosk last.
6. Health, within 90 s: the server answers `/api/snapshot` **and reports it runs
   the new release** (`root` in `GET /api/update`), and, if the kiosk is
   running, its page has POSTed `/api/update/booted` since the switch (sent by
   `main.js` right after boot). If not: the symlink goes back, units restart,
   and the reason is recorded.
7. Prune to the last 3 releases (current and previous are always kept).

A "pending" marker survives a crash of the updater itself: the next
`omacar update tick` judges the release and rolls it back if unhealthy.
From the screen, apply runs in its own transient `systemd-run --user` unit,
since restarting the kiosk would otherwise take the updater with it.

## Config: `~/.config/omacar/update.json`

    { "branch": "main", "auto": false, "window": "02:00-05:00",
      "keep": 3, "health_s": 90, "min_battery": 30, "gate_skip": [] }

`auto` is **off** by default. When on, `omacar-update.timer` (every 20 min)
fetches and applies only inside `window` (may wrap midnight) and only when every
condition above holds. `gate_skip` may name `guards`, `demoserve`, `js`.

## Tests

`python3 test/update_test.py` runs real git against a local bare "origin" in a
scratch directory (no network, no real units: `systemctl` is a stand-in that
logs its calls): the condition function, window, health, adopt, check, dirty
refusal (files left byte-for-byte), refusal while moving, gate failure keeps
the old release, switch + restart order, automatic rollback (old server,
dead server), interrupted-update recovery, manual rollback, pruning, auto mode,
and the recording lock. JS: `test/js/update.test.js`.

## What is not verified without the tablet

systemd restarts and `systemd-run`, the real kiosk page's boot report, the real
JS gate under Chromium on Arch, and the live ignition/charger signals.
