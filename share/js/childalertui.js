// Mounts the child alert (childalert.js) and its camera watcher (childwatch.js)
// on the app page, as ignitionui.js mounts the parking countdown: a module of
// its own, so a test page can import childalert.js without a card appearing.
// The card first, then the watcher: nothing is reported that nothing could show.
import { temp } from "./core.js";
import { mountChildAlert } from "./childalert.js";
import { startChildWatch } from "./childwatch.js";

mountChildAlert({ t: (c) => temp(c) });
startChildWatch();
