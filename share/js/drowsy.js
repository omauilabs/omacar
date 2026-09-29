// Drowsy mode's measures, from timestamped frames. Pure: no camera, no clock,
// no DOM. A frame is { t (seconds), face, blink, jaw, pitch, gated, restart? }, and every
// measure is a function of the frames fed so far, so each can be tested on a
// synthetic series.
//
//   eye closure  the mean of eyeBlinkLeft and eyeBlinkRight
//   baseline     the first 60 s above 30 mph (gated) with a face in view set
//                the driver's open-eye baseline -- the median, so blinks do
//                not raise it; "closed" is above baseline + 0.35, capped at 0.8
//   closure      how long the eyes have been continuously closed
//   PERCLOS      the share of frames in the last 60 s with the eyes closed
//                (P80), once the window holds 30 s
//   yawn         jawOpen > 0.6 held for 1.5 s or more
//   nod          head pitch more than 15 degrees below its baseline for 0.5 s
//                or more, then recovering
//   face lost    no face for more than 5 s: "Can't see you". Never an alert.

// Which way is chin-down. The facial transformation matrix is column-major
// in MediaPipe's y-up camera space, so a nod tips the face's forward axis to
// negative y and this reads negative. The owner confirms it on the tablet
// (Task 13); if nodding reads positive there, this becomes -1.
export const PITCH_SIGN = 1;

export function pitchDeg(data) {
  if (!data || data.length < 11) return null;
  const y = Math.max(-1, Math.min(1, data[9]));
  return PITCH_SIGN * Math.asin(y) * 180 / Math.PI;
}

// A MediaPipe FaceLandmarkerResult as a measures frame.
export function frameFrom(result, t) {
  const bs = result && result.faceBlendshapes && result.faceBlendshapes[0];
  if (!bs || !bs.categories || !bs.categories.length) return { t, face: false };
  const s = {};
  for (const c of bs.categories) s[c.categoryName] = c.score;
  const m = result.facialTransformationMatrixes && result.facialTransformationMatrixes[0];
  return { t, face: true, blink: ((s.eyeBlinkLeft || 0) + (s.eyeBlinkRight || 0)) / 2,
           jaw: s.jawOpen || 0, pitch: m ? pitchDeg(m.data) : null };
}

function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const n = s.length;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}

export function createMeasures(cfg) {
  let E, W, Y, N, L1, lostSecs;
  // New thresholds, from a settings change, keep the baseline and every
  // window: a change of sensitivity must not leave the driver a minute blind.
  function setConfig(c) {
    E = c.eyes; W = c.perclos; Y = c.yawn; N = c.nod; L1 = c.level1; lostSecs = c.face_lost_secs;
  }
  setConfig(cfg);
  let gatedSecs = 0, prevGatedT = null;
  const blinks = [], pitches = [];
  let baseline = null, pitch0 = null;
  let closedSince = null, openSince = null;
  const win = [];                      // [t, closed], with a face, after the baseline
  let jawSince = null, yawnCounted = false;
  const yawns = [];
  let downSince = null, downLast = null;
  const nods = [];
  let firstT = null, lastFace = null, lastT = null;
  let snap = null;

  function feed(f) {
    const t = f.t;

    // The same frame fed twice -- a re-read MJPEG frame, or a clock too
    // coarse to move between two real reads -- is not a discontinuity. It is
    // ignored outright, with no reset: treating a repeat as a gap would keep
    // zeroing closedFor and every hold before either could ever reach its
    // threshold, so drowsy mode would silently never alert -- worse than the
    // false alert the discontinuity guard exists to prevent.
    if (lastT !== null && t === lastT) return snap;

    if (firstT === null) firstT = t;
    if (f.face) lastFace = t;

    // A frame whose time is earlier than the last one, or that jumped more
    // than 1 s past it -- a paused/resumed camera, or a stalled tab -- is a
    // discontinuity. Nothing about the gap may read through: every running
    // duration and hold starts over, and the PERCLOS window drops what came
    // before rather than mixing two eras. Left unguarded, a gap can surface
    // as an instantly huge "eyes closed" on the very first frame after it --
    // exactly the spurious alert a driver must never get startled by.
    //
    // A frame marked `restart` is one too, however short the pause before
    // it: drowsyrun.js marks the first frame it feeds after 10 s stopped, a
    // dropped link, or a camera restart (it feeds at any speed while the car
    // rolls). Frames it did not feed are frames nobody measured, so nothing
    // is held over them -- and a PERCLOS reading after moving off is never
    // built from a long stop.
    const discontinuity = lastT !== null && (!!f.restart || t < lastT || t - lastT > 1);
    lastT = t;
    if (discontinuity) {
      closedSince = null; openSince = null;
      jawSince = null; yawnCounted = false;
      downSince = null; downLast = null;
      win.length = 0;
    }

    // The baseline: gated time with a face in view, in unbroken runs. A
    // backward jump must never subtract from gatedSecs, so the delta is
    // credited only when it is a real, small step forward.
    if (baseline === null) {
      if (f.gated && f.face) {
        if (prevGatedT !== null && t > prevGatedT && t - prevGatedT < 1) gatedSecs += t - prevGatedT;
        prevGatedT = t;
        blinks.push(f.blink);
        if (typeof f.pitch === "number") pitches.push(f.pitch);
        if (gatedSecs >= E.baseline_secs) { baseline = median(blinks); pitch0 = median(pitches); }
      } else prevGatedT = null;
    }

    const threshold = baseline === null ? null : Math.min(baseline + E.closed_over_baseline, E.closed_cap);
    const closed = !!f.face && threshold !== null && f.blink > threshold;
    if (closed) { if (closedSince === null) closedSince = t; } else closedSince = null;
    const open = !!f.face && threshold !== null && !closed;
    if (open) { if (openSince === null) openSince = t; } else openSince = null;

    if (f.face && threshold !== null) win.push([t, closed]);
    while (win.length && t - win[0][0] > W.window_secs) win.shift();
    const span = win.length ? t - win[0][0] : 0;
    const perclos = span >= W.min_secs ? win.filter((w) => w[1]).length / win.length : null;

    if (f.face && f.jaw > Y.jaw_open) {
      if (jawSince === null) jawSince = t;
      if (!yawnCounted && t - jawSince >= Y.hold_secs) { yawns.push(t); yawnCounted = true; }
    } else { jawSince = null; yawnCounted = false; }

    if (pitch0 !== null && f.face && typeof f.pitch === "number") {
      if (pitch0 - f.pitch > N.below_deg) {
        if (downSince === null) downSince = t;
        downLast = t;
      } else {
        if (downSince !== null && downLast - downSince >= N.hold_secs) nods.push(t);
        downSince = null;
        downLast = null;
      }
    }

    const recent = (xs) => { while (xs.length && t - xs[0] > L1.count_window_secs) xs.shift(); return xs.length; };
    snap = {
      t, face: !!f.face, calibrated: baseline !== null, baseline, threshold,
      blink: f.face ? f.blink : null, closed,
      closedFor: closedSince === null ? 0 : t - closedSince,
      openFor: openSince === null ? 0 : t - openSince,
      perclos, yawns: recent(yawns), nods: recent(nods),
      faceLost: t - (lastFace === null ? firstT : lastFace) > lostSecs,
      pitch: f.face && typeof f.pitch === "number" ? f.pitch : null, pitchBaseline: pitch0,
      discontinuity,
    };
    return snap;
  }

  return { feed, setConfig, get snapshot() { return snap; } };
}
