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

export async function record({ getStream = () => navigator.mediaDevices.getUserMedia({ audio: true }),
                               ctx, onLevel = () => {} } = {}) {
  const stream = await getStream();
  const type = "audio/webm;codecs=opus";
  const rec = new MediaRecorder(stream, MediaRecorder.isTypeSupported(type) ? { mimeType: type } : undefined);
  const chunks = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const src = ctx.createMediaStreamSource(stream);
  const an = ctx.createAnalyser(); an.fftSize = 1024; src.connect(an);
  const buf = new Float32Array(an.fftSize);
  const levels = []; const dt = 0.05; const started = performance.now();
  rec.start();
  await new Promise((resolve) => {
    const timer = setInterval(() => {
      an.getFloatTimeDomainData(buf);
      const rms = Math.sqrt(buf.reduce((a, v) => a + v * v, 0) / buf.length);
      levels.push(rms); onLevel(rms);
      if (silenceStop(levels, dt) || (performance.now() - started) / 1000 >= MAX_S) {
        clearInterval(timer); resolve();
      }
    }, dt * 1000);
  });
  const done = new Promise((r) => { rec.onstop = r; });
  rec.stop(); await done;
  stream.getTracks().forEach((t) => t.stop());
  src.disconnect();
  return new Blob(chunks, { type: "audio/webm" });
}
