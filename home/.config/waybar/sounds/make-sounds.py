#!/usr/bin/env python3
# Makes the theme's interface sounds (the .wav files next to this script).
# They're short, soft sine tones, quiet on purpose. Change a recipe below and run this again.
import math
import random
import struct
import wave
from pathlib import Path

RATE = 44100
HERE = Path(__file__).resolve().parent


def note(start_hz, seconds, volume, end_hz=None, decay=5.0):
    """A sine tone that fades out. With end_hz it glides from one pitch to the other."""
    end_hz = end_hz or start_hz
    count = int(RATE * seconds)
    samples, phase = [], 0.0
    for i in range(count):
        t = i / count
        phase += 2 * math.pi * (start_hz + (end_hz - start_hz) * t) / RATE
        attack = min(1.0, i / (RATE * 0.004))
        release = min(1.0, (count - i) / (RATE * 0.008))
        tone = math.sin(phase) + 0.18 * math.sin(2 * phase)
        samples.append(volume * attack * release * math.exp(-decay * t) * tone)
    return samples


def hiss(seconds, volume):
    """A soft burst of noise, for the camera shutter."""
    noise = random.Random(7)
    count = int(RATE * seconds)
    samples, smooth = [], 0.0
    for i in range(count):
        smooth += 0.35 * (noise.uniform(-1, 1) - smooth)
        samples.append(volume * math.sin(math.pi * i / count) * smooth)
    return samples


def silence(seconds):
    return [0.0] * int(RATE * seconds)


def mix(*layers):
    """Plays (delay in seconds, samples) layers on top of each other."""
    length = max(int(RATE * delay) + len(samples) for delay, samples in layers)
    out = [0.0] * length
    for delay, samples in layers:
        offset = int(RATE * delay)
        for i, sample in enumerate(samples):
            out[offset + i] += sample
    return out


SOUNDS = {
    "open":       note(430, 0.11, 0.20, end_hz=640, decay=3.5),
    "close":      note(560, 0.09, 0.16, end_hz=380, decay=3.5),
    "on":         mix((0, note(660, 0.07, 0.17)), (0.055, note(880, 0.10, 0.17))),
    "off":        mix((0, note(880, 0.07, 0.15)), (0.055, note(660, 0.10, 0.15))),
    "volume":     note(880, 0.07, 0.20, decay=6),
    "notify":     mix((0, note(784, 0.45, 0.20, decay=6)), (0.09, note(1047, 0.55, 0.20, decay=6))),
    "clip":       mix((0, note(659, 0.14, 0.17)), (0.08, note(784, 0.14, 0.17)), (0.16, note(988, 0.40, 0.18, decay=6))),
    "screenshot": hiss(0.03, 0.55) + silence(0.045) + hiss(0.05, 0.45),
}

for name, samples in SOUNDS.items():
    with wave.open(str(HERE / f"{name}.wav"), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767)) for sample in samples))
