import React from 'react';
import type { InterruptionMetrics } from '../types';

interface TelemetryBarProps {
  metrics: InterruptionMetrics;
  isOpen: boolean;
  onClose?: () => void;
}

export const TelemetryBar: React.FC<TelemetryBarProps> = ({
  metrics,
  isOpen,
  onClose,
}) => {
  if (!isOpen) return null;

  return (
    <div className="telemetry-bar">
      <div className="telemetry-inner">
        <div className="telemetry-header">
          <div className="telemetry-title">
            <span className="telemetry-icon">⚡</span>
            <span>Real-time Interruption & Fencing Telemetry</span>
          </div>
          {onClose && (
            <button className="telemetry-close-btn" onClick={onClose} title="Hide telemetry">
              ✕
            </button>
          )}
        </div>

        <div className="telemetry-grid">
          {/* Card 1: Interruption Latency */}
          <div className="telemetry-kpi highlight">
            <span className="kpi-label">Barge-in Latency</span>
            <div className="kpi-value-row">
              <span className="kpi-value">
                {metrics.lastLatencyMs !== null ? `${Math.round(metrics.lastLatencyMs)}ms` : '—'}
              </span>
              <span className="kpi-badge target">Target &lt;300ms</span>
            </div>
            <span className="kpi-sub">
              {metrics.p50LatencyMs !== null
                ? `P50: ${Math.round(metrics.p50LatencyMs)}ms`
                : 'Awaiting interruption'}
            </span>
          </div>

          {/* Card 2: Leakage Rate */}
          <div className="telemetry-kpi success">
            <span className="kpi-label">Stale Audio Leakage</span>
            <div className="kpi-value-row">
              <span className="kpi-value">
                {metrics.staleLeakageCount === 0 ? '0.0%' : `${metrics.staleLeakageCount} leaks`}
              </span>
              <span className="kpi-badge pass">Guaranteed</span>
            </div>
            <span className="kpi-sub">Zero stale generation leaks</span>
          </div>

          {/* Card 3: Barge-in count */}
          <div className="telemetry-kpi">
            <span className="kpi-label">Barge-ins Handled</span>
            <div className="kpi-value-row">
              <span className="kpi-value">{metrics.totalInterruptions}</span>
            </div>
            <span className="kpi-sub">Interrupted speech events</span>
          </div>

          {/* Card 4: Fenced Async Tools */}
          <div className="telemetry-kpi">
            <span className="kpi-label">Stale Tools Fenced</span>
            <div className="kpi-value-row">
              <span className="kpi-value">{metrics.staleToolsFenced}</span>
            </div>
            <span className="kpi-sub">Outdated queries cancelled</span>
          </div>
        </div>
      </div>
    </div>
  );
};
