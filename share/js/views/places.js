// Navigation, Places: where home is and where the pickups are.
//
// There is no GPS receiver on this tablet yet (a u-blox + gpsd is planned), so
// the places are typed in by hand for now: a latitude and longitude each. When
// there IS a fix, "Use where I am" fills them from it. Nothing here invents a
// position; with no fix the screen says so.
//
// EDITING IS PARKED-ONLY, like every editor in the app (store.lockedAsMoving):
// the fields grey out with the reason, never disappear. The single taps that
// are safe at speed stay live: "Heading there" on a pickup.

import { h, icon, store, toast, U } from "../core.js";
import { arrivalFeed, arrivalView, postPlaces, fmtDist, HOME_ICON, PIN_ICON } from "../arrival.js";
import { pickupPanel } from "../arrivalui.js";

const num = (s) => {
  const t = String(s).trim().replace(",", ".");
  return t === "" ? NaN : Number(t);
};
const field = (label, ph) => {
  const input = h("input", { type: "text", inputmode: "decimal", placeholder: ph, autocomplete: "off" });
  return { input, node: h("label.pl-f", h("span.pl-fk", label), input) };
};

export default function placesView(root) {
  const lamp = h("span.ar-lamp", { "aria-hidden": "true" });
  const src = h("span.ar-src", lamp, h("span.ar-src-t"));
  const status = h("div.pl-status");
  const note = h("div.pl-note");

  // ---- home
  const hLat = field("Latitude", "0.0000"), hLon = field("Longitude", "0.0000"), hRad = field("Radius, m", "150");
  const hNow = h("div.pl-now");
  const hSave = h("button.btn.primary", { type: "button" }, "Save home");
  const hHere = h("button.btn", { type: "button" }, "Use where I am");
  const hClear = h("button.btn", { type: "button" }, "Clear");
  const homeCard = h("section.card.pl-card",
    h("div.pl-h", icon(HOME_ICON, 18), h("span", "Home")), hNow,
    h("div.pl-row", hLat.node, hLon.node, hRad.node),
    h("div.pl-act", hSave, hHere, hClear));

  // ---- pickups
  const list = h("div.pl-list");
  const pLat = field("Latitude", "0.0000"), pLon = field("Longitude", "0.0000");
  const pName = h("input", { type: "text", placeholder: "Pickup name", autocomplete: "off", maxlength: "40" });
  const pAdd = h("button.btn.primary", { type: "button" }, "Add pickup");
  const pHere = h("button.btn", { type: "button" }, "Use where I am");
  const pickCard = h("section.card.pl-card.pl-pick",
    h("div.pl-h", icon(PIN_ICON, 18), h("span", "Pickups")), list,
    h("div.pl-row.pl-add", h("label.pl-f.pl-fname", h("span.pl-fk", "Name"), pName), pLat.node, pLon.node),
    h("div.pl-act", pAdd, pHere));

  // ---- the plan, and the eco set point
  const panel = pickupPanel({ title: "Heading to a pickup" });
  const ecoDown = h("button.fz-step", { type: "button", "aria-label": "Colder" }, "−");
  const ecoUp = h("button.fz-step", { type: "button", "aria-label": "Warmer" }, "+");
  const ecoV = h("span.pl-ecov");
  const eco = h("div.pl-eco",
    h("div.pl-ecot", h("span.pl-fk", "Eco set point"),
      h("span.pl-ecos", "What the Fridge to eco key sets when you are nearly home")),
    h("div.fz-setbox", ecoDown, h("div.fz-setv", ecoV), ecoUp));
  const planCard = h("section.card.pl-card", panel.el, eco);

  root.appendChild(h("div.pl",
    h("div.pl-head", h("h1.pl-title", "Places"), status, src),
    note,
    h("div.pl-grid", h("div.pl-col", homeCard, planCard), pickCard)));

  let last = null, ecoC = null, editing = false;
  const here = () => (last && last.location && last.location.here) || null;
  const fill = (la, lo, p) => { la.input.value = p.lat.toFixed(6); lo.input.value = p.lon.toFixed(6); };

  const act = (p, okMsg) => p.then((r) => { arrivalFeed.refresh(); if (okMsg) toast(okMsg); return r; })
    .catch((e) => toast(e.message || "That did not work", "bad"));

  hSave.onclick = () => {
    const lat = num(hLat.input.value), lon = num(hLon.input.value), r = hRad.input.value.trim();
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return toast("Latitude and longitude are numbers", "bad");
    act(postPlaces({ home: { lat, lon, radius_m: r === "" ? 150 : num(r) } }), "Home saved");
  };
  hClear.onclick = () => act(postPlaces({ home: null }), "Home cleared");
  hHere.onclick = () => { if (here()) fill(hLat, hLon, here()); };
  pHere.onclick = () => { if (here()) fill(pLat, pLon, here()); };
  pAdd.onclick = () => {
    const lat = num(pLat.input.value), lon = num(pLon.input.value), name = pName.value.trim();
    if (!name) return toast("Give the pickup a name", "bad");
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return toast("Latitude and longitude are numbers", "bad");
    act(postPlaces({ add_pickup: { name, lat, lon } }), `${name} saved`).then(() => { pName.value = ""; pLat.input.value = ""; pLon.input.value = ""; });
  };
  const bumpEco = (d) => {
    if (ecoC === null) return;
    ecoC = Math.max(-22, Math.min(10, Math.round((ecoC + d * 0.5) * 2) / 2));
    ecoV.textContent = U.imperial ? `${Math.round(ecoC * 9 / 5 + 32)}°` : `${ecoC}°`;
    act(postPlaces({ fridge_eco_c: ecoC }));
  };
  ecoDown.onclick = () => bumpEco(-2);   // one degree C a tap: two of the panel's half-degree steps
  ecoUp.onclick = () => bumpEco(2);

  let sig = "";
  function paint() {
    const v = arrivalView(last, { imperial: U.imperial });
    const locked = !!store.lockedAsMoving;
    src.lastChild.textContent = v.source;
    src.dataset.tone = v.sourceTone;
    const loc = (last && last.location) || {};
    status.textContent = loc.fix ? "Position known" : (loc.note || "No GPS receiver yet");
    note.textContent = locked ? "Places change when you stop. Heading to a pickup stays one tap."
      : v.watching || !last ? "" : v.watchNote;
    const p = (last && last.places) || { home: null, pickups: [] };
    // Fields keep what is being typed: only fill them from the server when nobody has touched them.
    const typing = document.activeElement && root.contains(document.activeElement)
      && document.activeElement.tagName === "INPUT";
    if (!typing && !editing) {
      if (p.home) { hLat.input.value = String(p.home.lat); hLon.input.value = String(p.home.lon); hRad.input.value = String(p.home.radius_m); }
    }
    hNow.textContent = p.home
      ? (v.state === "away" || v.state === "near" ? `${fmtDist(last.arrival.dist_m, U.imperial)} from here` : "Saved")
      : "Not set: type its latitude and longitude, or use where you are when there is a fix";
    for (const el of [hLat.input, hLon.input, hRad.input, pName, pLat.input, pLon.input]) el.disabled = locked;
    hSave.disabled = hClear.disabled = pAdd.disabled = locked;
    hHere.disabled = pHere.disabled = locked || !here();
    hHere.title = pHere.title = here() ? "" : "Needs a GPS fix";
    if (p.home) hClear.disabled = locked;
    else hClear.disabled = true;
    ecoDown.disabled = ecoUp.disabled = locked;
    if (last && last.actions && last.actions.fridge && last.actions.fridge.eco_c !== undefined && !ecoDown.matches(":active")) {
      ecoC = last.actions.fridge.eco_c;
      ecoV.textContent = U.imperial ? `${Math.round(ecoC * 9 / 5 + 32)}°` : `${ecoC}°`;
    }
    const nsig = JSON.stringify([p.pickups, locked, last && last.pickup && last.pickup.name]);
    if (nsig !== sig) {
      sig = nsig;
      list.replaceChildren(...(p.pickups.length ? p.pickups.map((r) => {
        const rm = h("button.btn.sm", { type: "button" }, "Remove");
        rm.disabled = locked;
        rm.onclick = () => act(postPlaces({ remove_pickup: r.name }), `${r.name} removed`);
        return h("div.pl-item",
          h("div.pl-it", h("span.pl-in", r.name), h("span.pl-ic", `${r.lat.toFixed(4)}, ${r.lon.toFixed(4)}`)),
          rm);
      }) : [h("div.pl-empty", "No pickups saved yet")]));
    }
    panel.paint(v);
  }
  root.addEventListener("focusin", () => { editing = true; });
  root.addEventListener("focusout", () => { editing = false; });
  const off = arrivalFeed.subscribe((st) => { last = st; paint(); });
  paint();
  return () => off();
}
