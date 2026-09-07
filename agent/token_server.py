"""
token_server.py — Simple HTTP token server for LiveKit JWT generation.

Provides a /token endpoint that the React frontend calls to get a JWT
for joining a LiveKit room. Runs on port 7880.

Usage:
    python agent/token_server.py
"""

import io
import json
import os
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from dotenv import load_dotenv

# Ensure utf-8 output encoding on Windows console
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(Path(__file__).parent.parent / ".env")

# LiveKit credentials from environment
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")

if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET or "your_" in LIVEKIT_API_KEY:
    print("[WARN] LIVEKIT_API_KEY and LIVEKIT_API_SECRET not configured in .env")
    print("       The token server will return placeholder tokens for testing.")


class TokenHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        """Generate a LiveKit JWT for room access."""
        if self.path != "/token":
            self.send_error(404)
            return

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}

        room = data.get("room", "studybuddy-room")
        identity = data.get("identity", f"student-{int(time.time())}")

        try:
            from livekit.api import AccessToken, VideoGrants

            key = LIVEKIT_API_KEY if LIVEKIT_API_KEY and "your_" not in LIVEKIT_API_KEY else "devkey"
            secret = LIVEKIT_API_SECRET if LIVEKIT_API_SECRET and "your_" not in LIVEKIT_API_SECRET else "secret_key_32_bytes_long_minimum_value!"

            token = (
                AccessToken(key, secret)
                .with_identity(identity)
                .with_name(identity)
                .with_grants(
                    VideoGrants(
                        room_join=True,
                        room=room,
                        can_publish=True,
                        can_subscribe=True,
                        can_publish_data=True,
                    )
                )
            )
            jwt_str = token.to_jwt()
        except ImportError:
            print("[WARN] livekit-api not installed, returning placeholder token")
            jwt_str = "PLACEHOLDER_TOKEN_INSTALL_LIVEKIT_API"
        except Exception as e:
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
            return

        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "token": jwt_str,
            "room": room,
            "identity": identity,
        }).encode())

        print(f"[OK] Token issued for {identity} -> room={room}")

    def _cors_headers(self):
        """Add CORS headers for local dev."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format, *args):
        """Suppress default HTTP server logs."""
        pass


def main():
    port = 7880
    server = HTTPServer(("0.0.0.0", port), TokenHandler)
    key_status = "[OK] set" if (LIVEKIT_API_KEY and "your_" not in LIVEKIT_API_KEY) else "[MISSING/PLACEHOLDER]"
    secret_status = "[OK] set" if (LIVEKIT_API_SECRET and "your_" not in LIVEKIT_API_SECRET) else "[MISSING/PLACEHOLDER]"
    print(f"[TOKEN SERVER] Running on http://localhost:{port}/token")
    print(f"               LIVEKIT_API_KEY: {key_status}")
    print(f"               LIVEKIT_API_SECRET: {secret_status}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nToken server stopped.")


if __name__ == "__main__":
    main()
