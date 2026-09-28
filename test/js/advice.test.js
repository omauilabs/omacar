// The advisor's last answer, as Home's Oma Agent card and Vehicle's insight
// card read it. The records here are shaped exactly as lib/ai.py writes them
// (records.write_record("ai", "<kind>: <question>", {"kind", "headline"})):
// the headline sits directly on the payload. Both cards used to read
// payload.data.headline, a field no record has ever carried, so Home always
// said "Ask about your car..." and Vehicle's insight never appeared.
import { eq } from "./assert.js";
import { latestAdvice } from "../js/advice.js";

const SYMPTOM = {
  id: 25, kind: "ai", at: 1787742333.8,
  label: "symptom: It stalls when I come to a stop",
  payload: { kind: "symptom", headline: "Most likely a marginal front oxygen sensor heater" },
};
const TRIAGE = {
  id: 24, kind: "ai", at: 1787742208.8, label: "triage:",
  payload: { kind: "triage", headline: "No safety-critical faults" },
};
const EMPTY = { id: 26, kind: "ai", at: 1787742400.0, label: "ask: why", payload: { kind: "ask", headline: "" } };

export default [
  ["the headline is read from where lib/ai.py writes it", () =>
    eq(latestAdvice([SYMPTOM, TRIAGE]),
       { headline: "Most likely a marginal front oxygen sensor heater",
         question: "It stalls when I come to a stop" })],
  ["a triage has no question, and says nothing rather than 'triage:'", () =>
    eq(latestAdvice([TRIAGE]).question, "")],
  ["the newest answer that has a headline wins", () =>
    eq(latestAdvice([EMPTY, TRIAGE, SYMPTOM]).headline, "No safety-critical faults")],
  ["no history is no advice", () => eq(latestAdvice([]), null)],
  ["and nothing is invented from a record with no headline", () => eq(latestAdvice([EMPTY]), null)],
];
