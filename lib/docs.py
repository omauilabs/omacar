"""The document library: receipts, registrations, citations, everything else.

WHY THIS BELONGS IN A DIAGNOSTIC TOOL.

A car's history is not only what its computers remember. It is the oil change
receipt in the glovebox, the registration renewal, the inspection certificate,
the parking citation, the warranty card for a part somebody fitted three years
ago. That paper is what a buyer asks for, what a warranty claim needs, and what
tells you whether the timing belt was actually done at 90,000 miles or just
written on a sticker.

It also gets lost, which is the point. OmaCar already knows the odometer, the
drives, the faults and the service schedule. Filing the paperwork against the
same vehicle record turns a folder of receipts into a history that can be
searched, totalled and handed over.

FILED AGAINST THE VEHICLE, NOT THE APPLICATION.

Documents live in the per-VIN database like everything else, so a garage of
several cars keeps them apart with no chance of a Fit's inspection certificate
appearing under the CR-Z. Selling a car should hand over its documents with it.

THE ORIGINAL FILE IS NEVER MODIFIED.

Whatever is extracted from a document -- a date, a vendor, an amount, an
odometer reading -- is stored ALONGSIDE it and never in place of it. Extraction
can be wrong; a scan of a receipt cannot. Anything derived is marked with where
it came from, so a total that looks wrong can be traced back to the page it was
read off.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import records  # noqa: E402

# NOTE FOR ANYONE WRITING A TEST: this derives from records.STATE, not from
# records.DB. Overriding records.DB alone therefore isolates the DATABASE and
# not the FILES -- rows land in your temp database while documents are written
# into the real library, and deleting the temp database orphans them. Override
# docs.ROOT too. (Found exactly this way, by orphaning a file.)
ROOT = os.path.join(records.STATE, "documents")
MAX_BYTES = 32 * 1024 * 1024

# What a document IS, which decides how it is treated and what is asked of it.
KINDS = {
    "service":      "Oil change, repair, maintenance — anything done to the car",
    "receipt":      "Parts, fuel, consumables",
    "registration": "Registration, title, tax",
    "insurance":    "Policy documents and claims",
    "inspection":   "Emissions, safety, roadworthiness certificates",
    "citation":     "Tickets and fines",
    "warranty":     "Warranty cards and coverage",
    "manual":       "Owner's manual, service manual, wiring diagram",
    "other":        "Anything else worth keeping with the car",
}

SAFE = re.compile(r"[^A-Za-z0-9_.-]")

EXT_FOR = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "text/plain": ".txt",
}


def folder(key=None):
    import garage
    d = os.path.join(ROOT, key or garage.current())
    os.makedirs(d, exist_ok=True)
    return d


def sniff(blob):
    """Content type from the bytes themselves.

    Not from the filename. A phone that names a scan `receipt.pdf` when it is
    actually a JPEG is common, and storing it under the wrong extension makes
    it unopenable later for no reason anybody will be able to reconstruct.
    """
    if blob[:4] == b"%PDF":
        return "application/pdf"
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc"):
        return "image/heic"
    try:
        blob[:2048].decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return "application/octet-stream"


def open_db():
    db = sqlite3.connect(records.DB, timeout=5.0)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY,
        added REAL, kind TEXT, title TEXT,
        vendor TEXT, doc_date TEXT, amount REAL, currency TEXT,
        odometer REAL, note TEXT, tags TEXT,
        file TEXT, mime TEXT, bytes INTEGER, sha TEXT,
        extracted TEXT, extracted_at REAL)""")
    db.execute("CREATE INDEX IF NOT EXISTS documents_date ON documents(doc_date)")
    db.commit()
    return db


def add(path_or_bytes, kind="other", title="", vendor="", doc_date="",
        amount=None, odometer=None, note="", tags=None, filename=""):
    """File a document against the current vehicle. Returns its record."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        blob = bytes(path_or_bytes)
        src_name = filename or "document"
    else:
        # Size FIRST. Reading a five-gigabyte file into memory and then
        # rejecting it for being too large is a peculiar way to enforce a
        # limit, and the machine this runs on has two cores and no headroom.
        try:
            size = os.path.getsize(path_or_bytes)
        except OSError as e:
            raise ValueError(str(e)) from e
        if size > MAX_BYTES:
            raise ValueError(f"{size // (1024*1024)} MB is larger than the "
                             f"{MAX_BYTES // (1024*1024)} MB limit")
        with open(path_or_bytes, "rb") as f:
            blob = f.read(MAX_BYTES + 1)
        src_name = filename or os.path.basename(path_or_bytes)

    if not blob:
        raise ValueError("that file is empty")
    if len(blob) > MAX_BYTES:
        raise ValueError(f"{len(blob) // (1024*1024)} MB is larger than the "
                         f"{MAX_BYTES // (1024*1024)} MB limit")
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")

    mime = sniff(blob)
    sha = hashlib.sha256(blob).hexdigest()

    db = open_db()
    try:
        # The same document filed twice is nearly always a mistake -- a phone
        # re-uploading, or somebody scanning the glovebox again. Returning the
        # existing record rather than a duplicate keeps totals honest.
        row = db.execute("SELECT * FROM documents WHERE sha=?", (sha,)).fetchone()
        if row:
            out = dict(row)
            out["duplicate"] = True
            return out

        ext = EXT_FOR.get(mime, os.path.splitext(src_name)[1] or ".bin")
        stem = SAFE.sub("_", os.path.splitext(src_name)[0])[:48] or "document"
        name = f"{sha[:12]}_{stem}{ext}"
        with open(os.path.join(folder(), name), "wb") as f:
            f.write(blob)

        cur = db.execute(
            "INSERT INTO documents (added, kind, title, vendor, doc_date, amount,"
            " currency, odometer, note, tags, file, mime, bytes, sha)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), kind, title or stem, vendor, doc_date, amount, "USD",
             odometer, note, json.dumps(tags or []), name, mime, len(blob), sha))
        db.commit()
        return dict(db.execute("SELECT * FROM documents WHERE id=?",
                               (cur.lastrowid,)).fetchone())
    finally:
        db.close()


def listing(kind=None, n=500):
    db = open_db()
    try:
        if kind:
            rows = db.execute("SELECT * FROM documents WHERE kind=? "
                              "ORDER BY COALESCE(doc_date,'') DESC, added DESC "
                              "LIMIT ?", (kind, n)).fetchall()
        else:
            rows = db.execute("SELECT * FROM documents "
                              "ORDER BY COALESCE(doc_date,'') DESC, added DESC "
                              "LIMIT ?", (n,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except ValueError:
                d["tags"] = []
            try:
                d["extracted"] = json.loads(d.get("extracted") or "null")
            except ValueError:
                d["extracted"] = None
            out.append(d)
        return out
    finally:
        db.close()


def get(doc_id):
    """One document, by id.

    Was a linear scan over `listing(n=10000)`: O(n) on every lookup, and worse,
    a library with more than ten thousand documents made older ones simply
    invisible to get() -- remove() and parse() would report "no such document"
    for a document plainly on screen.
    """
    db = open_db()
    try:
        r = db.execute("SELECT * FROM documents WHERE id=?",
                       (int(doc_id),)).fetchone()
        if not r:
            return None
        d = dict(r)
        for field, default in (("tags", []), ("extracted", None)):
            try:
                d[field] = json.loads(d.get(field) or ("[]" if default == [] else "null"))
            except ValueError:
                d[field] = default
        return d
    except (ValueError, TypeError):
        return None
    finally:
        db.close()


def path_of(name):
    """Resolve a stored filename, refusing anything that climbs out.

    Never join a request path directly. The same rule photos.py follows, for
    the same reason: this is reachable from the browser.
    """
    safe = SAFE.sub("_", os.path.basename(name or ""))
    if not safe:
        return None
    p = os.path.join(folder(), safe)
    real = os.path.realpath(p)
    if not real.startswith(os.path.realpath(folder()) + os.sep):
        return None
    return real if os.path.exists(real) else None


def update(doc_id, **fields):
    allowed = ("kind", "title", "vendor", "doc_date", "amount", "odometer",
               "note", "tags")
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        sets.append(f"{k}=?")
        args.append(json.dumps(v) if k == "tags" else v)
    if not sets:
        return False
    args.append(int(doc_id))
    db = open_db()
    try:
        db.execute(f"UPDATE documents SET {','.join(sets)} WHERE id=?", args)
        db.commit()
        return True
    finally:
        db.close()


def remove(doc_id):
    d = get(doc_id)
    if not d:
        return False
    db = open_db()
    try:
        db.execute("DELETE FROM documents WHERE id=?", (int(doc_id),))
        db.commit()
    finally:
        db.close()
    p = path_of(d.get("file") or "")
    if p:
        try:
            os.remove(p)
        except OSError:
            pass
    return True


def orphans():
    """Files in the library with no row pointing at them.

    They happen: a database restored from an older backup, an interrupted
    write, or a test that isolated the database but not the file root. Worth
    being able to find rather than leaving somebody to wonder why the folder is
    bigger than the library says it is.
    """
    known = {d.get("file") for d in listing(n=100000)}
    out = []
    d = folder()
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if name not in known:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                out.append({"file": name, "bytes": os.path.getsize(p), "path": p})
    return out


def sweep_orphans():
    """Delete them. Returns what went."""
    gone = []
    for o in orphans():
        try:
            os.remove(o["path"])
            gone.append(o["file"])
        except OSError:
            pass
    return gone


def totals():
    """What this car has cost, from the paperwork rather than from guesses."""
    docs = listing(n=10000)
    by_kind, spend = {}, 0.0
    for d in docs:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1
        if d.get("amount"):
            spend += float(d["amount"])
    dated = [d for d in docs if d.get("doc_date")]
    return {
        "count": len(docs),
        "by_kind": by_kind,
        "spend": round(spend, 2),
        "with_amount": sum(1 for d in docs if d.get("amount")),
        "earliest": min((d["doc_date"] for d in dated), default=None),
        "latest": max((d["doc_date"] for d in dated), default=None),
        "bytes": sum(d.get("bytes") or 0 for d in docs),
    }


# ---- reading what a document says -------------------------------------------

def extract_text(doc, max_chars=12000):
    """The words in a document, however they are stored.

    Three routes, and which one runs is reported back so a bad extraction can
    be explained rather than just distrusted:

      - text/plain: read it.
      - PDF: pdftotext. If that yields almost nothing the PDF is a photograph
        of paper rather than text -- extremely common for a receipt scanned by
        a phone -- so it is rendered and OCR'd instead.
      - images: tesseract.

    Returns (text, how, problem).
    """
    import subprocess
    p = path_of(doc.get("file") or "")
    if not p:
        return "", "", "the file is missing"
    mime = doc.get("mime") or ""

    def run(cmd, **kw):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, **kw)
            return r.stdout if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    if mime == "text/plain":
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read()[:max_chars], "plain text", ""
        except OSError as e:
            return "", "", str(e)

    if mime == "application/pdf":
        if not shutil.which("pdftotext"):
            return "", "", "pdftotext is not installed (pacman -S poppler)"
        text = run(["pdftotext", "-layout", p, "-"])
        # A born-digital PDF gives plenty of text; a scan gives a handful of
        # stray characters from the page furniture. That threshold is what
        # decides whether to fall back to OCR.
        if len(text.strip()) >= 40:
            return text[:max_chars], "pdftotext", ""
        if not shutil.which("tesseract") or not shutil.which("pdftoppm"):
            return "", "", ("this PDF is a scan and needs OCR; install "
                            "tesseract and poppler")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            run(["pdftoppm", "-r", "200", "-png", "-f", "1", "-l", "3",
                 p, os.path.join(td, "pg")])
            out = []
            for f in sorted(os.listdir(td)):
                out.append(run(["tesseract", os.path.join(td, f), "stdout"]))
            text = "\n".join(out)
        return text[:max_chars], "OCR (scanned PDF)", ""

    if mime.startswith("image/"):
        if not shutil.which("tesseract"):
            return "", "", "tesseract is not installed (pacman -S tesseract tesseract-data-eng)"
        return run(["tesseract", p, "stdout"])[:max_chars], "OCR", ""

    return "", "", f"cannot read {mime}"


EXTRACT_SCHEMA = {
    "kind": "one of: service, receipt, registration, insurance, inspection, "
            "citation, warranty, manual, other",
    "title": "a short human title, e.g. 'Oil change — Jiffy Lube'",
    "vendor": "who issued it",
    "doc_date": "YYYY-MM-DD, the date ON the document",
    "amount": "total paid, as a number, or null",
    "odometer": "mileage recorded on the document, as a number, or null",
    "items": "list of what was done or bought, each {description, amount}",
    "service_items": "list of maintenance actions in OmaCar's vocabulary, e.g. "
                     "['engine oil', 'oil filter', 'tire rotation']",
    "confidence": "high, medium or low — how legible and unambiguous this was",
    "unreadable": "true if the text was too poor to trust",
}


def parse(doc_id, model=None):
    """Ask the advisor what a document says. Never overwrites the original.

    Everything returned lands in `extracted` as a separate column, tagged with
    when it was read. The document's own fields are only filled in where they
    were EMPTY -- an extraction never overwrites something a person typed,
    because the person was looking at the paper and the OCR was guessing at it.
    """
    import ai
    d = get(doc_id)
    if not d:
        return None, "no such document"
    if not ai.available():
        return None, ("the advisor needs the `claude` CLI, which is not "
                      "installed. Everything else in the library works without it.")

    text, how, problem = extract_text(d)
    if problem:
        return None, problem
    if len(text.strip()) < 20:
        return None, ("no readable text came out of that document — if it is a "
                      "photograph, a straighter, brighter one usually fixes it")

    prompt = (
        "Read this vehicle document and return JSON matching the schema.\n"
        "Report only what the document actually says. Where a field is not "
        "present, use null rather than inferring it — a plausible guess in a "
        "maintenance record is worse than a gap, because it will be trusted "
        "later.\n\n"
        f"SCHEMA:\n{json.dumps(EXTRACT_SCHEMA, indent=2)}\n\n"
        f"DOCUMENT (read by {how}):\n{text}\n")

    try:
        # run_claude returns (text, seconds, envelope), not a string.
        text_out, took, _env = ai.run_claude(
            prompt, model or ai.DEFAULT_MODEL,
            "You are reading a vehicle maintenance document.")
        got = ai.extract_json(text_out)
    except Exception as e:                                    # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    if not isinstance(got, dict):
        return None, "the advisor did not return usable JSON"

    got["_read_by"] = how
    got["_took_s"] = round(took, 1)
    db = open_db()
    try:
        db.execute("UPDATE documents SET extracted=?, extracted_at=? WHERE id=?",
                   (json.dumps(got), time.time(), int(doc_id)))
        # Fill only what is empty. See the docstring.
        for col, key in (("kind", "kind"), ("title", "title"),
                         ("vendor", "vendor"), ("doc_date", "doc_date"),
                         ("amount", "amount"), ("odometer", "odometer")):
            val = got.get(key)
            if val in (None, "", []):
                continue
            if col == "kind" and val not in KINDS:
                continue
            cur = d.get(col)
            if cur in (None, "", 0):
                db.execute(f"UPDATE documents SET {col}=? WHERE id=?",
                           (val, int(doc_id)))
        db.commit()
    finally:
        db.close()
    return got, ""
