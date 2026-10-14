# Work by voice: dispatching coding sessions from the car

Capability `agent.work`. The Work tab today is a placeholder
(`share/js/views/work.js`). This spec turns it into a voice channel to coding
sessions on the owner's box: speak a task while driving, hear short spoken
reports, answer the session's questions by voice, and review the result when
parked.

## Decisions (owner, 2026-10-13)

- **Interaction: dispatch and spoken reports.** The owner speaks a task; a
  Claude Code session runs on the box; the car speaks milestones and the
  session's questions; the owner answers by voice. Diffs, approvals and PRs wait
  until the car is parked.
- **Autonomy while driving: branch and draft PR.** A session works on its own
  branch in its own worktree, runs the repo's gate, commits, pushes and opens a
  draft PR. It never merges, never touches `main`, never touches the tablet's
  live checkout or the car.
- **Trigger: push-to-talk now, wake word later.** One tap starts listening. A
  wake word is a later, opt-in follow-up that runs on the tablet.
- **Host: the omarchy box only.** One relay, on the box, as decided on
  2026-09-28 (no cloud hosting; the car reaches the box over Tailscale; sessions
  run on the owner's Claude subscription).
- **Approach A: thin car, smart box.** Speech-to-text, intent, sessions and
  speech rendering run on the box. The tablet records, queues and plays.

Rejected:

- **B, speech on the tablet.** Whisper and Piper on the Surface would compete for
  four cores and 8 GB with the cameras, drowsy detection and the CAN recorder,
  and would mean two speech installs to maintain.
- **C, Claude Code Remote Control from the Claude phone app.** It nearly works
  today, but it puts a phone in the driver's hand, is outside OmaCar's driving
  locks, and gives no control over branches. At most a parked-only fallback.

## What is true today (checked 2026-10-13)

- **The box has no Claude Code.** `claude` is not on its PATH since its home was
  rebuilt; `~/.claude` holds only `plugins`, `skills` and `themes`. Installing it
  and signing in is the owner's step; no agent enters credentials.
- **The box has voxtype** (`/usr/bin/voxtype`), 12 cores and 11 GB.
- **The tablet has neither voxtype nor Piper.** Its only microphone is the
  Surface's own, `Built-in Audio Analog Stereo`.
- **Existing voice:** OmaTalk (`voice/talk.qml`) toggles voxtype dictation into
  the focused window; `lib/screen.py` lets an assistant change the app's screen.
  Nothing speaks a session's progress.
- **Existing parts this reuses:** the audiobus (`share/js/audiobus.js`: music
  12 dB under full scale, alerts may use all of it), the red-light rule (Park
  confirmed only from frame 0x191 byte 0 = 0x01), the by-value scrub of the
  car's identifying fields (`VEHICLE_SECRET` and `_redact()` in `lib/mcp.py`),
  and `lib/ai.py`'s practice of shelling out to the owner's
  own `claude` rather than holding an API key.

## 1. Components

### On the box: the relay, `omacar work`

A systemd user service on the box. It listens on the box's tailnet address
only, never on `0.0.0.0`, and accepts requests only from the tailnet node
`omacar-1`, checked by Tailscale identity (`tailscale whois`), not by a shared
secret in the page.

1. **relay** — the HTTP API:
   - `POST /utterance` — one audio clip and its client id; answers with the
     utterance id.
   - `POST /answer` — a clip answering a pending question or confirmation.
   - `GET /events?after=<id>` — the session and speech events since an id.
   - `GET /clip/<id>` — a rendered speech clip.
   - `GET /health` — up, sessions running, today's session count.
2. **ears** — local whisper speech-to-text. The engine (whisper.cpp or
   faster-whisper) and model size are chosen in the plan by measuring both on
   the box with clips recorded in the car.
3. **intent** — turns a transcript into exactly one of: `new_task` (project +
   instruction), `status`, `answer`, `stop` (a session), `result` (read me the
   result). One small Claude call with a fixed JSON schema; the result is
   checked in Python against the project allowlist and its spoken names.
   Anything that does not check out is "Didn't catch that" or one either/or
   question, never a guess.
4. **sessions** — the runner, on the Python Claude Agent SDK. Each task gets:
   - a worktree under `~/Projects/.omacar-work/<repo>-<slug>`;
   - a branch `work/<slug>` cut from the repo's up-to-date default branch;
   - the repo's own `CLAUDE.md`/`AGENTS.md` and the owner's user-level rules,
     loaded as usual;
   - at the end: the repo's gate, a commit, a push of `work/<slug>`, and a draft
     PR through `gh`.
   At most two sessions run at once; a third waits and is told so.
5. **voice** — turns events into spoken lines and renders them with Piper on the
   box. Piper is a system package run as its own program (GPL-3.0; never
   bundled). Lines are spoken only at milestones: started, has a question, gate
   failed, PR opened, finished, stuck, stopped.

### On the tablet: the Work tab

- **Push-to-talk:** a large fixed button, recording in the kiosk page itself
  (`getUserMedia`, the built-in microphone).
- **Session list:** one line per session (project, state, last spoken line) and
  a card for a pending question.
- **Through the app's own server:** clips go to `lib/api.py`, which keeps them
  in an on-disk queue under the app's state directory and forwards them to the
  relay. The page never talks to the relay directly, so the queue survives dead
  zones and a reload, and the relay's address never reaches the browser.
- **Playback:** a new `speech` lane on the audiobus.
- **Offline lines:** the fixed lines the tablet must say with no relay ("No
  signal — saved", "Work is offline") are rendered by Piper on the box ahead of
  time and installed as plain audio files.

## 2. Data flow and how it sounds

### Starting a task

1. The owner taps push-to-talk. A short rising tone plays and music ducks.
   Recording stops after 1.2 s of silence or 30 s, whichever comes first.
2. The tablet queues the clip and forwards it. With no relay answer within 5 s,
   the tablet plays "No signal — saved" and retries in the background, oldest
   clip first.
3. The relay transcribes the clip and parses the intent. A new task is always
   read back first: *"Omacar: fix the fastbaud read timeout. Start it?"*
4. The owner answers "yes" or "no" with another tap. Silence never starts
   anything: with no answer within 20 s, the task is dropped and the car says
   "Not started."
5. The session runs. The owner hears milestones only, each at most two
   sentences. Examples:
   - *"Omacar session started."*
   - *"Omacar has a question: should the timeout be 0.8 seconds, or
     configurable?"*
   - *"Omacar: gate passed, draft PR 41 opened."*
   - *"Omacar is stuck: the tests need the box's Chromium, which is busy."*

### Questions from a session

The session blocks on the SDK's callback for its question. The question is
spoken once, and again the next time the car stops. The owner answers with
push-to-talk. If 30 minutes pass with no answer, the session writes the question
into its PR description, stops cleanly, and the car says so.

### Status on demand

"What's going on?" gets one line per session, newest first, three at most.

### What is never spoken, and what waits for Park

Diffs, file contents, test output and long reasons are never spoken. "Read me
the result" gives the PR's two-line summary. The full PR view on the Work tab
appears only while Park is confirmed (frame 0x191 = P, the red-light rule);
anything unknown counts as moving. Approving or merging is still done in GitHub,
never from the car.

### Who gets the speaker

- A drowsy alert, or any other alert, cuts speech off at once. The interrupted
  line is replayed when the alert ends, unless it is more than 2 minutes old.
- Spoken lines queue; two never overlap.
- Music ducks under speech as it does under alerts.

### Quiet hours

While the tablet is parked at home overnight, lines are held and shown, not
spoken: the owner's child sleeps near the tablet. "At home" and the hours are
settings in `work.toml`, defaulting to 21:00–07:00.

## 3. Gates and failure handling

These rules are enforced in the relay's Python, in the SDK's permission
callback, not asked of the model. Every refusal is logged, and spoken once per
session.

### What a session can never do

- Write outside its own worktree.
- `git push` anything but its own `work/<slug>` branch; force-push; rewrite
  history it has pushed.
- `gh pr merge`, mark a PR ready for review, approve a PR, or edit a PR that is
  not its own.
- Reach the car or another machine: no `ssh` to `omacar-1` or to any other
  host, no `omacar` subcommand that writes,
  tunes or takes the adapter, nothing in the tablet's live checkout. This sits
  under the car's own gates, not in place of them.
- Touch the box's `~/Projects/omacar` master checkout or any
  `share/assets/private/`.
- `sudo`, `systemctl`, package installs, or `docker … prune` (the box is a
  shared runner host).
- Put a GitHub-hosted runner in CI: an edit that adds `ubuntu-*`, `macos-*`,
  `windows-*`, or a bare `runs-on: self-hosted` is refused (self-hosted only,
  pinned by label).

### The project allowlist

`~/.config/omacar/work.toml` on the box, edited by the owner. Each project has
its spoken names, its path and its gate command. A project not listed cannot be
dispatched, and the car says so.

```toml
[project.omacar]
names = ["omacar", "car", "the car app"]
path  = "~/Projects/omacar"
gate  = "test/all.sh"
```

### Privacy

- Transcripts and spoken lines pass through the same by-value scrub as
  `lib/mcp.py`'s `_redact()` before any model sees them or they are logged.
  The values to hunt for (the `VEHICLE_SECRET` fields: VIN, plate, driver's
  name, …) travel from the tablet's server with each forwarded clip and are
  held in memory only, never written on the box.
- Audio clips are deleted once transcribed. Transcripts are kept for 7 days.

### Failures, and what the owner hears

| Failure | What happens | What the owner hears |
|---|---|---|
| Empty or unsure transcription | Nothing runs | "Didn't catch that." |
| Two projects match | One either/or question | "Omacar or OmaLens?" |
| Gate fails | Two repair attempts, then push and draft PR marked *gate failing* | "Omacar: gate still failing, draft PR 42 opened." |
| Relay down or box asleep | The tablet keeps its queue | "Work is offline", once per drive |
| Claude usage limit | Sessions pause; no retry loop | "Usage limit reached — sessions paused until \<time\>." |
| Session silent 20 min | Marked stuck; stopped at the next stop or on "stop" | "Omacar is stuck." |
| Relay restarts mid-session | Resume from the SDK session id; otherwise marked *interrupted* in the PR | "Omacar was interrupted." |

### Usage

Sessions use Sonnet by default; intent parsing and spoken lines use a small
model. A daily cap on sessions, 6 by default, set in `work.toml`, protects the
weekly limit.

## 4. Testing and rollout

### Tests

- **Gates (Python unit tests).** A table of tool calls that must be refused
  (push to `main`, `ssh omacar-1`, a write outside the worktree,
  `runs-on: ubuntu-latest`, `gh pr merge`, …) and calls that must be allowed. A
  refusal quietly becoming an allow is the bug that matters most here, so this
  table is the core of the suite.
- **Intent.** Recorded transcripts through a fake model: the allowlist, the
  either/or question, and that no answer other than a spoken yes starts a task.
- **Sessions.** A fake SDK emitting scripted events (question, gate failure, PR
  opened, silence, crash): the spoken lines, the timeouts and resume. Never
  calls the real Claude.
- **Speech (box only, behind a marker).** whisper on clips recorded in the car
  with road noise; Piper rendering one line.
- **Work tab (JS tests).** Push-to-talk, the offline queue, an alert pre-empting
  speech, the parked-only PR view (0x191 = P simulated), and quiet hours.
- **CI:** self-hosted only, `[self-hosted, Linux]`, as now.

### Rollout

1. **Prerequisite (owner):** install Claude Code on the box and sign in.
2. **The box alone.** The relay, then a desk test from the Mac: send a recorded
   clip, hear the reply. Then one real dispatch to a scratch repo, ending in a
   draft PR.
3. **Parked in the car.** The Work tab in a tablet worktree, never the live
   checkout until the owner says. Tested parked in the driveway.
4. **On a drive.** Only after step 3 passes, and only with the owner's go-ahead.

## Out of scope

- The wake word (its own follow-up, on the tablet).
- Hosts other than the box.
- Messages read aloud (`media.messages`).
- A Bluetooth push-to-talk button.
