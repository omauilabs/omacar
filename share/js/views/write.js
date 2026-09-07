// Write by identifier. The one thing god mode adds.
//
// UDS 0x2E changes a stored configuration value inside a module, and there is
// no undo: the previous value is gone unless it was written down. So this
// screen never writes in one tap. Read shows what the identifier holds now,
// with the consequence beside it. Write sends that prior value back as a claim
// -- "I saw this" -- and the server reads again before sending; if the module
// has moved on, the write is refused and you look again. After the write the
// identifier is read a third time, so the result is before and after, not
// "sent".
//
// THE DENY-LIST IS ON THE SCREEN, WITH ITS CAVEAT. A refused emissions write
// carries a statute, and the ranges it covers are drawn here from the same
// strings the server refuses with -- so the screen cannot promise more than
// the code enforces. The caveat is part of the list, because a deny-list that
// implies completeness is itself a lie.

import { h, clear, api, toast, confirmDialog } from "../core.js";
import { explain } from "../learn.js";

const HEX = /^[0-9A-Fa-f]*$/;

export default function write(root) {
  const _ex = explain(h, "write");
  if (_ex) root.appendChild(_ex);

  let stopFns = [];
  let last = null;          // the last read-back, which a write must carry

  root.appendChild(h("section.sect",
    h("div.head", h("div", h("div.eyebrow", "God mode"),
      h("div.title", "Write by identifier"))),
    h("p.lede",
      "Reads a stored value out of a module and, only on a second deliberate "
      + "step, writes a new one. The previous value is shown first and is the "
      + "only record of it there will be. Write it down.")));

  // ---- the deny-list, from the server ---------------------------------------
  const denyCard = h("div.card.tint-warn");
  root.appendChild(denyCard);
  async function paintDeny() {
    clear(denyCard);
    let m = null;
    try { m = await api.mode(); } catch { /* an older server */ }
    const deny = (m && m.deny) || null;
    denyCard.appendChild(h("div.title", "Refused whatever the mode"));
    if (!deny) {
      denyCard.appendChild(h("p.lede", "The deny-list could not be read from the server."));
      return;
    }
    const list = h("ul", { style: { margin: "8px 0 0 18px", padding: 0 } });
    for (const r of deny.ranges || []) {
      list.appendChild(h("li", h("span.mono", r.from + "–" + r.to), " — " + r.why));
    }
    for (const [id, why] of Object.entries(deny.ids || {})) {
      list.appendChild(h("li", h("span.mono", id), " — " + why));
    }
    denyCard.appendChild(list);
    denyCard.appendChild(h("p.muted", { style: { marginTop: "8px" } }, deny.citation));
    denyCard.appendChild(h("p.muted", { style: { marginTop: "6px" } }, deny.coverage));
    denyCard.appendChild(h("p.muted", { style: { marginTop: "6px" } },
      "Reprogramming (0x34, 0x36, 0x37) is not implemented at all, in any mode."));
  }
  paintDeny();

  // ---- the form -------------------------------------------------------------
  const header = h("input.mono", { type: "text", placeholder: "7E0", value: "7E0", maxLength: 8,
    style: { width: "7ch" } });
  const did = h("input.mono", { type: "text", placeholder: "F190", maxLength: 4,
    style: { width: "6ch" } });
  const value = h("input.mono", { type: "text", placeholder: "new value, hex", maxLength: 64,
    style: { width: "22ch" } });
  const readBtn = h("button.btn", "Read");
  const writeBtn = h("button.btn.danger", { disabled: true }, "Write…");
  const result = h("div", { style: { marginTop: "12px" } });

  root.appendChild(h("div.card",
    h("div.row.wrapline", { style: { gap: "12px", alignItems: "end" } },
      h("label.field", h("div.eyebrow", "Module"), header),
      h("label.field", h("div.eyebrow", "Identifier"), did),
      h("label.field", h("div.eyebrow", "Value"), value),
      readBtn, writeBtn),
    h("p.muted", { style: { marginTop: "8px" } },
      "Module is the request address (7E0 on 11-bit CAN, 18DA10F1 on 29-bit). "
      + "The identifier is two bytes. Nothing is sent until Read answers, and "
      + "nothing is written until you confirm against what Read showed."),
    result));

  function args() {
    const hd = header.value.trim().toUpperCase();
    const id = did.value.trim().toUpperCase();
    const val = value.value.replace(/\s+/g, "").toUpperCase();
    if (!HEX.test(hd) || ![3, 6, 8].includes(hd.length)) return toast("Module must be a 3-, 6- or 8-digit hex address.", "bad"), null;
    if (!HEX.test(id) || id.length !== 4) return toast("Identifier must be four hex digits.", "bad"), null;
    if (val && (!HEX.test(val) || val.length % 2)) return toast("Value must be whole bytes as hex.", "bad"), null;
    return { header: hd, did: id, value: val };
  }

  function paintResult(r, wrote) {
    clear(result);
    const rows = [["Module", r.header], ["Identifier", r.did],
      ["Holds now", r.prior || "(empty)"]];
    if (wrote) rows.push(["Sent", r.sent], ["Module said", r.reply], ["Holds after", r.after || "(unreadable)"]);
    else if (r.would_send && r.would_send.length > 6) rows.push(["Would send", r.would_send]);
    const tbl = h("table.tbl", h("tbody", ...rows.map(([k, v]) =>
      h("tr", h("td.muted", k), h("td.mono", v)))));
    result.appendChild(h("div.card" + (wrote ? ".tint-ok" : ""), tbl,
      h("p.muted", { style: { marginTop: "8px" } },
        wrote ? "Written, and read back. Both values are in the record book and the write ledger."
              : r.what + ". " + r.consequence.split("\n")[0])));
  }

  readBtn.addEventListener("click", async () => {
    const a = args();
    if (!a) return;
    readBtn.disabled = true; writeBtn.disabled = true; last = null;
    try {
      const r = await api.writeDid({ header: a.header, did: a.did, value: a.value || undefined });
      last = { ...r, value: a.value };
      paintResult(r, false);
      writeBtn.disabled = !a.value;
    } catch (e) {
      clear(result);
      result.appendChild(h("div.card.tint-bad", h("p.lede", String(e.message || e))));
    } finally {
      readBtn.disabled = false;
    }
  });

  writeBtn.addEventListener("click", async () => {
    const a = args();
    if (!a || !a.value) return toast("Read first, and give a value.", "bad");
    if (!last || last.did !== a.did || last.header !== a.header) {
      return toast("Read this identifier first; a write carries the value you were shown.", "bad");
    }
    const yes = await confirmDialog({
      title: "Write " + a.did + " on " + a.header + "?",
      body: h("div.sect",
        h("p.lede", "Now: " + (last.prior || "(empty)") + "   →   " + a.value),
        h("p.lede", last.consequence),
        h("p.muted", "The server reads the identifier again before sending and refuses "
          + "if it no longer holds what is shown here.")),
      confirm: "Write it",
      tone: "danger",
    });
    if (!yes) return;
    writeBtn.disabled = true; readBtn.disabled = true;
    try {
      const r = await api.writeDid({ header: a.header, did: a.did, value: a.value,
        confirm: true, prior: last.prior });
      paintResult(r, true);
      toast("Written and read back.", "ok");
      last = null;
    } catch (e) {
      clear(result);
      result.appendChild(h("div.card.tint-bad", h("p.lede", String(e.message || e))));
    } finally {
      readBtn.disabled = false;
    }
  });

  // The god tier decays on the server. When it does, the next request from
  // this screen is refused with the reason, and the navigation hides the
  // screen on its next paint; nothing here needs to guess at the clock.
  return () => { for (const f of stopFns) f(); };
}
