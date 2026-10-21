#!/bin/bash

# Django dev server port. Defaults to 8001 because 127.0.0.1:8000 is the
# production Docker stack on the server Mac. Override with SPINE_DEV_PORT.
PORT="${SPINE_DEV_PORT:-8001}"

cd "$(dirname "$0")" || exit 1

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "❌ Port $PORT is already in use. Set SPINE_DEV_PORT to a free port." >&2
    exit 1
fi

echo "🎨 Starting Tailwind CSS watcher..."
npm run build-css &
CSS_PID=$!

echo "🚀 Starting Django development server..."
cd src
uv run --locked python manage.py runserver "127.0.0.1:$PORT" &
DJANGO_PID=$!

echo "✅ Development servers started!"
echo "   - CSS watcher: PID $CSS_PID"
echo "   - Django server: PID $DJANGO_PID"
echo "   - Visit: http://127.0.0.1:$PORT"
echo ""
echo "Press Ctrl+C to stop both servers"

# Function to kill both processes when script is interrupted
cleanup() {
    echo ""
    echo "🛑 Stopping servers..."
    kill $CSS_PID 2>/dev/null
    kill $DJANGO_PID 2>/dev/null
    echo "✅ Servers stopped"
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

# Wait for both processes
wait
