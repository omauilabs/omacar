"""Ears: one recorded clip in, the words and how sure, out.

faster-whisper on the CPU, small.en at int8, loaded once. It reads the
browser's webm/opus directly. Not installed -> ("", -9.0), which the relay
speaks as "Didn't catch that."
"""
_model = None


def transcribe(path):
    global _model
    try:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel("small.en", device="cpu", compute_type="int8")
        segments, _info = _model.transcribe(path, vad_filter=True, beam_size=1)
        segs = list(segments)
    except Exception:                                          # noqa: BLE001
        return "", -9.0
    if not segs:
        return "", -9.0
    text = " ".join(s.text.strip() for s in segs).strip()
    return text, sum(s.avg_logprob for s in segs) / len(segs)
