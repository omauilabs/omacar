import { eq } from "./assert.js";
import { voiceView, needsToast, stepHour, clock12, speakNote, setupNote, titled } from "../js/voiceview.js";

// What the voice readout says (share/js/voiceview.js). It shows what it HEARD
// before what it answers, and where a sentence came from is never blank.
const on = (over = {}) => Object.assign({
  enabled: true, phase: "listening", phrase_words: "hey jarvis", source: "mic",
  can: { wake: true, transcribe: true, mic: true, speak: true },
}, over);

export default [
  ["off or unknown draws nothing", () => {
    eq([voiceView(null).show, voiceView({ enabled: false, phase: "off" }).show, voiceView(on({ phase: "?" })).show], [false, false, false]);
  }],
  ["listening is calm and names the phrase", () => {
    const v = voiceView(on());
    eq([v.show, v.tone, v.state, v.text], [true, "ok", "Listening", "“Hey Jarvis”"]);
  }],
  ["hearing shows the words as heard", () => {
    const v = voiceView(on({ phase: "heard", transcript: "fridge to thirty" }));
    eq([v.tone, v.state, v.text], ["live", "Hearing", "fridge to thirty"]);
  }],
  ["hearing with nothing yet shows an ellipsis, not blank", () => {
    eq(voiceView(on({ phase: "heard", transcript: "" })).text, "…");
  }],
  ["thinking keeps the transcript", () => {
    const v = voiceView(on({ phase: "working", transcript: "mute the radar" }));
    eq([v.state, v.text], ["Thinking", "mute the radar"]);
  }],
  ["a spoken reply says Said; an unspoken one says Reply and keeps what was heard in the title", () => {
    const a = voiceView(on({ phase: "replied", reply: "Radar muted.", spoke: true, transcript: "mute the radar" }));
    const b = voiceView(on({ phase: "replied", reply: "Radar muted.", spoke: false, transcript: "mute the radar" }));
    eq([a.state, b.state, b.text, b.title], ["Said", "Reply", "Radar muted.", "Heard: mute the radar"]);
  }],
  ["quiet hours say so, in the faint tone", () => {
    const v = voiceView(on({ phase: "quiet" }));
    eq([v.state, v.tone], ["Quiet hours", ""]);
  }],
  ["a service that is not running is a warning that says how to start it", () => {
    const v = voiceView(on({ phase: "stopped" }));
    eq([v.tone, v.state, v.title.includes("omacar-voice.service")], ["warn", "Not running", true]);
  }],
  ["an error shows its reason", () => {
    const v = voiceView(on({ phase: "error", error: "no parec or pw-record" }));
    eq([v.tone, v.text], ["warn", "no parec or pw-record"]);
  }],
  ["the simulator is never mistaken for a microphone", () => {
    eq(voiceView(on({ source: "sim" })).source, "Simulated");
    eq(voiceView(on({ source: "typed" })).source, "Typed");
    eq(voiceView(on({ source: "mic" })).source, null);
  }],
  ["long transcripts are cut with an ellipsis", () => {
    const v = voiceView(on({ phase: "working", transcript: "x".repeat(200) }));
    eq([v.text.length, v.text.endsWith("…")], [64, true]);
  }],
  ["only an unspoken, new reply becomes a note", () => {
    const p = on({ phase: "replied", reply: "Hi", spoke: false, seq: 3 });
    eq([needsToast(p, 2), needsToast(p, 3), needsToast({ ...p, spoke: true }, 2), needsToast({ ...p, enabled: false }, 2),
        needsToast({ ...p, phase: "listening" }, 2)], [true, false, false, false, false]);
  }],
  ["hours step round midnight", () => {
    eq([stepHour("21:00"), stepHour("23:30"), stepHour("00:00", -1), stepHour("junk")], ["22:00", "00:30", "23:00", "22:00"]);
  }],
  ["12-hour clock", () => {
    eq([clock12("21:00"), clock12("07:05"), clock12("00:00"), clock12("12:30"), clock12("x")], ["9:00 pm", "7:05 am", "12:00 am", "12:30 pm", "—"]);
  }],
  ["titled phrase", () => eq(titled("hey omacar"), "Hey Omacar")],
  ["spoken-reply note says why it is silent", () => {
    eq(speakNote({ can: { speak: true } }), "Piper reads the answer aloud");
    eq(speakNote({ can: { speak: false, speak_why: "piper is not installed" } }), "Shown as a note, not spoken: piper is not installed");
  }],
  ["setup note names what is missing", () => {
    eq(setupNote({ can: { wake: true, transcribe: true, mic: true } }), null);
    eq(setupNote({ can: { wake: false, transcribe: true, mic: false } }), "Not installed yet: wake word, microphone tool. Run: omacar voice setup");
  }],
];
