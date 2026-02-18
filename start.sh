#!/bin/sh
set -e

echo "Starting ComfyUI server..."
python -m comfyui.server --listen 0.0.0.0 --port 8188 &

echo "Waiting for ComfyUI to be ready..."
MAX_WAIT=60
WAIT_TIME=0

# Use Python for health check instead of netcat (more portable)
while ! python -c "import socket; s=socket.socket(); s.connect(('localhost', 8188)); s.close()" 2>/dev/null; do
    if [ $WAIT_TIME -ge $MAX_WAIT ]; then
        echo "Timeout: ComfyUI did not start within ${MAX_WAIT}s"
        exit 1
    fi
    echo "Waiting for ComfyUI... (${WAIT_TIME}/${MAX_WAIT}s)"
    sleep 1
    WAIT_TIME=$((WAIT_TIME + 1))
done

echo "ComfyUI is ready. Starting RunPod handler..."
python /handler.py