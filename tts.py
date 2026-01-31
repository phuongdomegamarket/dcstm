import json
import os

import requests
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()
TTS_KEYS = json.loads(str(os.getenv("TTS_KEY")))
TTS_SERVER = os.getenv("TTS_SERVER")


def process(content: str, extra_tts_keys=None):
    ttsKeys = TTS_KEYS
    if extra_tts_keys:
        ttsKeys.extend(extra_tts_keys)
    if TTS_SERVER:
        for ttsKey in TTS_KEYS:
            url = TTS_SERVER

            payload = content
            headers = {
                "api-key": ttsKey,
                "speed": "",
                "voice": "banmai",
            }

            response = requests.request(
                "POST", url, data=payload.encode("utf-8"), headers=headers
            )
            if response:
                jsData = response.json()
                if jsData["error"] == 0:
                    return (response.json())["async"]
    return None
