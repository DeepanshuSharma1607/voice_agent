# Gemini + Exotel AI Phone Agent

A general-purpose voice assistant on a phone number: Exotel carries the call,
Gemini Live does the listening, thinking and speaking.
live at : https://voice-agent-4ney.onrender.com
## Layout

```
telephony/
  voice_agent/
    config.py          settings, .env discovery (walks up to the parent .env), validation
    logging_setup.py   logging with the call id on every line
    prompts.py         system prompt + greeting trigger
    hangup.py          caller hang-up phrase detection
    audio.py           streaming resampler, PCM helpers, level meter
    exotel.py          Exotel protocol: parse events, send/flush/clear audio
    gemini.py          Gemini config, connect, greeting, end_call tool, diagnostics
    metrics.py         reply-latency tracking
    session.py         CallSession: one call end to end
    main.py            FastAPI app and routes
  tests/               unit tests + a fake-Gemini smoke test
  requirements.txt  .env.example  conftest.py
```

## Run

From the `telephony` folder, with your venv active:

```
pip install -r requirements.txt
uvicorn voice_agent.main:app --port 8000
ngrok http --url=<your-domain>.ngrok-free.dev 8000
```

Exotel Voicebot applet: point it at `https://<your-domain>/` (returns the
WebSocket URL) or directly at `wss://<your-domain>/exotel/media`. Set the
applet's Next Applet to Hangup.

## Test

```
pytest
```

## How a call flows

1. Exotel connects to `/exotel/media`; Gemini connects in parallel.
2. On Exotel's `start` event the code sends a hidden greeting trigger; Gemini
   says hello first. Caller audio is held back until the greeting ends
   (`GREETING_PROTECT_SECONDS`).
3. Caller audio goes Exotel -> resample -> Gemini; Gemini audio goes
   resample -> Exotel in 3200-byte packets.
4. The call ends when Gemini says goodbye and calls `end_call`, or the caller
   clearly asks to hang up. A timeout forces hang-up if a goodbye never finishes.

## Tuning and troubleshooting

- Replies feel slow: lower `VAD_SILENCE_MS`. Agent cuts people off: raise it.
- Call fails to connect with a Gemini error mentioning the config: set
  `VAD_SILENCE_MS=0` to use Gemini's default voice-activity settings.
- Look for `latency_ms=` in the logs to see reply delay.
- Optional security: set `EXOTEL_WS_TOKEN` and add `?token=...` to the
  WebSocket URL you give Exotel.
