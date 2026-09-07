"""
test_interruption_manager.py — Unit tests for Problem 2 Interruption & Fencing logic.
"""

import asyncio
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add agent directory to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from interruption_manager import InterruptionManager
from tutor import simulate_slow_lookup


def test_generation_fencing():
    mgr = InterruptionManager()
    gen1 = mgr.start_new_generation()
    assert gen1 == 1
    assert mgr.is_valid(1) is True

    # User interrupts
    new_gen, interrupt_ts = mgr.on_user_interrupt(user_text="Hold on!")
    assert new_gen == 2
    # Old generation must now be invalid (fenced)
    assert mgr.is_valid(1) is False
    assert mgr.is_valid(2) is True
    print("✅ test_generation_fencing passed")


def test_audio_stopped_latency():
    mgr = InterruptionManager()
    mgr.start_new_generation()
    mgr.on_speaking_started(1)
    assert mgr.is_speaking is True

    # Interruption occurs
    mgr.on_user_interrupt("Wait!")
    assert mgr.state.last_interrupted_gen_id == 1

    # Audio ceases
    lat = mgr.on_audio_stopped()
    assert lat is not None
    assert lat >= 0
    assert mgr.is_speaking is False
    print("✅ test_audio_stopped_latency passed")


def test_tool_fencing():
    mgr = InterruptionManager()
    gen1 = mgr.start_new_generation()

    # Tool starts under gen 1
    started = mgr.on_tool_started(gen1, "lookup_topic")
    assert started is True

    # User interrupts during tool execution
    mgr.on_user_interrupt("Never mind, do something else")

    # Tool finishes under gen 1 -> MUST be rejected!
    finished = mgr.on_tool_finished(gen1, "lookup_topic")
    assert finished is False, "Stale tool should have been rejected by fence!"

    summary = mgr.get_summary()
    assert summary["stale_tools_fenced"] == 1
    assert summary["stale_leakage_count"] == 0
    assert summary["leakage_rate_pct"] == 0.0
    print("✅ test_tool_fencing passed")


async def test_simulate_slow_lookup_cancellation():
    cancelled = False

    def is_cancelled():
        return cancelled

    task = asyncio.create_task(
        simulate_slow_lookup("quantum", check_cancelled=is_cancelled, delay_seconds=2.0)
    )

    # Allow tool to run briefly
    await asyncio.sleep(0.3)
    cancelled = True  # simulate user barge-in

    result = await task
    assert "[CANCELLED]" in result
    print("✅ test_simulate_slow_lookup_cancellation passed")


async def main():
    test_generation_fencing()
    test_audio_stopped_latency()
    test_tool_fencing()
    await test_simulate_slow_lookup_cancellation()
    print("\n🎉 ALL INTERRUPTION & FENCING UNIT TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
