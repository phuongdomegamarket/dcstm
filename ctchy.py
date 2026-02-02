import asyncio
import json
import locale
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

from datetime import datetime

import aiohttp
import discord
import requests
import streamlit as st
from discord.ext import commands, tasks

import tts

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

if "log_queue" not in st.session_state:
    st.session_state["log_queue"] = queue.Queue()

if "logs" not in st.session_state:
    st.session_state["logs"] = []

if "task_running" not in st.session_state:
    st.session_state["task_running"] = False
bot = commands.Bot(command_prefix="!", intents=intents)

# Biến global để lưu voice client hiện tại (nếu bot join 1 channel duy nhất)
current_voice_client = None
CHANNELS = None
HISTORY_CHANNEL = os.getenv("HISTORY_CHANNEL")
GUILD = None
WATCH_ON_CHANNEL = os.getenv("WATCH_ON_CHANNEL")

tts_keys = set()
ttsKeysChannel = None
processed_threads = set()


def myStyle(log_queue):
    @bot.event
    async def on_ready():
        global CHANNELS, GUILD, current_voice_client, ttsKeysChannel, tts_keys
        print(f"Bot ready: {bot.user}")
        for guild in bot.guilds:
            if guild.name.lower() == "phượng đỏ mega":
                GUILD = guild
                CHANNELS = guild.channels
                for channel in CHANNELS:
                    if "voice transactions" in channel.name.lower():
                        stopped = False
                        while not stopped:
                            try:
                                current_voice_client = await channel.connect()
                                print("Connect thành công!")
                                stopped = True

                            except asyncio.TimeoutError:
                                print("Timeout → thử lại ngay...")
                                await asyncio.sleep(
                                    10
                                )  # delay nhỏ để không spam quá nhanh

                            except Exception as e:
                                print(f"Lỗi nghiêm trọng: {e}")
                                await asyncio.sleep(10)  # delay dài hơn nếu lỗi khác
                    elif "fpt-voice" in channel.name.lower():
                        ttsKeysChannel = channel
                        async for msg in channel.history():
                            tts_keys.add(msg.content)
        if not periodic_api_check.is_running():
            periodic_api_check.start(guild)

    @bot.event
    async def on_message(message):
        global tts_keys
        if ttsKeysChannel and message.channel.id == ttsKeysChannel.id:
            tts_keys.add(message.content)

    @bot.command(name="join")
    async def join(ctx):
        global current_voice_client
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            current_voice_client = await channel.connect()
            await ctx.send(f"Bot joined voice: {channel.name}")
        else:
            await ctx.send("Bạn phải ở voice channel trước!")

    @bot.command(name="leave")
    async def leave(ctx):
        global current_voice_client
        if current_voice_client:
            await current_voice_client.disconnect()
            current_voice_client = None
            await ctx.send("Bot left voice channel.")
        else:
            await ctx.send("Bot chưa join voice.")

    # Task định kỳ: Gửi request API mỗi 30 giây
    @tasks.loop(seconds=1)  # Chỉnh thời gian ở đây (seconds, minutes, hours)
    async def periodic_api_check(guild):
        try:
            global current_voice_client, CHANNELS, processed_threads
            if current_voice_client is None or not current_voice_client.is_connected():
                print("Bot chưa join voice channel → skip play voice")
                return  # Bỏ qua nếu chưa join voice
            if CHANNELS:
                lastThreads = None
                historyChannel = None
                for channel in CHANNELS:
                    if channel.name.lower() == WATCH_ON_CHANNEL:
                        pattern = re.compile(
                            r"""
                            ^\s*
                            (?P<sign>[+-])\s*                       # + hoặc -
                            (?P<amount>[\d,]+(?:\.\d+)?)\s*         # số tiền, hỗ trợ dấu phẩy + thập phân
                            \s*(?P<currency>[A-Za-z]{2,10})?\s*     # currency (optional, 2-10 chữ cái)
                            /\s*
                            (?P<ts>\d{13})\s*                       # 13 chữ số timestamp
                            (?:/.*)?\s*$                            # mọi thứ sau dấu / đầu tiên đều chấp nhận (hoặc không có)
                            """,
                            re.VERBOSE | re.IGNORECASE,
                        )
                        entries = []
                        for line in channel.threads:
                            m = pattern.match(line.name)
                            if not m:
                                continue  # skip malformed lines
                            sign = m.group("sign")
                            # Remove commas from the numeric part, keep it as int for sorting
                            amount = int(m.group("amount").replace(",", ""))
                            currency = m.group("currency") or ""
                            ts = int(m.group("ts"))  # Unix epoch in milliseconds
                            entries.append(
                                {
                                    "original": line.name.strip(),
                                    "timestamp": ts,
                                    "sign": sign,
                                    "amount": amount,
                                    "currency": currency,
                                }
                            )
                        entries.sort(key=lambda e: e["timestamp"])
                        lastThreads = entries[-20:]
                    elif channel.name.lower() == HISTORY_CHANNEL:
                        historyChannel = channel
                if historyChannel:
                    historyChannel = await guild.fetch_channel(historyChannel.id)
                    print(historyChannel)
                if lastThreads and historyChannel:
                    for threadMeta in lastThreads:
                        if (
                            str(threadMeta["original"])
                            not in list(
                                map(lambda item: item.name, historyChannel.threads)
                            )
                            and threadMeta["original"] not in processed_threads
                        ):
                            processed_threads.add(threadMeta["original"])
                            audioUrl = str(
                                tts.process(f"{threadMeta['amount']} đồng", tts_keys)
                            )
                            # Polling: Kiểm tra URL tồn tại (HEAD request nhẹ, không download full)
                            max_attempts = 12  # Max chờ ~60 giây (5s * 12)
                            async with aiohttp.ClientSession() as session:
                                async with session.get(
                                    audioUrl, timeout=10
                                ) as head_resp:
                                    if head_resp.status < 400:
                                        print(f"TTS ready")
                                    else:
                                        for attempt in range(max_attempts):
                                            async with session.get(
                                                audioUrl, timeout=10
                                            ) as head_resp:
                                                if head_resp.status == 200:
                                                    print(
                                                        f"TTS ready sau {attempt * 1} giây!"
                                                    )
                                                    break
                                                else:
                                                    print(
                                                        f"URL chưa sẵn sàng (status {head_resp.status}), chờ 5s..."
                                                    )
                                                    await asyncio.sleep(1)
                                        else:
                                            print(
                                                "Timeout: TTS URL không sẵn sàng sau 60s"
                                            )
                                            return
                            # Logic xử lý response → quyết định có play voice không
                            # Ví dụ: Nếu response có key "alert" hoặc "new_message"

                            if (
                                audioUrl and not current_voice_client.is_playing()
                            ):  # Thay logic của bạn

                                def play_next_audio(error, meta):
                                    if error:
                                        print(f"File đầu tiên lỗi: {error}")

                                    # Phát file thứ hai (URL TTS)
                                    if not current_voice_client.is_playing():
                                        try:
                                            source2 = discord.FFmpegPCMAudio(
                                                str(audioUrl),  # URL TTS
                                                **ffmpeg_options,
                                            )
                                            current_voice_client.play(
                                                source2,
                                                after=lambda e,
                                                meta1=meta: bot.loop.create_task(
                                                    onComplete(e, meta1)
                                                ),  # callback khi file 2 xong
                                            )
                                            print("Đang phát file thứ hai (TTS URL)")
                                        except Exception as ex:
                                            print(f"Lỗi phát file thứ hai: {ex}")
                                    else:
                                        print("Đang phát rồi, không queue file thứ hai")

                                ffmpeg_options = {"options": "-vn"}  # Chỉ audio
                                if threadMeta["sign"] == "+":
                                    source = discord.FFmpegPCMAudio(
                                        str("./daNhan.mp3"),
                                        **ffmpeg_options,
                                    )  # File audio bạn chuẩn bị
                                else:
                                    source = discord.FFmpegPCMAudio(
                                        str("./daChuyen.mp3"),
                                        **ffmpeg_options,
                                    )  # File audio bạn chuẩn bị

                                # Nếu muốn dùng TTS từ response text (cần thêm lib như gTTS hoặc ElevenLabs)
                                # from gtts import gTTS
                                # tts = gTTS(alert_text, lang='vi')
                                # tts.save("temp.mp3")
                                # source = discord.FFmpegPCMAudio("temp.mp3", **ffmpeg_options)
                                async def onComplete(e, meta):
                                    if e:
                                        print(f"Voice play error: {e}")
                                    else:
                                        print("Voice played OK")
                                        await historyChannel.create_thread(
                                            name=meta["original"], content="done"
                                        )

                                if not current_voice_client.is_playing():
                                    current_voice_client.play(
                                        source,
                                        after=lambda e,
                                        meta=threadMeta: play_next_audio(e, meta),
                                    )
                                    print("Đang play voice từ API response!")
                                else:
                                    print("Đang play rồi → skip")
        except Exception as e:
            print(e)
            pass

    # Optional: Chờ bot ready trước khi start loop (tránh lỗi nếu dùng bot.wait_until_ready())
    @periodic_api_check.before_loop
    async def before_check():
        await bot.wait_until_ready()
        print("Periodic API check started!")

    @bot.command()
    async def stop(ctx):
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            await ctx.send("Stopped and disconnected")

    bot.run(os.environ.get("DC_TK"))


thread = None


@st.cache_resource
def initialize_heavy_stuff():
    global thread
    # Đây là phần chỉ chạy ĐÚNG 1 LẦN khi server khởi động (hoặc khi cache miss)
    with st.spinner("running your scripts..."):
        thread = threading.Thread(target=myStyle, args=(st.session_state.log_queue,))
        thread.start()
        print(
            "Heavy initialization running..."
        )  # bạn sẽ thấy log này chỉ 1 lần trong console/cloud log
        return {
            "model": "loaded_successfully",
            "timestamp": time.time(),
            "db_status": "connected",
        }


# Trong phần chính của app
st.title("my style")

# Dòng này đảm bảo: chạy 1 lần duy nhất, mọi user đều dùng chung kết quả
result = initialize_heavy_stuff()

st.success("The system is ready!")
st.write("Result:")
st.json(result)
stopped = False
while not stopped:
    try:
        stopped = True
        with st.status("Processing...", expanded=True) as status:
            placeholder = st.empty()
            logs = []
            while thread.is_alive() or not st.session_state.log_queue.empty():
                try:
                    level, message = st.session_state.log_queue.get_nowait()
                    logs.append((level, message))

                    with placeholder.container():
                        for lvl, msg in logs:
                            if lvl == "info":
                                st.write(msg)
                            elif lvl == "success":
                                st.success(msg)
                            elif lvl == "error":
                                st.error(msg)

                    time.sleep(0.2)
                except queue.Empty:
                    time.sleep(0.3)

            status.update(label="Hoàn thành!", state="complete", expanded=False)

    except Exception as e:
        st.session_state.log_queue.put(("error", f"Error occurred: {str(e)}"))
