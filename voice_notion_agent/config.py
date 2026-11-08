import os
from dotenv import load_dotenv


load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

CHAT_MODEL = os.getenv("CHAT_MODEL","gemini-3.5-flash")
STT_MODEL = os.getenv("STT_MODEL","gemini-3.5-transcribe")
TTS_MODEL = os.getenv("TTS_MODEL","gemini-3.8-flash-tts")

def validate() ->None:
    if not GEMINI_API_KEY : 
        raise ValueError("Gemini key not found")
    if not TAVILY_API_KEY : 
        raise ValueError("Tavily key not found")