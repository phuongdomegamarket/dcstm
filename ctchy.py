import asyncio
import json
import logging
import os
import queue
import re
import shutil
import tempfile
import threading
import time

from datetime import datetime, timedelta
import uuid
import edge_tts
from vietnormalizer import VietnameseNormalizer

normalizer = VietnameseNormalizer()
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

from datetime import datetime

import aiohttp
import discord
import streamlit as st
from discord.ext import commands, tasks
from dotenv import load_dotenv
from vietnormalizer import VietnameseNormalizer

import tts

load_dotenv()

# Cấu hình logging ĐÚNG 1 LẦN cho toàn process. discord.py (bot.run) tự add
# handler mỗi lần chạy nếu ta không tắt (log_handler=None) → sau vài lần
# restart bởi watchdog, log bị in lặp nhiều lần cùng lúc.
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

normalizer = VietnameseNormalizer()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# ======== Trạng thái toàn cục — được reset mỗi lần bot khởi động lại ========
bot = None
current_voice_client = None
CHANNELS = None
GUILD = None
ttsKeysChannel = None
# Lock/queue gắn với event loop cũ của bot → phải tạo lại sau mỗi lần restart
processing_lock = asyncio.Lock()
voice_connect_lock = asyncio.Lock()
tts_queue = asyncio.Queue()

# Giữ nguyên qua các lần restart để không phát lại giao dịch cũ
tts_keys = set()
processed_threads = set()

HISTORY_CHANNEL = os.getenv("HISTORY_CHANNEL")
WATCH_ON_CHANNEL = os.getenv("WATCH_ON_CHANNEL")

INSTANCE_ID = str(uuid.uuid4())  # mỗi process có 1 ID riêng biệt
LOCK_TTL_SECONDS = 15  # tối đa 1 lần phát không nên dài hơn số này
RESTART_DELAY_SECONDS = 10  # chờ bao lâu trước khi khởi động lại bot sau khi lỗi
MAX_RESTART_DELAY_SECONDS = 300  # backoff tối đa khi bot crash liên tục
PLAY_TIMEOUT_SECONDS = 120  # chờ phát 1 file tối đa bao lâu trước khi bỏ qua


async def acquire_playback_lock(lock_channel):
    """
    Trả về True nếu instance này giành được quyền phát audio.
    Trả về False nếu instance khác đang giữ khóa (chưa hết hạn).
    """
    now = time.time()

    # Đọc message lock gần nhất
    last_lock_thread=None
    if(len(lock_channel.threads)>0):
        last_lock_thread = lock_channel.threads[0]

    if last_lock_thread:
        try:
            oldestMsg=[msg async for msg in last_lock_thread.history(limit=1,oldest_first=True)][0]
            data = json.loads(oldestMsg.content)
            holder_id = data.get("instance_id")
            expires_at = data.get("expires_at", 0)

            # Nếu khóa còn hạn và không phải của mình → bỏ qua
            if expires_at > now and holder_id != INSTANCE_ID:
                return False
        except (json.JSONDecodeError, TypeError):
            pass  # message hỏng, coi như không có khóa

    # Không ai giữ khóa (hoặc khóa đã hết hạn) → mình chiếm khóa
    lock_payload = json.dumps({
        "instance_id": INSTANCE_ID,
        "expires_at": now + LOCK_TTL_SECONDS,
    })
    tags=lock_channel.available_tags
    expiredTag=None
    if(len(tags)>0):
        for tag in tags:
            if(tag.name.lower()=='expired'):
                expiredTag=tag
    await lock_channel.create_thread(name=INSTANCE_ID,content=lock_payload,applied_tags=[expiredTag])
    return True


async def release_playback_lock(lock_channel):
    last_lock_thread = lock_channel.threads[0]
    oldestMsg=[msg async for msg in last_lock_thread.history(limit=1,oldest_first=True)][0]
    """Giải phóng khóa sớm ngay sau khi phát xong, không cần đợi hết TTL."""
    expired_payload = json.dumps({
        "instance_id": INSTANCE_ID,
        "expires_at": 0,  # đánh dấu hết hạn ngay lập tức
    })


    await oldestMsg.edit(content=expired_payload)
    tags=lock_channel.available_tags
    expiredTag=None
    if(len(tags)>0):
        for tag in tags:
            if(tag.name.lower()=='expired'):
                expiredTag=tag
    await last_lock_thread.edit(applied_tags=[expiredTag],locked=True)
async def tts_worker(lock_channel):
    while True:
        text, thread_original, history_channel = await tts_queue.get()
        try:
            # Xin khóa trước khi phát — nếu bot khác đang phát thì chờ & thử lại
            while not await acquire_playback_lock(lock_channel):
                log("Instance khác đang phát audio → chờ...")
                await asyncio.sleep(2)

            if current_voice_client is None or not current_voice_client.is_connected():
                await ensure_voice_connected(CHANNELS or [])
            if current_voice_client is None or not current_voice_client.is_connected():
                raise RuntimeError("Chưa kết nối được voice channel → bỏ qua phát")

            file_path = tempfile.mktemp(suffix=".mp3")
            await tts.process(text, file_path)

            done_event = asyncio.Event()

            def after_play(error, path=file_path):
                if error:
                    print(f"Lỗi playback: {error}", flush=True)
                try:
                    os.remove(path)
                except OSError:
                    pass
                try:
                    bot.loop.call_soon_threadsafe(done_event.set)
                except Exception:
                    pass  # bot đã chết giữa chừng → timeout bên dưới sẽ xử lý

            if FFMPEG_PATH is None:
                raise RuntimeError(
                    "Không tìm thấy ffmpeg (cả PATH và imageio-ffmpeg) → không phát được"
                )
            source = discord.FFmpegPCMAudio(
                file_path, options="-vn", executable=FFMPEG_PATH
            )
            current_voice_client.play(source, after=after_play)
            try:
                # Timeout chống kẹt: nếu callback after không bao giờ fire
                # (voice chết giữa chừng), worker không bị treo vĩnh viễn
                await asyncio.wait_for(
                    done_event.wait(), timeout=PLAY_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                log("Phát audio quá lâu → bỏ qua (voice có thể đã mất)", "error")
                if current_voice_client and current_voice_client.is_playing():
                    current_voice_client.stop()

            await release_playback_lock(lock_channel)  # giải phóng ngay khi xong
            await history_channel.create_thread(name=thread_original, content="done")
        except Exception as e:
            log(f"tts_worker error: {e}", "error")
        finally:
            tts_queue.task_done()
def myStyle(log_queue):
    @bot.event
    async def on_ready():
        global CHANNELS, GUILD, current_voice_client, ttsKeysChannel, tts_keys
        lock_channel=None
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
                    elif 'lock_keys' ==channel.name.lower():
                        lock_channel=channel
        bot.loop.create_task(tts_worker(lock_channel))
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
            vc = ctx.guild.voice_client
            if vc and vc.is_connected():
                await vc.move_to(channel)
                current_voice_client = vc
            else:
                if vc:
                    await vc.disconnect(force=True)
                current_voice_client = await channel.connect(reconnect=True)
            await ctx.send(f"Bot joined voice: {channel.name}")
        else:
            await ctx.send("Bạn phải ở voice channel trước!")

    @bot.command(name="leave")
    async def leave(ctx):
        global current_voice_client
        vc = ctx.guild.voice_client or current_voice_client
        if vc:
            await vc.disconnect()
            current_voice_client = None
            await ctx.send("Bot left voice channel.")
        else:
            await ctx.send("Bot chưa join voice.")

    # Task định kỳ: Quét thread giao dịch mỗi 1 giây

    @tasks.loop(seconds=1)  # Chỉnh thời gian ở đây (seconds, minutes, hours)
    async def periodic_api_check(guild):
        try:
            global current_voice_client, CHANNELS, processed_threads
            if current_voice_client is None or not current_voice_client.is_connected():

                print("Bot chưa join voice channel → skip play voice")
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
                                stopped = True
                    elif "fpt-voice" in channel.name.lower():
                        ttsKeysChannel = channel
                        async for msg in channel.history():
                            tts_keys.add(msg.content)
                # return  # Bỏ qua nếu chưa join voice
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
                if lastThreads and historyChannel:
                    for threadMeta in lastThreads:
                        if (
                            str(threadMeta["original"])
                            not in list(
                                map(lambda item: item.name, historyChannel.threads)
                            )
                            and threadMeta["original"] not in processed_threads
                        ):
                            if processing_lock.locked():
                                continue
                            async with processing_lock:
                                processed_threads.add(threadMeta["original"])
                                TEXT = normalizer.normalize(
                                    f"đã {'nhận' if threadMeta['sign'] == '+' else 'chuyển'} {threadMeta['amount']} đồng"
                                )

                                await tts_queue.put((TEXT, threadMeta["original"], historyChannel))
                                # fileId = 'output.mp3'#f"{datetime.now().timestamp()}.mp3"
                                # await tts.process(TEXT, fileId)
                                # if (
                                #     not current_voice_client.is_playing()
                                # ):  # Thay logic của bạn
                                #     # def play_next_audio(error, meta):
                                #     #     if error:
                                #     #         print(f"File đầu tiên lỗi: {error}")

                                #     #     # Phát file thứ hai (URL TTS)
                                #     #     if not current_voice_client.is_playing():
                                #     #         try:
                                #     #             source2 = discord.FFmpegPCMAudio(
                                #     #                 str(audioUrl),  # URL TTS
                                #     #                 **ffmpeg_options,
                                #     #             )
                                #     #             current_voice_client.play(
                                #     #                 source2,
                                #     #                 after=lambda e, meta1=meta: (
                                #     #                     bot.loop.create_task(
                                #     #                         onComplete(e, meta1)
                                #     #                     )
                                #     #                 ),  # callback khi file 2 xong
                                #     #             )
                                #     #             print("Đang phát file thứ hai (TTS URL)")
                                #     #         except Exception as ex:
                                #     #             print(f"Lỗi phát file thứ hai: {ex}")
                                #     #     else:
                                #     #         print("Đang phát rồi, không queue file thứ hai")
                                #     ffmpeg_options = {"options": "-vn"}  # Chỉ audio
                                #     source = discord.FFmpegPCMAudio(
                                #         f"{fileId}",
                                #         **ffmpeg_options,
                                #     )
                                #     if not current_voice_client.is_playing():
                                #         current_voice_client.play(source)
                                #         await asyncio.sleep(1)
                                #         print("Đang play voice từ API response!")
                                #         await historyChannel.create_thread(
                                #             name=threadMeta["original"], content="done"
                                #         )
                                #         print("Voice played OK")
                                #     else:
                                #         print("Đang play rồi → skip")

        except Exception as e:
            print(e, flush=True)

    @periodic_api_check.before_loop
    async def before_check():
        await bot.wait_until_ready()
        print("Periodic API check started!", flush=True)

    log(f"[{INSTANCE_ID}] Khởi động bot...")
    # log_handler=None: discord.py mặc định add 1 StreamHandler vào root logger
    # mỗi lần bot.run() — watchdog restart nhiều lần → log bị in lặp 5-6 lần
    # (giả tạo cảm giác "nhiều kết nối cùng lúc"). Ta cấu hình logging 1 lần duy nhất.
    bot.run(token, log_handler=None)


def myStyle():
    """Thread giám sát: chạy bot và TỰ ĐỘNG KHỞI ĐỘNG LẠI bot khi gặp lỗi.

    discord.py Bot không thể chạy lại trên cùng một object sau khi chết, nên
    mỗi lần restart ta tạo một instance bot hoàn toàn mới (run_bot_once).
    """
    log = make_logger()
    crash_count = 0
    while True:
        token = os.environ.get("DC_TK")
        if not token:
            log("Thiếu biến môi trường DC_TK → thử lại sau 60s", "error")
            time.sleep(60)
            continue
        started = time.time()
        try:
            reset_runtime_state()
            run_bot_once(log, token)
            # bot.run trả về bình thường (bot bị thoát) → chạy lại sau một lúc
            log(f"Bot dừng bình thường → khởi động lại sau {RESTART_DELAY_SECONDS}s")
            time.sleep(RESTART_DELAY_SECONDS)
        except discord.LoginFailure:
            # Token sai — retry nhanh chỉ làm Discord flag IP, chờ dài hơn
            log("Token Discord không hợp lệ (DC_TK) → thử lại sau 60s", "error")
            time.sleep(60)
        except BaseException as e:
            if time.time() - started > 300:
                crash_count = 0  # đã chạy ổn định ≥5 phút → đếm lỗi lại từ đầu
            crash_count += 1
            delay = min(RESTART_DELAY_SECONDS * crash_count, MAX_RESTART_DELAY_SECONDS)
            log(
                f"Bot gặp lỗi: {type(e).__name__}: {e} → "
                f"khởi động lại sau {delay}s (lần {crash_count})",
                "error",
            )
            time.sleep(delay)


def ensure_bot_thread():
    """Đảm bảo thread bot đang chạy — spawn lại nếu thread đã chết hoàn toàn."""
    global thread
    with thread_spawn_lock:
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(target=myStyle, name="myStyle", daemon=True)
        thread.start()


@st.cache_resource
def initialize_heavy_stuff():
    # Đây là phần chỉ chạy ĐÚNG 1 LẦN khi server khởi động (hoặc khi cache miss)
    with st.spinner("running your scripts..."):
        ensure_bot_thread()
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

# Thread bot chết (lỗi cực hiếm) → spawn lại ngay, không phụ thuộc cache
ensure_bot_thread()

st.success("The system is ready!")
st.write("Result:")
st.json(result)
while True:
    try:
        ensure_bot_thread()  # thread bot đã chết → spawn lại ngay tại đây
        with st.status("Processing...", expanded=True) as status:
            placeholder = st.empty()
            logs = []
            while (thread and thread.is_alive()) or not log_queue.empty():
                try:
                    level, message = log_queue.get_nowait()
                    logs.append((level, message))

                    with placeholder.container():
                        for lvl, msg in logs[-50:]:
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
        log_queue.put(("error", f"Error occurred: {str(e)}"))
        time.sleep(1)  # tránh vòng lặp lỗi chạy liên tục
