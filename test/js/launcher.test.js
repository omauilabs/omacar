// The launcher's Begin runs `omacar begin`, which stops the drive recorder and
// restarts the daemon (lib/begin.py). Nothing on a drive screen leads here any
// more, but the button itself also refuses while the car is moving, or was
// last seen moving, and says why -- the same rule as every write screen.
import { eq } from "./assert.js";
import launcher from "../js/views/launcher.js";
import { store } from "../js/core.js";

function mount(before) {
  before();
  const root = document.createElement("div");
  document.body.appendChild(root);
  const un = launcher(root);
  return { root, btn: root.querySelector(".launch-go"),
           done: () => { un(); root.remove(); store.live = null; store.emit("live"); } };
}
const live = (values, connected = true) => { store.live = { connected, values }; store.emit("live"); };

export default [
  ["parked, Begin can be pressed", () => {
    const m = mount(() => live({ SPEED: 0, RPM: 0 }));
    try { eq([m.btn.disabled, m.btn.title], [false, ""]); } finally { m.done(); }
  }],
  ["moving, it greys out with the reason", () => {
    const m = mount(() => live({ SPEED: 60, RPM: 2500 }));
    try { eq([m.btn.disabled, m.btn.title], [true, "Available when you stop"]); } finally { m.done(); }
  }],
  ["and stays grey if the adapter drops while moving", () => {
    const m = mount(() => live({ SPEED: 60, RPM: 2500 }));
    try {
      live({}, false);
      eq(m.btn.disabled, true, "disabled after the drop");
      live({ SPEED: 0, RPM: 0 });
      eq(m.btn.disabled, false, "and free again once the car is seen stopped");
    } finally { m.done(); }
  }],
];
