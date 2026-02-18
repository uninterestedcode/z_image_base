#!/bin/sh
set -e

echo "=== START.SH BEGIN ==="
echo "Starting ComfyUI server..."
cd /comfyui
echo "Changed directory to /comfyui"
python main.py --listen 0.0.0.0 --port 8188 &
echo "ComfyUI started in background (PID: $!)"

echo "Waiting for ComfyUI to be ready..."
MAX_WAIT=60
WAIT_TIME=0

# Use Python for health check instead of netcat (more portable)
echo "Starting health check for port 8188..."
while ! python -c "import socket; s=socket.socket(); s.connect(('localhost', 8188)); s.close()" 2>/dev/null; do
    if [ $WAIT_TIME -ge $MAX_WAIT ]; then
        echo "=== ERROR: TIMEOUT ==="
        echo "Timeout: ComfyUI did not start within ${MAX_WAIT}s"
        echo "Checking if process is still running..."
        ps aux || echo "Process check failed"
        exit 1
    fi
    echo "Waiting for ComfyUI... (${WAIT_TIME}/${MAX_WAIT}s)"
    sleep 1
    WAIT_TIME=$((WAIT_TIME + 1))
done

echo "=== COMFYUI READY ==="
echo "ComfyUI is ready. Starting RunPod handler..."
echo "About to execute: python -u /handler.py (unbuffered)"
python -u /handler.py 2>&1
EXIT_CODE=$?
echo "=== HANDLER EXITED (code: $EXIT_CODE) ==="