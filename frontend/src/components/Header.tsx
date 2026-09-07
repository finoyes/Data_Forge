import React from 'react';

interface HeaderProps {
  showTelemetry: boolean;
  onToggleTelemetry: () => void;
  onDisconnect: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  showTelemetry,
  onToggleTelemetry,
  onDisconnect,
}) => {
  return (
    <header className="app-header">
      <div className="header-brand">
        <div className="brand-logo-wrap">
          <span className="brand-emoji">🎓</span>
          <div className="brand-titles">
            <span className="brand-main">VoiceForge</span>
            <span className="brand-sub">StudyBuddy</span>
          </div>
        </div>
        <div className="live-pill">
          <span className="live-dot" />
          <span>Connected</span>
        </div>
      </div>

      <div className="header-meta">
        <span className="engine-tag">Rime TTS · mistv3</span>
        <span className="fencing-tag">Gen-ID Fencing Active</span>
      </div>

      <div className="header-controls">
        <button
          type="button"
          className={`telemetry-toggle-btn ${showTelemetry ? 'active' : ''}`}
          onClick={onToggleTelemetry}
          title="Toggle telemetry dashboard"
        >
          <span className="btn-icon">📊</span>
          <span>Telemetry</span>
        </button>

        <button
          type="button"
          className="disconnect-action-btn"
          onClick={onDisconnect}
          title="End voice session"
        >
          <span>End Session</span>
        </button>
      </div>
    </header>
  );
};
