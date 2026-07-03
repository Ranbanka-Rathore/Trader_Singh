import asyncio
import random
import logging
import subprocess
import time
import os
import signal
from typing import List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ChaosMonkey")

class ChaosMonkey:
    """
    V8 Institutional Resilience Tester.
    Randomly kills local system processes to verify 100% state recovery from DB.
    """
    def __init__(self):
        # Targets are now the python script names
        self.targets = ["run_api.py", "run_harvester.py", "run_quant.py", "run_risk_committee.py", "run_oms.py"]
        
    def _get_pid(self, script_name: str) -> List[int]:
        """Finds PIDs for a given script name."""
        try:
            # Use powershell to find python processes with the script name in command line
            cmd = f"Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object {{ $_.CommandLine -match '{script_name}' }} | Select-Object -ExpandProperty ProcessId"
            result = subprocess.run(["powershell", "-Command", cmd], capture_output=True, text=True)
            pids = result.stdout.strip().split()
            return [int(p) for p in pids if p]
        except Exception as e:
            logger.error(f"Error finding PID for {script_name}: {e}")
            return []

    async def kill_random_service(self):
        script = random.choice(self.targets)
        logger.warning(f"🐒 CHAOS MONKEY: Targeting service '{script}'...")
        
        pids = self._get_pid(script)
        if not pids:
            logger.info(f"   ℹ️ {script} is not running. Reviving directly.")
        else:
            for pid in pids:
                logger.warning(f"   💀 Killing process {pid} ({script})...")
                try:
                    os.kill(pid, signal.SIGTERM)
                except:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            logger.info(f"   💀 {script} has been terminated.")
            
        # Wait a bit
        await asyncio.sleep(random.randint(10, 20))
        
        logger.info(f"   ♻️ Reviving {script}...")
        try:
            # Start in background using venv python
            python_path = os.path.join("venv", "Scripts", "python.exe")
            subprocess.Popen([python_path, script], creationflags=subprocess.CREATE_NEW_CONSOLE)
            logger.info(f"   💖 {script} is back online.")
        except Exception as e:
            logger.error(f"   ❌ Failed to revive {script}: {e}")

    async def run_havoc(self, rounds=3):
        logger.info(f"🚀 Starting Chaos Havoc ({rounds} rounds)...")
        for i in range(rounds):
            await self.kill_random_service()
            # Wait between rounds
            await asyncio.sleep(40)
        logger.info("🏁 Chaos Havoc Complete. Check logs for state recovery verification.")

if __name__ == "__main__":
    monkey = ChaosMonkey()
    asyncio.run(monkey.run_havoc())
