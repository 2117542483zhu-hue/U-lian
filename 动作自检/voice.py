"""给动作自检视频加语音播报。

用微软在线语音合成（edge-tts，中文女声 Xiaoxiao）把播报文字转成 WAV，
按时间轴拼成一条完整音轨，再用 ffmpeg 混进骨架视频。相比原来的 Windows SAPI，
edge-tts 跨平台（Linux 服务器也能用）、免费；需要联网，合成失败会自动退回
静音视频，不影响主流程。

播报形式：视频全部解析完成后，在结尾一次性播放一句「整体汇总播报」
（完成次数 + 整体评价 + 逐次纠错 + 体态联动提示），替代原来的逐次角度播报。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Sequence, Tuple

import edge_tts

SAMPLE_RATE = 16000
VOICE = "zh-CN-XiaoxiaoNeural"


async def _edge_tts_save(text: str, mp3_path: Path) -> None:
    """用 edge-tts 把文字合成 mp3。"""
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(str(mp3_path))


def _speak_to_wav(text: str, wav_path: Path) -> bool:
    """用 edge-tts 合成 WAV（16kHz 单声道），返回是否成功。

    先合成 mp3，再用 ffmpeg 转成 16kHz 单声道 WAV，供后续铺轨使用；
    合成或转码失败返回 False（退回静音视频），不影响主流程。
    """
    mp3_path = wav_path.with_suffix(".mp3")
    try:
        asyncio.run(_edge_tts_save(text, mp3_path))
    except Exception:  # noqa: BLE001
        return False
    if not mp3_path.exists() or mp3_path.stat().st_size == 0:
        return False

    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return False
    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(mp3_path),
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        str(wav_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60)
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            mp3_path.unlink()
        except OSError:
            pass
    return wav_path.exists() and wav_path.stat().st_size > 44


def _wav_duration(wav_path: Path) -> float:
    try:
        with wave.open(str(wav_path), "rb") as wav_file:
            return wav_file.getnframes() / wav_file.getframerate()
    except Exception:  # noqa: BLE001
        return 0.0


def _build_audio_track(
    segments: Sequence[Tuple[float, Path]],
    total_duration: float,
    out_wav: Path,
) -> bool:
    """把若干段 WAV 按各自的时间点铺到一条静音底轨上，得到整段音轨。"""
    total_frames = max(1, int(total_duration * SAMPLE_RATE))
    pcm = bytearray(total_frames * 2)
    for start_sec, seg_path in segments:
        try:
            with wave.open(str(seg_path), "rb") as wav_file:
                if (
                    wav_file.getframerate() != SAMPLE_RATE
                    or wav_file.getsampwidth() != 2
                    or wav_file.getnchannels() != 1
                ):
                    continue
                data = wav_file.readframes(wav_file.getnframes())
        except Exception:  # noqa: BLE001
            continue
        start = int(start_sec * SAMPLE_RATE)
        if start >= total_frames:
            continue
        nbytes = min(len(data), len(pcm) - start * 2)
        pcm[start * 2 : start * 2 + nbytes] = data[:nbytes]

    with wave.open(str(out_wav), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(bytes(pcm))
    return out_wav.exists() and out_wav.stat().st_size > 44


def _mux_audio(video_path: Path, audio_wav: Path, out_video: Path) -> bool:
    """用 ffmpeg 把音轨混进视频（视频流原样复制，音频编码成 AAC）。"""
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return False

    tmp = out_video.with_name(out_video.stem + "_withaudio.mp4")
    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(audio_wav),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=300)
    except Exception:  # noqa: BLE001
        return False
    if tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(out_video)
        return True
    return False


def add_narration(video_path: Path, summary_text: str, video_duration: float) -> bool:
    """给分析视频加一句「整体汇总播报」。

    把整句汇总播报合成 WAV，铺到视频结尾播放。成功返回 True，
    失败返回 False（视频保持静音）。
    """
    if not video_path.exists() or not summary_text:
        return False

    work_dir = Path(tempfile.mkdtemp(prefix="pose_voice_"))
    try:
        summary_wav = work_dir / "summary.wav"
        if not _speak_to_wav(summary_text, summary_wav):
            return False
        summary_dur = _wav_duration(summary_wav)
        # 播报放在视频末尾，留 0.4 秒缓冲
        start = max(0.0, video_duration - summary_dur - 0.4)

        full_wav = work_dir / "full.wav"
        if not _build_audio_track([(start, summary_wav)], video_duration, full_wav):
            return False
        return _mux_audio(video_path, full_wav, video_path)
    except Exception:  # noqa: BLE001
        return False
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
