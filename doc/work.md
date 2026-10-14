# Work by voice

Capability `agent.work`. Speak a task in the car, hear short spoken reports,
answer a session's questions by voice, and review the result when parked. The
design and its reasons are in `doc/design/2026-10-13-work-voice-relay.md`; this
page is the how-to.

## What Work is

You tap push-to-talk on the Work tab and say a task: "Omacar: fix the fastbaud
read timeout." The relay transcribes it, reads it back ("Start it?"), and only
a short spoken yes starts it (see "What you hear"). A Claude Code session then works in its own
clone, runs the project's gate, and opens a **draft PR**. You hear milestones
only: started, a question, gate failed, PR opened, finished, stuck, stopped.
You never hear diffs, files or test output. Approving and merging happen in
GitHub, never from the car.

Two pieces:

- **The relay**, `omacar work serve`, runs the sessions. It runs on the tablet
  first (parked only), then on the box.
- **The Work tab** records, queues clips on disk, and plays the replies. It
  talks to the relay through the app's own server, never directly.

## What a session can never do

Every session runs in Claude Code's own sandbox (bubblewrap on Linux; it needs
the `bubblewrap` and `socat` packages). OmaCar sets: sandbox enabled, no
unsandboxed escape, fail if the sandbox is unavailable (the session does not
start), and the network list. Inside it:

- Writes are confined to the session folder and no Unix sockets are reachable
  (no keyring, SSH agent or D-Bus). These two limits are Claude Code's sandbox
  defaults, not something OmaCar sets; they are checked at rollout step 2, not
  by OmaCar's tests.
- No network except the project's `network = [...]` list in `work.toml`.
  github.com domains are refused in that list: the agent never talks to GitHub.
- Credential environment variables (`GH_TOKEN`, `GITHUB_TOKEN`,
  `SSH_AUTH_SOCK`, `DBUS_SESSION_BUS_ADDRESS`) are blanked.
- No web tools: WebFetch and WebSearch are refused ("Work sessions have no
  web access."). A session cannot read online docs.
- No reads of the credential places (`~/.ssh`, `~/.config/gh`,
  `~/.claude/.credentials.json`, `~/.netrc`, ...), the app's state and
  config, the live checkout, `~/Projects/.omacar-wt/` or any
  `share/assets/private/`. They are Claude Code `Read` deny rules for the
  file tools and `sandbox.filesystem.denyRead` for shell commands.

On top of that, `workgates.judge()` refuses the obvious forms early (a push to
`main`, `gh pr merge`, `ssh omacar-1`, `sudo`, `systemctl`, a GitHub-hosted
runner in CI, the car's adapter, the live checkout, ...) and gives the car a
sentence to say. It is the first line, not the wall; the sandbox is the wall.

The runner, not the session, pushes `work/<slug>` and opens the draft PR. It
does that from the trusted checkout (`path`), outside the sandbox, with git
hooks disabled. A session never merges, never marks a PR ready, never touches
`main`.

Each session works in its own **clone** under `~/Projects/.omacar-work/` (not a
git worktree), on branch `work/<slug>`. The project's gate runs under
bubblewrap with no network, the session folder writable and credential paths
hidden. Without bubblewrap the gate fails closed. A gate that needs the network
will fail; deciding what to do about that is left to the owner.

## work.toml

`~/.config/omacar/work.toml`, edited by you. A project that is not listed
cannot be dispatched. See `examples/work.toml`.

```toml
host = "tablet"          # "tablet" (parked only) or "box"
daily_cap = 6            # sessions per day
quiet = [21, 7]          # hours when lines are shown, not spoken, while parked
# relay_url = "http://<box tailnet name>:7570"   # host = "box" only
# relay_token = "..."                           # host = "box" only
# tablet_node = "omacar-1"   # box only: the one tailnet node the relay answers

[project.omacar]
names = ["omacar", "car", "the car app"]
path  = "~/Projects/omacar-work-src"   # a separate clone, never ~/Projects/omacar
gate  = "test/all.sh"
# network = ["registry.npmjs.org"]     # domains the sandbox may reach; never GitHub
```

At most 2 sessions run at once; a third waits and is told so. The daily cap
defaults to 6.

## Setting up on the tablet

1. Install: `OMACAR_WORK=1 ./install.sh`. It installs `claude-agent-sdk` and
   `faster-whisper` into the omacar venv (run `omacar setup` first if there is
   no venv). Piper is a separate system package (GPL-3.0, never bundled); with
   it present the install also renders the offline lines. To do that by hand:
   `tools/work_render_offline.py` writes `no_signal`, `offline` and
   `not_started` into `share/assets/work/`. The wavs are git-ignored because
   the Piper voice's dataset is CC BY-NC-SA. Without Piper, lines are shown
   instead of spoken.
2. Install `bubblewrap` and `socat`.
3. Copy `examples/work.toml` to `~/.config/omacar/work.toml`. Point `path` at a
   **separate clone** of the project, never `~/Projects/omacar`.
4. Start the relay: `systemctl --user enable --now omacar-work`. The unit
   (`share/systemd/omacar-work.service`) runs `bin/omacar work serve` from
   `~/Projects/omacar`, the live checkout. For rollout steps 2 and 3, on a
   tablet worktree, do not enable it: run `bin/omacar work serve` by hand from
   that worktree instead.
5. Check it: `omacar work status`.

### The relay token

The relay refuses any request without a bearer token. At startup it writes one
to `~/.local/state/omacar/work/token` (mode 0600, kept across restarts). On
host `tablet` the app reads that file by itself. `omacar work status` uses the
token too (it is not a plain curl).

## Rollout

1. **Desk.** The relay and its tests with a fake SDK, then a recorded clip and
   a spoken reply.
2. **Tablet, one real dispatch.** One real session against a scratch repo,
   ending in a draft PR. Before trusting the sandbox, from inside a session
   confirm that a write outside its folder fails, that a connect to the
   D-Bus socket fails, and that `cat ~/.claude/.credentials.json` fails.
3. **Parked in the car.** The Work tab in a tablet worktree, never the live
   checkout until you say. Tested parked in the driveway.
4. **Move to the box.** Only then, and only with your go-ahead, on a drive.

## Moving to the box

1. Install Claude Code on the box and sign in (your step; no agent enters
   credentials). Install `bubblewrap`, `socat`, and Piper.
2. Run `OMACAR_WORK=1 ./install.sh` there, make a `work.toml` with
   `host = "box"`, and `systemctl --user enable --now omacar-work`.
3. Copy the contents of the box's `~/.local/state/omacar/work/token` into the
   tablet's `work.toml` as `relay_token = "..."`.
4. On the tablet set `host = "box"` and
   `relay_url = "http://<box tailnet name>:7570"`.
5. The box's `work.toml` may set `tablet_node` (default `"omacar-1"`). The box
   relay answers only that tailnet node and refuses to start if it is empty.

## When sessions start and stop

- **On the tablet,** the relay starts a session only while parked, using the
  app's current rule (`lib/workpark.py`): connected, speed at or under
  3 km/h, no adapter hand-off, held for 60 s. After the relay starts, the car
  must be seen stopped for 60 s (a restart proves nothing about Park), and
  after any hand-off the 60 s start again. Unknown speed, link or
  daemon counts as moving. (The confirmed-Park rule from frame 0x191 is not
  merged yet.) When the car moves it stops running
  sessions and drops queued and pending tasks.
- **On the box,** sessions are not tied to the car's motion, but the Work tab
  shows PR views only while parked.

## What you hear

- **Starting.** A rising tone, then you speak. Recording ends after 1.2 s of
  silence or 30 s.
- **Confirming.** "Omacar: fix the fastbaud read timeout. Start it?" A short
  affirmative from a fixed list starts the task: yes, yeah, yep, sure, ok,
  okay, go, start it, do it, go ahead, optionally plus one of please, it or
  now. Any no, stop or cancel word anywhere in the reply means it does not
  start. Silence or anything else: "Not started." after 20 s.
- **Milestones.** "Omacar session started." "Omacar has a question: ..."
  "Omacar: gate passed, draft PR 41 opened." "Omacar is stuck."
- **Answering a question.** Push-to-talk again. "Stop", "cancel" or "never
  mind" answers by stopping that session.
- **Problems.** "Didn't catch that." "Omacar or OmaLens?" for two matches.
  "That's today's limit of sessions." "Work is offline." "No signal, saved."
  "Usage limit reached, sessions paused until <time>."
- **Alerts first.** A drowsy or other alert cuts speech off; the line is
  replayed afterwards unless it is over 2 minutes old. Music ducks under speech.
- **Quiet hours.** While parked at home between the `quiet` hours (default
  21:00-07:00), lines are shown, not spoken.

## Limits to know

- **Resume is not built.** If the relay restarts, a running session is reported
  "interrupted"; its folder and branch are kept, so you can pick it up by hand.
- Transcripts and spoken lines pass through the same by-value scrub as
  `lib/mcp.py` before any model sees them. Audio clips are deleted once
  transcribed; transcripts are kept 7 days.
- Out of scope: a wake word, hosts other than the tablet and the box, reading
  messages aloud.
