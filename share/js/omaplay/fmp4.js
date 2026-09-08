// Annex-B H.264, wrapped as fragmented MP4.
//
// WHY THIS FILE EXISTS AT ALL, given that WebCodecs already decodes H.264 and
// is the better path in every respect.
//
// Because on some browsers it does not, and it does not SAY so. A headless
// Chromium with no hardware decoder answers `VideoDecoder.isConfigSupported`
// with yes, configures without complaint, accepts every chunk it is handed,
// and returns no frames at all -- there is no software fallback wired up for
// this codec. Nothing throws. The canvas stays exactly as black as it would
// with no adapter plugged in, which is the worst possible failure for a screen
// whose whole job is telling the driver what is actually true.
//
// The same browser plays the same bytes through a <video> element without
// complaint, because that path does have a software decoder. It just wants
// them in a container.
//
// So this is a muxer. It is about three hundred lines to avoid one black
// rectangle, and it was written after measuring both paths in the same
// browser rather than after guessing which one would work.
//
// WHAT IT PRODUCES. An ISO base media initialisation segment (ftyp + moov)
// once, then one media segment (moof + mdat) per picture. Sample data is
// length-prefixed rather than start-code delimited, which is the difference
// between Annex-B and what an MP4 sample wants.

// ------------------------------------------------------------------- reading
//
// Exp-Golomb, the bit-level coding an SPS is written in. Nothing else in the
// stream needs it, and the SPS needs it for exactly two values -- the picture
// width and height -- which the container has to state and only the stream
// knows.

class Bits {
  constructor(bytes) {
    // EMULATION PREVENTION COMES OUT FIRST. An encoder inserts a 0x03 after
    // any 00 00 that would otherwise look like a start code, and reading the
    // bits without removing them gives a plausible, wrong answer rather than
    // an error -- a picture size off by a factor that only shows up as a
    // stretched image.
    const out = [];
    for (let i = 0; i < bytes.length; i++) {
      if (i >= 2 && bytes[i] === 3 && bytes[i - 1] === 0 && bytes[i - 2] === 0) continue;
      out.push(bytes[i]);
    }
    this.b = out;
    this.i = 0;
  }
  bit() {
    const byte = this.b[this.i >> 3];
    const v = byte === undefined ? 0 : (byte >> (7 - (this.i & 7))) & 1;
    this.i += 1;
    return v;
  }
  bits(n) { let v = 0; for (let k = 0; k < n; k++) v = (v << 1) | this.bit(); return v >>> 0; }
  ue() {
    let zeros = 0;
    while (this.i < this.b.length * 8 && this.bit() === 0) zeros++;
    if (zeros === 0) return 0;
    return ((1 << zeros) >>> 0) - 1 + this.bits(zeros);
  }
  se() { const k = this.ue(); return (k & 1) ? (k + 1) >> 1 : -(k >> 1); }
}

// The profiles that carry the extra chroma and scaling fields. Baseline, which
// is what a car adapter emits, is not among them -- but a file dropped in for
// a replay may well be, and reading one of those with the baseline layout
// gives a wrong size rather than a failure.
const RICH_PROFILES = [100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135];

export function parseSPS(nal) {
  // `nal` includes its type byte. Everything after it is the payload.
  const r = new Bits(nal.subarray(1));
  const profile = r.bits(8);
  const compat = r.bits(8);
  const level = r.bits(8);
  r.ue();                                   // seq_parameter_set_id
  let chroma = 1;
  if (RICH_PROFILES.includes(profile)) {
    chroma = r.ue();
    if (chroma === 3) r.bit();              // separate_colour_plane_flag
    r.ue(); r.ue();                         // bit depths
    r.bit();                                // qpprime_y_zero_transform_bypass
    if (r.bit()) {                          // scaling matrices, skipped in full
      const lists = chroma !== 3 ? 8 : 12;
      for (let i = 0; i < lists; i++) {
        if (r.bit()) {
          let last = 8, next = 8;
          const size = i < 6 ? 16 : 64;
          for (let j = 0; j < size; j++) {
            if (next !== 0) next = (last + r.se() + 256) % 256;
            last = next === 0 ? last : next;
          }
        }
      }
    }
  }
  r.ue();                                   // log2_max_frame_num_minus4
  const pocType = r.ue();
  if (pocType === 0) r.ue();
  else if (pocType === 1) {
    r.bit(); r.se(); r.se();
    const n = r.ue();
    for (let i = 0; i < n; i++) r.se();
  }
  r.ue();                                   // max_num_ref_frames
  r.bit();                                  // gaps_in_frame_num_allowed
  const widthMbs = r.ue() + 1;
  const heightUnits = r.ue() + 1;
  const frameMbsOnly = r.bit();
  if (!frameMbsOnly) r.bit();               // mb_adaptive_frame_field
  r.bit();                                  // direct_8x8_inference
  let cropL = 0, cropR = 0, cropT = 0, cropB = 0;
  if (r.bit()) { cropL = r.ue(); cropR = r.ue(); cropT = r.ue(); cropB = r.ue(); }

  // Cropping is counted in chroma samples, so the multiplier depends on the
  // sampling: 4:2:0 is two across and two down, 4:2:2 is two across and one
  // down, and monochrome and 4:4:4 are one of each. Treating monochrome as
  // 4:2:0 -- which is what "anything but 4:4:4 is 2" does -- cropped it twice
  // as much as it should across.
  const subW = (chroma === 1 || chroma === 2) ? 2 : 1;
  const subH = chroma === 1 ? 2 : 1;
  const width = widthMbs * 16 - (cropL + cropR) * subW;
  const height = (2 - frameMbsOnly) * heightUnits * 16
                 - (cropT + cropB) * subH * (2 - frameMbsOnly);

  // A TRUNCATED SPS PARSES INTO SOMETHING PLAUSIBLE RATHER THAN FAILING.
  // Exp-Golomb reading past the end returns zeros, so the arithmetic above
  // completes and hands back a real-looking 16x16 -- which would then be
  // latched into an initialisation segment for the rest of the drive, with
  // the fallback playing the whole phone screen into a postage stamp. The
  // bounds below are wider than any real picture and reject only nonsense.
  if (!(width >= 64 && width <= 8192 && height >= 64 && height <= 8192)) {
    return null;
  }
  return { profile, compat, level, width, height };
}

// -------------------------------------------------------------------- boxes

function u32(v) { return [(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255]; }
function u16(v) { return [(v >>> 8) & 255, v & 255]; }

function box(type, ...parts) {
  let size = 8;
  const bodies = parts.map((p) => (p instanceof Uint8Array ? p : Uint8Array.from(p)));
  for (const b of bodies) size += b.length;
  const out = new Uint8Array(size);
  out.set(u32(size), 0);
  out.set([type.charCodeAt(0), type.charCodeAt(1), type.charCodeAt(2), type.charCodeAt(3)], 4);
  let at = 8;
  for (const b of bodies) { out.set(b, at); at += b.length; }
  return out;
}

function join(list) {
  let n = 0;
  for (const b of list) n += b.length;
  const out = new Uint8Array(n);
  let at = 0;
  for (const b of list) { out.set(b, at); at += b.length; }
  return out;
}

const FOURCC = (s) => [s.charCodeAt(0), s.charCodeAt(1), s.charCodeAt(2), s.charCodeAt(3)];

// The media timescale. 90 kHz is the customary one for video and divides every
// frame rate an adapter is likely to use without accumulating rounding drift.
const TIMESCALE = 90000;

// The profiles whose configuration record carries three more fields. Baseline,
// which is what an adapter emits, is not among them -- but a High-profile file
// dropped in for a replay is, and a record that stops early for one of those is
// short by four bytes.
const AVCC_EXTENDED = [100, 110, 122, 144];

function avcC(sps, pps) {
  const head = [
    1, sps[1], sps[2], sps[3],
    0xFF,                       // 6 bits reserved, then a 4-byte length prefix
    0xE1,                       // 3 bits reserved, then one SPS
    ...u16(sps.length)];
  const tail = AVCC_EXTENDED.includes(sps[1])
    ? [0xFC | 1,                // 6 bits reserved, chroma_format 4:2:0
       0xF8 | 0,                // 5 bits reserved, bit_depth_luma_minus8
       0xF8 | 0,                // 5 bits reserved, bit_depth_chroma_minus8
       0]                       // numOfSequenceParameterSetExt
    : [];
  return box("avcC", head, sps, [1, ...u16(pps.length)], pps, tail);
}

function avc1(sps, pps, w, h) {
  return box("avc1",
    [0, 0, 0, 0, 0, 0,          // reserved
     0, 1],                     // data_reference_index
    [0, 0, 0, 0,                // pre_defined, reserved
     0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [...u16(w), ...u16(h)],
    [0, 0x48, 0, 0, 0, 0x48, 0, 0],   // 72 dpi horizontal and vertical
    [0, 0, 0, 0],
    [0, 1],                     // frame_count
    new Uint8Array(32),         // compressor name
    [0, 0x18],                  // depth
    [0xFF, 0xFF],               // pre_defined = -1
    avcC(sps, pps));
}

export function initSegment({ sps, pps, width, height }) {
  const ftyp = box("ftyp", FOURCC("isom"), u32(0x200),
                   [...FOURCC("isom"), ...FOURCC("iso2"),
                    ...FOURCC("avc1"), ...FOURCC("mp41")]);
  const mvhd = box("mvhd", [0, 0, 0, 0], u32(0), u32(0), u32(1000), u32(0),
    [0, 1, 0, 0],                                   // rate 1.0
    [1, 0],                                         // volume 1.0
    [0, 0],                                         // reserved
    new Uint8Array(8),                              // reserved[2]
    [0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,            // the identity matrix
     0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 0, 0, 0, 0x40, 0, 0, 0],
    new Uint8Array(24), u32(2));                    // next_track_ID
  const tkhd = box("tkhd", [0, 0, 0, 3], u32(0), u32(0), u32(1), u32(0), u32(0),
    new Uint8Array(8),          // reserved[2]
    [0, 0],                     // layer
    [0, 0],                     // alternate_group
    [0, 0],                     // volume: zero, because this track is video
    [0, 0],                     // reserved
    [0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 0, 0, 0, 0x40, 0, 0, 0],
    u32(width << 16), u32(height << 16));
  const mdhd = box("mdhd", [0, 0, 0, 0], u32(0), u32(0), u32(TIMESCALE), u32(0),
    [0x55, 0xC4, 0, 0]);                            // 'und', unspecified
  const hdlr = box("hdlr", [0, 0, 0, 0], u32(0), FOURCC("vide"),
    new Uint8Array(12), [0x56, 0x69, 0x64, 0x65, 0x6F, 0]);   // "Video\0"
  const stbl = box("stbl",
    box("stsd", [0, 0, 0, 0], u32(1), avc1(sps, pps, width, height)),
    box("stts", [0, 0, 0, 0], u32(0)),
    box("stsc", [0, 0, 0, 0], u32(0)),
    box("stsz", [0, 0, 0, 0], u32(0), u32(0)),
    box("stco", [0, 0, 0, 0], u32(0)));
  const minf = box("minf",
    box("vmhd", [0, 0, 0, 1], new Uint8Array(8)),
    box("dinf", box("dref", [0, 0, 0, 0], u32(1),
                    box("url ", [0, 0, 0, 1]))),
    stbl);
  const moov = box("moov", mvhd,
    box("trak", tkhd, box("mdia", mdhd, hdlr, minf)),
    box("mvex", box("trex", [0, 0, 0, 0], u32(1), u32(1), u32(0), u32(0), u32(0))));
  return join([ftyp, moov]);
}

// An access unit's NALs, length-prefixed, which is what an MP4 sample is.
//
// Access unit delimiters and filler are dropped: they carry nothing a decoder
// reading a container needs, and one of them at the head of a sample is enough
// to make a strict demuxer reject the whole thing.
const DROP = [9, 12];

// The NALs in one access unit, without their start codes. One reader, used by
// both the muxer's learning and its packing: two of these drifted apart once
// already in this project and it cost an evening.
export function nalsOf(unit) {
  const marks = [];
  let i = 0;
  while (i + 3 < unit.length) {
    if (unit[i] === 0 && unit[i + 1] === 0) {
      if (unit[i + 2] === 1) { marks.push([i, i + 3]); i += 3; continue; }
      if (unit[i + 2] === 0 && unit[i + 3] === 1) { marks.push([i, i + 4]); i += 4; continue; }
    }
    i += 1;
  }
  const out = [];
  for (let k = 0; k < marks.length; k++) {
    const from = marks[k][1];
    const to = k + 1 < marks.length ? marks[k + 1][0] : unit.length;
    if (to > from) out.push(unit.subarray(from, to));
  }
  return out;
}

export function toAvcc(unit) {
  const parts = [];
  for (const nal of nalsOf(unit)) {
    if (DROP.includes(nal[0] & 0x1F)) continue;
    parts.push(Uint8Array.from(u32(nal.length)), nal);
  }
  return join(parts);
}

// A picture a container may declare a random-access point. NOT the same
// question as "can a decoder start here": many low-latency encoders repeat the
// parameter sets in front of every picture, so an SPS is no evidence of an IDR,
// and declaring every P-frame a seek target invites the player to splice into
// the middle of a group and show a smear.
export function isSyncSample(unit) {
  for (const nal of nalsOf(unit)) if ((nal[0] & 0x1F) === 5) return true;
  return false;
}

export function createMuxer() {
  let sps = null, pps = null, seq = 1, clock = 0, ready = null, generation = 0;

  // Whether two parameter sets are the same one. Byte equality, because a
  // parameter set that differs anywhere describes a different stream.
  const same = (a, b) => !!a && a.length === b.length
    && a.every((v, i) => v === b[i]);


  return {
    get ready() { return ready; },

    // Parameter sets are learned from the stream rather than configured,
    // because the stream is the only place they are certainly correct.
    learn(unit) {
      let fresh = false;
      for (const nal of nalsOf(unit)) {
        const kind = nal[0] & 0x1F;
        // COMPARED, NOT LATCHED. A phone that rotates, or a head unit told a
        // new size, sends a different sequence parameter set mid-stream. The
        // first version of this took the first one it ever saw and never
        // looked again, so the container went on declaring a resolution the
        // stream had stopped using -- and the only way out was a reload.
        if (kind === 7 && !same(sps, nal)) { sps = nal.slice(); fresh = true; }
        else if (kind === 8 && !same(pps, nal)) { pps = nal.slice(); fresh = true; }
      }
      if (sps && pps && (fresh || !ready)) {
        const s = parseSPS(sps);
        if (!s) {
          // Unreadable. Keep what we had rather than replacing it with
          // nonsense, and wait for the next parameter set.
          sps = ready ? sps : null;
          return ready;
        }
        ready = {
          width: s.width, height: s.height,
          codec: "avc1." + [s.profile, s.compat, s.level]
            .map((v) => v.toString(16).padStart(2, "0")).join(""),
          init: initSegment({ sps, pps, width: s.width, height: s.height }),
          generation: generation += 1,
        };
      }
      return ready;
    },

    // One picture, as its own fragment. Per-picture fragments are what keeps
    // the latency down: a fragment cannot be decoded until it is whole, so a
    // fragment holding a second of video costs a second of delay.
    segment(unit, isKey, durationTicks) {
      const data = toAvcc(unit);
      const trun = box("trun",
        // version 0, then the flags naming exactly the fields written below:
        // 0x000400 sample-flags, 0x000200 sample-size, 0x000100 duration,
        // 0x000001 data-offset. Naming a field and not writing it -- 0x000800,
        // the composition offset, was in here once -- makes every subsequent
        // field read one word early, and the only symptom is a source buffer
        // that refuses the segment with no reason given.
        [0, 0, 0x07, 0x01],
        u32(1),                             // one sample
        u32(0),                             // patched below
        u32(durationTicks),
        u32(data.length),
        u32(isKey ? 0x02000000 : 0x01010000));
      const traf = box("traf",
        box("tfhd", [0, 0x02, 0, 0], u32(1)),          // default-base-is-moof
        box("tfdt", [1, 0, 0, 0], u32(Math.floor(clock / 4294967296)),
            u32(clock >>> 0)),
        trun);
      const moof = box("moof", box("mfhd", [0, 0, 0, 0], u32(seq++)), traf);
      // The data offset is measured from the start of the moof, and cannot be
      // known until the moof has been built. This is the one field every
      // muxer patches after the fact.
      const offset = moof.length + 8;
      const at = moof.length - trun.length + 16;
      moof.set(u32(offset), at);
      clock += durationTicks;
      return join([moof, box("mdat", data)]);
    },

    get timescale() { return TIMESCALE; },
    reset() { seq = 1; clock = 0; },
  };
}
