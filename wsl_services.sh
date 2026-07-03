#!/bin/bash
# WSL Infrastructure Keeper Script v4 - FIXED
# Starts PostgreSQL + runs Redis in foreground (keeps WSL alive).
# Start from Windows: Start-Process wsl "-d Ubuntu -u root bash /mnt/c/...path.../wsl_services.sh"

echo "[WSL] === Trader Singh Infrastructure Startup v4 ==="

# Fix overcommit for Redis
sysctl vm.overcommit_memory=1 2>/dev/null

# ─── PostgreSQL ──────────────────────────────────────────────────────────
echo "[WSL] Checking PostgreSQL status..."
# Try multiple methods to verify if PG is really running
if pg_isready -h localhost -p 5432 >/dev/null 2>&1 || pgrep -x "postgres" >/dev/null 2>&1; then
    echo "[WSL] PostgreSQL already running. Keeping as-is."
else
    # Only clear stale PID file if PG is NOT running
    echo "[WSL] PostgreSQL not running. Cleaning up stale lock files..."
    rm -f /var/lib/postgresql/16/main/postmaster.pid 2>/dev/null
    echo "[WSL] Starting PostgreSQL..."
    pg_ctlcluster 16 main start 2>&1
fi

echo "[WSL] Waiting for PostgreSQL to accept TCP connections..."
for i in $(seq 1 60); do
    # Use pg_isready to check if PG is accepting connections
    if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
        echo "[WSL] PostgreSQL accepting connections after ${i}s!"
        break
    fi
    sleep 1
    if [ $((i % 10)) -eq 0 ]; then echo "[WSL] Waiting... ${i}s"; fi
done

echo "[WSL] PostgreSQL final status: $(pg_ctlcluster 16 main status 2>&1)"

# ─── Redis ───────────────────────────────────────────────────────────────
echo "[WSL] Stopping any existing Redis..."
pkill -9 redis-server 2>/dev/null
sleep 1

echo "[WSL] Starting Redis in foreground (keeps WSL alive)..."
echo "[WSL] WSL IP: $(hostname -I)"

# exec replaces this shell with redis-server, keeping wsl.exe alive
exec redis-server \
    --bind 0.0.0.0 \
    --protected-mode no \
    --daemonize no \
    --port 6379 \
    --loglevel warning \
    --save "" \
    --appendonly no
