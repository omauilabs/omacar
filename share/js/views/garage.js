// The garage — every car this tool has met.
//
// A household does not own "a car", it owns a fleet with people attached to it:
// the one you commute in, your partner's, and the one a teenager just started
// driving. The last of those is usually the one whose fault codes matter most
// and the one nobody checks, because checking meant remembering which VIN was
// which.
//
// So a car here is identified by WHO DRIVES IT first and what it is second.
// "Emma — 2014 Civic" is a thing you can find in a list at a glance; a VIN is
// not, and a model name is not either once two cars in the family are the same
// model.
//
// Switching is deliberately explicit. Plugging into a car switches
// automatically by VIN (see lib/garage.py), and that is the common case; this
// screen is for looking at a car that is not in front of you, which is exactly
// when picking the wrong one is easiest and least obvious.

import { h, store, api, since, toast } from "../core.js";
import { explain } from "../learn.js";
import { vin as maskVin, plate as maskPlate, person } from "../privacy.js";

const FIELDS = [
  ["driver", "Driver", "who normally drives it"],
  ["name", "Name", "e.g. 2014 Civic"],
  ["plate", "Plate", "registration"],
  ["notes", "Notes", "anything worth remembering"],
];

export default function garage(root) {
  let cars = [];
  let editing = null;

  const wrap = h("div.garage");
  root.appendChild(wrap);

  async function load() {
    try {
      const r = await api.vehicles();
      cars = r.vehicles || [];
    } catch (e) {
      cars = [];
      toast("Could not read the garage: " + (e.message || e), "bad");
    }
    draw();
  }

  async function save(key, field, value) {
    try {
      await api.vehicle({ key, [field]: value });
      await load();
      toast("Saved.");
    } catch (e) {
      toast("Could not save: " + (e.message || e), "bad");
    }
  }

  async function switchTo(key) {
    try {
      await api.vehicle({ key });
      await store.refreshCar();
      await load();
      toast("Now looking at this car.");
    } catch (e) {
      toast("Could not switch: " + (e.message || e), "bad");
    }
  }

  function title(car) {
    // Driver first, deliberately. See the note at the top of this file.
    const what = car.name || maskVin(car.vin) || car.key;
    return car.driver ? `${person(car.driver)} — ${what}` : what;
  }

  function card(car) {
    const open = editing === car.key;
    const bits = [];
    if (car.vin) bits.push(h("span.mono", maskVin(car.vin)));
    if (car.plate) bits.push(h("span", maskPlate(car.plate)));
    bits.push(h("span", car.last_seen ? "seen " + since(car.last_seen) : "never connected"));
    // Where the model came from, when the owner did not type it. The VIN
    // carries make and year; the model is asked of the free government
    // decoder, and a reader deserves to know which words are whose.
    if (car.model_source) bits.push(h("span.muted", "model from " + car.model_source));
    if (car.simulated) bits.push(h("span.warn", "simulated"));

    return h("section.card" + (car.current ? ".tint-ok" : ""),
      h("div.row.wrapline",
        h("div", { style: { minWidth: "0" } },
          h("div.title", title(car)),
          h("div.sub.row.wrapline", { style: { gap: "10px" } }, ...bits),
          car.notes ? h("p.lede", { style: { marginTop: "6px" } }, car.notes) : null),
        h("div.right.row.wrapline", { style: { gap: "8px" } },
          car.codes
            ? h("span.pill.bad", `${car.codes} code${car.codes > 1 ? "s" : ""}`)
            : h("span.pill.ok", "no codes"),
          car.current
            ? h("span.pill", "current")
            : h("button.btn", { onclick: () => switchTo(car.key) }, "Look at this car"),
          h("button.btn.ghost", {
            onclick: () => { editing = open ? null : car.key; draw(); },
          }, open ? "Done" : "Edit"))),

      open
        ? h("div.garage-edit",
            ...FIELDS.map(([field, label, hint]) => {
              const input = h("input.input", {
                type: "text", value: car[field] || "", placeholder: hint,
                "aria-label": label,
                onkeydown: (e) => { if (e.key === "Enter") save(car.key, field, e.target.value); },
              });
              return h("label.garage-field",
                h("span.garage-label", label),
                input,
                h("button.btn.ghost", {
                  onclick: () => save(car.key, field, input.value),
                }, "Save"));
            }))
        : null);
  }

  function draw() {
    while (wrap.firstChild) wrap.removeChild(wrap.firstChild);

    const ex = explain(h, "garage");
    if (ex) wrap.appendChild(ex);

    if (!cars.length) {
      wrap.appendChild(h("section.card",
        h("div.title", "No cars yet"),
        h("p.lede", "Plug the adapter into a car and run a scan. OmaCar reads the "
          + "VIN and creates a record for it automatically — one database per "
          + "car, so two vehicles can never mix their history.")));
      return;
    }

    // Drivers first, then everything else, so a family fleet groups the way
    // people actually think about it.
    const named = cars.filter((c) => c.driver).sort((a, b) => a.driver.localeCompare(b.driver));
    const rest = cars.filter((c) => !c.driver);
    for (const c of [...named, ...rest]) wrap.appendChild(card(c));

    wrap.appendChild(h("p.lede", { style: { marginTop: "14px" } },
      "Plugging into a car switches to it automatically by VIN. This screen is "
      + "for looking at one that is not in front of you."));
  }

  load();
}
