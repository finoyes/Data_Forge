"""
main.py — LiveKit Voice Agent entrypoint for VoiceForge StudyBuddy.

Problem 2: Interruption & Recovery in Voice AI.

Architecture:
  User Mic → WebRTC → LiveKit Room
    → Deepgram STT (Nova-3) — streaming speech-to-text
    → InterruptionManager — generation ID fencing & barge-in tracking
    → xAI Grok (text mode) — reasoning / quiz logic
    → Rime TTS (Mist v3, speaker: luna, lang: eng) — streaming speech synthesis
    → WebRTC → User Speaker

Generation Fencing:
  When a user interrupts, InterruptionManager advances generation_id. Any
  in-flight LLM tokens or pending slow tool lookups are flagged stale and
  discarded before reaching Rime TTS, ensuring 0% stale response leakage.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import AsyncIterable, Optional

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
    Agent,
    AgentSession,
)
from livekit.plugins import deepgram, rime, silero

try:
    from livekit.plugins import openai
except ImportError:
    import subprocess
    import sys
    req_path = Path(__file__).parent / "requirements.txt"
    print(f"⚠️ [NOTICE] 'livekit-plugins-openai' is missing from the environment.")
    print(f"   Auto-installing updated requirements from {req_path}...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", str(req_path)])
        from livekit.plugins import openai
        print("✅ Successfully installed missing dependencies!")
    except Exception as err:
        print(f"❌ Failed to auto-install dependencies: {err}")
        print("👉 To resolve permanently, please run: docker compose up --build")
        raise


from latency import LatencyTracker
from tutor import SYSTEM_PROMPT, simulate_slow_lookup
from failure_handler import FailureHandler
from interruption_manager import InterruptionManager

# ─── Load environment ────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent.parent / ".env")

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("voiceforge.main")

# ─── Rime Configuration ─────────────────────────────────────────────────────
RIME_MODEL = "mistv3"       # Mist v3 — ultra-low TTFB
RIME_SPEAKER = "luna"       # Female, chill, Gen-Z optimist
RIME_LANGUAGE = "eng"       # English ('eng' in Rime TTS)
RIME_AUDIO_FORMAT = "pcm"   # Raw PCM 16-bit for lowest decode overhead
RIME_TRANSPORT = "websocket"  # WebSocket /ws3 endpoint

logger.info(
    "Rime config: model=%s, speaker=%s, lang=%s, format=%s, transport=%s",
    RIME_MODEL, RIME_SPEAKER, RIME_LANGUAGE, RIME_AUDIO_FORMAT, RIME_TRANSPORT,
)

# ─── Global State & Coordinators ─────────────────────────────────────────────
tracker = LatencyTracker()
failure_handler = FailureHandler()
interruption_mgr = InterruptionManager()
current_turn_index = {"value": -1}
global_dispatcher = [None]  # Populated with dispatch_data during entrypoint

# Track active barge-in attempt between speech onset and transcript confirmation
pending_interruption = {
    "active": False,
    "interrupt_ts": 0.0,
    "latency_ms": None,
    "source": "",
}

# Common conversational backchannel cues (listening nods, not substantive commands)
BACKCHANNELS = {
    "uh-huh", "uh huh", "yeah", "yes", "mhm", "mm", "mm-hmm", "mm hmm",
    "okay", "ok", "hmm", "yep", "right", "ah", "got it", "i see",
}


# ─── Tool Definitions with Generation Fencing ────────────────────────────────

@llm.function_tool(
    description=(
        "Look up detailed information about a specific topic. "
        "Use this when the student asks you to search for, look up, "
        "or find information about something specific. "
        "This may take a few seconds."
    )
)
async def lookup_topic(topic: str) -> str:
    """Simulate a slow external lookup with generation fencing."""
    gen_id = interruption_mgr.current_generation_id
    logger.info("🔍 [Gen %d] Tool call: lookup_topic('%s') — starting", gen_id, topic)

    if not interruption_mgr.on_tool_started(gen_id, "lookup_topic"):
        return "Lookup cancelled because the question changed."

    def is_cancelled() -> bool:
        return not interruption_mgr.is_valid(gen_id)

    result = await simulate_slow_lookup(topic, check_cancelled=is_cancelled)

    if not interruption_mgr.on_tool_finished(gen_id, "lookup_topic"):
        turn_idx = current_turn_index["value"]
        if turn_idx >= 0:
            tracker.mark_stale_tool_discarded(turn_idx)
        # Update frontend telemetry immediately when a tool is cancelled
        if global_dispatcher[0]:
            global_dispatcher[0]({
                "type": "metrics_update",
                "turn_index": turn_idx,
                "interruption_latency_ms": None,
                "summary": interruption_mgr.get_summary(),
            })
        return "Lookup result discarded because you asked something else."

    logger.info("🔍 [Gen %d] Tool call: lookup_topic('%s') — completed valid", gen_id, topic)
    return result


class StudyAgent(Agent):
    """Voice study tutor Agent with semantic backchannel recognition."""

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        raw_text = getattr(new_message, "text_content", "") or ""
        clean_text = raw_text.lower().strip().strip(".,!?:")

        # If user only said a passive backchannel, raise StopResponse so paused speech smoothly resumes
        if clean_text in BACKCHANNELS:
            logger.info(
                "🎧 Passive backchannel '%s' detected — suppressing LLM reply so paused speech resumes",
                clean_text,
            )
            raise llm.StopResponse()

        await super().on_user_turn_completed(turn_ctx, new_message)



# ─── Agent Lifecycle ─────────────────────────────────────────────────────────

def prewarm(proc: JobProcess) -> None:
    """Pre-warm Silero VAD model during process initialization."""
    logger.info("Pre-warming: loading Silero VAD model...")
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("Pre-warming complete.")


async def entrypoint(ctx: JobContext) -> None:
    """Main agent entrypoint — creates and runs the AgentSession."""
    logger.info("Agent entrypoint started, connecting to room...")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Wait for a participant to join
    participant = await ctx.wait_for_participant()
    logger.info("Participant joined: %s", participant.identity)

    # Helper to send real-time metrics to frontend over DataChannel
    async def send_data_channel(payload: dict):
        try:
            if ctx.room and ctx.room.local_participant:
                msg = json.dumps(payload).encode("utf-8")
                await ctx.room.local_participant.publish_data(msg, reliable=True)
        except Exception as e:
            logger.debug("DataChannel publish failed: %s", e)

    def dispatch_data(payload: dict):
        asyncio.create_task(send_data_channel(payload))

    # Expose dispatcher globally so tool fencing can broadcast live updates
    global_dispatcher[0] = dispatch_data

    # Send initial telemetry snapshot on connect
    dispatch_data({
        "type": "metrics_update",
        "turn_index": 0,
        "interruption_latency_ms": None,
        "summary": interruption_mgr.get_summary(),
    })

    # ─── Pipeline components ─────────────────────────────────────────────

    stt_engine = deepgram.STT(
        model="nova-3",
        language="en-US",
    )

    # ─── LLM: Auto-detect Groq (gsk_...) vs xAI Grok (xai-...) ───────────
    llm_key = (
        os.environ.get("GROQ_API_KEY")
        or os.environ.get("XAI_API_KEY")
        or os.environ.get("GROK_API_KEY")
        or ""
    ).strip()

    if llm_key.startswith("gsk_"):
        active_provider = "Groq"
        # Determine available Groq model dynamically or from env
        configured_model = os.environ.get("GROQ_MODEL") or os.environ.get("LLM_MODEL")
        if configured_model:
            active_model = configured_model
        else:
            # Query Groq /models to verify active models on this specific key
            candidate_models = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b", "groq/compound"]
            active_model = "openai/gpt-oss-120b"
            try:
                import urllib.request
                req = urllib.request.Request(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {llm_key}", "User-Agent": "VoiceForge/1.0"}
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    avail_ids = {m["id"] for m in json.loads(resp.read().decode()).get("data", [])}
                    for cand in candidate_models:
                        if cand in avail_ids:
                            active_model = cand
                            break
            except Exception as err:
                logger.debug("Could not query Groq models dynamically: %s", err)

        logger.info("⚡ [LLM] Detected Groq API key (gsk_...). Provider: Groq | Model: %s", active_model)
        llm_engine = openai.LLM(
            model=active_model,
            base_url="https://api.groq.com/openai/v1",
            api_key=llm_key,
            temperature=0.7,
        )
    else:
        active_provider = "xAI Grok"
        active_model = os.environ.get("GROK_MODEL") or os.environ.get("XAI_MODEL") or "grok-2-latest"
        logger.info("🧠 [LLM] Provider: xAI Grok | Model: %s", active_model)
        llm_engine = openai.LLM.with_x_ai(
            model=active_model,
            api_key=llm_key,
            temperature=0.7,
        )

    tts_engine = rime.TTS(
        model=RIME_MODEL,
        speaker=RIME_SPEAKER,
        lang=RIME_LANGUAGE,
        use_websocket=True,
    )

    vad = ctx.proc.userdata.get("vad") or silero.VAD.load()

    # Balanced 3-pillar interruption handling:
    # 1. min_duration=0.35s filters out micro-noise / coughs (<350ms)
    # 2. min_words=1 requires at least 1 STT word for permanent turn commit
    # 3. backchannel_boundary=None eliminates the 1s lockout window
    # 4. resume_false_interruption=True with 1.0s timeout allows clean recovery on backchannels
    session = AgentSession(
        stt=stt_engine,
        llm=llm_engine,
        tts=tts_engine,
        vad=vad,
        turn_handling={
            "interruption": {
                "enabled": True,
                "mode": "vad",
                "min_duration": 0.35,
                "min_words": 1,
                "resume_false_interruption": True,
                "false_interruption_timeout": 1.0,
                "backchannel_boundary": None,
            },
        },
    )

    study_agent = StudyAgent(
        instructions=SYSTEM_PROMPT,
        tools=[lookup_topic],
    )

    # ─── Event Hooks & Interruption Fencing ───────────────────────────────

    @session.on("user_state_changed")
    def on_user_state_changed(ev):
        """Fired the instant user voice activity transitions state."""
        new_state = getattr(ev, "new_state", "")
        if new_state == "speaking":
            is_agent_busy = (
                interruption_mgr.is_speaking
                or getattr(session, "agent_state", "") in ("speaking", "thinking")
            )
            if is_agent_busy and not pending_interruption["active"]:
                now = time.monotonic()
                pending_interruption["active"] = True
                pending_interruption["interrupt_ts"] = now
                pending_interruption["source"] = "user_state_changed"
                logger.info(
                    "⚡ [SPEECH ONSET] User voice activity detected during active agent turn (ts=%.4f)",
                    now,
                )

    @session.on("agent_false_interruption")
    def on_agent_false_interruption(ev):
        """Fired when LiveKit determines an interruption was false and resumes audio."""
        resumed = getattr(ev, "resumed", False)
        logger.info("🎧 LiveKit false interruption event: speech resumed = %s", resumed)
        if pending_interruption["active"]:
            pending_interruption["active"] = False
            interruption_mgr.cancel_false_interruption()
            dispatch_data({
                "type": "metrics_update",
                "summary": interruption_mgr.get_summary(),
            })

    @session.on("agent_state_changed")
    def on_agent_state_changed(ev):
        """Fired when agent transitions state (speaking, listening, thinking)."""
        turn_idx = current_turn_index["value"]
        new_state = getattr(ev, "new_state", "")
        old_state = getattr(ev, "old_state", "")

        if new_state == "speaking":
            gen_id = interruption_mgr.current_generation_id
            if not interruption_mgr.on_speaking_started(gen_id):
                # Stale generation was blocked!
                logger.warning("🛡️ Blocked speech start for stale generation %d", gen_id)
                return

            if turn_idx >= 0:
                tracker.mark_first_audio_out(
                    turn_index=turn_idx,
                    provider="rime",
                )
            dispatch_data({"type": "agent_state", "state": "speaking", "gen": gen_id})

        elif old_state in ("speaking", "thinking"):
            stopped_ts = time.monotonic()
            interruption_latency = None

            # If user voice activity was detected, measure exact barge-in latency to pause/silence
            if pending_interruption["active"]:
                interruption_latency = round(
                    (stopped_ts - pending_interruption["interrupt_ts"]) * 1000, 2
                )
                pending_interruption["latency_ms"] = interruption_latency
                logger.info(
                    "🔇 Agent audio paused/halted: barge-in latency = %.1fms",
                    interruption_latency,
                )

            if turn_idx >= 0:
                metrics = tracker.flush_turn(turn_idx)
                summary = interruption_mgr.get_summary()
                dispatch_data({
                    "type": "metrics_update",
                    "turn_index": turn_idx,
                    "interruption_latency_ms": interruption_latency,
                    "summary": summary,
                })
                if metrics:
                    dispatch_data({
                        "type": "turn_completed",
                        "turn_index": turn_idx,
                        "e2e_ms": metrics.e2e_latency_ms,
                        "was_interrupted": metrics.was_interrupted,
                    })

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(ev):
        """Fired when STT transcribes user input."""
        if not getattr(ev, "is_final", True):
            return

        user_text = getattr(ev, "transcript", "").strip()
        if not user_text:
            return

        clean_text = user_text.lower().strip().strip(".,!?:")

        # ── Check if this was purely a passive backchannel (e.g. 'yeah', 'uh-huh')
        if clean_text in BACKCHANNELS:
            logger.info(
                "🎧 User said passive backchannel '%s' — letting paused speech resume",
                user_text,
            )
            pending_interruption["active"] = False
            interruption_mgr.cancel_false_interruption()
            dispatch_data({
                "type": "metrics_update",
                "summary": interruption_mgr.get_summary(),
            })
            return

        # ── Check if user barged in with an intentional command or question
        was_barge_in = pending_interruption["active"]
        curr_turn = current_turn_index["value"]

        if was_barge_in:
            new_gen, interrupt_ts = interruption_mgr.on_user_interrupt(user_text)
            lat_ms = pending_interruption["latency_ms"]
            if lat_ms is not None and interruption_mgr._current_event:
                interruption_mgr._current_event.latency_ms = lat_ms
                interruption_mgr._current_event.audio_stopped_ts = interrupt_ts + (lat_ms / 1000.0)

            if curr_turn >= 0:
                tracker.mark_interruption(curr_turn, interrupt_ts, new_gen)
                if lat_ms is not None:
                    tracker.mark_audio_stopped(
                        curr_turn, interrupt_ts + (lat_ms / 1000.0)
                    )

            pending_interruption["active"] = False

            # Broadcast real-time interruption event to UI centerpiece & telemetry
            dispatch_data({
                "type": "interrupted",
                "interruption_ts": interrupt_ts,
                "generation_id": new_gen,
                "user_text": user_text,
            })
            dispatch_data({
                "type": "metrics_update",
                "turn_index": curr_turn,
                "interruption_latency_ms": lat_ms,
                "summary": interruption_mgr.get_summary(),
            })
        else:
            new_gen = interruption_mgr.start_new_generation()

        # Start tracking this new turn
        turn_idx = tracker.next_turn_index()
        current_turn_index["value"] = turn_idx

        is_stress = any(
            kw in user_text.lower()
            for kw in ["look up", "search", "find information", "look that up"]
        )

        tracker.mark_user_speech_end(
            turn_index=turn_idx,
            user_text=user_text,
            is_stress=is_stress,
            generation_id=new_gen,
        )
        failure_handler.reset_turn_state()

        dispatch_data({
            "type": "user_transcription",
            "text": user_text,
            "turn_index": turn_idx,
            "generation_id": new_gen,
        })

        logger.info(
            "📝 User input committed (turn %d, gen %d): '%s'%s%s",
            turn_idx,
            new_gen,
            user_text[:80],
            " [STRESS]" if is_stress else "",
            " [BARGE-IN]" if was_barge_in else "",
        )

    @session.on("tool_execution_updated")
    def on_tool_execution(ev):
        """Log tool invocation lifecycle."""
        logger.info("🛠️ Tool execution status: %s", ev)

    # ─── Start the Session ───────────────────────────────────────────────

    await session.start(agent=study_agent, room=ctx.room)
    logger.info("🎙️ VoiceForge StudyBuddy is live with Interruption Manager!")
    logger.info(
        "🔊 TTS: RIME | Model: %s | Speaker: %s | Lang: %s",
        RIME_MODEL, RIME_SPEAKER, RIME_LANGUAGE,
    )
    logger.info("🧠 LLM: %s (%s)", active_provider, active_model)

    # Speak greeting as Turn 0 so greeting interruptions are tracked in telemetry
    turn_idx = tracker.next_turn_index()
    current_turn_index["value"] = turn_idx
    gen_id = interruption_mgr.start_new_generation()
    interruption_mgr.on_speaking_started(gen_id)
    tracker.mark_first_audio_out(
        turn_index=turn_idx,
        provider="rime",
        agent_text="Greeting",
    )
    dispatch_data({"type": "agent_state", "state": "speaking", "gen": gen_id})

    speech = session.say(
        "Hey there! I'm StudyBuddy, your voice-first study tutor. "
        "Feel free to interrupt me at any time if you want to change topics or ask something new. "
        "What would you like to study today?",
        allow_interruptions=True,
    )
    try:
        await speech
    except Exception as e:
        logger.debug("Greeting playback ended/interrupted: %s", e)
    finally:
        if interruption_mgr.is_speaking:
            interruption_mgr.on_audio_stopped()


# ─── Worker Entry Point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )

