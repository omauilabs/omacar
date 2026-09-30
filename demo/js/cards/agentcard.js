// Home's Oma Agent card in the demo, as mockup 3 draws it: the agent's mark, a
// question the driver asked and the agent's short answer. Tapping it opens
// Oma Agent.
//
// The live card (share/js/views/home.js agentCard) shows the real advisor's
// last answer, or, with no `claude` CLI, "Not set up on this machine", which is
// what it said in front of the room. The demo never runs the real advisor (the
// demo server refuses /api/ai/*), so this card says what the demo's own agent
// would, and the Agent screen under it is labelled "Illustrative agent
// responses".
//
//   agentCard() -> { node, paint(), destroy() }    home.js's card shape
//   register(D)                                     D.cards.agent = agentCard

import { h, icon } from "../../../js/core.js";
import { ICONS } from "../../../js/icons.js";
import { waveform } from "../views/agent.js";

export const QUESTION = "How is my CR-Z doing?";
export const ANSWER = "Temperatures look steady.";

const go = () => { location.hash = "#advisor"; };

export function agentCard() {
  const wave = waveform(9, "dac-wave");
  const node = h("div.card.hc.hc-agent.dac",
    h("div.hc-title.dac-title", wave.node, h("span", "Oma Agent"), h("span.chev", icon(ICONS.chevron, 18))),
    h("div.dac-chat",
      h("div.dac-q", QUESTION),
      h("div.dac-row", h("span.dac-ring", { "aria-hidden": "true" }), h("div.dac-a", ANSWER))));
  node.setAttribute("role", "button");
  node.setAttribute("aria-label", `Oma Agent. ${QUESTION} ${ANSWER}`);
  node.tabIndex = 0;
  node.addEventListener("click", () => { if (!node.closest(".editing")) go(); });
  node.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  return { node, paint() {}, destroy() {} };
}

export function register(D) {
  D.cards.agent = agentCard;
}
