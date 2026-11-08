import logging
from dotenv import load_dotenv
load_dotenv()
import config
from google import genai

_logger = logging.getLogger(__name__)

client = genai.Client(api_key = config.GEMINI_API_KEY)

def transcribe_audio(audio_path: str) -> str:
    """Transcribe an audio file."""

    _logger.info("Transcribing audio: %s", audio_path)

    audio_file = client.files.upload(file=audio_path)

    response = client.interactions.create(
        model=config.STT_MODEL,
        input=[
            {
                "type": "audio",
                "uri": audio_file.uri,
                "mime_type": audio_file.mime_type
            }
        ]
    )

    return response.output_text



if __name__ == "__main__":

    audio_path = r"C:\Users\Deepanshu sharma\Desktop\voice_agent\voices\harvard.wav"

    transcript = transcribe_audio(audio_path)

    print("Transcription:", transcript)
