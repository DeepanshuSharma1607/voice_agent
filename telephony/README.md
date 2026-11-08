# Gemini + Exotel AI Phone Agent

A general-purpose voice assistant on a phone number: Exotel carries the call,
Gemini Live does the listening, thinking and speaking. Anyone can call it and
ask anything, like a friendly voice version of ChatGPT.

**Live at:** https://voice-agent-4ney.onrender.com
(health check: `/health` · WebSocket: `wss://voice-agent-4ney.onrender.com/exotel/media`)

## Tech stack

Python 3.12, FastAPI, Uvicorn, WebSockets, Google `google-genai` (Gemini Live),
Exotel Voicebot, NumPy + `soxr` (audio resampling), `asyncio`. Hosted on Render.

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

## How a call flows

1. Exotel connects to `/exotel/media`; Gemini connects in parallel.
2. On Exotel's `start` event the code sends a hidden greeting trigger; Gemini
   says hello first. Caller audio is held back until the greeting ends
   (`GREETING_PROTECT_SECONDS`).
3. Caller audio goes Exotel -> resample -> Gemini; Gemini audio goes
   resample -> Exotel in 3200-byte packets.
4. The call ends when Gemini says goodbye and calls `end_call`, or the caller
   clearly asks to hang up. A timeout forces hang-up if a goodbye never finishes.

## Run locally

From the `telephony` folder, with your venv active:

```
pip install -r requirements.txt
uvicorn voice_agent.main:app --port 8000
ngrok http --url=<your-domain>.ngrok-free.dev 8000
```

Needs a `.env` in the parent folder (or any parent folder) with
`GEMINI_API_KEY`, `GEMINI_LIVE_MODEL`, `EXOTEL_WS_URL`. See `.env.example`
for the optional settings.

## Deploy on Render

Create a **Web Service** from the GitHub repo with:

| Setting | Value |
|---|---|
| Root Directory | `telephony` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn voice_agent.main:app --host 0.0.0.0 --port $PORT` |
| Region | Singapore (closest to callers in India) |
| Health Check Path | `/health` |

Add these under **Environment** (the real `.env` is gitignored and never
pushed; Secret Files are not needed):

| Key | Value |
|---|---|
| `GEMINI_API_KEY` | your Gemini key |
| `GEMINI_LIVE_MODEL` | the Gemini Live model name |
| `EXOTEL_WS_URL` | `wss://voice-agent-4ney.onrender.com/exotel/media` |
| `PYTHON_VERSION` | `3.12.7` |

"No .env file found; using process environment only" in the logs is normal on Render.

### Exotel setup

Voicebot applet: point it at `https://voice-agent-4ney.onrender.com/` (returns
the WebSocket URL) or directly at `wss://voice-agent-4ney.onrender.com/exotel/media`.
Set the applet's Next Applet to Hangup.

### Free plan note

Render's Free instance sleeps after 15 minutes without traffic, and waking takes
about a minute, so a call that arrives then rings and fails. To avoid that for
free, use a monitor (for example cron-job.org) to request
`https://voice-agent-4ney.onrender.com/health` every 10 minutes. Free instances
also have only 0.1 CPU and can be restarted by Render at any time; a paid
instance fixes both. Memory is not a concern: the app uses roughly 100 MB.

## Test

```
pip install pytest httpx
pytest
```

## Tuning and troubleshooting

- Replies feel slow: lower `VAD_SILENCE_MS`. Agent cuts people off: raise it.
- Call fails to connect with a Gemini error mentioning the config: set
  `VAD_SILENCE_MS=0` to use Gemini's default voice-activity settings.
- Caller's first words are lost at the start of a call: set
  `GREETING_PROTECT_SECONDS=0`.
- Look for `latency_ms=` in the logs to see reply delay.
- Calls that ring and never connect, or drop mid-call with
  `keepalive ping timeout`, are almost always network problems (unstable
  internet on a laptop, or a sleeping Free instance), not code bugs.
- Optional security: set `EXOTEL_WS_TOKEN` and add `?token=...` to the
  WebSocket URL you give Exotel.
