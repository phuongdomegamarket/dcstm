#!/usr/bin/env python3

"""Generate audio cho bot TTS.

Chuỗi ưu tiên:
1. edge-tts   — giọng "vi-VN-HoaiMyNeural", chất lượng tốt nhất (online, Microsoft).
2. piper      — chạy offline ngay trong container, không phụ thuộc IP/đồng hồ
                cloud; giọng vi_VN-vais1000-medium (VAIS1000 dataset).
3. gTTS       — phương án cuối cùng: online (Google), giọng kém tự nhiên hơn.

edge-tts thường fail trên cloud vì Microsoft trả 403 (block/throttle IP cloud
hoặc lệch giờ container làm hỏng token DRM) — lúc đó piper (offline) tiếp tay
trước khi phải đi tới gTTS.
"""

import asyncio
import os
import wave
from pathlib import Path

import edge_tts
from gtts import gTTS
from vietnormalizer import VietnameseNormalizer

normalizer = VietnameseNormalizer()

EDGE_VOICE = "vi-VN-HoaiMyNeural"
EDGE_RETRIES = 2  # thử lại khi 403 thoáng qua (throttle/block tạm thời)

# Piper: model tải về sẵn trong repo tại voices/ (không tải lúc runtime
# để tránh phụ thuộc mạng thêm một tầng nữa).
_PIPER_MODEL = Path(__file__).parent / "voices" / "vi_VN-vais1000-medium.onnx"
_piper_voice = None
_piper_load_failed = False


def _get_piper_voice():
    """Load model piper 1 lần (lazy) — trả về None nếu không dùng được."""
    global _piper_voice, _piper_load_failed
    if _piper_voice is not None or _piper_load_failed:
        return _piper_voice
    if not _PIPER_MODEL.exists():
        _piper_load_failed = True
        return None
    try:
        from piper import PiperVoice

        _piper_voice = PiperVoice.load(str(_PIPER_MODEL))
    except Exception as e:
        print(f"piper load thất bại ({e}) → bỏ qua piper, chỉ còn edge/gTTS", flush=True)
        _piper_load_failed = True
    return _piper_voice


async def process(content, fileName):
    TEXT = normalizer.normalize(content)

    # 1) edge-tts — giọng chính
    last_error = None
    for attempt in range(EDGE_RETRIES):
        try:
            communicate = edge_tts.Communicate(TEXT, EDGE_VOICE)
            return await communicate.save(fileName)
        except Exception as e:
            last_error = e
            await asyncio.sleep(1)

    # 2) piper — offline, không cần mạng
    print(f"edge_tts thất bại ({last_error}) → thử piper (offline)", flush=True)
    voice = _get_piper_voice()
    if voice is not None:
        try:
            # synthesize_wav chỉ nhận file wav — piper sinh PCM 22050Hz mono 16-bit
            await asyncio.to_thread(_piper_synth, voice, TEXT, fileName)
            return
        except Exception as e:
            print(f"piper thất bại ({e}) → fallback sang gTTS", flush=True)
    else:
        print("piper không khả dụng → fallback sang gTTS", flush=True)

    # 3) gTTS — phương án cuối cùng (online, chặn → chạy trong thread riêng)
    await asyncio.to_thread(lambda: gTTS(TEXT, lang="vi").save(fileName))


def _piper_synth(voice, text, fileName):
    """Chạy trong thread: piper là CPU-bound, viết thẳng ra fileName (.wav)."""
    with wave.open(fileName, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)


# Ghi chú format cho người gọi: ctchy.py dùng FFmpegPCMAudio(file_path) —
# FFmpeg tự nhận diện .wav và .mp3 nên không cần đổi gì phía phát.
