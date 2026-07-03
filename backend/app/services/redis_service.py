import os
import json
import subprocess
import asyncio
import logging
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("RedisService")


def _get_wsl_ip() -> str:
    """
    Auto-discovers the WSL2 virtual IP address.
    WSL2 runs on a dynamic virtual NIC — `localhost` only works if portproxy is set up.
    This fallback reads the WSL IP directly so we can connect even without admin portproxy.
    """
    try:
        result = subprocess.run(
            ["wsl", "-d", "Ubuntu", "hostname", "-I"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            ip = result.stdout.strip().split()[0]
            if ip and ip != "127.0.0.1":
                return ip
    except Exception:
        pass
    return None


def _resolve_redis_host() -> str:
    """
    Resolve the Redis host, trying localhost first, then auto-detecting WSL2 IP.
    The REDIS_HOST env var always wins if explicitly set.
    """
    env_host = os.getenv("REDIS_HOST", "")
    if env_host and env_host != "localhost" and env_host != "127.0.0.1":
        # Explicit non-default set in .env, trust it
        return env_host
    
    # Check if localhost:6379 is actually reachable (portproxy may be set up)
    import socket
    port = int(os.getenv("REDIS_PORT", 6379))
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        s.close()
        return "127.0.0.1"  # localhost works, great
    except (ConnectionRefusedError, OSError):
        pass
    
    # Localhost failed — try WSL2 direct IP
    wsl_ip = _get_wsl_ip()
    if wsl_ip:
        logger.info(f"[REDIS] Not on localhost -- using WSL2 IP: {wsl_ip}")
        return wsl_ip
    
    logger.warning("[REDIS] Could not resolve host. Defaulting to localhost.")
    return "127.0.0.1"


REDIS_HOST = _resolve_redis_host()
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

logger.info(f"[REDIS] Connecting to {REDIS_HOST}:{REDIS_PORT}")


class RedisService:
    def __init__(self):
        self.client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
            socket_connect_timeout=2,  # Must be < asyncio.wait_for timeout (3s)
            socket_timeout=2,          # Must be < asyncio.wait_for timeout (3s)
            retry_on_timeout=False,    # Don't retry — let asyncio.wait_for handle retries
        )

    async def ping(self) -> bool:
        """Check if Redis is reachable."""
        try:
            return await self.client.ping()
        except Exception:
            return False

    async def set_json(self, key: str, value, expire: int = None):
        await self.client.set(key, json.dumps(value), ex=expire)

    async def get_json(self, key: str):
        data = await self.client.get(key)
        return json.loads(data) if data else None

    async def set(self, key: str, value: str, expire: int = None):
        await self.client.set(key, value, ex=expire)

    async def get(self, key: str) -> str:
        return await self.client.get(key)

    async def publish(self, channel: str, message: dict):
        await self.client.publish(channel, json.dumps(message))

    async def subscribe(self, channel: str):
        pubsub = self.client.pubsub()
        await pubsub.subscribe(channel)
        return pubsub


# Singleton instance
redis_service = RedisService()
