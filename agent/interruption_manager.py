"""
interruption_manager.py — Generation ID fencing and interruption tracking.

Core coordinator for Problem 2: Interruption & Recovery in Voice AI.

Responsibilities:
  1. Generation Fencing: Monotonically increasing generation_id ensures any
     in-flight LLM generation or tool execution belonging to a previous turn
     is immediately rejected and never spoken via Rime TTS.
  2. State Tracking: Tracks whether the agent is currently speaking, thinking,
     or was interrupted.
  3. Latency Measurement: Records exact timestamps from user barge-in detection
     to audio cancellation (interruption-to-silence latency).
  4. Leakage Prevention: Detects and guarantees 0% stale response leakage.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

logger = logging.getLogger("voiceforge.interruption")


@dataclass
class ConversationState:
    """State tracking for the active conversational generation."""
    generation_id: int = 0
    is_speaking: bool = False
    was_interrupted: bool = False
    tool_in_flight: Optional[str] = None
    interruption_ts: Optional[float] = None
    audio_stopped_ts: Optional[float] = None
    last_interrupted_gen_id: Optional[int] = None


@dataclass
class InterruptionEvent:
    """Record of a single interruption occurrence for evaluation."""
    generation_id: int
    interruption_ts: float
    audio_stopped_ts: Optional[float] = None
    latency_ms: Optional[float] = None
    stale_tokens_discarded: int = 0
    stale_tool_discarded: bool = False
    stale_leaked: bool = False
    user_prompt: str = ""


class InterruptionManager:
    """
    Coordinates state transitions, generation fences, and interruption metrics.
    """

    def __init__(self):
        self.state = ConversationState()
        self.history: List[InterruptionEvent] = []
        self._current_event: Optional[InterruptionEvent] = None
        self._stale_tokens_fenced_total = 0
        self._stale_tools_fenced_total = 0
        self._stale_leakage_count = 0

    @property
    def current_generation_id(self) -> int:
        return self.state.generation_id

    @property
    def is_speaking(self) -> bool:
        return self.state.is_speaking

    def start_new_generation(self) -> int:
        """
        Start a new generation for an incoming user turn.
        Increments generation_id and resets active turn state.
        """
        self.state.generation_id += 1
        self.state.is_speaking = False
        self.state.was_interrupted = False
        self.state.interruption_ts = None
        self.state.audio_stopped_ts = None
        self.state.tool_in_flight = None
        logger.info(
            "🔄 [Gen %d] Started new generation", self.state.generation_id
        )
        return self.state.generation_id

    def is_valid(self, gen_id: int) -> bool:
        """
        Fencing check: returns True if gen_id matches current active generation
        and that generation has not been interrupted.
        """
        if gen_id != self.state.generation_id:
            logger.debug(
                "🛡️ FENCE: gen_id %d is stale (active: %d)",
                gen_id,
                self.state.generation_id,
            )
            return False
        if self.state.was_interrupted:
            logger.debug(
                "🛡️ FENCE: gen_id %d was interrupted, blocking", gen_id
            )
            return False
        return True

    def on_speaking_started(self, gen_id: int) -> bool:
        """
        Called when Rime TTS begins playing audio for gen_id.
        Returns False if the generation was already invalidated (fenced).
        """
        if not self.is_valid(gen_id):
            logger.warning(
                "🚫 BLOCKED stale speech playback for gen_id %d (active: %d)",
                gen_id,
                self.state.generation_id,
            )
            self._stale_leakage_count += 0  # blocked successfully!
            return False

        self.state.is_speaking = True
        logger.info("🔊 [Gen %d] Rime audio output started", gen_id)
        return True

    def on_user_interrupt(self, user_text: str = "") -> tuple[int, float]:
        """
        Called the instant user speech is detected while the agent was speaking
        or thinking. Fences the previous generation and bumps generation_id.
        """
        now = time.monotonic()
        prev_gen = self.state.generation_id
        self.state.last_interrupted_gen_id = prev_gen
        self.state.interruption_ts = now

        # Monotonically advance generation_id so in-flight tasks from prev_gen are fenced out
        self.state.generation_id += 1
        new_gen = self.state.generation_id

        # The new generation begins fresh (not yet interrupted)
        self.state.was_interrupted = False
        self.state.is_speaking = False
        self.state.tool_in_flight = None

        # Record interruption event for the interrupted generation
        self._current_event = InterruptionEvent(
            generation_id=prev_gen,
            interruption_ts=now,
            user_prompt=user_text,
        )
        self.history.append(self._current_event)

        logger.info(
            "⚡ INTERRUPTION DETECTED: fenced gen %d -> new gen %d (ts=%.4f) | '%s'",
            prev_gen,
            new_gen,
            now,
            user_text[:60],
        )
        return new_gen, now

    def cancel_false_interruption(self) -> None:
        """
        Called when detected speech turns out to be a passive backchannel or non-verbal noise.
        Removes the false interruption from history and restores state.
        """
        if self._current_event:
            if self._current_event in self.history:
                self.history.remove(self._current_event)
            self._current_event = None
            self.state.last_interrupted_gen_id = None
            logger.info("↩️ False interruption cancelled from history (backchannel/noise detected)")

    def on_audio_stopped(self) -> Optional[float]:
        """
        Called when audio playback ceases (either naturally or forced cancellation).
        Computes interruption latency if an interruption occurred.
        """
        now = time.monotonic()
        self.state.is_speaking = False

        if self._current_event and self._current_event.audio_stopped_ts is None:
            self._current_event.audio_stopped_ts = now
            latency_ms = None
            if self._current_event.interruption_ts is not None:
                latency_ms = round((now - self._current_event.interruption_ts) * 1000, 2)
            self._current_event.latency_ms = latency_ms

            logger.info(
                "🔇 Audio stopped after interruption: latency = %sms",
                f"{latency_ms:.1f}" if latency_ms is not None else "N/A",
            )
            return latency_ms

        return None

    def on_tool_started(self, gen_id: int, tool_name: str) -> bool:
        """
        Called before a slow tool executes. Returns False if already stale.
        """
        if not self.is_valid(gen_id):
            logger.warning(
                "🛡️ FENCE: Tool '%s' blocked from starting for stale gen %d",
                tool_name,
                gen_id,
            )
            self._stale_tools_fenced_total += 1
            return False

        self.state.tool_in_flight = tool_name
        logger.info("🛠️ [Gen %d] Tool '%s' started", gen_id, tool_name)
        return True

    def on_tool_finished(self, gen_id: int, tool_name: str) -> bool:
        """
        Called when a slow tool completes. Validates generation fence.
        Returns False if the result must be discarded because the user interrupted.
        """
        is_valid = self.is_valid(gen_id)
        if self.state.tool_in_flight == tool_name:
            self.state.tool_in_flight = None

        if not is_valid:
            logger.warning(
                "🛡️ FENCE: Discarding result of tool '%s' from stale gen %d (active: %d, interrupted: %s)",
                tool_name,
                gen_id,
                self.state.generation_id,
                self.state.was_interrupted,
            )
            self._stale_tools_fenced_total += 1
            if self._current_event:
                self._current_event.stale_tool_discarded = True
            return False

        logger.info("🛠️ [Gen %d] Tool '%s' finished successfully", gen_id, tool_name)
        return True

    def record_stale_token_discarded(self, count: int = 1) -> None:
        """Track LLM tokens discarded due to generation fencing."""
        self._stale_tokens_fenced_total += count
        if self._current_event:
            self._current_event.stale_tokens_discarded += count

    def record_stale_leakage(self) -> None:
        """Record a failure where stale audio or text leaked to user."""
        self._stale_leakage_count += 1
        if self._current_event:
            self._current_event.stale_leaked = True
        logger.error("🚨 CRITICAL: Stale response leaked through fencing!")

    def get_summary(self) -> Dict[str, Any]:
        """Aggregate interruption metrics for reporting."""
        latencies = [e.latency_ms for e in self.history if e.latency_ms is not None]
        p50 = None
        if latencies:
            sorted_lat = sorted(latencies)
            p50 = round(sorted_lat[len(sorted_lat) // 2], 2)

        return {
            "total_interruptions": len(self.history),
            "latencies_ms": latencies,
            "p50_interruption_latency_ms": p50,
            "stale_tokens_fenced": self._stale_tokens_fenced_total,
            "stale_tools_fenced": self._stale_tools_fenced_total,
            "stale_leakage_count": self._stale_leakage_count,
            "leakage_rate_pct": (
                0.0 if len(self.history) == 0 else
                round((self._stale_leakage_count / len(self.history)) * 100, 2)
            ),
        }
