"""
latency.py — End-to-end latency and interruption instrumentation.

Records timestamps at key points in the voice pipeline:
  - end_of_user_speech: when VAD/STT commits the user's final transcript
  - first_audio_byte_out: when the first TTS audio chunk is sent to the room
  - interruption_ts: exact moment barge-in is triggered while agent was speaking
  - audio_stopped_ts: exact moment previous audio output stops playing
  - recovery_time_ms: delay from user barge-in to first audio byte of next response

Writes structured JSON lines that scripts/evaluate_interruption.py consumes.
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger("voiceforge.latency")

LATENCY_LOG_PATH = Path(__file__).parent.parent / "latency_log.jsonl"


@dataclass
class TurnMetrics:
    """Metrics for a single conversational turn."""

    turn_index: int
    generation_id: int = 0
    is_cold_run: bool = False
    end_of_user_speech_ts: Optional[float] = None
    first_audio_byte_out_ts: Optional[float] = None
    llm_first_token_ts: Optional[float] = None
    tts_provider: str = "rime"
    user_text: str = ""
    agent_text_preview: str = ""
    is_stress_case: bool = False

    # Interruption & Recovery fields
    was_interrupted: bool = False
    interruption_ts: Optional[float] = None
    audio_stopped_ts: Optional[float] = None
    stale_response_leaked: bool = False
    stale_tool_discarded: bool = False

    # Computed deltas (filled in on flush)
    e2e_latency_ms: Optional[float] = None
    stt_to_llm_ms: Optional[float] = None
    llm_to_tts_ms: Optional[float] = None
    interruption_latency_ms: Optional[float] = None
    recovery_time_ms: Optional[float] = None

    def compute_deltas(self) -> None:
        """Compute latency deltas from raw timestamps."""
        if self.end_of_user_speech_ts and self.first_audio_byte_out_ts:
            self.e2e_latency_ms = round(
                (self.first_audio_byte_out_ts - self.end_of_user_speech_ts) * 1000, 2
            )
        if self.end_of_user_speech_ts and self.llm_first_token_ts:
            self.stt_to_llm_ms = round(
                (self.llm_first_token_ts - self.end_of_user_speech_ts) * 1000, 2
            )
        if self.llm_first_token_ts and self.first_audio_byte_out_ts:
            self.llm_to_tts_ms = round(
                (self.first_audio_byte_out_ts - self.llm_first_token_ts) * 1000, 2
            )
        if self.interruption_ts and self.audio_stopped_ts:
            self.interruption_latency_ms = round(
                (self.audio_stopped_ts - self.interruption_ts) * 1000, 2
            )
        if self.interruption_ts and self.first_audio_byte_out_ts:
            self.recovery_time_ms = round(
                (self.first_audio_byte_out_ts - self.interruption_ts) * 1000, 2
            )


class LatencyTracker:
    """
    Tracks per-turn latency and interruption metrics across the voice pipeline.
    """

    def __init__(self, log_path: Optional[Path] = None):
        self.log_path = log_path or LATENCY_LOG_PATH
        self._turns: dict[int, TurnMetrics] = {}
        self._turn_counter = 0
        self._is_first_turn = True
        self._last_interruption_ts: Optional[float] = None

        # Ensure log file exists (truncate on start for clean runs)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("")  # Fresh log each session
        logger.info("LatencyTracker initialized, logging to %s", self.log_path)

    def _get_or_create_turn(self, turn_index: int) -> TurnMetrics:
        if turn_index not in self._turns:
            is_cold = self._is_first_turn
            self._turns[turn_index] = TurnMetrics(
                turn_index=turn_index,
                is_cold_run=is_cold,
            )
            if is_cold:
                self._is_first_turn = False
        return self._turns[turn_index]

    def next_turn_index(self) -> int:
        """Get the next sequential turn index."""
        idx = self._turn_counter
        self._turn_counter += 1
        return idx

    def mark_user_speech_end(
        self,
        turn_index: int,
        user_text: str = "",
        is_stress: bool = False,
        generation_id: int = 0,
    ) -> None:
        """Record the moment user speech is committed."""
        turn = self._get_or_create_turn(turn_index)
        turn.end_of_user_speech_ts = time.monotonic()
        turn.user_text = user_text
        turn.is_stress_case = is_stress
        turn.generation_id = generation_id

        # If a prior interruption was recorded recently, link it for recovery time
        if self._last_interruption_ts is not None:
            turn.interruption_ts = self._last_interruption_ts

        logger.debug(
            "Turn %d (Gen %d): user speech ended at %.4f — '%s'",
            turn_index,
            generation_id,
            turn.end_of_user_speech_ts,
            user_text[:60],
        )

    def mark_llm_first_token(self, turn_index: int) -> None:
        """Record when the LLM emits its first token."""
        turn = self._get_or_create_turn(turn_index)
        if turn.llm_first_token_ts is None:
            turn.llm_first_token_ts = time.monotonic()
            logger.debug(
                "Turn %d: LLM first token at %.4f", turn_index, turn.llm_first_token_ts
            )

    def mark_first_audio_out(
        self, turn_index: int, provider: str = "rime", agent_text: str = ""
    ) -> None:
        """Record when the first TTS audio byte is sent to the room."""
        turn = self._get_or_create_turn(turn_index)
        if turn.first_audio_byte_out_ts is None:
            turn.first_audio_byte_out_ts = time.monotonic()
            turn.tts_provider = provider
            turn.agent_text_preview = agent_text[:100]
            logger.debug(
                "Turn %d: first audio out at %.4f via %s",
                turn_index,
                turn.first_audio_byte_out_ts,
                provider,
            )

    def mark_interruption(
        self,
        turn_index: int,
        interruption_ts: float,
        generation_id: int = 0,
    ) -> None:
        """Mark that this turn's speech was interrupted by user barge-in."""
        turn = self._get_or_create_turn(turn_index)
        turn.was_interrupted = True
        turn.interruption_ts = interruption_ts
        turn.generation_id = generation_id
        self._last_interruption_ts = interruption_ts
        logger.info(
            "⚡ Turn %d marked as INTERRUPTED at %.4f (gen %d)",
            turn_index,
            interruption_ts,
            generation_id,
        )

    def mark_audio_stopped(
        self,
        turn_index: int,
        audio_stopped_ts: float,
    ) -> Optional[float]:
        """Record the moment audio playback stopped after an interruption."""
        turn = self._turns.get(turn_index)
        if not turn:
            return None
        turn.audio_stopped_ts = audio_stopped_ts
        if turn.interruption_ts:
            turn.interruption_latency_ms = round(
                (audio_stopped_ts - turn.interruption_ts) * 1000, 2
            )
            logger.info(
                "🔇 Turn %d audio stopped: interruption latency = %.1fms",
                turn_index,
                turn.interruption_latency_ms,
            )
            return turn.interruption_latency_ms
        return None

    def mark_stale_tool_discarded(self, turn_index: int) -> None:
        """Mark that a slow tool result for this turn was successfully fenced."""
        turn = self._get_or_create_turn(turn_index)
        turn.stale_tool_discarded = True
        logger.info("🛡️ Turn %d: stale tool result fenced and discarded", turn_index)

    def mark_stale_leak(self, turn_index: int) -> None:
        """Mark that a stale response leaked through (should remain 0)."""
        turn = self._get_or_create_turn(turn_index)
        turn.stale_response_leaked = True
        logger.error("🚨 Turn %d: STALE RESPONSE LEAK DETECTED!", turn_index)

    def flush_turn(self, turn_index: int) -> Optional[TurnMetrics]:
        """Compute deltas and write turn metrics to the log file."""
        turn = self._turns.get(turn_index)
        if not turn:
            logger.warning("Turn %d not found, skipping flush", turn_index)
            return None

        turn.compute_deltas()

        # Write to JSONL
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn)) + "\n")

        # Log summary
        interrupted_label = " [INTERRUPTED]" if turn.was_interrupted else ""
        cold_label = " [COLD]" if turn.is_cold_run else ""
        stress_label = " [STRESS]" if turn.is_stress_case else ""
        leak_label = " [🚨 LEAK]" if turn.stale_response_leaked else ""

        logger.info(
            "Turn %d%s%s%s%s (Gen %d): E2E = %sms | Interruption-to-silence = %sms | Recovery = %sms | provider = %s",
            turn_index,
            cold_label,
            stress_label,
            interrupted_label,
            leak_label,
            turn.generation_id,
            f"{turn.e2e_latency_ms:.1f}" if turn.e2e_latency_ms is not None else "N/A",
            f"{turn.interruption_latency_ms:.1f}"
            if turn.interruption_latency_ms is not None
            else "N/A",
            f"{turn.recovery_time_ms:.1f}"
            if turn.recovery_time_ms is not None
            else "N/A",
            turn.tts_provider,
        )

        # Reset last interruption after a successful recovery
        if turn.first_audio_byte_out_ts is not None:
            self._last_interruption_ts = None

        # Clean up
        del self._turns[turn_index]
        return turn

    def get_all_logged_metrics(self) -> List[dict]:
        """Read all logged turn metrics from the JSONL file."""
        if not self.log_path.exists():
            return []
        metrics = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    metrics.append(json.loads(line))
        return metrics
