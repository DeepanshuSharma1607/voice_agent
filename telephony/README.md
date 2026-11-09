# Gemini Live + Exotel Voice Agent

A real-time AI phone assistant built with **FastAPI**, **Exotel Voicebot WebSockets**, and **Google Gemini Live**. Exotel connects the phone call to this service, Gemini Live understands the caller and generates spoken replies, and the service streams the audio back to Exotel.

Every call that reaches Exotel's `start` event is also saved as an individual text transcript file. The file name includes the Exotel call SID so conversations can be found by call identifier.

## What the application does

- Answers an incoming Exotel Voicebot call and sends a short greeting first.
- Streams caller audio to Gemini Live and streams Gemini's generated speech back to Exotel.
- Supports English, Hindi, Hinglish, and other languages supported by the configured Gemini Live model. The prompt asks Gemini to reply in the caller's language.
- Uses Gemini Live input and output transcription events to capture the caller's words and the agent's replies.
- Detects clear caller requests such as "hang up", "end the call", and "goodbye". Gemini can also invoke the `end_call` tool after speaking a goodbye.
- Saves a text transcript for each started call, including calls that end unexpectedly or disconnect mid-conversation.
- Exposes health and Exotel URL-resolver HTTP endpoints.

This project does **not** save call audio recordings. The saved file contains text supplied by Gemini Live transcription events.

## Project structure

```text
telephony/
├── voice_agent/
│   ├── __init__.py
│   ├── audio.py             # PCM16 helpers and streaming sample-rate conversion
│   ├── config.py            # .env discovery, settings, and validation
│   ├── exotel.py            # Exotel event parsing and outbound audio packet sender
│   ├── gemini.py            # Gemini Live connection, configuration, and end_call tool
│   ├── hangup.py            # Explicit caller hang-up phrase detection
│   ├── logging_setup.py     # Call-ID-aware console logging
│   ├── main.py              # FastAPI routes and WebSocket endpoint
│   ├── metrics.py           # First-audio response latency tracking
│   ├── prompts.py           # Voice-agent instructions and initial greeting
│   ├── session.py            # Complete lifecycle for one phone call
│   └── transcripts.py       # Per-call transcript file creation
├── tests/
│   ├── test_audio.py
│   ├── test_exotel.py
│   ├── test_hangup.py
│   ├── test_session_smoke.py
│   └── test_transcripts.py
├── .env.example
├── conftest.py
├── requirements.txt
└── README.md
```

## Requirements

- Python 3.10 or newer
- An Exotel account with a Voicebot/WebSocket-enabled applet
- A Google Gemini API key and access to a Gemini Live model
- A public HTTPS/WSS URL for Exotel to reach your running service (for local development, use a tunnel such as ngrok)

## Setup

Run these commands from the `telephony` project directory.

### 1. Create and activate a virtual environment

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux**

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` in the project root and fill in the required values.

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux**

```bash
cp .env.example .env
```

Required settings:

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API key. |
| `GEMINI_LIVE_MODEL` | Gemini Live model ID available to your API key. |
| `EXOTEL_WS_URL` | Public WebSocket URL for this service, normally `wss://YOUR-DOMAIN/exotel/media`. |

The application searches for `.env` in the `voice_agent` directory, the project root, and then parent directories. Keeping it in the project root is recommended. Do not commit the real `.env` file or expose your API key. The included `.gitignore` excludes `.env` and the default transcript directory because both can contain sensitive information.

## Run locally

Start the FastAPI server from the `telephony` directory:

```bash
uvicorn voice_agent.main:app --host 0.0.0.0 --port 8000
```

Useful endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /` | Returns `{"url": EXOTEL_WS_URL}` for an Exotel dynamic URL resolver. |
| `GET /health` | Returns service status and the configured Gemini model. |
| `WS /exotel/media` | Receives the Exotel call's media WebSocket stream. |

For local development, expose port `8000` through a public HTTPS tunnel. For example:

```bash
ngrok http 8000
```

Set `EXOTEL_WS_URL` to the resulting secure WebSocket address, for example `wss://YOUR-NGROK-DOMAIN/exotel/media`, and restart the application after changing `.env`.

## Exotel configuration

1. Create or open the Exotel Voicebot applet for the phone number.
2. Configure the applet to connect to this service. You can use the dynamic URL resolver at `https://YOUR-DOMAIN/` (the `GET /` route returns the `EXOTEL_WS_URL`) or provide the WebSocket endpoint directly if the applet configuration supports it.
3. The WebSocket endpoint is `wss://YOUR-DOMAIN/exotel/media`.
4. Set the applet's **Next Applet** to **Hangup**. The application closes the media WebSocket when the conversation ends so Exotel can continue to that applet.
5. Make a test call and check the server console for the call SID and transcript-save message.

### Optional WebSocket token

To require a shared token, set `EXOTEL_WS_TOKEN` in `.env`. Exotel must then connect to:

```text
wss://YOUR-DOMAIN/exotel/media?token=YOUR_SHARED_TOKEN
```

The token is checked before the WebSocket is accepted. Keep it secret and use a long, random value.

## Per-call transcript files

After a call has received Exotel's `start` event, the application writes one text file when the session closes. By default, files are stored in:

```text
telephony/call_transcripts/
```

File names look like:

```text
call_no_<EXOTEL_CALL_SID>.txt
```

For example, a call with SID `CA123456789` produces:

```text
call_transcripts/call_no_CA123456789.txt
```

Each file contains:

- `Call No` — the Exotel call SID; this is a call identifier, not the caller's phone number.
- Call SID and stream SID.
- Call start and end timestamps in UTC.
- Caller and agent text, grouped by conversation turn.

The transcript is saved during normal hang-up, caller disconnect, and session cleanup after an error, provided the Exotel `start` event was received. If Gemini does not return transcription text, the file still contains the call metadata and a note explaining that no transcript text was received. Partial text received before a disconnect is saved as well.

Change the output directory with the optional `CALL_TRANSCRIPT_DIR` setting:

```dotenv
CALL_TRANSCRIPT_DIR=call_transcripts
```

A relative path is resolved from the project root. An absolute path is also supported. Make sure the process has permission to create and write to the directory. In a cloud deployment, use a persistent disk or external storage if transcripts must survive replacement of the server instance.

**Privacy note:** transcripts may contain personal information spoken during calls. Restrict access to the transcript directory, define a retention policy, and inform callers about recording/transcription as required by applicable law and your deployment policy. This implementation stores text transcripts only; it does not store raw call audio.

## Audio and conversation flow

1. Exotel opens `WS /exotel/media` and sends `connected` followed by a `start` event containing the stream SID, call SID, and media format.
2. The service validates the start event and opens a Gemini Live session.
3. The service sends a hidden greeting trigger to Gemini so the agent speaks first. Caller audio is temporarily ignored while the greeting is playing (or until the configured protection timeout).
4. Caller audio is decoded from Exotel's base64 media payload, converted to 16 kHz mono PCM16, and streamed to Gemini Live.
5. Gemini Live returns speech audio at 24 kHz. The service converts it to Exotel's sample rate and sends base64 audio packets back to Exotel.
6. Gemini's input/output transcription events are collected. At the end of each response turn, the caller and agent text is added to that call's transcript history.
7. A clear caller hang-up request or Gemini's `end_call` tool starts the goodbye flow. The service waits for the goodbye audio to finish (with a timeout safety net), closes the session, and writes the transcript file.

Supported Exotel input sample rates are 8 kHz, 16 kHz, and 24 kHz. Audio is handled as mono PCM16. The output sender buffers audio into 3,200-byte packets and pads the final packet when needed.

## Configuration reference

All settings are optional except the three required credentials/connection values described above.

| Variable | Default | Purpose |
|---|---:|---|
| `EXOTEL_WS_TOKEN` | unset | Optional shared secret required as `?token=...` on the media WebSocket URL. |
| `VOICE_NAME` | `Puck` | Gemini prebuilt voice name. |
| `GREETING_TRIGGER` | Built-in greeting instruction | Text sent internally to make the agent greet the caller first. |
| `VAD_SILENCE_MS` | `600` | Silence duration used by Gemini automatic voice activity detection; `0` leaves this setting to Gemini. |
| `GREETING_PROTECT_SECONDS` | `4.0` | Maximum initial period in which caller audio is ignored while the greeting plays; `0` disables protection. |
| `GOODBYE_TIMEOUT_SECONDS` | `10.0` | Safety timeout while waiting for the goodbye flow to finish. |
| `MAX_PLAYBACK_WAIT_SECONDS` | `15.0` | Upper bound on waiting for goodbye audio playback. |
| `PLAYBACK_MARGIN_SECONDS` | `0.5` | Extra playback wait margin. |
| `IDLE_WARN_SECONDS` | `10.0` | Logs a warning if Gemini sends no message for this long. |
| `RECENT_TURN_SECONDS` | `5.0` | Window used to identify goodbye audio that completed just before `end_call`. |
| `START_EVENT_TIMEOUT_SECONDS` | `30.0` | Maximum wait for Exotel's `start` event. |
| `RAW_RESPONSES_TO_LOG` | `5` | Number of initial raw Gemini responses included in diagnostic logs. |
| `LOG_LEVEL` | `INFO` | Console log level. |
| `CALL_TRANSCRIPT_DIR` | `call_transcripts` | Directory where per-call text transcripts are saved. |

## Tests

Run the test suite from the project directory:

```bash
pytest -q
```

The tests cover PCM conversion/resampling, Exotel event parsing and packet sending, hang-up phrase detection, transcript file creation, and a WebSocket session smoke test using a fake Gemini session (no live Gemini or Exotel connection required).

## Troubleshooting

- **Missing `.env` keys:** check that the project-root `.env` contains `GEMINI_API_KEY`, `GEMINI_LIVE_MODEL`, and `EXOTEL_WS_URL`. Restart Uvicorn after editing it.
- **Exotel cannot connect:** verify the public HTTPS/WSS tunnel is running, the configured URL ends in `/exotel/media`, and the Exotel applet is configured with the resolver or direct WebSocket URL correctly.
- **WebSocket closes immediately:** check the server logs for a rejected token, invalid Exotel `start` event, unsupported sample rate, or Gemini connection failure.
- **Agent does not speak first:** check the Gemini API key/model and look for `Greeting trigger sent` and subsequent Gemini events in the console.
- **Caller audio is not understood:** verify the incoming Exotel audio sample rate and inspect the logged packet/RMS diagnostics. Set `VAD_SILENCE_MS=0` to use Gemini's default voice activity detection if the configured value causes a Gemini setup error.
- **No conversation text in the file:** the file is created from Gemini Live transcription events, not by transcribing stored audio. Confirm that input/output transcription events are being returned and check the Gemini model/API configuration.
- **Transcript file is missing:** confirm that Exotel sent a `start` event, the configured output directory is writable, and the log contains `Call transcript saved` or `Could not save call transcript`.
- **Replies are too slow or callers are interrupted:** adjust `VAD_SILENCE_MS`. A lower value can make replies faster but may cause the agent to treat pauses as the end of a caller's turn too quickly.

## Current limitations

- There is no database, web dashboard, or transcript search interface; each call is stored as a local `.txt` file.
- Raw call audio is not recorded or archived.
- Transcript completeness depends on Gemini Live returning input and output transcription events.
- The application does not transfer calls, send messages, or perform actions outside the phone conversation. The voice prompt explicitly tells Gemini not to claim otherwise.
- Production deployments should add persistent storage, access controls, monitoring, and a caller consent/retention policy appropriate to the deployment.
