#  VoiceForge StudyBuddy

**A voice-native study tutor with sub-300ms barge-in interruption & recovery, powered by Rime TTS.**

> DataForge × Pathway × Rime Hackathon Submission — **Problem 2: Interruption & Recovery in Voice AI**

---

##  The Product & The Challenge

**User:** A student reviewing study material hands-free — commuting, exercising, cooking, or anytime looking at a screen isn't possible.

**Problem:** Natural conversations require real-time interruptions. Students frequently cut in to ask clarification, change topics, or correct themselves. If an AI tutor continues speaking over the student, or bleeds stale background information into the conversation seconds later, the experience collapses.

**Why Rime TTS is essential:** Rime's ultra-low TTFB (Mist v3 / PCM streaming via WebSocket) allows instant speech cancellation and lightning-fast vocal recovery (<1.2s turn turnaround). Without voice, interruption and recovery does not exist.

---

##  Problem 2: Interruption & Recovery Performance

| Metric | Target | Actual | Verdict |
|---|---|---|---|
| **Interruption-to-Silence Latency (P50)** | < 300 ms | **240.5 ms** (P90: 270.8 ms) |  **PASS** |
| **Stale Response Leakage Rate** | 0.0% (0 leaks) | **0.0%** |  **PASS** |
| **Tool Generation Fencing Efficacy** | 100.0% | **100.0%** (3/3 safely discarded) |  **PASS** |
| **Conversational Recovery Time (P50)** | < 1500 ms | **1196.5 ms** |  **PASS** |

See [RIME_EVIDENCE.md](RIME_EVIDENCE.md) for full acceptance testing details and methodology.

---

##  Architecture & Generation Fencing

```
┌────────────────────────────────────────────────────────────────┐
│                    Web Browser (Judge UI)                       │
│  Microphone → WebRTC → LiveKit Room ← WebRTC ← Speaker        │
│  Live Dashboard: Interruption Latency, Stale Leakage, Fencing  │
└──────────────┬────────────────────────────────┬────────────────┘
               │ Audio In                       │ Audio Out
               ▼                                ▲
┌────────────────────────────────────────────────────────────────┐
│               LiveKit Agents (Python Backend)                   │
│                                                                 │
│  ┌───────────┐    ┌────────────────────┐    ┌────────────────┐ │
│  │ Deepgram  │───▶│    Interruption    │───▶│   Rime TTS     │ │
│  │ STT       │    │      Manager       │    │  (Mist v3 /    │ │
│  │ (Nova-3)  │    │ (Generation Fence) │    │   PCM stream)  │ │
│  └───────────┘    └────────────────────┘    └────────────────┘ │
│       │                    ▲                        ▲          │
│       ▼                    │                        │          │
│  ┌──────────────────────────────────────────────────────┐      │
│  │ xAI Grok (Text mode reasoning)                       │      │
│  │ Slow Tool Simulation: simulate_slow_lookup()         │      │
│  │ Cooperatively aborts when generation becomes stale   │      │
│  └──────────────────────────────────────────────────────┘      │
└────────────────────────────────────────────────────────────────┘
```

### Generation ID Fencing Mechanism
1. Every conversational turn increments a monotonic `generation_id`.
2. When user barge-in is detected by STT/VAD while the agent is speaking:
   - `on_user_interrupt()` halts active audio playback (<300ms).
   - `generation_id` increments immediately.
   - Any in-flight LLM tokens or pending slow tool lookups check `is_valid(gen_id)` and are instantly dropped.
3. This guarantees **0.0% stale response leakage** even during complex multi-step reasoning.

---

##  Technology Stack

| Component | Technology | Role |
|---|---|---|
| **Voice Synthesis** | **Rime TTS (`mistv3` / `luna`)** | Primary spoken voice output with ultra-low TTFB |
| **Speech Recognition** | **Deepgram Nova-3** | Streaming STT with real-time barge-in detection |
| **Orchestration** | **LiveKit Agents** | WebRTC room management, audio routing |
| **Reasoning** | **xAI Grok (`grok-2-latest`)** | Spoken-friendly tutoring & quiz evaluation |
| **Voice Activity** | **Silero VAD** | Pre-warmed voice boundary detection |
| **Frontend** | **React + Vite + TypeScript** | Audio visualizer & real-time metrics dashboard |

---

##  Setup & Running Instructions

### Option A: Docker (Recommended for Teammates — Zero Local Setup)

No need to install Python, Node, or dependencies. Simply have Docker Desktop installed:

1. **Configure `.env`**:
   ```bash
   cp .env.example .env
   # Add your LiveKit, Rime, Deepgram, and xAI Grok API keys to .env
   ```

2. **Start all services with Docker Compose**:
   ```bash
   docker compose up --build
   ```
   *Or with standard Docker:*
   ```bash
   docker build -t voiceforge-studybuddy .
   docker run -p 5173:5173 -p 7880:7880 --env-file .env voiceforge-studybuddy
   ```

3. Open **`http://localhost:5173`** in your browser and click **🎙️ Start Studying**.

4. **Run evaluation in Docker**:
   ```bash
   docker compose run --rm studybuddy evaluate --simulate
   ```

---

### Option B: Local Setup (Without Docker)

#### 1. Prerequisites
- Python 3.10+
- Node.js 18+
- API keys configured in `.env` (LiveKit, Rime, Deepgram, xAI Grok)

#### 2. Start Services

```bash
# Terminal 1: Token Server
python agent/token_server.py

# Terminal 2: Voice Agent
cd agent
python main.py dev

# Terminal 3: Frontend Web UI
cd frontend
npm run dev
```

Open `http://localhost:5173` and click **🎙️ Start Studying**.

#### 3. Run Acceptance Test Suite

Evaluate the system against all Problem 2 acceptance criteria:

```bash
# Run 20-scenario simulated benchmark suite
python scripts/evaluate_interruption.py --simulate

# Or evaluate your live session logs
python scripts/evaluate_interruption.py
```

---

##  Project Structure

```
dataforge/
├── agent/
│   ├── main.py                   # LiveKit agent entrypoint & event dispatch
│   ├── interruption_manager.py   # Core: Generation fencing & state machine
│   ├── tutor.py                  # Tutor prompt & cancellable slow lookup tool
│   ├── latency.py                # Interruption & E2E latency instrumentation
│   ├── failure_handler.py        # Graceful degradation logic
│   ├── token_server.py           # LiveKit token server for browser
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx               # UI with real-time interruption dashboard
│   │   ├── App.css               # Styling, visual states, and badges
│   │   └── main.tsx
│   └── package.json
├── scripts/
│   ├── evaluate_interruption.py  # Acceptance test evaluation suite
│   ├── measure_latency.py        # General latency measurement
│   └── verify_rime_catalog.py    # Rime catalog verification
├── RIME_EVIDENCE.md              # Hard Voice Problem evidence & claims
└── README.md
```

---

##  License

Built for the DataForge × Pathway × Rime Hackathon.
