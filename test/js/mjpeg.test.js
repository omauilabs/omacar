import { eq } from "./assert.js";
import { createMjpegParser } from "../js/mjpeg.js";

const enc = (s) => new TextEncoder().encode(s);
const part = (bytes) => {
  const head = enc(`--omacarframe\r\nContent-Type: image/jpeg\r\nContent-Length: ${bytes.length}\r\n\r\n`);
  const out = new Uint8Array(head.length + bytes.length + 2);
  out.set(head);
  out.set(bytes, head.length);
  out.set(enc("\r\n"), head.length + bytes.length);
  return out;
};
const J1 = new Uint8Array([0xff, 0xd8, 1, 2, 3, 0xff, 0xd9]);
// A picture with a blank line inside it: the parser must take it by length.
const J2 = new Uint8Array([0xff, 0xd8, 0x0d, 0x0a, 0x0d, 0x0a, 9, 0xff, 0xd9]);
const both = () => { const a = part(J1), b = part(J2); const o = new Uint8Array(a.length + b.length); o.set(a); o.set(b, a.length); return o; };
const arr = (fs) => fs.map((f) => [...f]);

export default [
  ["two parts in one read are two pictures", () => eq(arr(createMjpegParser().push(both())), [[...J1], [...J2]])],
  ["split anywhere, they come out whole", () => {
    const all = both();
    for (let cut = 1; cut < all.length; cut++) {
      const p = createMjpegParser();
      eq(arr([...p.push(all.slice(0, cut)), ...p.push(all.slice(cut))]), [[...J1], [...J2]], `cut at ${cut}`);
    }
  }],
  ["a picture is taken by its length, not by looking for a blank line", () =>
    eq(createMjpegParser().push(part(J2)).map((f) => f.length), [J2.length])],
];
