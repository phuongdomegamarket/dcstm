# This example requires the 'message_content' intent.

import os
import socket

import discord

print("Testing DNS resolution...")
try:
    ip = socket.gethostbyname("discord.com")
    print(f"discord.com resolves to: {ip}")
except socket.gaierror as e:
    print(f"DNS error: {e}")

try:
    ip = socket.gethostbyname("google.com")
    print(f"google.com resolves to: {ip}")
except socket.gaierror as e:
    print(f"DNS error google: {e}")

print("Environment:", os.environ.get("DC_TK") is not None)  # check token có set không


class MyClient(discord.Client):
    async def on_ready(self):
        print(f"Logged on as {self.user}!")

    async def on_message(self, message):
        print(f"Message from {message.author}: {message.content}")


intents = discord.Intents.default()
intents.message_content = True

client = MyClient(intents=intents)
client.run(os.environ.get("DC_TK"))
