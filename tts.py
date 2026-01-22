import json
import os

import requests
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()
TTS_KEYS = json.loads(str(os.getenv("TTS_KEY")))


def process(content: str):
    for ttsKey in TTS_KEYS:
        url = "https://api.fpt.ai/hmi/tts/v5"

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
