"""
verify_rime_catalog.py — Validate Rime model/speaker/language against the live catalog.

Run before demo to satisfy the hackathon requirement:
"Pick a specific model, speaker, and language from Rime's LIVE catalog at build time."

Usage:
    python scripts/verify_rime_catalog.py
"""

import json
import sys
import os
import urllib.request
import urllib.error

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# --- Configuration to verify (must match agent/main.py) ---
TARGET_MODEL = "mistv3"
TARGET_SPEAKER = "luna"
TARGET_LANGUAGE = "eng"  # Catalog uses 3-letter codes

# Rime public catalog endpoints
VOICES_URL = "https://users.rime.ai/data/voices/all-v2.json"
VOICE_DETAILS_URL = "https://users.rime.ai/data/voices/voice_details.json"


def fetch_json(url: str):
    """Fetch and parse JSON from a URL."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VoiceForge/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"[FAIL] Could not fetch {url}: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"[FAIL] Invalid JSON from {url}: {e}")
        return None


def main():
    print("=" * 60)
    print("VoiceForge -- Rime Live Catalog Verification")
    print("=" * 60)
    print(f"\nTarget configuration:")
    print(f"  Model:    {TARGET_MODEL}")
    print(f"  Speaker:  {TARGET_SPEAKER}")
    print(f"  Language: {TARGET_LANGUAGE}")
    print()

    # --- Fetch voice catalog ---
    print("Fetching Rime voice catalog...")
    data = fetch_json(VOICES_URL)
    if data is None:
        print("[FAIL] Could not fetch catalog")
        sys.exit(1)

    # Catalog structure: { model: { lang: [speakers] } }
    print(f"Models in catalog: {list(data.keys())}")

    # Check if our model exists
    if TARGET_MODEL not in data:
        print(f"\n[FAIL] Model '{TARGET_MODEL}' not found in catalog.")
        print(f"  Available models: {list(data.keys())}")
        sys.exit(1)

    model_data = data[TARGET_MODEL]
    print(f"Languages for {TARGET_MODEL}: {list(model_data.keys())}")

    # Check if our language exists for this model
    if TARGET_LANGUAGE not in model_data:
        print(f"\n[FAIL] Language '{TARGET_LANGUAGE}' not available for model '{TARGET_MODEL}'.")
        print(f"  Available languages: {list(model_data.keys())}")
        sys.exit(1)

    speakers = model_data[TARGET_LANGUAGE]
    print(f"Speakers for {TARGET_MODEL}/{TARGET_LANGUAGE}: {len(speakers)} available")

    # Check if our speaker exists
    if TARGET_SPEAKER in speakers:
        print(f"\n[PASS] Configuration VERIFIED in Rime live catalog!")
        print(f"  Model:    {TARGET_MODEL}")
        print(f"  Speaker:  {TARGET_SPEAKER}")
        print(f"  Language: {TARGET_LANGUAGE}")
        print(f"  Speaker index: {speakers.index(TARGET_SPEAKER) + 1}/{len(speakers)}")
    else:
        print(f"\n[FAIL] Speaker '{TARGET_SPEAKER}' not found for {TARGET_MODEL}/{TARGET_LANGUAGE}.")
        print(f"  Available speakers (first 20): {speakers[:20]}")
        sys.exit(1)

    # --- Fetch voice details for richer info ---
    print("\nFetching voice details...")
    details_data = fetch_json(VOICE_DETAILS_URL)
    if details_data and isinstance(details_data, dict):
        speaker_details = details_data.get(TARGET_SPEAKER, None)
        if not speaker_details:
            for name, detail in details_data.items():
                if name.lower() == TARGET_SPEAKER.lower():
                    speaker_details = detail
                    break
        if speaker_details and isinstance(speaker_details, dict):
            print(f"\nVoice details for '{TARGET_SPEAKER}':")
            for key, value in speaker_details.items():
                print(f"  {key}: {value}")

    print("\n" + "=" * 60)
    print("[PASS] Catalog verification PASSED")
    print(f"  Ready to use: {TARGET_MODEL}/{TARGET_LANGUAGE}/{TARGET_SPEAKER}")
    print("=" * 60)
    sys.exit(0)


if __name__ == "__main__":
    main()
