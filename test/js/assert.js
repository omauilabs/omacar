// The two assertions every unit here needs, and nothing more. Comparison is by
// JSON so arrays and plain objects compare by value.
export function eq(got, want, what = "value") {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g !== w) throw new Error(`${what}: got ${g}, want ${w}`);
}

export function ok(cond, what) {
  if (!cond) throw new Error(what);
}
