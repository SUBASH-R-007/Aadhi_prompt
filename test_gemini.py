import os
import asyncio
from google import genai
from google.genai import types

async def test():
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv('GEMINI_API_KEY')
    
    client = genai.Client(api_key=api_key)
    prompt_text = "Please read the following text aloud with high energy, a natural conversational tone, and a fluent Indian English accent:\nTesting this audio snippet."
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash-preview-tts',
            contents=prompt_text,
            config=types.GenerateContentConfig(
                response_modalities=['AUDIO'],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name='Puck'
                        )
                    )
                )
            )
        )
        print('Success!')
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(test())
