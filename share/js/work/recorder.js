// Push-to-talk: record from the tablet's microphone until the driver stops
// talking (SILENCE_S of quiet after some sound) or MAX_S, whichever is first.
export const SILENCE_S = 1.2;
export const MAX_S = 30;

export function silenceStop(levels, dtS, threshold = 0.02) {
  const heard = levels.findIndex((l) => l > threshold);
  if (heard < 0) return false;
  let quiet = 0;
  for (let i = levels.length - 1; i > heard && levels[i] <= threshold; i--) quiet++;
  return quiet * dtS >= SILENCE_S;
}

const TYPE = "audio/webm;codecs=opus";
const defaultMake = (stream) => new MediaRecorder(stream,
  MediaRecorder.isTypeSupported(TYPE) ? { mimeType: TYPE } : undefined);

// The microphone is released on every path out, whatever throws: the tablet's
// mic indicator must never stay on after a failed recording.
export async function record({ getStream = () => navigator.mediaDevices.getUserMedia({ audio: true }),
                               ctx, onLevel = () => {}, makeRecorder = defaultMake } = {}) {
  const stream = await getStream();
  let src = null, timer = null;
  try {
    const rec = makeRecorder(stream);
    const chunks = [];
    rec.ondataavailable = (e) => e.data && e.data.size && chunks.push(e.data);
    src = ctx.createMediaStreamSource(stream);
    const an = ctx.createAnalyser(); an.fftSize = 1024; src.connect(an);
    const buf = new Float32Array(an.fftSize);
    const levels = []; const dt = 0.05; const started = performance.now();
    rec.start();
    await new Promise((resolve) => {
      rec.onerror = resolve;
      stream.getTracks().forEach((t) => { t.onended = resolve; });
      timer = setInterval(() => {
        try {
          an.getFloatTimeDomainData(buf);
          const rms = Math.sqrt(buf.reduce((a, v) => a + v * v, 0) / buf.length);
          levels.push(rms); onLevel(rms);
          if (silenceStop(levels, dt) || (performance.now() - started) / 1000 >= MAX_S) resolve();
        } catch { resolve(); }
      }, dt * 1000);
    });
    clearInterval(timer); timer = null;
    if (rec.state !== "inactive") {
      const stopped = new Promise((r) => { rec.onstop = r; setTimeout(r, 2000); });
      rec.stop(); await stopped;
    }
    return new Blob(chunks, { type: "audio/webm" });
  } finally {
    if (timer !== null) clearInterval(timer);
    try { stream.getTracks().forEach((t) => t.stop()); } catch { /* already gone */ }
    try { if (src) src.disconnect(); } catch { /* already gone */ }
  }
}
