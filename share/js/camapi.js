// JSON to and from the routes the cameras branch adds (lib/camroutes.py), and
// the URLs of its two streams. core.js keeps its own list of routes; these
// live apart so this branch adds a file rather than lines to one the
// foundation branch is also editing.
import { withToken } from "./core.js";

// ?still=1: every live feed asks for one frame and stops, so a headless
// screenshot is not held open by a stream that never ends.
export const STILL = new URLSearchParams(location.search).has("still");

async function call(path, opts) {
  const r = await fetch(withToken(path), Object.assign({ cache: "no-store" }, opts || {}));
  let body = null;
  try { body = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) throw new Error((body && body.error) || String(r.status));
  return body;
}

export const getJSON = (path) => call(path);
// `opts` (signal, etc.) is merged in on top of the JSON POST defaults, so a
// caller that needs an abortable request -- audiostate.js's applyAudio(),
// so a stuck /api/audio can never pile up behind Chromium's 6-connection
// limit -- can pass one without every other postJSON caller knowing it
// exists.
export const postJSON = (path, data, opts) => call(path, Object.assign({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}),
}, opts || {}));
export const liveUrl = (role) => withToken(`/api/cams/${role}/live${STILL ? "?frames=1" : ""}`);
export const clipUrl = (role, file) => withToken(`/api/cams/clip/${role}/${file}`);
