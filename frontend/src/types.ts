export interface TranscriptEntry {
  id: string;
  role: 'user' | 'agent' | 'system';
  text: string;
  timestamp: number;
  latencyMs?: number;
  interrupted?: boolean;
}

export interface InterruptionMetrics {
  totalInterruptions: number;
  lastLatencyMs: number | null;
  p50LatencyMs: number | null;
  staleTokensFenced: number;
  staleToolsFenced: number;
  staleLeakageCount: number;
  recoveryCount: number;
}
