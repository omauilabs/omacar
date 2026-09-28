// The cards Home can hold (share/data/home-cards.json, which the server reads
// too), and the small pure operations the layout editor needs.

let catalogue = null;

export function loadCatalogue() {
  if (!catalogue) {
    catalogue = fetch("data/home-cards.json", { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error(`home-cards.json: ${r.status}`);
      return r.json();
    });
  }
  return catalogue;
}

export function orientation() {
  return matchMedia("(orientation: portrait)").matches ? "portrait" : "landscape";
}

export function spanOf(cat, id, size, orient) {
  const c = cat.cards[id];
  if (!c) return [3, 1];
  const s = c.sizes[size] || Object.values(c.sizes)[0];
  return s[orient === "portrait" ? "port" : "land"];
}

export function defaultLayout(cat) {
  const out = {};
  for (const o of ["landscape", "portrait"]) {
    out[o] = { cards: cat.default[o].map((x) => x.slice()), hidden: [] };
  }
  return out;
}

export function moveItem(list, from, to) {
  const out = list.slice();
  const [it] = out.splice(from, 1);
  out.splice(Math.max(0, Math.min(out.length, to)), 0, it);
  return out;
}

export function nextSize(cat, id, size) {
  const sizes = Object.keys(cat.cards[id].sizes);
  return sizes[(sizes.indexOf(size) + 1) % sizes.length];
}
