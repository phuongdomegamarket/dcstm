#!/usr/bin/env python3

"""Generate audio: edge-tts là chính, gTTS là dự phòng khi Microsoft trả 403."""

import asyncio

import edge_tts
from gtts import gTTS
from vietnormalizer import VietnameseNormalizer

normalizer = VietnameseNormalizer()

EDGE_VOICE = "vi-VN-NamMinhNeural"
EDGE_RETRIES = 2  # thử lại khi 403 thoáng qua (throttle/block tạm thời)


async def process(content, fileName):
    TEXT = normalizer.normalize(content)
    last_error = None
    for attempt in range(EDGE_RETRIES):
        try:
            communicate = edge_tts.Communicate(TEXT, EDGE_VOICE)
            return await communicate.save(fileName)
        except Exception as e:
            last_error = e
            await asyncio.sleep(1)
    # edge-tts hỏng (thường là 403 từ Microsoft — IP cloud bị block/throttle,
    # hoặc lệch giờ container làm hỏng token DRM) → phát mp3 bằng gTTS.
    # gTTS là lệnh gọi mạng chặn → chạy trong thread riêng để không treo event loop.
    print(f"edge_tts thất bại ({last_error}) → fallback sang gTTS", flush=True)
    await asyncio.to_thread(lambda: gTTS(TEXT, lang="vi").save(fileName))
