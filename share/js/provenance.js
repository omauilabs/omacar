// Where a number came from, in words.
//
// The badge in the top bar and the line under Home say this, and they come
// from one function each so the two can never disagree. The failure that
// matters is reading invented numbers as your own car's, so the simulator and
// the bench are named before anything else.

// serverError is store.noServer: true whenever the server has stopped
// answering, including long after the first snapshot. A snapshot still in
// hand is not a reason to say anything else -- it is exactly what goes stale.
export function badge(car, live, serverError) {
  if (serverError) {
    return { text: "NO SERVER", tone: "bad",
             title: "cannot reach the OmaCar server — omacar server status" };
  }
  if (!car) return { text: "STARTING", tone: "", title: "Waiting for the first snapshot" };
  const s = live || car.live || {};
  if (car.simulated) {
    return { text: "SIMULATED", tone: "warn",
             title: "Simulated by omacar-sim. None of these numbers are your car's." };
  }
  if (s.kind === "bench") {
    return { text: "BENCH", tone: "warn",
             title: "A real adapter path talking to an emulator, not a car." };
  }
  if (s.connected) {
    return { text: "LIVE · OBD-II", tone: "ok",
             title: s.protocol ? `Live from the car · ${s.protocol}` : "Live from the car" };
  }
  return { text: "NO CAR", tone: "",
           title: "The adapter has not answered. Anything shown is the last thing recorded." };
}

export function footerLine(car, live) {
  if (!car) return "";
  const s = live || car.live || {};
  const parts = ["OBD-II"];
  if ((car.signals || []).length) parts.push("Honda enhanced");
  const how = car.simulated ? "simulated"
    : s.kind === "bench" ? "bench emulator"
    : s.connected ? "live" : "last recorded";
  return `Vehicle data: ${parts.join(" + ")} · ${how}`;
}

// The data-src a tile carries when it draws a number. Never empty: a drawn
// number always came from somewhere, and test/app_test.py checks that it says so.
export function sourceKey(car, live) {
  const s = live || (car && car.live) || {};
  if (car && car.simulated) return "sim";
  if (s.kind === "bench") return "bench";
  if (s.connected) return "obd";
  return "recorded";
}
