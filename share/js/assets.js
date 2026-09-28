// The private pictures (lib/assets.py): asked for once per page, because they
// only change when somebody runs `omacar assets push`.
import { api } from "./core.js";

let all = null;

export function asset(name) {
  if (!all) all = api.assets().catch(() => ({}));
  return all.then((a) => (a && a[name]) || null);
}
