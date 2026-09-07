import React, { useEffect, useRef } from 'react';
import type { TranscriptEntry } from '../types';

interface TranscriptFeedProps {
  transcript: TranscriptEntry[];
}

export const TranscriptFeed: React.FC<TranscriptFeedProps> = ({ transcript }) => {
  const scrollEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [transcript]);

  return (
    <div className="transcript-panel">
      <div className="transcript-top-bar">
        <div className="transcript-title-wrap">
          <span className="transcript-title">Live Transcript</span>
          <span className="transcript-count">{transcript.length} turns</span>
        </div>
        <div className="transcript-indicator">
          <span className="pulse-dot-green" />
          <span>Real-time</span>
        </div>
      </div>

      <div className="transcript-messages">
        {transcript.length === 0 ? (
          <div className="transcript-placeholder">
            <div className="placeholder-icon">💬</div>
            <h3>Conversation will appear here</h3>
            <p>
              Speak naturally using your microphone. StudyBuddy will listen, think, and reply.
            </p>
            <div className="placeholder-suggestion">
              💡 <em>Try interrupting mid-sentence to see instant recovery in action.</em>
            </div>
          </div>
        ) : (
          transcript.map((entry) => {
            const isUser = entry.role === 'user';
            const isInterrupted = entry.interrupted;

            return (
              <div
                key={entry.id}
                className={`message-bubble-wrapper ${isUser ? 'user-align' : 'agent-align'}`}
              >
                <div
                  className={`message-bubble ${isUser ? 'bubble-user' : 'bubble-agent'} ${
                    isInterrupted ? 'bubble-interrupted' : ''
                  }`}
                >
                  <div className="bubble-header">
                    <span className="bubble-author">
                      {isUser ? 'You' : 'StudyBuddy'}
                    </span>
                    {isInterrupted && (
                      <span className="interrupted-tag">⚡ Fenced & Aborted</span>
                    )}
                    {entry.latencyMs && (
                      <span className="latency-tag">{entry.latencyMs}ms</span>
                    )}
                  </div>
                  <div className="bubble-text">{entry.text}</div>
                </div>
              </div>
            );
          })
        )}
        <div ref={scrollEndRef} />
      </div>
    </div>
  );
};
