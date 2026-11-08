import base64
import logging
from google import genai
import config

_logger = logging.getLogger(__name__)

client = genai.Client(api_key = config.GEMINI_API_KEY)

def tts(text : str):
    interaction = client.interactions.create(
        model=config.TTS_MODEL,
        input=[{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": text,
                "annotations": [{
                    "type": "speech_metadata",
                    "style": "cheerful and friendly",
                }],
            }],
        }],
        response_format={"type": "audio"},
        generation_config={
            "speech_config": [
                {"voice": "Kore"},
            ]
        },
    )
    with open(r"C:\Users\Deepanshu sharma\Desktop\voice_agent\voices\out.wav", "wb") as f:
            f.write(base64.b64decode(interaction.output_audio.data))

if __name__=="__main__":

    tts("hello, hindi me ise kehte hai namaste")