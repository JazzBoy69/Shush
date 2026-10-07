"""Shared active neural rendering pipeline, including the lexical stress pass."""

from stress import PASS_VERSION, postprocess_stress
from preview_voice import digest, write_wav


def add_mode_beat(model, corrected, audio, rate):
    """Add a 40 ms rhythmic hold at the model-aligned interword boundary."""
    import numpy as np
    from kokoro_onnx import trim_audio

    outputs = model.sess.last_outputs
    raw = outputs["waveform"]
    durations = np.asarray(outputs["duration"], dtype=np.int64).reshape(-1)
    tokens = model.tokenizer.tokenize(corrected)
    separator = model.tokenizer.tokenize(" ")
    if len(separator) != 1 or tokens.count(separator[0]) != 1:
        raise ValueError("Expected one mode word boundary")
    if durations.size != len(tokens) + 2 or (durations <= 0).any():
        raise ValueError("Unexpected model duration alignment")
    total = int(durations.sum())
    if raw.size % total:
        raise ValueError("Waveform does not match duration frame grid")
    frame_samples = raw.size // total
    trimmed, bounds = trim_audio(raw)
    if not np.array_equal(trimmed, audio):
        raise ValueError("Model trim differs from captured alignment")
    position = tokens.index(separator[0]) + 1  # leading model pad token
    start = int(durations[:position].sum()) * frame_samples - int(bounds[0])
    end = start + int(durations[position]) * frame_samples
    if not 0 < start < end < audio.size:
        raise ValueError("Mode boundary lies outside trimmed speech")
    # Use the quietest 5 ms window inside that boundary, then a nearby
    # zero crossing. Preserve the single utterance and all its spoken parts.
    window = rate // 200
    if end - start < window:
        raise ValueError("Mode boundary too short to locate a hold")
    energy = np.convolve(
        np.square(audio[start:end].astype(np.float64)), np.ones(window), mode="valid"
    )
    center = start + int(np.argmin(energy)) + window // 2
    radius = rate // 1000
    low, high = max(start, center - radius), min(end, center + radius + 1)
    point = low + int(np.argmin(np.abs(audio[low:high])))
    fade = min(rate // 500, point, audio.size - point)  # 2 ms click protection
    left, right = audio[:point].copy(), audio[point:].copy()
    left[-fade:] *= np.linspace(1.0, 0.0, fade)
    right[:fade] *= np.linspace(0.0, 1.0, fade)
    hold = rate * 40 // 1000
    result = np.concatenate([left, np.zeros(hold, dtype=audio.dtype), right])
    return result, {
        "protocol": "single-utterance-aligned-rhythmic-hold",
        "hold_frames": hold,
        "hold_milliseconds": 40,
        "hold_start_frame": point,
        "fade_frames": fade,
        "boundary_start_frame": start,
        "boundary_end_frame": end,
        "duration_frame_samples": int(frame_samples),
        "model_durations": durations.tolist(),
        "base_utterance_frames": int(audio.size),
    }


def render_prompt(model, voice, text, path):
    import numpy as np

    raw = model.tokenizer.phonemize(text, "en-us")
    corrected, corrections = postprocess_stress(text, raw)
    # Synthesize the connected phrase once, preserving its intonation.
    audio, rate = model.create(
        corrected, voice=voice, speed=1.0, lang="en-us", is_phonemes=True, trim=True
    )
    if rate != 24000 or audio.size == 0 or not np.isfinite(audio).all():
        raise ValueError("Invalid neural speech output")
    beat = None
    if text == "Without reading.":
        audio, beat = add_mode_beat(model, corrected, audio, rate)
    pcm = np.rint(np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    write_wav(path, pcm, rate)
    metadata = {
        "file": path.name,
        "text": text,
        "raw_phonemes": raw,
        "corrected_phonemes": corrected,
        "corrections": corrections,
        "stress_pass_version": PASS_VERSION,
        "raw_tokens": model.tokenizer.tokenize(raw),
        "model_tokens": model.tokenizer.tokenize(corrected),
        "frames": int(pcm.size),
        "sha256": digest(path),
    }
    if beat is not None:
        metadata["rhythmic_hold"] = beat
    return metadata, pcm
