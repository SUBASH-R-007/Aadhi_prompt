import asyncio
import os
import subprocess
import wave
from dotenv import load_dotenv
load_dotenv(override=True)
from openai import AsyncOpenAI

async def generate():
    client = AsyncOpenAI()
    response = await client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input="hello",
        response_format="mp3"
    )
    with open("temp.mp3", "wb") as f:
        f.write(response.content)
    subprocess.run(['ffmpeg', '-y', '-i', 'temp.mp3', 'test_ffmpeg.wav'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    f = wave.open('test_ffmpeg.wav', 'rb')
    print("Duration:", f.getnframes() / f.getframerate())

asyncio.run(generate())
