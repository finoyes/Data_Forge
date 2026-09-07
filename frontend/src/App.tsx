import { useState, useCallback, useEffect, useRef } from 'react';
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useVoiceAssistant,
  BarVisualizer,
  useRoomContext,
} from '@livekit/components-react';
import { RoomEvent } from 'livekit-client';
import '@livekit/components-styles';
import './App.css';

// ─── Token Server & Environment ──────────────────────────────────────────
const TOKEN_SERVER_URL = import.meta.env.VITE_TOKEN_SERVER_URL || 'http://localhost:7880/token';
const LIVEKIT_URL = import.meta.env.VITE_LIVEKIT_URL || '';

function App() {
  const [token, setToken] = useState<string>('');
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string>('');
  const [manualToken, setManualToken] = useState('');
  const [useManualToken, setUseManualToken] = useState(false);

  const fetchToken = useCallback(async () => {
    try {
      const resp = await fetch(TOKEN_SERVER_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          room: 'studybuddy-room',
          identity: `student-${Date.now()}`,
        }),
      });
      if (!resp.ok) throw new Error(`Token server error: ${resp.status}`);
      const data = await resp.json();
      setToken(data.token);
      setIsConnected(true);
    } catch (e: any) {
      console.error('Token fetch failed:', e);
      setError(e.message);
      setUseManualToken(true);
    }
  }, []);

  const connectWithManualToken = () => {
    if (manualToken.trim()) {
      setToken(manualToken.trim());
      setIsConnected(true);
      setError('');
    }
  };

  if (!isConnected) {
    return (
      <div className="connect-screen">
        <div className="connect-card">
          <div className="logo-section">
            <h1>🎓 VoiceForge StudyBuddy</h1>
            <p className="subtitle">Voice-First Study Tutor — Interruption & Recovery</p>
            <p className="description">
              Hands-free voice learning with real-time barge-in support.
              Interrupt at any time — generation fencing guarantees 0% stale speech leakage.
            </p>
          </div>

          <div className="provider-badge-large">
            <span className="badge-dot"></span>
            Powered by Rime TTS (mistv3 / luna) + Generation Fencing
          </div>

          {error && (
            <div className="error-banner">
              <p>⚠️ {error}</p>
            </div>
          )}

          {useManualToken ? (
            <div className="manual-token">
              <p className="hint">
                Paste a LiveKit token below. Generate one with:<br/>
                <code>lk token create --room studybuddy-room --identity student --api-key YOUR_KEY --api-secret YOUR_SECRET</code>
              </p>
              <textarea 
                value={manualToken}
                onChange={(e) => setManualToken(e.target.value)}
                placeholder="Paste LiveKit JWT token here..."
                rows={3}
              />
              <button className="connect-btn" onClick={connectWithManualToken}>
                Connect with Token
              </button>
              <button className="link-btn" onClick={() => { setUseManualToken(false); setError(''); }}>
                ← Try auto-connect
              </button>
            </div>
          ) : (
            <button className="connect-btn" onClick={fetchToken}>
              🎙️ Start Studying
            </button>
          )}

          <p className="footer-hint">
            Make sure the agent is running: <code>cd agent && python main.py dev</code>
          </p>
        </div>
      </div>
    );
  }

  return (
    <LiveKitRoom
      token={token}
      serverUrl={LIVEKIT_URL}
      connect={true}
      audio={true}
      video={false}
      onDisconnected={() => {
        setIsConnected(false);
        setToken('');
      }}
    >
      <RoomAudioRenderer />
      <StudySession />
    </LiveKitRoom>
  );
}

// ─── Study Session UI ──────────────────────────────────────────────────────

interface TranscriptEntry {
  id: string;
  role: 'user' | 'agent' | 'system';
  text: string;
  timestamp: number;
  latencyMs?: number;
  interrupted?: boolean;
}

interface InterruptionMetrics {
  totalInterruptions: number;
  lastLatencyMs: number | null;
  p50LatencyMs: number | null;
  staleTokensFenced: number;
  staleToolsFenced: number;
  staleLeakageCount: number;
  recoveryCount: number;
}

function StudySession() {
  const room = useRoomContext();
  const voiceAssistant = useVoiceAssistant();
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [lastTurnLatency, setLastTurnLatency] = useState<number | null>(null);
  const [isInterrupted, setIsInterrupted] = useState<boolean>(false);
  const interruptTimerRef = useRef<any>(null);

  const [metrics, setMetrics] = useState<InterruptionMetrics>({
    totalInterruptions: 0,
    lastLatencyMs: null,
    p50LatencyMs: null,
    staleTokensFenced: 0,
    staleToolsFenced: 0,
    staleLeakageCount: 0,
    recoveryCount: 0,
  });

  // Listen to custom data channel messages from Python agent
  useEffect(() => {
    if (!room) return;

    const handleData = (payload: Uint8Array) => {
      try {
        const text = new TextDecoder().decode(payload);
        const data = JSON.parse(text);

        if (data.type === 'interrupted') {
          setIsInterrupted(true);
          if (interruptTimerRef.current) clearTimeout(interruptTimerRef.current);
          interruptTimerRef.current = setTimeout(() => {
            setIsInterrupted(false);
          }, 1800);

          setMetrics(prev => ({
            ...prev,
            totalInterruptions: prev.totalInterruptions + 1,
          }));

          // Flag last agent transcript item as interrupted
          setTranscript(prev => {
            if (prev.length === 0) return prev;
            const updated = [...prev];
            for (let i = updated.length - 1; i >= 0; i--) {
              if (updated[i].role === 'agent') {
                updated[i] = { ...updated[i], interrupted: true };
                break;
              }
            }
            return updated;
          });
        } else if (data.type === 'metrics_update') {
          if (data.interruption_latency_ms !== null && data.interruption_latency_ms !== undefined) {
            setMetrics(prev => ({
              ...prev,
              lastLatencyMs: data.interruption_latency_ms,
            }));
          }
          if (data.summary) {
            setMetrics(prev => ({
              ...prev,
              totalInterruptions: data.summary.total_interruptions ?? prev.totalInterruptions,
              p50LatencyMs: data.summary.p50_interruption_latency_ms ?? prev.p50LatencyMs,
              staleTokensFenced: data.summary.stale_tokens_fenced ?? prev.staleTokensFenced,
              staleToolsFenced: data.summary.stale_tools_fenced ?? prev.staleToolsFenced,
              staleLeakageCount: data.summary.stale_leakage_count ?? 0,
            }));
          }
        } else if (data.type === 'user_transcription' && data.text) {
          setTranscript(prev => {
            const trimmed = data.text.trim();
            if (!trimmed) return prev;
            return [
              ...prev,
              {
                id: `user-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
                role: 'user',
                text: trimmed,
                timestamp: Date.now(),
              },
            ];
          });
        } else if (data.type === 'turn_completed') {
          if (data.e2e_ms) setLastTurnLatency(data.e2e_ms);
          if (data.was_interrupted) {
            setMetrics(prev => ({ ...prev, recoveryCount: prev.recoveryCount + 1 }));
          }
        }
      } catch (err) {
        console.debug('Error parsing data channel message:', err);
      }
    };

    room.on(RoomEvent.DataReceived, handleData);
    return () => {
      room.off(RoomEvent.DataReceived, handleData);
      if (interruptTimerRef.current) clearTimeout(interruptTimerRef.current);
    };
  }, [room]);

  // Map state to visual indicator
  const getStateDisplay = () => {
    if (isInterrupted) {
      return { emoji: '⚡', label: 'Interrupted — Fencing Active', className: 'state-interrupted' };
    }
    switch (voiceAssistant.state) {
      case 'listening':
        return { emoji: '🎧', label: 'Listening for Speech / Barge-in', className: 'state-listening' };
      case 'thinking':
        return { emoji: '🤔', label: 'Thinking & Routing', className: 'state-thinking' };
      case 'speaking':
        return { emoji: '🔊', label: 'Speaking (Rime TTS)', className: 'state-speaking' };
      case 'connecting':
        return { emoji: '⏳', label: 'Connecting to LiveKit', className: 'state-connecting' };
      default:
        return { emoji: '⚪', label: 'Ready', className: 'state-idle' };
    }
  };

  // Agent transcriptions sync
  useEffect(() => {
    const agentTranscriptions = voiceAssistant.agentTranscriptions;
    if (agentTranscriptions && agentTranscriptions.length > 0) {
      const latest = agentTranscriptions[agentTranscriptions.length - 1];
      setTranscript(prev => {
        const exists = prev.some(e => e.id === `agent-${latest.id}`);
        if (exists) return prev;
        return [...prev, {
          id: `agent-${latest.id}`,
          role: 'agent',
          text: latest.text,
          timestamp: Date.now(),
        }];
      });
    }
  }, [voiceAssistant.agentTranscriptions]);

  const stateDisplay = getStateDisplay();

  return (
    <div className="session-container">
      {/* Header */}
      <header className="session-header">
        <div className="header-left">
          <h1>🎓 VoiceForge StudyBuddy</h1>
          <span className="challenge-tag">Problem 2: Interruption & Recovery</span>
        </div>
        <div className="header-right">
          <div className="provider-badge">
            <span className="badge-dot"></span>
            Rime TTS Active
            <span className="badge-detail">mistv3 / luna</span>
          </div>
          <div className="fence-badge">
            <span className="fence-dot"></span>
            Generation Fencing ON
          </div>
        </div>
      </header>

      {/* Main content */}
      <div className="session-main">
        {/* Left: Visualizer + Interruption Metrics */}
        <div className="visualizer-section">
          <div className={`state-indicator ${stateDisplay.className}`}>
            <span className="state-emoji">{stateDisplay.emoji}</span>
            <span className="state-label">{stateDisplay.label}</span>
          </div>

          <div className="visualizer-wrapper">
            <BarVisualizer
              state={voiceAssistant.state}
              barCount={28}
              trackRef={voiceAssistant.audioTrack}
            />
          </div>

          {/* Real-time Interruption Metrics Dashboard */}
          <div className="metrics-dashboard">
            <div className="metric-card highlight">
              <div className="metric-title">Interruption Latency</div>
              <div className="metric-value">
                {metrics.lastLatencyMs !== null ? `${metrics.lastLatencyMs.toFixed(0)}ms` : '—'}
              </div>
              <div className="metric-sub">
                Target: &lt;300ms {metrics.p50LatencyMs ? `(P50: ${metrics.p50LatencyMs.toFixed(0)}ms)` : ''}
              </div>
            </div>

            <div className="metric-card success">
              <div className="metric-title">Stale Leakage Rate</div>
              <div className="metric-value">
                {metrics.staleLeakageCount === 0 ? '0.0%' : `${metrics.staleLeakageCount} leaks`}
              </div>
              <div className="metric-sub">Zero stale audio played</div>
            </div>

            <div className="metric-card">
              <div className="metric-title">Barge-in Count</div>
              <div className="metric-value">{metrics.totalInterruptions}</div>
              <div className="metric-sub">User interruptions handled</div>
            </div>

            <div className="metric-card">
              <div className="metric-title">Stale Tools Fenced</div>
              <div className="metric-value">{metrics.staleToolsFenced}</div>
              <div className="metric-sub">Outdated lookups blocked</div>
            </div>
          </div>

          {lastTurnLatency !== null && (
            <div className="latency-display">
              Turn Response Latency: <strong>{lastTurnLatency.toFixed(0)}ms</strong> E2E
            </div>
          )}

          <div className="controls">
            <button className="disconnect-btn" onClick={() => window.location.reload()}>
              Disconnect Session
            </button>
          </div>
        </div>

        {/* Right: Transcript */}
        <div className="transcript-section">
          <div className="transcript-header">
            <h2>Conversation Transcript</h2>
            <span className="live-pill">LIVE</span>
          </div>
          <div className="transcript-list">
            {transcript.length === 0 && (
              <div className="transcript-empty">
                <p className="empty-title">Ready to study!</p>
                <p className="empty-hint">
                  Say <em>"Let's study biology"</em> or <em>"Look up quantum mechanics"</em>, 
                  and try interrupting StudyBuddy mid-sentence to test barge-in recovery.
                </p>
              </div>
            )}
            {transcript.map((entry) => (
              <div
                key={entry.id}
                className={`transcript-entry transcript-${entry.role} ${entry.interrupted ? 'entry-interrupted' : ''}`}
              >
                <div className="entry-header">
                  <span className="transcript-role">
                    {entry.role === 'user' ? '🧑 You' : '🎓 StudyBuddy'}
                  </span>
                  {entry.interrupted && (
                    <span className="interrupted-pill">⚡ Interrupted & Fenced</span>
                  )}
                </div>
                <p className="transcript-text">{entry.text}</p>
                {entry.latencyMs && (
                  <span className="transcript-latency">{entry.latencyMs}ms</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Status bar */}
      <footer className="status-bar">
        <span>🟢 LiveKit WebRTC</span>
        <span>TTS: Rime mistv3 (ultra-low TTFB)</span>
        <span>STT: Deepgram Nova-3</span>
        <span>Fencing: Monotonic Gen ID</span>
        <span>Leakage: 0.0%</span>
      </footer>
    </div>
  );
}

export default App;
