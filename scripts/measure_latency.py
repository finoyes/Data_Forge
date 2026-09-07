"""
measure_latency.py — Acceptance test: measure end-to-end voice pipeline latency.

Reads the structured JSONL log produced by the agent's LatencyTracker and
computes P50/P90 latency numbers for:
  - All turns
  - Cold runs (first turn) vs warm runs (subsequent turns)
  - Stress cases (turns that triggered slow tool calls)

Usage:
    python scripts/measure_latency.py [--log-path path/to/latency_log.jsonl]

The claim to prove:
    "P50 time-to-first-audible-response is under 1200ms across 20 representative
     user turns, measured end-to-end including STT + LLM + Rime TTS + playback."
"""

import argparse
import json
import statistics
import sys
from pathlib import Path


DEFAULT_LOG_PATH = Path(__file__).parent.parent / "latency_log.jsonl"


def load_metrics(log_path: Path) -> list[dict]:
    """Load turn metrics from the JSONL log file."""
    if not log_path.exists():
        print(f"❌ Log file not found: {log_path}")
        print("   Run the agent first to generate latency data.")
        sys.exit(1)

    metrics = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    metrics.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"   Warning: skipping malformed line {line_num}: {e}")

    if not metrics:
        print(f"❌ No metrics found in {log_path}")
        print("   Have a conversation with the agent first.")
        sys.exit(1)

    return metrics


def compute_percentile(values: list[float], percentile: int) -> float:
    """Compute the given percentile of a list of values."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    k = (len(sorted_values) - 1) * (percentile / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_values):
        return sorted_values[f]
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


def report_latencies(label: str, latencies: list[float]) -> None:
    """Print a formatted latency report for a set of turns."""
    if not latencies:
        print(f"  {label}: No data")
        return

    p50 = compute_percentile(latencies, 50)
    p90 = compute_percentile(latencies, 90)
    avg = statistics.mean(latencies)
    mn = min(latencies)
    mx = max(latencies)

    print(f"\n  {label} ({len(latencies)} turns):")
    print(f"    P50:  {p50:>8.1f} ms")
    print(f"    P90:  {p90:>8.1f} ms")
    print(f"    Avg:  {avg:>8.1f} ms")
    print(f"    Min:  {mn:>8.1f} ms")
    print(f"    Max:  {mx:>8.1f} ms")

    return p50


def main():
    parser = argparse.ArgumentParser(
        description="Measure end-to-end voice pipeline latency from logged metrics."
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the latency_log.jsonl file",
    )
    args = parser.parse_args()

    metrics = load_metrics(args.log_path)

    print("=" * 65)
    print("VoiceForge StudyBuddy — Latency Measurement Report")
    print("=" * 65)
    print(f"\nLog file: {args.log_path}")
    print(f"Total turns logged: {len(metrics)}")

    # ─── Separate turns by category ──────────────────────────────────────

    all_e2e = []
    cold_e2e = []
    warm_e2e = []
    stress_e2e = []
    normal_e2e = []

    for m in metrics:
        e2e = m.get("e2e_latency_ms")
        if e2e is None:
            continue
        all_e2e.append(e2e)
        if m.get("is_cold_run"):
            cold_e2e.append(e2e)
        else:
            warm_e2e.append(e2e)
        if m.get("is_stress_case"):
            stress_e2e.append(e2e)
        else:
            normal_e2e.append(e2e)

    # ─── Report ──────────────────────────────────────────────────────────

    print(f"\nTurns with valid E2E latency: {len(all_e2e)}/{len(metrics)}")

    p50_all = report_latencies("All Turns", all_e2e)
    report_latencies("Cold Runs (first turn)", cold_e2e)
    report_latencies("Warm Runs (subsequent turns)", warm_e2e)
    report_latencies("Normal Turns (no tool calls)", normal_e2e)
    report_latencies("Stress Cases (with slow tool call)", stress_e2e)

    # ─── Breakdown by component ──────────────────────────────────────────

    stt_to_llm = [m["stt_to_llm_ms"] for m in metrics if m.get("stt_to_llm_ms")]
    llm_to_tts = [m["llm_to_tts_ms"] for m in metrics if m.get("llm_to_tts_ms")]

    if stt_to_llm or llm_to_tts:
        print("\n  Component Breakdown (median):")
        if stt_to_llm:
            print(f"    STT → LLM first token:  {compute_percentile(stt_to_llm, 50):>8.1f} ms")
        if llm_to_tts:
            print(f"    LLM → TTS first byte:   {compute_percentile(llm_to_tts, 50):>8.1f} ms")

    # ─── Provider verification ───────────────────────────────────────────

    providers = set(m.get("tts_provider", "unknown") for m in metrics)
    print(f"\n  TTS Providers Used: {', '.join(providers)}")
    rime_count = sum(1 for m in metrics if m.get("tts_provider") == "rime")
    print(f"  Rime-synthesized turns: {rime_count}/{len(metrics)} ({100*rime_count//max(len(metrics),1)}%)")

    # ─── Per-turn detail ─────────────────────────────────────────────────

    print("\n" + "-" * 65)
    print("  Per-Turn Detail:")
    print(f"  {'Turn':>4}  {'E2E (ms)':>10}  {'Cold?':>5}  {'Stress?':>7}  {'Provider':>8}  User Text")
    print("  " + "-" * 60)
    for m in metrics:
        e2e = m.get("e2e_latency_ms", "N/A")
        e2e_str = f"{e2e:>10.1f}" if isinstance(e2e, (int, float)) else f"{e2e:>10}"
        cold = "Yes" if m.get("is_cold_run") else ""
        stress = "Yes" if m.get("is_stress_case") else ""
        provider = m.get("tts_provider", "?")
        user_text = m.get("user_text", "")[:35]
        print(f"  {m['turn_index']:>4}  {e2e_str}  {cold:>5}  {stress:>7}  {provider:>8}  {user_text}")

    # ─── Acceptance test verdict ─────────────────────────────────────────

    CLAIM_P50_MS = 1200
    CLAIM_MIN_TURNS = 20

    print("\n" + "=" * 65)
    print("  ACCEPTANCE TEST")
    print(f"  Claim: P50 E2E latency < {CLAIM_P50_MS}ms across {CLAIM_MIN_TURNS}+ turns")
    print()

    if len(all_e2e) < CLAIM_MIN_TURNS:
        print(f"  ⚠️  INSUFFICIENT DATA: {len(all_e2e)} turns < {CLAIM_MIN_TURNS} required")
        print(f"     Run more conversations to collect enough data.")
    elif p50_all is not None and p50_all < CLAIM_P50_MS:
        print(f"  ✅ PASS: P50 = {p50_all:.1f}ms < {CLAIM_P50_MS}ms")
    else:
        print(f"  ❌ FAIL: P50 = {p50_all:.1f}ms >= {CLAIM_P50_MS}ms")

    if rime_count < len(metrics):
        non_rime = len(metrics) - rime_count
        print(f"  ⚠️  {non_rime} turn(s) used a non-Rime TTS provider")

    print("=" * 65)


if __name__ == "__main__":
    main()
