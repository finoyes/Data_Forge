#!/bin/bash
set -e

# VoiceForge StudyBuddy — Docker Entrypoint

cleanup() {
    echo ""
    echo "Stopping all services..."
    kill $(jobs -p) 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

MODE="${1:-all}"

case "$MODE" in
    token-server)
        echo "=== Starting Token Server only (port 7880) ==="
        exec python agent/token_server.py
        ;;
    frontend)
        echo "=== Starting Frontend only (port 5173) ==="
        cd frontend
        exec npm run dev
        ;;
    agent)
        echo "=== Starting Voice Agent only ==="
        cd agent
        exec python main.py dev
        ;;
    evaluate)
        shift
        echo "=== Running Acceptance Evaluation Suite ==="
        exec python scripts/evaluate_interruption.py "$@"
        ;;
    test)
        echo "=== Running Unit Tests ==="
        exec python scripts/test_interruption_manager.py
        ;;
    all|*)
        echo "=========================================================="
        echo " 🎓 VoiceForge StudyBuddy — Container Starting"
        echo " Problem 2: Interruption & Recovery in Voice AI"
        echo "=========================================================="

        # 1. Start Token Server in background
        echo "[1/3] Starting Token Server on http://0.0.0.0:7880..."
        python agent/token_server.py &
        TOKEN_PID=$!

        # 2. Start Frontend UI in background
        echo "[2/3] Starting Web Frontend on http://0.0.0.0:5173..."
        (cd frontend && npm run dev) &
        FRONTEND_PID=$!

        sleep 2

        # 3. Check LiveKit credentials & start Voice Agent
        if [ -z "$LIVEKIT_API_KEY" ] || [ "$LIVEKIT_API_KEY" = "your_livekit_api_key" ]; then
            echo ""
            echo "⚠️ [NOTICE] LIVEKIT_API_KEY is not configured or using placeholder."
            echo "   Token server and web frontend are active at:"
            echo "     - Web UI:       http://localhost:5173"
            echo "     - Token Server: http://localhost:7880/token"
            echo "   Pass your real .env file to enable the agent worker:"
            echo "     docker run -p 5173:5173 -p 7880:7880 --env-file .env voiceforge-studybuddy"
            echo ""
            wait $TOKEN_PID $FRONTEND_PID
        else
            echo "[3/3] Starting Voice Agent Worker (connecting to LiveKit)..."
            cd agent
            python -m pip install --no-cache-dir -q -r requirements.txt 2>/dev/null || true
            python main.py dev
        fi
        ;;
esac
