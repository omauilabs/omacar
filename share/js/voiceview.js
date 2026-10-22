// What the voice readout in the top bar says (lib/voice.py): pure, so the
// wording and its tones are tested (test/js/voice.test.js).
//
// A READOUT, NOT A CONTROL PANEL. Calm while it waits: one lit point and the
// word "Listening". It changes only in answer to somebody speaking, and then it
// shows what it HEARD (the transcript, as the speech model wrote it) before it
// shows what it answers, so a misheard fridge temperature is visible before it
// is believed. Where the sentence came from is never blank: "Simulated" from
// `omacar voice sim`, "Typed" from `omacar voice say`, nothing extra from the
// microphone.

export const SOURCE_NAME = { sim: "Simulated", typed: "Typed" };

const clip = (s, n = 64) => {
  s = String(s || "").trim();
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
};

// The phrase the owner says, in quotes, as a person writes it.
export function titled(words) {
  return String(words || "").replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

export function voiceView(pub) {
  const none = { show: false, tone: "", state: "", text: "", title: "", source: null };
  if (!pub || !pub.enabled) return none;
  const quoted = `“${titled(pub.phrase_words)}”`;
  const source = SOURCE_NAME[pub.source] || null;
  const base = { show: true, source, title: "" };
  switch (pub.phase) {
    case "stopped":
      return { ...base, tone: "warn", state: "Not running", text: "omacar voice run",
               title: "Voice is on but its service is not running: systemctl --user enable --now omacar-voice.service" };
    case "quiet":
      return { ...base, tone: "", state: "Quiet hours", text: "Asleep until the car starts",
               title: "No listening and no speech in quiet hours, unless the car is on." };
    case "error":
      return { ...base, tone: "warn", state: "Voice problem", text: clip(pub.error || "see omacar voice status"),
               title: pub.error || "" };
    case "heard":
      return { ...base, tone: "live", state: "Hearing", text: clip(pub.transcript) || "…" };
    case "working":
      return { ...base, tone: "live", state: "Thinking", text: clip(pub.transcript) };
    case "replied":
      return { ...base, tone: "live", state: pub.spoke ? "Said" : "Reply", text: clip(pub.reply, 80),
               title: pub.transcript ? `Heard: ${pub.transcript}` : "" };
    case "listening":
      return { ...base, tone: "ok", state: "Listening", text: quoted };
    default:
      return none;
  }
}

// A reply nobody heard (no Piper, or quiet hours) is shown as a note instead.
export function needsToast(pub, lastSeq) {
  return !!(pub && pub.enabled && pub.phase === "replied" && pub.reply && !pub.spoke
            && Number.isFinite(pub.seq) && pub.seq > lastSeq);
}

// "21:00" -> next hour round, for the tap-to-step rows.
export function stepHour(hhmm, by = 1) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || ""));
  const h = m ? Number(m[1]) : 21;
  const mm = m ? m[2] : "00";
  return `${String((h + by + 24) % 24).padStart(2, "0")}:${mm}`;
}

// 24 h -> what the tablet says: "9:00 pm".
export function clock12(hhmm) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || ""));
  if (!m) return "—";
  const h = Number(m[1]);
  return `${h % 12 || 12}:${m[2]} ${h < 12 ? "am" : "pm"}`;
}

// What the "Spoken replies" row says about itself.
export function speakNote(pub) {
  const c = (pub && pub.can) || {};
  if (c.speak) return "Piper reads the answer aloud";
  return `Shown as a note, not spoken: ${c.speak_why || "Piper is not set up"}`;
}

// What is missing for the wake word to work at all, or null.
export function setupNote(pub) {
  const c = (pub && pub.can) || {};
  const miss = [];
  if (!c.wake) miss.push("wake word");
  if (!c.transcribe) miss.push("speech model");
  if (!c.mic) miss.push("microphone tool");
  return miss.length ? `Not installed yet: ${miss.join(", ")}. Run: omacar voice setup` : null;
}
