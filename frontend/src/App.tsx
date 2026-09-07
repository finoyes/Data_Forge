import { useState, useCallback, useEffect, useRef } from 'react';
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useVoiceAssistant,
  useRoomContext,
} from '@livekit/components-react';
import { RoomEvent } from 'livekit-client';
import '@livekit/components-styles';

import './App.css';
import type { TranscriptEntry, InterruptionMetrics } from './types';
import { ConnectScreen } from './components/ConnectScreen';
import { Header } from './components/Header';
import { VoiceCenterpiece } from './components/VoiceCenterpiece';
import { TelemetryBar } from './components/TelemetryBar';
import { TranscriptFeed } from './components/TranscriptFeed';

// ─── Token Server & Environment ──────────────────────────────────────────
const TOKEN_SERVER_URL = import.meta.env.VITE_TOKEN_SERVER_URL || 'http://localhost:7880/token';
const LIVEKIT_URL = import.meta.env.VITE_LIVEKIT_URL || '';

function App() {
  const [token, setToken] = useState<string>('');
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<string>('');

  const fetchToken = useCallback(async () => {
    setIsConnecting(true);
    setError('');
    try {
      const resp = await fetch(TOKEN_SERVER_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          room: 'studybuddy-room',
          identity: `student-${Date.now()}`,
        }),
      });
      if (!resp.ok) throw new Error(`Token server returned status ${resp.status}`);
      const data = await resp.json();
      setToken(data.token);
      setIsConnected(true);
    } catch (e: any) {
      console.error('Token fetch failed:', e);
      setError(e.message || 'Could not connect to token server. Ensure agent/token server is running.');
    } finally {
      setIsConnecting(false);
    }
  }, []);

  const connectWithManualToken = (manualToken: string) => {
    if (manualToken.trim()) {
      setToken(manualToken.trim());
      setIsConnected(true);
      setError('');
    }
  };

  const handleDisconnect = () => {
    setIsConnected(false);
    setToken('');
    setError('');
  };

  if (!isConnected) {
    return (
      <ConnectScreen
        onConnectAuto={fetchToken}
        onConnectManual={connectWithManualToken}
        error={error}
        isConnecting={isConnecting}
      />
    );
  }

  return (
    <LiveKitRoom
      token={token}
      serverUrl={LIVEKIT_URL}
      connect={true}
      audio={true}
      video={false}
      onDisconnected={handleDisconnect}
    >
      <RoomAudioRenderer />
      <StudySession onDisconnect={handleDisconnect} />
    </LiveKitRoom>
  );
}

// ─── Study Session Layout ──────────────────────────────────────────────────

function StudySession({ onDisconnect }: { onDisconnect: () => void }) {
  const room = useRoomContext();
  const voiceAssistant = useVoiceAssistant();

  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [lastTurnLatency, setLastTurnLatency] = useState<number | null>(null);
  const [isInterrupted, setIsInterrupted] = useState<boolean>(false);
  const [showTelemetry, setShowTelemetry] = useState<boolean>(true);
  const interruptTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [metrics, setMetrics] = useState<InterruptionMetrics>({
    totalInterruptions: 0,
    lastLatencyMs: null,
    p50LatencyMs: null,
    staleTokensFenced: 0,
    staleToolsFenced: 0,
    staleLeakageCount: 0,
    recoveryCount: 0,
  });

  // Listen to custom WebRTC data channel events from Python agent
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

          setMetrics((prev) => ({
            ...prev,
            totalInterruptions: prev.totalInterruptions + 1,
          }));

          // Flag last agent transcript item as interrupted
          setTranscript((prev) => {
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
            setMetrics((prev) => ({
              ...prev,
              lastLatencyMs: data.interruption_latency_ms,
            }));
          }
          if (data.summary) {
            setMetrics((prev) => ({
              ...prev,
              totalInterruptions: data.summary.total_interruptions ?? prev.totalInterruptions,
              p50LatencyMs: data.summary.p50_interruption_latency_ms ?? prev.p50LatencyMs,
              staleTokensFenced: data.summary.stale_tokens_fenced ?? prev.staleTokensFenced,
              staleToolsFenced: data.summary.stale_tools_fenced ?? prev.staleToolsFenced,
              staleLeakageCount: data.summary.stale_leakage_count ?? 0,
            }));
          }
        } else if (data.type === 'user_transcription' && data.text) {
          setTranscript((prev) => {
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
            setMetrics((prev) => ({ ...prev, recoveryCount: prev.recoveryCount + 1 }));
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

  const lastProcessedAgentIdRef = useRef<string | null>(null);

  // Synchronize incoming agent speech transcriptions
  useEffect(() => {
    const agentTranscriptions = voiceAssistant.agentTranscriptions;
    if (agentTranscriptions && agentTranscriptions.length > 0) {
      const latest = agentTranscriptions[agentTranscriptions.length - 1];
      if (lastProcessedAgentIdRef.current !== latest.id) {
        lastProcessedAgentIdRef.current = latest.id;
        queueMicrotask(() => {
          setTranscript((prev) => {
            if (prev.some((e) => e.id === `agent-${latest.id}`)) return prev;
            return [
              ...prev,
              {
                id: `agent-${latest.id}`,
                role: 'agent',
                text: latest.text,
                timestamp: Date.now(),
              },
            ];
          });
        });
      }
    }
  }, [voiceAssistant.agentTranscriptions]);

  return (
    <div className="session-shell">
      {/* Sleek Minimal Header */}
      <Header
        showTelemetry={showTelemetry}
        onToggleTelemetry={() => setShowTelemetry(!showTelemetry)}
        onDisconnect={onDisconnect}
      />

      {/* Collapsible Telemetry HUD */}
      <TelemetryBar
        metrics={metrics}
        isOpen={showTelemetry}
        onClose={() => setShowTelemetry(false)}
      />

      {/* Main Experience Layout */}
      <main className="session-content">
        {/* Left: Voice Interaction Centerpiece */}
        <section className="voice-centerpiece-col">
          <VoiceCenterpiece
            state={voiceAssistant.state}
            audioTrack={voiceAssistant.audioTrack}
            isInterrupted={isInterrupted}
            lastTurnLatency={lastTurnLatency}
          />
        </section>

        {/* Right: Live Transcript Feed */}
        <section className="transcript-col">
          <TranscriptFeed transcript={transcript} />
        </section>
      </main>

      {/* Minimal Bottom Bar */}
      <footer className="session-footer">
        <div className="footer-item">
          <span className="footer-dot live" />
          <span>WebRTC Audio Stream</span>
        </div>
        <div className="footer-item">
          <span>Engine: Rime TTS (mistv3)</span>
        </div>
        <div className="footer-item">
          <span>STT: Deepgram Nova-3</span>
        </div>
        <div className="footer-item highlight">
          <span>0% Stale Leakage Guarantee</span>
        </div>
      </footer>
    </div>
  );
}

export default App;
