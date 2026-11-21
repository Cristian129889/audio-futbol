"""Import, synthesize, and EBU R128 loudness-normalize all AudioFutbol audio assets.

Targets comfortable, non-fatiguing levels so sound effects and music never
overpower NVDA or Windows SAPI5 TTS:
  - Music & themes (.ogg): -19 LUFS, -2.0 dBTP
  - Crowd ambience loop (.wav): -22 LUFS, -4.0 dBTP
  - Sound effects (.wav): -16 LUFS, -3.0 dBTP (gentle peak limiting on impulses)
"""

from __future__ import annotations

import math
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import wave

SAMPLE_RATE = 44100


def _write_wav(path: Path, samples: list[float], sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = max((abs(s) for s in samples), default=1.0)
    scale = 0.85 / peak if peak > 0 else 1.0
    frames = bytearray()
    for s in samples:
        val = max(-1.0, min(1.0, s * scale))
        frames.extend(struct.pack("<h", int(val * 32767)))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))


def _env(t: float, dur: float, attack: float = 0.01, release: float = 0.05) -> float:
    if t < 0 or t > dur:
        return 0.0
    a = min(1.0, t / max(attack, 1e-5))
    r = min(1.0, (dur - t) / max(release, 1e-5))
    return a * r


def _synth_whistle(
    segments: list[tuple[float, float, float]],
    base_freq: float = 2650.0,
    trill_hz: float = 34.0,
    trill_depth: float = 110.0,
) -> list[float]:
    """Synthesize a realistic referee pea-whistle with FM trill and breath noise."""
    total_dur = max(start + dur for start, dur, _ in segments) + 0.08
    n = int(total_dur * SAMPLE_RATE)
    out = [0.0] * n
    rng = random.Random(42)
    phase = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        amp = 0.0
        pitch_mult = 1.0
        for start, dur, p_mul in segments:
            if start <= t <= start + dur:
                local_t = t - start
                amp = max(amp, _env(local_t, dur, 0.015, 0.04))
                pitch_mult = p_mul
        if amp > 0.0:
            freq = base_freq * pitch_mult + trill_depth * math.sin(2 * math.pi * trill_hz * t)
            phase += 2 * math.pi * freq / SAMPLE_RATE
            tone = 0.72 * math.sin(phase) + 0.16 * math.sin(2 * phase)
            breath = 0.08 * (rng.random() * 2 - 1)
            out[i] = amp * (tone + breath)
    return out


def _synth_kick(
    dur: float = 0.24,
    f_start: float = 155.0,
    f_end: float = 44.0,
    punch: float = 0.35,
    seed: int = 7,
) -> list[float]:
    """Synthesize a leather football impact thump with transient click."""
    n = int(dur * SAMPLE_RATE)
    rng = random.Random(seed)
    out: list[float] = []
    phase = 0.0
    lp = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        freq = f_end + (f_start - f_end) * math.exp(-t * 36.0)
        phase += 2 * math.pi * freq / SAMPLE_RATE
        body = math.sin(phase) * math.exp(-t * 17.0)
        noise = rng.random() * 2 - 1
        lp = lp * 0.72 + noise * 0.28
        click = lp * punch * math.exp(-t * 65.0)
        out.append((body + click) * _env(t, dur, 0.002, 0.04))
    return out


def _synth_chime(
    notes_hz: list[tuple[float, float, float]],
    dur: float,
    warmth: float = 0.25,
) -> list[float]:
    """Soft broadcast chime (for VAR, substitution board, UI cues)."""
    n = int(dur * SAMPLE_RATE)
    out = [0.0] * n
    for start, length, freq in notes_hz:
        i0 = int(start * SAMPLE_RATE)
        i1 = min(n, int((start + length) * SAMPLE_RATE))
        for i in range(i0, i1):
            lt = (i - i0) / SAMPLE_RATE
            e = _env(lt, length, 0.008, length * 0.65) * math.exp(-lt * 4.5)
            val = (
                math.sin(2 * math.pi * freq * lt)
                + warmth * math.sin(2 * math.pi * freq * 2.0 * lt) * math.exp(-lt * 8.0)
            )
            out[i] += val * e
    return out


def _synth_crowd_boo(dur: float = 1.6) -> list[float]:
    """Warm, low-mid filtered stadium booing murmur (non-harsh)."""
    n = int(dur * SAMPLE_RATE)
    rng = random.Random(101)
    out: list[float] = []
    b0 = b1 = b2 = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        w = rng.random() * 2 - 1
        # Simple 3-pole low-pass for brown/pink-ish vocal crowd body
        b0 = 0.94 * b0 + 0.06 * w
        b1 = 0.91 * b1 + 0.09 * b0
        b2 = 0.85 * b2 + 0.15 * b1
        formant = (
            0.30 * math.sin(2 * math.pi * (165 + 6 * math.sin(4 * t)) * t)
            + 0.22 * math.sin(2 * math.pi * (245 + 9 * math.sin(3.1 * t)) * t)
            + 0.14 * math.sin(2 * math.pi * (330 + 7 * math.sin(2.5 * t)) * t)
        )
        swell = _env(t, dur, 0.18, 0.45) * (0.75 + 0.25 * math.sin(math.pi * t / dur))
        out.append((b2 * 3.8 + formant * (0.35 + abs(b1) * 2.5)) * swell)
    return out


def _synth_crowd_roar(dur: float = 2.4) -> list[float]:
    """Layered stadium goal euphoria roar with warm air and swell."""
    n = int(dur * SAMPLE_RATE)
    rng = random.Random(202)
    out: list[float] = []
    lp1 = lp2 = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        w = rng.random() * 2 - 1
        lp1 = 0.82 * lp1 + 0.18 * w
        lp2 = 0.92 * lp2 + 0.08 * lp1
        cheering = lp1 * 0.55 + lp2 * 1.4
        harmonic = 0.12 * math.sin(2 * math.pi * (420 + 25 * math.sin(3 * t)) * t) * abs(lp2)
        env = _env(t, dur, 0.12, 0.75) * (0.65 + 0.35 * math.exp(-((t - 0.5) ** 2) / 0.45))
        out.append((cheering + harmonic) * env)
    return out


def _synth_card_snap(dramatic: bool = False) -> list[float]:
    """Crisp card flick + subtle authoritative tone."""
    dur = 0.55 if dramatic else 0.38
    n = int(dur * SAMPLE_RATE)
    rng = random.Random(303 if dramatic else 304)
    out: list[float] = []
    hp_prev = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        w = rng.random() * 2 - 1
        hp = w - 0.7 * hp_prev
        hp_prev = w
        snap = hp * math.exp(-t * 48.0) * 0.65
        if dramatic:
            tone = (
                0.45 * math.sin(2 * math.pi * 220 * t)
                + 0.30 * math.sin(2 * math.pi * 165 * t)
            ) * _env(t - 0.04, dur - 0.04, 0.015, 0.25)
        else:
            tone = (
                0.35 * math.sin(2 * math.pi * 520 * t)
                * _env(t - 0.03, 0.22, 0.01, 0.12)
            )
        out.append(snap + tone)
    return out


def _synth_bicycle_kick() -> list[float]:
    """Air swoosh followed by powerful acrobatic strike."""
    dur = 0.46
    n = int(dur * SAMPLE_RATE)
    rng = random.Random(404)
    out = [0.0] * n
    lp = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        w = rng.random() * 2 - 1
        lp = 0.78 * lp + 0.22 * w
        if t < 0.22:
            out[i] += lp * 0.45 * math.sin(math.pi * t / 0.22)
    kick = _synth_kick(dur=0.26, f_start=180.0, f_end=48.0, punch=0.5, seed=405)
    offset = int(0.17 * SAMPLE_RATE)
    for i, sample in enumerate(kick):
        if offset + i < n:
            out[offset + i] += sample
    return out


def _normalize_ffmpeg(
    src: Path,
    dst: Path,
    target_lufs: float,
    target_tp: float,
    as_ogg: bool = False,
    soft_high_cut_hz: int | None = None,
) -> None:
    """Normalize audio loudness using ffmpeg loudnorm + gentle limiter."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    filters: list[str] = []
    if soft_high_cut_hz is not None:
        filters.append(f"lowpass=f={soft_high_cut_hz}")
    filters.append(f"loudnorm=I={target_lufs}:TP={target_tp}:LRA=11")
    filters.append(f"alimiter=limit={10 ** (target_tp / 20.0):.4f}:attack=5:release=50")
    af = ",".join(filters)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", af]
    if as_ogg:
        cmd.extend(["-ar", "44100", "-c:a", "libvorbis", "-q:a", "5", str(dst)])
    else:
        cmd.extend(["-ar", "44100", "-c:a", "pcm_s16le", str(dst)])
    subprocess.run(cmd, check=True)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    portable_src = Path(
        r"C:\Users\angel_hmv3h04\Downloads\Compressed\audio futbol veta 14\AudioFutbol_Portable"
    )
    if not portable_src.is_dir():
        raise FileNotFoundError(f"Original portable folder not found: {portable_src}")

    sonidos_dst = repo_root / "sonidos"
    musica_dst = repo_root / "musica"
    temas_dst = repo_root / "temas"

    for folder in (sonidos_dst, musica_dst, temas_dst):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True, exist_ok=True)

    # 1. Import and normalize original SFX (skipping unused notas/)
    orig_sonidos = portable_src / "sonidos"
    for wav_file in sorted(orig_sonidos.rglob("*.wav")):
        rel = wav_file.relative_to(orig_sonidos)
        if rel.parts[0].lower() == "notas":
            continue
        dst = sonidos_dst / rel
        name_l = wav_file.stem.lower()
        if "ambiente" in name_l:
            _normalize_ffmpeg(wav_file, dst, target_lufs=-22.5, target_tp=-4.5, soft_high_cut_hz=5500)
        elif "silbato" in name_l:
            # Slightly softer ceiling on sharp whistles so they never pierce ears
            _normalize_ffmpeg(wav_file, dst, target_lufs=-18.5, target_tp=-4.0, soft_high_cut_hz=6500)
        elif "menu" in name_l:
            _normalize_ffmpeg(wav_file, dst, target_lufs=-19.0, target_tp=-4.0)
        else:
            _normalize_ffmpeg(wav_file, dst, target_lufs=-16.5, target_tp=-3.0)

    # 2. Import, loudness-normalize, and compress music & themes to .ogg
    for wav_file in sorted((portable_src / "musica").glob("*.wav")):
        dst = musica_dst / f"{wav_file.stem}.ogg"
        _normalize_ffmpeg(wav_file, dst, target_lufs=-19.5, target_tp=-2.5, as_ogg=True)

    for wav_file in sorted((portable_src / "temas").glob("*.wav")):
        dst = temas_dst / f"{wav_file.stem}.ogg"
        _normalize_ffmpeg(wav_file, dst, target_lufs=-19.0, target_tp=-2.5, as_ogg=True)

    readme_temas = portable_src / "temas" / "LEEME.txt"
    if readme_temas.is_file():
        shutil.copy2(readme_temas, temas_dst / "LEEME.txt")

    # 3. Synthesize and normalize rich additional football SFX
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        generated: list[tuple[str, list[float], float, float, int | None]] = [
            # Distinct referee whistles
            ("silbatos/silbato_falta.wav", _synth_whistle([(0.0, 0.42, 1.03)], base_freq=2550.0), -18.5, -4.0, 6200),
            ("silbatos/silbato_penal.wav", _synth_whistle([(0.0, 0.22, 1.05), (0.28, 0.45, 1.08)], base_freq=2680.0), -18.0, -4.0, 6200),
            ("silbatos/silbato_descanso.wav", _synth_whistle([(0.0, 0.24, 1.0), (0.34, 0.52, 0.98)], base_freq=2580.0), -18.5, -4.0, 6200),
            ("silbatos/silbato_final_triple.wav", _synth_whistle([(0.0, 0.20, 1.0), (0.28, 0.20, 1.0), (0.56, 0.58, 0.97)], base_freq=2580.0), -18.5, -4.0, 6200),
            # Ball variations
            ("balon/balon_pase.wav", _synth_kick(0.18, 130.0, 48.0, punch=0.28, seed=11), -17.0, -3.5, None),
            ("balon/balon_corner.wav", _synth_kick(0.32, 175.0, 38.0, punch=0.45, seed=12), -16.5, -3.0, None),
            ("balon/balon_cabezazo.wav", _synth_kick(0.16, 115.0, 52.0, punch=0.18, seed=13), -17.0, -3.5, 3200),
            ("balon/balon_barrera.wav", _synth_kick(0.24, 95.0, 36.0, punch=0.30, seed=14), -16.5, -3.0, 2200),
            ("balon/balon_chilena.wav", _synth_bicycle_kick(), -16.5, -3.0, None),
            # Cards
            ("efectos/tarjeta_amarilla.wav", _synth_card_snap(dramatic=False), -18.0, -4.0, None),
            ("efectos/tarjeta_roja.wav", _synth_card_snap(dramatic=True), -17.0, -3.5, None),
            # Broadcast VAR & 4th official electronic board chimes
            ("efectos/var_aviso.wav", _synth_chime([(0.0, 0.22, 660.0), (0.20, 0.34, 880.0)], 0.60), -18.5, -4.0, None),
            ("efectos/var_confirmado.wav", _synth_chime([(0.0, 0.18, 523.25), (0.16, 0.32, 783.99)], 0.55), -18.5, -4.0, None),
            ("efectos/var_anulado.wav", _synth_chime([(0.0, 0.20, 587.33), (0.18, 0.34, 392.00)], 0.58), -18.5, -4.0, None),
            ("efectos/cambio_tablero.wav", _synth_chime([(0.0, 0.14, 784.0), (0.15, 0.20, 1046.5)], 0.42, warmth=0.12), -19.0, -4.5, None),
            # Crowd variations
            ("tribuna/tribuna_abucheo.wav", _synth_crowd_boo(1.65), -17.5, -3.5, 4200),
            ("tribuna/tribuna_gol_euforia.wav", _synth_crowd_roar(2.3), -16.5, -3.0, 6800),
        ]
        for rel_str, samples, lufs, tp, lp in generated:
            raw_path = tmp / Path(rel_str).name
            _write_wav(raw_path, samples)
            _normalize_ffmpeg(raw_path, sonidos_dst / rel_str, lufs, tp, soft_high_cut_hz=lp)

    # 4. Write rich sonidos.txt mapping all events (original + new)
    sonidos_txt = """# Asignación de sonidos normalizados para AudioFútbol.
# Formato: evento = nombre_de_archivo (sin extensión).
# Si un evento tiene varias líneas, se elige una variación al azar.

# Silbatos
silbato = silbato_inicio
silbato = silbato_final
silbato_descanso = silbato_descanso
silbato_final = silbato_final_triple
falta = silbato_falta
penal = silbato_penal

# Balón y jugadas
balon = balon_patada
balon = balon_tiro
balon = balon_pase
corner = balon_corner
cabezazo = balon_cabezazo
barrera = balon_barrera
barrida = balon_barrida
regate = balon_regate
chilena = balon_chilena
choque = efecto_choque

# Portero, poste y ocasiones
parada = tribuna_atajada
poste = tribuna_poste
casi = tribuna_gol_fallado

# Tribuna y goles
gol = tribuna_gol
gol = tribuna_gol_euforia
ovacion = tribuna_aplausos_corto
ovacion = tribuna_aplausos
abucheo = tribuna_abucheo
abucheo = tribuna_gol_fallado
tribuna = tribuna_ambiente

# Tarjetas, VAR y cambios
tarjeta = tarjeta_amarilla
tarjeta_roja = tarjeta_roja
var = var_aviso
var_confirmado = var_confirmado
var_anulado = var_anulado
cambio = cambio_tablero

# Menú y sorteo
sorteo = menu_seleccionar
mover = menu_mover
aceptar = menu_aceptar
volver = menu_error
"""
    (repo_root / "sonidos.txt").write_text(sonidos_txt, encoding="utf-8")
    print("Audio import, synthesis, and normalization complete!")


if __name__ == "__main__":
    main()
