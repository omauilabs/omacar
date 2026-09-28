import { ICONS } from "../icons.js";
import { placeholder } from "../placeholder.js";

export default function workView(root) {
  root.appendChild(placeholder({
    icon: ICONS.agent, title: "Work", step: "Coming in step 4 of the redesign",
    lines: ["Your coding sessions on your Omarchy machines: what each is doing, "
            + "which one needs you, and a spoken instruction when you are driving.",
            "Voice only while the car moves. Reviews wait until you are parked."],
  }));
}
