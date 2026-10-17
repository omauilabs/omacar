// Calls the Clips view, the Cameras tab's Save clip and Home's card share.
import { toast, withToken } from "./core.js";
import { getJSON, postJSON } from "./camapi.js";
import { pokeClipToast } from "./clipstoast.js";

export const listClips = (opts) => getJSON("/api/clips", opts);
export const getClip = (id, opts) => getJSON(`/api/clips/item?id=${encodeURIComponent(id)}`, opts);
export const mediaUrl = (id, file) => withToken(`/api/clips/media/${id}/${file}`);

// Save clip, one tap. "Clip saved" itself comes from the toast poller when
// the service has the incident; this only speaks for what the poller cannot:
// a refusal, or a save made without the service.
export async function saveClipNow() {
  try {
    const r = await postJSON("/api/clips/save", {});
    if (r && r.via === "loop") toast(r.note || "Saved as a locked loop clip.", "warn", 6000);
    else pokeClipToast();
    return r;
  } catch (e) {
    toast("Could not save the clip: " + e.message, "bad");
    return null;
  }
}
