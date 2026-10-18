# Work dashboard (Agent → Sessions)

A glanceable and parked-readable view of the Work relay's sessions. The relay
(`omacar work serve`, "Work by voice", draft PR omauilabs/omacar#36) is an
**optional dependency**: nothing here starts it, imports it or invents what it
would say.

## What the screen shows

- Counts by state (waiting for you, running, failed, done) and a source lamp:
  **Work relay · live**, **Simulated**, or **Relay not running**.
- One card per session the relay reports: project, repo (when reported), a state
  lamp, the relay's last line as the milestone (or the open question), elapsed
  time, and, parked, the draft PR link with the relay's pre-push gate result
  and **Show diff**.
- Show diff: the relay's diffstat as files grouped by folder (collapsible, large
  wrapped mono type, a change bar per file) and, if "read me the diff" has been
  said, the spoken summary.
- With the relay down: "Work relay not running — omacar work serve", the reason
  (no token, unreachable, refused) and no sessions. Never faked.

## What the relay does not report, so the screen does not show

The relay's `/health` row is `id, project, state, pr, repo, line`. It reports no
session title, no branch, no progress steps and no CI state. Elapsed time comes
from the session's `started` event in `/events`. "Pre-push gate passed/failing"
is the relay's own check on the tablet (events `pr_opened` / `gate_failed`), not
CI, and is labelled that way. A diff is the stat, not a patch (`/diff/<id>`
returns `{stat, summary}`), which is why files fold by folder and show a line
count, not hunks. The `+`/`-` characters in a stat are scaled by git and are not
exact counts, so none are shown.

## Moving vs parked (mirrors doc/work.md on the PR branch)

| | parked | moving, `while_moving = false` (default) | moving, `while_moving = true` |
|---|---|---|---|
| screen | full dashboard | glance: counts + the one session waiting for you | full dashboard |
| PR link, gate, Show diff | yes | **no** | yes, with the one-per-drive reading notice |

`while_moving` is read from the relay's `/health`, which reports the owner's
work.toml choice. Unknown (relay down) is the cautious side. "Moving" is
`store.lockedAsMoving`, the app's own rule. The pure logic is
`share/js/workdashlogic.js`, tested in `test/js/workdash.test.js`.
The server does not know the car's motion, so `/api/workdash/diff` is not
gated; the page never asks for a diff in a glance.

## Actions

None. The relay's only write is `POST /utterance` (a voice clip); it has no
pause or stop route. `POST /api/workdash/action` therefore answers 400 and
`actions` is always `[]`: no buttons are drawn. If the relay grows a stop route,
add it to `workclient.act` and the card, nowhere else.

## API (read-only; no cockpit writes)

- `GET /api/workdash` aggregate (`present`, `source` relay|sim|none,
  `simulated`, `while_moving`, `counts`, `waiting`, `sessions`, `actions`).
- `GET /api/workdash/diff?id=<session>` the relay's `{stat, summary}`.
- `POST /api/workdash/action` always 400.

`lib/workclient.py` reads the token from `$XDG_STATE_HOME/omacar/work/token`
(or `relay_token` in work.toml when `host = "box"`), sends it as a bearer token
to `relay_url` (default `http://127.0.0.1:7570`, http only, no redirects, 2 s
timeout) and sends nothing at all without a token. Demo server: all three
routes are inert stubs in `DEMO_ROUTES`.

## Simulator

`omacar workdash sim [seconds] [--while-moving]` rewrites
`$XDG_STATE_HOME/omacar/workdash-sim.json` every 2 s (stale after 15 s) with
three sessions: waiting (omacar), running (littleprints), done with a draft PR
and a diff (finances-tracker). It is used only when the real relay does not
answer, and the screen says **Simulated**. `omacar workdash status` prints what
the screen is told.

## Tests

`python3 test/workdash_test.py` (shaping, a fake relay speaking the real
routes, relay down, no token, refused token, diff and action routes, the
simulator) and `test/js/workdash.test.js` (moving vs parked, notice, elapsed,
stat parsing).
