// Started beside the app (share/app.html), like awake.js, because it is not
// part of any view. It holds the Surface's volume where `omacar audio on`
// asked for it (lib/audio.py), at start and every half minute: plugging the
// AUX cable in switches to a port that keeps a volume of its own.
import { applyAudio } from "./audiostate.js";

applyAudio();
setInterval(applyAudio, 30000);
