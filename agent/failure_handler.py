"""
failure_handler.py — Graceful degradation for VoiceForge StudyBuddy.

Defines clear failure behavior for each pipeline component:
  - STT failure: prompt user to repeat
  - LLM timeout: speak apology, retry once
  - Rime TTS unreachable: log clearly, attempt reconnect, degrade to text-only

None of these failures should cause the app to crash silently.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("voiceforge.failure")


# ─── Spoken error messages (kept short for TTS) ─────────────────────────────

ERRORS = {
    "stt_failed": "Sorry, I didn't catch that. Could you say that again?",
    "llm_timeout": "I'm having a bit of trouble thinking right now. Let me try that again.",
    "llm_retry_failed": "I'm sorry, I'm still having trouble. Could you try asking in a different way?",
    "tts_unreachable": "I'm having trouble with my voice right now. Please check the transcript for my response.",
    "tts_reconnecting": "One moment, reconnecting my voice.",
    "generic_error": "Something went wrong. Let me try that again.",
}


class FailureHandler:
    """
    Centralized failure handling for the voice pipeline.

    Provides spoken error messages via the agent's TTS, and tracks
    retry state to prevent infinite loops.
    """

    def __init__(self, max_llm_retries: int = 1, llm_timeout_seconds: float = 10.0):
        self.max_llm_retries = max_llm_retries
        self.llm_timeout_seconds = llm_timeout_seconds
        self._llm_retry_count = 0
        self._tts_failure_count = 0
        self._tts_is_degraded = False

    def reset_turn_state(self) -> None:
        """Reset per-turn retry counters."""
        self._llm_retry_count = 0

    @property
    def tts_is_degraded(self) -> bool:
        """True if TTS has failed and we're in text-only fallback mode."""
        return self._tts_is_degraded

    def handle_stt_error(self, error: Exception) -> str:
        """
        Handle STT transcription failure.
        Returns a spoken message to send to the user via TTS.
        """
        logger.error("STT error: %s", error, exc_info=True)
        return ERRORS["stt_failed"]

    def handle_llm_timeout(self) -> tuple[str, bool]:
        """
        Handle LLM response timeout.
        Returns (spoken_message, should_retry).
        """
        self._llm_retry_count += 1
        if self._llm_retry_count <= self.max_llm_retries:
            logger.warning(
                "LLM timeout (attempt %d/%d), will retry",
                self._llm_retry_count,
                self.max_llm_retries,
            )
            return ERRORS["llm_timeout"], True
        else:
            logger.error(
                "LLM timeout after %d retries, giving up", self._llm_retry_count
            )
            return ERRORS["llm_retry_failed"], False

    def handle_llm_error(self, error: Exception) -> str:
        """Handle unexpected LLM errors."""
        logger.error("LLM error: %s", error, exc_info=True)
        return ERRORS["generic_error"]

    def handle_tts_error(self, error: Exception) -> None:
        """
        Handle TTS synthesis failure.
        Logs the error and marks the TTS as degraded if failures persist.
        """
        self._tts_failure_count += 1
        logger.error(
            "TTS error (failure #%d): %s",
            self._tts_failure_count,
            error,
            exc_info=True,
        )

        if self._tts_failure_count >= 3:
            self._tts_is_degraded = True
            logger.critical(
                "TTS has failed %d times, marking as degraded. "
                "Responses will appear in transcript only.",
                self._tts_failure_count,
            )

    def handle_tts_recovery(self) -> None:
        """Called when TTS starts working again after failures."""
        if self._tts_is_degraded or self._tts_failure_count > 0:
            logger.info(
                "TTS recovered after %d failures", self._tts_failure_count
            )
            self._tts_failure_count = 0
            self._tts_is_degraded = False

    def get_status_summary(self) -> dict:
        """Return current failure state for observability."""
        return {
            "tts_degraded": self._tts_is_degraded,
            "tts_failure_count": self._tts_failure_count,
            "llm_retries_this_turn": self._llm_retry_count,
        }
