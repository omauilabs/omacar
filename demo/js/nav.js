// The turn banner's words and the ETA row, for the demo's Navigation screen,
// Home's nav card and the CarPlay and Android Auto maps.
//
//   bannerOf(next)             -> { icon, distance, text, street }
//   etaOf(demo, now)           -> { time, remaining, distance }
//   iconOf(type, modifier)     -> an icon id
//   iconSvg(id)                -> that icon, as inline SVG markup
//   distanceText(metres)       -> "500 ft", "0.8 mi", "12 mi"
//   streetLine(banner)         -> "onto Reservation Rd", "toward CA 1", ""
//
// `next` and `demo` are live.json's demo block (lib/demoworld.py): OSRM's
// manoeuvre type and modifier, the street, and the metres to it.
//
// Imperial, as a US car's navigation is: feet under a tenth of a mile, rounded
// to 50; one decimal under ten miles; whole miles after that.

const FT = 3.28084;
const MI = 1609.344;

export function distanceText(m) {
  const metres = Math.max(0, Number(m) || 0);
  if (metres < 0.1 * MI) return `${Math.max(50, Math.round(metres * FT / 50) * 50)} ft`;
  const mi = metres / MI;
  const one = Math.round(mi * 10) / 10;
  if (one < 10) return `${one.toFixed(1)} mi`;
  return `${Math.round(mi)} mi`;
}

const side = (mod) => (String(mod || "").includes("left") ? "left" : "right");

// OSRM's maneuver.type and maneuver.modifier, to one of twelve pictures.
export function iconOf(type, modifier) {
  const t = String(type || ""), m = String(modifier || "");
  if (t === "arrive") return "arrive";
  if (m === "uturn") return "uturn";
  if (t === "depart") return "straight";
  if (t === "merge") return "merge";
  if (t === "on ramp" || t === "off ramp") return `ramp-${side(m)}`;
  if (t === "fork") return m === "straight" ? "straight" : `fork-${side(m)}`;
  switch (m) {
    case "sharp left": case "left": return "turn-left";
    case "sharp right": case "right": return "turn-right";
    case "slight left": return "slight-left";
    case "slight right": return "slight-right";
    default: return "straight";
  }
}

// What the banner says, short: the street goes on its own line.
function words(type, modifier) {
  const t = String(type || ""), m = String(modifier || "");
  if (t === "arrive") return "Arrive";
  if (m === "uturn") return "Make a U-turn";
  if (t === "depart") return "Head out";
  if (t === "merge") return "Merge";
  if (t === "on ramp") return "Take the ramp";
  if (t === "off ramp") return "Take the exit";
  if (t === "fork") return m === "straight" ? "Continue" : `Keep ${side(m)}`;
  if (t === "roundabout" || t === "rotary") return "Enter the roundabout";
  switch (m) {
    case "sharp left": return "Turn sharp left";
    case "left": return "Turn left";
    case "slight left": return t === "turn" ? "Bear left" : "Keep left";
    case "sharp right": return "Turn sharp right";
    case "right": return "Turn right";
    case "slight right": return t === "turn" ? "Bear right" : "Keep right";
    default: return "Continue";
  }
}

export function bannerOf(next) {
  const n = next || {};
  return {
    icon: iconOf(n.type, n.modifier),
    distance: distanceText(n.in_m),
    text: words(n.type, n.modifier),
    street: n.street ? String(n.street) : "",
  };
}

// The street, as the banner's second clause: "onto Reservation Rd" for a turn,
// "toward CA 1" where the road is kept rather than joined, the name alone on
// arrival, and nothing when OSRM has no name (a slip road).
export function streetLine(b) {
  if (!b || !b.street) return "";
  if (b.icon === "arrive") return b.street;
  return /^(Keep|Take the exit|Continue)/.test(b.text) ? `toward ${b.street}` : `onto ${b.street}`;
}

function clock(ms) {
  const d = new Date(ms);
  const hh = d.getHours(), mm = d.getMinutes();
  return `${hh % 12 || 12}:${String(mm).padStart(2, "0")} ${hh < 12 ? "AM" : "PM"}`;
}

function duration(secs) {
  const mins = Math.max(1, Math.round(secs / 60));
  if (mins < 60) return `${mins} min`;
  return `${Math.floor(mins / 60)} h ${mins % 60} min`;
}

export function etaOf(demo, now = Date.now()) {
  const eta = Number(demo && demo.eta) * 1000;
  return {
    time: Number.isFinite(eta) && eta > 0 ? clock(eta) : "—",
    remaining: Number.isFinite(eta) && eta > 0 ? duration((eta - now) / 1000) : "—",
    distance: distanceText(demo && demo.remaining_m),
  };
}

// ---- the icons ----------------------------------------------------------------
//
// Drawn on a 24 box with round caps, like share/js/icons.js, but filled heads
// and a heavier stroke: these are read from across a room, on a projector.
const HEAD = (x, y, a) => {
  // An arrowhead at (x, y) pointing along angle a (degrees, 0 = up).
  const r = a * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
  const P = (dx, dy) => `${(x + dx * c - dy * s).toFixed(2)} ${(y + dx * s + dy * c).toFixed(2)}`;
  return `<path d="M${P(0, -1.5)} L${P(5, 4.5)} L${P(-5, 4.5)} Z" fill="currentColor" stroke="none"/>`;
};
const LINE = (d, extra = "") => `<path d="${d}" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"${extra}/>`;
const FAINT = (d) => LINE(d, " opacity=\".4\"");

const ICONS = {
  "straight":     LINE("M12 21V7") + HEAD(12, 4.5, 0),
  "turn-right":   LINE("M7 21v-8a4 4 0 0 1 4-4h6") + HEAD(19.5, 9, 90),
  "turn-left":    LINE("M17 21v-8a4 4 0 0 0-4-4H7") + HEAD(4.5, 9, -90),
  "slight-right": LINE("M9 21v-6.5c0-2 .8-3.3 2.2-4.6L15 6.5") + HEAD(16.9, 4.7, 45),
  "slight-left":  LINE("M15 21v-6.5c0-2-.8-3.3-2.2-4.6L9 6.5") + HEAD(7.1, 4.7, -45),
  "merge":        FAINT("M6 21c0-5 6-6 6-11") + LINE("M18 21c0-5-6-6-6-11V7") + HEAD(12, 4.5, 0),
  "fork-right":   FAINT("M12 14 8 7") + LINE("M12 21v-7l4-6.2") + HEAD(17.4, 5.6, 33),
  "fork-left":    FAINT("M12 14l4-7") + LINE("M12 21v-7L8 7.8") + HEAD(6.6, 5.6, -33),
  "ramp-right":   FAINT("M9 21V4") + LINE("M9 21v-6c0-2.5 2-4.2 4.4-5.6L16 8") + HEAD(17.9, 6.8, 55),
  "ramp-left":    FAINT("M15 21V4") + LINE("M15 21v-6c0-2.5-2-4.2-4.4-5.6L8 8") + HEAD(6.1, 6.8, -55),
  "uturn":        LINE("M15 21V10a4 4 0 0 0-8 0v5") + HEAD(7, 17.5, 180),
  "arrive":       LINE("M12 21v-4") + `<path fill-rule="evenodd" d="M12 3.5a5.5 5.5 0 0 1 5.5 5.5c0 3.8-5.5 8-5.5 8S6.5 12.8 6.5 9A5.5 5.5 0 0 1 12 3.5ZM12 7a2 2 0 1 0 0 4a2 2 0 1 0 0-4Z" fill="currentColor" stroke="none"/>`,
};

export function iconSvg(id) {
  const body = ICONS[id] || ICONS.straight;
  return `<svg viewBox="0 0 24 24" width="100%" height="100%" aria-hidden="true">${body}</svg>`;
}
