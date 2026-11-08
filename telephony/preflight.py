"""Pre-call readiness checks for the local Exotel + Gemini Live backend.

Run this from the activated project virtual environment while Uvicorn and
ngrok are running. Set NGROK_PUBLIC_URL to the current ngrok HTTPS origin.
This script never prints API keys or ngrok tokens.
"""


'''

$env:NGROK_PUBLIC_URL = "https://squeak-moonwalk-anyplace.ngrok-free.dev"

cd .\telephony

python preflight.py
'''



"""Pre-call readiness checks for the local Exotel + Gemini Live backend.

Run this from the activated project virtual environment while Uvicorn and
ngrok are running. Set NGROK_PUBLIC_URL to the current ngrok HTTPS origin.
This script never prints API keys or ngrok tokens.
"""



"""Pre-call readiness checks for the local Exotel + Gemini Live backend.

Run this from the activated project virtual environment while Uvicorn and
ngrok are running. Set NGROK_PUBLIC_URL to the current ngrok HTTPS origin.
This script never prints API keys or ngrok tokens.
"""
import os
import asyncio
import importlib
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOCAL_HEALTH_URL = os.getenv("LOCAL_HEALTH_URL", "http://127.0.0.1:8000/health")
NGROK_PUBLIC_URL = "https://squeak-moonwalk-anyplace.ngrok-free.dev"
MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
API_KEY = os.getenv("GEMINI_API_KEY")


def result(label: str, ok: bool, detail: str = "") -> bool:
    marker = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{marker}] {label}{suffix}")
    return ok


def check_imports() -> bool:
    modules = ("fastapi", "uvicorn", "numpy", "soxr", "google.genai")
    failed = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # report module name, not secrets
            failed.append(f"{name} ({type(exc).__name__})")
    return result(
        "Python dependencies",
        not failed,
        "all required imports available" if not failed else "missing: " + ", ".join(failed),
    )


def check_health(url: str, label: str) -> bool:
    request = Request(
        url,
        headers={
            "User-Agent": "voice-agent-preflight/1.0",
            "ngrok-skip-browser-warning": "true",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            ok = response.status == 200 and data.get("status") == "online"
            detail = f"HTTP {response.status}; service={data.get('service', 'unknown')}"
            return result(label, ok, detail)
    except HTTPError as exc:
        return result(label, False, f"HTTP {exc.code}")
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return result(label, False, f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        return result(label, False, f"{type(exc).__name__}: {exc}")


async def check_gemini_live() -> bool:
    if not API_KEY:
        return result(
            "Gemini Live handshake",
            False,
            "GEMINI_API_KEY is not set in this PowerShell session",
        )

    try:
        from google import genai

        client = genai.Client(api_key=API_KEY)
        config = {"response_modalities": ["AUDIO"]}

        # Opens and closes a Live session without sending caller audio.
        # This checks credentials, model availability, and Live connectivity.
        await asyncio.wait_for(
            _open_and_close_live_session(client, config),
            timeout=25,
        )
        return result(
            "Gemini Live handshake",
            True,
            f"connected to {MODEL}; no prompt/audio was sent",
        )
    except asyncio.TimeoutError:
        return result("Gemini Live handshake", False, "connection timed out after 25 seconds")
    except Exception as exc:
        # Avoid printing exception payloads which could contain request details.
        return result(
            "Gemini Live handshake",
            False,
            f"{type(exc).__name__}: check API key, model access, network, and SDK version",
        )


async def _open_and_close_live_session(client, config: dict) -> None:
    async with client.aio.live.connect(model=MODEL, config=config):
        pass


async def main() -> int:
    print("\n=== EXOTEL + GEMINI PRE-CALL CHECK ===")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Gemini model: {MODEL}")
    print("Secrets: hidden\n")

    checks: list[bool] = []
    checks.append(check_imports())
    checks.append(result("Gemini API key present", bool(API_KEY), "key is set" if API_KEY else "set GEMINI_API_KEY"))
    checks.append(check_health(LOCAL_HEALTH_URL, "Local FastAPI health"))

    if NGROK_PUBLIC_URL:
        checks.append(check_health(f"{NGROK_PUBLIC_URL}/health", "Public ngrok health"))
    else:
        checks.append(result(
            "Public ngrok health",
            False,
            "set NGROK_PUBLIC_URL to the current https://...ngrok... origin",
        ))

    checks.append(await check_gemini_live())

    print("\n=== SUMMARY ===")
    if all(checks):
        print("LOCAL STACK READY. Complete the Exotel dashboard checks below before calling.")
    else:
        print("NOT READY YET. Fix the failed checks above, then run this again.")

    print("\nManual Exotel checks (the script cannot verify these without a real call):")
    print("1. Voicebot applet URL matches the current ngrok WSS URL + /exotel/media.")
    print("2. The correct Exophone is assigned to the saved call flow.")
    print("3. The Voicebot applet's Next Applet is Hangup (or your intended route).")
    print("4. AgentStream/Voicebot is enabled for this Exotel account and trial plan.")
    print("5. ngrok and Uvicorn remain running during the test call.")
    print("\nOnly an actual inbound test call can confirm Exotel routing and two-way audio.")

    return 0 if all(checks) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nPreflight cancelled.")
        raise SystemExit(130)
