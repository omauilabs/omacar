import { ICONS } from "../icons.js";
import { placeholder } from "../placeholder.js";

export default function navigationView(root) {
  root.appendChild(placeholder({
    icon: ICONS.nav, title: "Navigation", step: "Coming in step 3 of the redesign",
    lines: ["Offline maps of California with turn-by-turn and spoken directions, "
            + "working with no signal.",
            "It needs a GPS receiver plugged into the tablet: the Surface has none."],
  }));
}
