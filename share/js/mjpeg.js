// Frames out of the recorder's live stream (lib/cams.py, stream_live): a
// multipart body where every part carries its own Content-Length. A part is
// taken by its length, never by searching for the boundary or a blank line,
// which a JPEG is free to contain.

const CRLF2 = [13, 10, 13, 10];

function find(buf, seq) {
  outer: for (let i = 0; i + seq.length <= buf.length; i++) {
    for (let j = 0; j < seq.length; j++) if (buf[i + j] !== seq[j]) continue outer;
    return i;
  }
  return -1;
}

export function createMjpegParser() {
  let buf = new Uint8Array(0);
  const dec = new TextDecoder();
  return {
    push(chunk) {
      const joined = new Uint8Array(buf.length + chunk.length);
      joined.set(buf);
      joined.set(chunk, buf.length);
      buf = joined;
      const out = [];
      for (;;) {
        const head = find(buf, CRLF2);
        if (head < 0) break;
        const m = /content-length:\s*(\d+)/i.exec(dec.decode(buf.subarray(0, head)));
        if (!m) { buf = buf.slice(head + 4); continue; }
        const start = head + 4, n = Number(m[1]);
        if (buf.length < start + n) break;
        out.push(buf.slice(start, start + n));
        buf = buf.slice(start + n);
      }
      return out;
    },
  };
}
