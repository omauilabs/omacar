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
You never hear files or test output, and a diff only when you ask "read me the
diff" (a few sentences about what changed, never code). Approving and merging happen in
GitHub, never from the car.

Two pieces:

- **The relay**, `omacar work serve`, runs the sessions. It runs on the tablet
  first (parked only unless you set `while_moving`), then on the box.
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
host = "tablet"          # "tablet" or "box"
while_moving = true      # false (the default): the tablet is parked-only. See below.
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

## Work while driving: `while_moving`

By default the tablet is parked-only (below). Setting `while_moving = true` in
`work.toml` is your decision to use Work on the road, and it lifts every
parked-only rule on the tablet: sessions start and keep running while the car
moves, whisper and Piper run, clips are never held, and the Work tab shows the
PR link and a diff panel while moving. `examples/work.toml` sets it; a missing
value means `false`. It must be `true` or `false` (anything else is an error).
Quiet hours, the three hard refusals, the sandbox, the pre-push checks and the
token are unchanged. `/health` reports `"while_moving"`.

The two risks are yours to weigh: the tablet's CPU is shared with drowsy
detection, and California Vehicle Code section 27602 and NHTSA's guideline
(glances under 2 seconds) both bear on screens in the driver's view.

- **Load warning.** While a session runs and the car is not parked, the relay
  reads the one-minute load average every 10 s. Two readings in a row above 90%
  of the cores make it say once per drive: "The tablet is busy — drowsy alerts may lag." A drive ends when the car parks.
- **Reading notice.** The first time in a drive that a PR or diff is on the
  Work tab while moving, a small dismissible line says: "Reading while driving:
  CA Vehicle Code §27602 restricts screens in the driver's view, and NHTSA's
  guideline is glances under 2 seconds. Shown because you chose it in
  work.toml." It does not come back until the next drive.
- **Read me the diff.** Say "read me the diff" (or what changed) with a project
  name. The relay takes the project's newest session that has a PR (or at least
  an imported branch), reads the diffstat and commit subjects from the trusted
  repo, scrubs them, and has a small no-tools model say at most four sentences
  about which parts changed and why: no code, no file contents. If the model
  fails you hear the file count instead. The Work tab's diff panel shows the
  stat lines for any session with a PR on its own, and the summary once you
  have asked for it (never the patch).

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
   `host = "box"`, and `systemctl --user enable --now omacar-work`. Point
   each project's `path` at a **separate clone** on the box, not the live
   checkout `~/Projects/omacar`: the runner fetches and pushes from it.
3. Copy the contents of the box's `~/.local/state/omacar/work/token` into the
   tablet's `work.toml` as `relay_token = "..."`.
4. On the tablet set `host = "box"` and
   `relay_url = "http://<box tailnet name>:7570"`.
5. The tablet still needs its own offline clips ("No signal, saved", "Work
   is offline", "Not started."): the page plays them when the box cannot be
   reached. If the tablet has Piper, run `tools/work_render_offline.py` there;
   otherwise render them on the box and copy `share/assets/work/*.wav` to the
   tablet's checkout.
6. The box's `work.toml` may set `tablet_node` (default `"omacar-1"`). The box
   relay answers only that tailnet node and refuses to start if it is empty.

## When sessions start and stop

- **On the tablet with `while_moving = false` (the default),** the relay starts a session only while parked, using the
  app's current rule (`lib/workpark.py`): connected, speed at or under
  3 km/h, no adapter hand-off, held for 60 s. After the relay starts, the car
  must be seen stopped for 60 s (a restart proves nothing about Park), and
  after any hand-off the 60 s start again. Unknown speed, link or
  daemon counts as moving. (The confirmed-Park rule from frame 0x191 is not
  merged yet.) When the car moves it stops running
  sessions and drops queued and pending tasks. whisper and Piper also run
  only while parked: a clip you record while moving is saved on the tablet
  (the Work tab says "Saved, sent when parked.") and goes to the relay once
  parked, where it is read back and asked "Start it?" as usual; lines made
  while moving are shown, not spoken. With `while_moving = true` none of this
  applies: the tablet behaves like the box below, and the load warning and
  reading notice stand in for the block.
- **On the box,** sessions are not tied to the car's motion. The Work tab
  shows PR views only while parked, unless `while_moving = true`.

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
  mind" answers by stopping that session. With two questions waiting, the
  card shows (and the car repeats) the one your answer will go to: the oldest.
  A second session's question is spoken only when it becomes that one, after
  the first is answered, stopped or has timed out.
- **Stopping.** "Stop" or "stop it" with nothing pending stops the running
  session; with sessions in two projects running it asks "Omacar or
  OmaLens?". "Stop omacar" names one.
- **Problems.** "Didn't catch that." "Omacar or OmaLens?" for two matches.
  "That's today's limit of sessions." "Work is offline." "No signal, saved."
  (the clip did not reach the relay within 5 s; it is kept and retried. The
  relay answers as soon as it holds the clip, before it transcribes it, so a
  slow transcription is never "No signal". The clip stays on disk until it is
  processed, so a relay restart picks it up again; the car's identifying
  strings are never written, so such a clip is only held until the next
  request that carries them, and is heard then. If
  transcribing fails you hear "Didn't catch that." and the clip can be resent).
  "Usage limit reached, sessions paused until <time>." (after a usage limit
  no new session starts for an hour).
- **Alerts first.** A drowsy or other alert cuts speech off; the line is
  replayed afterwards unless it is over 2 minutes old. Music ducks under speech.
- **Quiet hours.** While parked between the `quiet` hours (default
  21:00-07:00), lines are shown, not spoken. On the tablet the relay holds
  them; on the box (which cannot know where the car is) the relay always
  renders and the Work tab holds them, by the tablet's clock, while parked.

## Limits to know

- **Resume is not built.** If the relay restarts, a running session is reported
  "interrupted"; its folder and branch are kept, so you can pick it up by hand.
- Transcripts and spoken lines pass through the same by-value scrub as
  `lib/mcp.py` before any model sees them. Audio clips are deleted once
  transcribed; transcripts are kept 7 days.
- Out of scope: a wake word, hosts other than the tablet and the box, reading
  messages aloud.
