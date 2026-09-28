import { ICONS } from "../icons.js";
import { placeholder } from "../placeholder.js";

export default function camerasView(root) {
  root.appendChild(placeholder({
    icon: ICONS.camera, title: "Cameras", step: "Coming in step 2 of the redesign",
    lines: ["Front, rear and cabin cameras wired to the tablet, recorded in one-minute "
            + "clips that loop, with hard braking locked from the car's own speed.",
            "Nothing is recorded yet."],
  }));
}
