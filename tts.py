#!/usr/bin/env python3

"""Simple example to generate audio with preset voice using async/await"""

import asyncio

import edge_tts
from gtts import gTTS
from vietnormalizer import VietnameseNormalizer

normalizer = VietnameseNormalizer()


# from gtts import gTTS
# from vietnormalizer import VietnameseNormalizer

# normalizer = VietnameseNormalizer()
# TEXT = normalizer.normalize("bạn vừa nhận 40039993 đồng")

# tts = gTTS(TEXT, lang="vi")
# tts.save("output.mp3")


async def process(content, fileName):
    # tts = gTTS(content, lang="vi")
    # tts.save("output.mp3")
    TEXT = normalizer.normalize(content)
    VOICE = "vi-VN-HoaiMyNeural"
    communicate = edge_tts.Communicate(TEXT, VOICE,rate='-20%')
    return await communicate.save(fileName)
