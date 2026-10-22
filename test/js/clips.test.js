// Cameras → Clips (views/clips.js), mounted on a scripted server and hand-held
// timers: nothing polls, and no video is fetched (the sources are paths the
// runner's static server answers with a 404, which nothing here looks at).
import { eq, ok } from "./assert.js";
import { store } from "../js/core.js";
import clipsView from "../js/views/clips.js";

const T = Math.floor(Date.now() / 1000) - 600;
const inc = (o) => Object.assign({ id: "20261017-120000-brake", trigger: "brake", label: "Hard braking", t: T,
  peak_g: 0.62, speed_before: 62, speed_after: 0, state: "ready", sim: false, also: [], size: 90e6,
  spark: [62, 62, 62, 40, 0, 0],
  roles: { front: { joined: true, parts: [{ file: "front.mp4", start: T - 30, dur: 60 }] },
           rear: { joined: true, parts: [{ file: "rear.mp4", start: T - 30, dur: 60 }] } } }, o);
const rows = (n) => Array.from({ length: n * 10 + 1 }, (_, i) => ({ dt: -30 + i * 0.1 * 6, speed: i < 30 ? 62 : Math.max(0, 62 - (i - 30) * 4), rpm: 2000 }));
const item = (i) => ({ incident: i, telemetry: { rows: rows(6), t: T } });
const LIST = (incidents, o) => Object.assign({ running: true, accel: { present: false, why: "no accelerometer" },
  incidents, totals: { count: incidents.length, bytes: 90e6 * incidents.length } }, o);

const settle = () => new Promise((r) => setTimeout(r, 20));
function mount({ list, getItem, post = async () => ({ ok: true }), reject = false } = {}) {
  for (const id of ["toasts", "modal-host"]) {
    if (!document.getElementById(id)) { const d = document.createElement("div"); d.id = id; if (id === "modal-host") d.hidden = true; document.body.appendChild(d); }
  }
  const root = document.createElement("div");
  document.body.appendChild(root);
  const calls = { item: [], post: [] };
  const timers = [];
  const stop = clipsView(root, {
    get: async () => { if (reject) throw new Error("down"); return list; },
    getItem: async (id) => { calls.item.push(id); return getItem ? getItem(id) : item(list.incidents.find((i) => i.id === id)); },
    post: async (p, b) => { calls.post.push([p, b]); return post(p, b); },
    every: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, stopEvery: () => {},
  });
  const $ = (s) => root.querySelector(s);
  return { root, stop, calls, $, $$: (s) => [...root.querySelectorAll(s)],
           done: () => { stop(); root.remove(); } };
}
const parked = () => { store.live = { connected: true, values: { SPEED: 0 } }; };
const driving = () => { store.live = { connected: true, values: { SPEED: 45 } }; };

export default [
  ["each incident is a row, newest first, with its trigger and peak", async () => {
    const v = mount({ list: LIST([inc({}), inc({ id: "20261017-110000-manual", trigger: "manual", peak_g: null, t: T - 3600 })]) });
    await settle();
    const r = v.$$(".cl-row");
    eq(r.length, 2);
    ok(r[0].textContent.includes("Hard braking") && r[0].textContent.includes("0.62 g"), "first row: " + r[0].textContent);
    ok(r[1].textContent.includes("Saved by hand") && r[1].querySelector(".cl-row-g").textContent === "—", "no peak is a dash, not 0.00");
    v.done();
  }],
  ["the newest incident opens by itself", async () => {
    const v = mount({ list: LIST([inc({}), inc({ id: "20261017-110000-jolt", trigger: "jolt", t: T - 3600 })]) });
    await settle(); await settle();
    eq(v.calls.item, ["20261017-120000-brake"]);
    eq(v.$$(".cl-row")[0].getAttribute("aria-current"), "true");
    v.done();
  }],
  ["only the cameras that recorded get a picture, and they hold their sources", async () => {
    const v = mount({ list: LIST([inc({})]) });
    await settle(); await settle();
    const held = v.$$("video.cl-video[src]").map((e) => e.closest(".cl-vid").dataset.role).sort();
    eq(held, ["front", "rear"]);
    eq(v.$(".cl-vids").dataset.n, "2");
    ok(v.$('.cl-vid[data-role="cabin"]').hidden, "no cabin tile for a camera that was not recording");
    v.done();
  }],
  ["the readouts are only what the car reported", async () => {
    const v = mount({ list: LIST([inc({})]) });
    await settle(); await settle();
    eq(v.$$(".cl-read").map((e) => e.dataset.key), ["speed", "rpm"]);
    v.done();
  }],
  ["the scrubber moves the clock, signed from the trigger", async () => {
    const v = mount({ list: LIST([inc({})]) });
    await settle(); await settle();
    const s = v.$(".cl-scrub");
    s.value = "10"; s.dispatchEvent(new Event("input"));
    eq(v.$(".cl-when").textContent, "+10.0 s");
    s.value = "-12.5"; s.dispatchEvent(new Event("input"));
    eq(v.$(".cl-when").textContent, "−12.5 s");
    v.done();
  }],
  ["nothing yet says what keeps a clip, and whether the service is on", async () => {
    const v = mount({ list: LIST([], { running: false, accel: null }) });
    await settle();
    ok(v.$(".cl-empty").textContent.includes("No incidents yet"), v.$(".cl-empty").textContent);
    ok(v.$(".cl-empty").textContent.includes("Clip service off"), "the service line");
    eq(v.$(".clips").dataset.empty, "1");
    v.done();
  }],
  ["an unreachable server is said, not drawn as an empty list", async () => {
    const v = mount({ list: null, reject: true });
    await settle();
    ok(v.$(".cl-empty").textContent.includes("Cannot reach"), v.$(".cl-empty").textContent);
    ok(!v.$(".cl-empty").textContent.includes("null"), "no stray null");
    v.done();
  }],
  ["Delete is greyed, with the reason, while the car is moving", async () => {
    driving();
    const v = mount({ list: LIST([inc({})]) });
    await settle(); await settle();
    const del = v.$(".cl-del");
    ok(del.disabled, "disabled while driving");
    ok(!v.$(".cl-act-note").hidden, "the reason is drawn");
    parked();
    v.root.querySelector(".cl-del").click();     // a click on a disabled key does nothing
    eq(v.calls.post, []);
    v.done();
  }],
  ["parked, Delete asks first, and removes only after a yes", async () => {
    parked();
    const v = mount({ list: LIST([inc({})]) });
    await settle(); await settle();
    // the 100 ms tick repaints the lock; fire it
    store.emit("live");
    const tick = v.root; void tick;
    v.$(".cl-del").disabled = false;
    v.$(".cl-del").click();
    await settle();
    const modal = document.getElementById("modal-host");
    ok(!modal.hidden && modal.textContent.includes("Delete this incident?"), "a confirmation is shown");
    modal.querySelector(".btn.danger").click();
    await settle(); await settle();
    eq(v.calls.post[0], ["/api/clips/delete", { id: "20261017-120000-brake" }]);
    v.done();
  }],
  ["declining the confirmation deletes nothing", async () => {
    parked();
    const v = mount({ list: LIST([inc({})]) });
    await settle(); await settle();
    v.$(".cl-del").disabled = false;
    v.$(".cl-del").click();
    await settle();
    [...document.getElementById("modal-host").querySelectorAll(".btn")].find((b) => b.textContent === "Cancel").click();
    await settle();
    eq(v.calls.post, []);
    v.done();
  }],
  ["Export copies, and says where", async () => {
    const v = mount({ list: LIST([inc({})]), post: async () => ({ ok: true, display: "~/Videos/OmaCar/exports/x" }) });
    await settle(); await settle();
    v.$$(".cl-act")[0].click();
    await settle(); await settle();
    eq(v.calls.post[0], ["/api/clips/export", { id: "20261017-120000-brake" }]);
    ok([...document.getElementById("toasts").children].some((t) => t.textContent.includes("~/Videos/OmaCar/exports/x")), "toast");
    v.done();
  }],
  ["a Simulated incident is labelled on every picture", async () => {
    const v = mount({ list: LIST([inc({ id: "20261017-120000-sim", trigger: "sim", sim: true })]) });
    await settle(); await settle();
    ok(v.$$(".cl-simtag").every((e) => !e.hidden) && v.$$(".cl-simtag").length === 3, "tags");
    v.done();
  }],
  ["leaving the tab lets go of every video", async () => {
    const v = mount({ list: LIST([inc({})]) });
    await settle(); await settle();
    ok(v.$$("video[src]").length > 0, "holds some");
    v.stop();
    eq(v.$$("video[src]").length, 0);
    v.root.remove();
  }],
];
