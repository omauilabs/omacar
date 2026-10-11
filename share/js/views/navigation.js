import { ICONS } from "../icons.js";
import { placeholder } from "../placeholder.js";

export default function navigationView(root) {
  root.appendChild(placeholder({
    icon: ICONS.nav, title: "Navigation", step: "Waiting for a GPS receiver",
    lines: ["Offline maps of California with turn-by-turn and spoken directions, "
            + "working with no signal.",
            "It needs a GPS receiver plugged into the tablet: the Surface has none."],
  }));
}
