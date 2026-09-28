// The advisor's last answer, for the two cards that quote it: Home's Oma Agent
// card and Vehicle's insight card.
//
// lib/ai.py writes one `ai` record per answer, labelled "<kind>: <question>",
// whose payload is { kind, headline } -- the headline sits directly on the
// payload. Both cards used to read payload.data.headline, which no record has
// ever carried, so neither ever showed anything. One copy of the shape, here,
// pinned by test/js/advice.test.js.

export function latestAdvice(records) {
  const r = (records || []).find((x) => x && x.payload && x.payload.headline);
  if (!r) return null;
  const p = r.payload;
  return {
    headline: String(p.headline),
    // What was asked, when anything was: the label's text after its "kind:".
    question: String(p.question || p.code || (r.label || "").replace(/^[a-z]+:\s*/, "")),
  };
}
