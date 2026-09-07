import React, { useState } from 'react';

interface ConnectScreenProps {
  onConnectAuto: () => void;
  onConnectManual: (token: string) => void;
  error?: string;
  isConnecting?: boolean;
}

export const ConnectScreen: React.FC<ConnectScreenProps> = ({
  onConnectAuto,
  onConnectManual,
  error,
  isConnecting = false,
}) => {
  const [manualToken, setManualToken] = useState('');
  const [showManualInput, setShowManualInput] = useState(false);

  const handleManualSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (manualToken.trim()) {
      onConnectManual(manualToken.trim());
    }
  };

  return (
    <div className="connect-screen">
      <div className="connect-glow" />
      <div className="connect-card">
        {/* Clean Logo & Title */}
        <div className="hero-brand">
          <h1 className="hero-title">🎓 VoiceForge StudyBuddy</h1>
          <p className="hero-subtitle">
            Voice-first study tutor with instant barge-in
          </p>
        </div>

        {/* Error notification if auto-connect fails */}
        {error && (
          <div className="connect-error">
            <span className="error-icon">⚠️</span>
            <div className="error-text">
              <strong>Connection Error</strong>
              <p>{error}</p>
            </div>
          </div>
        )}

        {/* Primary Action Button */}
        <div className="action-section">
          <button
            className="primary-start-btn"
            onClick={onConnectAuto}
            disabled={isConnecting}
          >
            {isConnecting ? (
              <>
                <span className="spinner" />
                <span>Connecting...</span>
              </>
            ) : (
              <>
                <span className="mic-icon">🎙️</span>
                <span>Start Studying</span>
              </>
            )}
          </button>
        </div>

        {/* Subtle manual token fallback — only if user wants it or if error occurs */}
        <div className="manual-token-fallback">
          {!showManualInput ? (
            <button
              type="button"
              className="text-link-btn"
              onClick={() => setShowManualInput(true)}
            >
              Enter token manually
            </button>
          ) : (
            <form onSubmit={handleManualSubmit} className="manual-token-form">
              <textarea
                value={manualToken}
                onChange={(e) => setManualToken(e.target.value)}
                placeholder="Paste LiveKit token here..."
                rows={3}
                className="token-textarea"
                autoFocus
              />
              <div className="manual-token-actions">
                <button
                  type="submit"
                  className="secondary-connect-btn"
                  disabled={!manualToken.trim()}
                >
                  Connect with Token
                </button>
                <button
                  type="button"
                  className="text-link-btn"
                  onClick={() => setShowManualInput(false)}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
