// Started beside the app (share/app.html), like awake.js, because it is not
// part of any view.
//
// It holds the Surface's volume where `omacar audio on` asked for it
// (lib/audio.py), at start and every half minute: plugging the AUX cable in
// switches to a port that keeps a volume of its own. That half-minute apply
// is also what keeps drowsy mode's "AUX disconnected" lines current
// (audiostate.js onAudio). It is the page's one audio poll.
//
// It also runs drowsy mode: its screens first (drowsyui.js: the chip in the
// top bar main.js builds, the alert cards, the test card), then the engine
// (drowsyrun.js). In that order, so an engine never runs with no card to
// tap: if the screens cannot mount, this module stops before startDrowsy()
// and no alert can sound that nothing on screen could stop.
//
// NOTHING HERE PLAYS A SOUND OR NAMES THE VOICE. The engine already hands
// every cue to the page's one alert player (alertplayer.js alertPlayer(),
// the same one Begin's chime uses), and sets that player's name itself
// (drowsyrun.js apply). Setting either again here would add nothing, and a
// second hand-off would be one more way for a cue to play twice.
import { applyAudio } from "./audiostate.js";
import { startDrowsy } from "./drowsyrun.js";
import { mountDrowsyUI } from "./drowsyui.js";

applyAudio();
setInterval(applyAudio, 30000);

mountDrowsyUI();
startDrowsy().catch((e) => console.warn("drowsy mode did not start:", e));
