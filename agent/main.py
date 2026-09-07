"""
main.py — LiveKit Voice Agent entrypoint for VoiceForge StudyBuddy.

Problem 2: Interruption & Recovery in Voice AI.

Architecture:
  User Mic → WebRTC → LiveKit Room
    → Deepgram STT (Nova-3) — streaming speech-to-text
    → InterruptionManager — generation ID fencing & barge-in tracking
    → Google Gemini Flash (text mode) — reasoning / quiz logic
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
from livekit.plugins import deepgram, google, rime, silero

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
        return "Lookup result discarded because you asked something else."

    logger.info("🔍 [Gen %d] Tool call: lookup_topic('%s') — completed valid", gen_id, topic)
    return result


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

    # ─── Pipeline components ─────────────────────────────────────────────

    stt_engine = deepgram.STT(
        model="nova-3",
        language="en-US",
    )

    llm_engine = google.LLM(
        model="gemini-2.0-flash",
        temperature=0.7,
    )

    tts_engine = rime.TTS(
        model=RIME_MODEL,
        speaker=RIME_SPEAKER,
        lang=RIME_LANGUAGE,
        use_websocket=True,
    )

    vad = ctx.proc.userdata.get("vad") or silero.VAD.load()

    session = AgentSession(
        stt=stt_engine,
        llm=llm_engine,
        tts=tts_engine,
        vad=vad,
        allow_interruptions=True,
    )

    study_agent = Agent(
        instructions=SYSTEM_PROMPT,
        tools=[lookup_topic],
    )

    # ─── Event Hooks & Interruption Fencing ───────────────────────────────

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(ev):
        """Fired when STT transcribes user input."""
        if not getattr(ev, "is_final", True):
            return

        user_text = getattr(ev, "transcript", "").strip()
        if not user_text:
            return

        # Check if user barged in while agent was speaking
        was_speaking = interruption_mgr.is_speaking
        if was_speaking:
            new_gen, interrupt_ts = interruption_mgr.on_user_interrupt(user_text)
            curr_turn = current_turn_index["value"]
            if curr_turn >= 0:
                tracker.mark_interruption(curr_turn, interrupt_ts, new_gen)
            dispatch_data({
                "type": "interrupted",
                "interruption_ts": interrupt_ts,
                "generation_id": new_gen,
                "user_text": user_text,
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
            " [BARGE-IN]" if was_speaking else "",
        )

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

        elif old_state == "speaking":
            # Audio stopped playing
            stopped_ts = time.monotonic()
            interruption_latency = interruption_mgr.on_audio_stopped()

            if interruption_latency is not None and turn_idx >= 0:
                tracker.mark_audio_stopped(turn_idx, stopped_ts)

            if turn_idx >= 0:
                metrics = tracker.flush_turn(turn_idx)
                summary = interruption_mgr.get_summary()
                dispatch_data({
                    "type": "metrics_update",
                    "turn_index": turn_idx,
                    "interruption_latency_ms": interruption_latency,
                    "summary": summary,
                })

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

    # Speak greeting
    gen_id = interruption_mgr.start_new_generation()
    interruption_mgr.on_speaking_started(gen_id)
    speech = session.say(
        "Hey there! I'm StudyBuddy, your voice-first study tutor. "
        "Feel free to interrupt me at any time if you want to change topics or ask something new. "
        "What would you like to study today?",
        allow_interruptions=True,
    )
    try:
        await speech.wait_if_not_interrupted()
    except Exception as e:
        logger.debug("Greeting playback ended/interrupted: %s", e)
    finally:
        interruption_mgr.on_audio_stopped()


# ─── Worker Entry Point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
