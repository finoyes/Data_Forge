import React from 'react';
import { BarVisualizer } from '@livekit/components-react';
import type { TrackReferenceOrPlaceholder } from '@livekit/components-react';

interface VoiceCenterpieceProps {
  state: string;
  audioTrack?: TrackReferenceOrPlaceholder;
  isInterrupted: boolean;
  lastTurnLatency?: number | null;
}

export const VoiceCenterpiece: React.FC<VoiceCenterpieceProps> = ({
  state,
  audioTrack,
  isInterrupted,
  lastTurnLatency,
}) => {
  const getStateInfo = () => {
    if (isInterrupted) {
      return {
        emoji: '⚡',
        label: 'Barge-In Detected · Turn Fenced',
        statusClass: 'status-interrupted',
        hint: 'Audio aborted instantly. Stale generation cancelled.',
      };
    }
    switch (state) {
      case 'listening':
        return {
          emoji: '🎧',
          label: 'Listening for Speech',
          statusClass: 'status-listening',
          hint: 'Ask a question or barge-in at any point.',
        };
      case 'thinking':
        return {
          emoji: '✨',
          label: 'Thinking & Routing',
          statusClass: 'status-thinking',
          hint: 'Generating response with Gemini Flash...',
        };
      case 'speaking':
        return {
          emoji: '🔊',
          label: 'Speaking (Rime TTS)',
          statusClass: 'status-speaking',
          hint: 'Interrupt whenever you want to redirect.',
        };
      case 'connecting':
        return {
          emoji: '⏳',
          label: 'Connecting Stream...',
          statusClass: 'status-connecting',
          hint: 'Establishing LiveKit WebRTC channel.',
        };
      default:
        return {
          emoji: '🟢',
          label: 'Ready',
          statusClass: 'status-idle',
          hint: 'Say "Let\'s study biology" or ask any question.',
        };
    }
  };

  const { emoji, label, statusClass, hint } = getStateInfo();

  return (
    <div className={`voice-stage ${statusClass}`}>
      {/* Ambient background glow behind the orb */}
      <div className="stage-ambient-glow" />

      {/* Modern Centerpiece Voice Orb */}
      <div className="orb-container">
        <div className="orb-outer-ring" />
        <div className="orb-core">
          <span className="orb-emoji">{emoji}</span>
        </div>
      </div>

      {/* State Badge */}
      <div className="stage-status-badge">
        <span className="status-dot" />
        <span className="status-label">{label}</span>
      </div>

      {/* LiveKit Waveform Visualizer */}
      <div className="stage-visualizer">
        <BarVisualizer
          state={state as any}
          barCount={32}
          trackRef={audioTrack}
        />
      </div>

      {/* Conversational prompt & latency hint */}
      <div className="stage-footer">
        <p className="stage-hint">{hint}</p>
        {lastTurnLatency !== null && lastTurnLatency !== undefined && (
          <div className="turn-latency-chip">
            <span className="chip-label">Turnaround</span>
            <span className="chip-value">{Math.round(lastTurnLatency)}ms</span>
          </div>
        )}
      </div>
    </div>
  );
};
