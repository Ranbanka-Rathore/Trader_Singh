import os
import time
import datetime
from paper_broker import PaperBroker

class DeadMansSwitch:
    def __init__(self, heartbeat_file="heartbeat.txt", timeout_seconds=120):
        self.heartbeat_file = heartbeat_file
        self.timeout_seconds = timeout_seconds
        self.broker = PaperBroker()

    def run_monitor(self):
        print(f"🛡️ DEAD MAN'S SWITCH ACTIVE. Monitoring {self.heartbeat_file}...")
        
        while True:
            try:
                if not os.path.exists(self.heartbeat_file):
                    # Create it if it doesn't exist to start the timer
                    with open(self.heartbeat_file, "w") as f:
                        f.write(str(time.time()))
                
                # Check last modification time
                last_heartbeat = os.path.getmtime(self.heartbeat_file)
                time_since_last = time.time() - last_heartbeat
                
                if time_since_last > self.timeout_seconds:
                    print(f"🚨 ALERT: Heartbeat missing for {int(time_since_last)}s! Triggering Failover...")
                    closed_count = self.broker.square_off_all_positions(reason="DEAD_MANS_SWITCH_FAILOVER")
                    if closed_count > 0:
                        print(f"✅ Failover Successful: {closed_count} positions closed.")
                    
                    # Reset the heartbeat file to avoid continuous triggering if system is dead
                    # but broker still works (e.g. main loop crashed but this script lives)
                    with open(self.heartbeat_file, "w") as f:
                        f.write(str(time.time()))
                
                # Check for manual PANIC button
                if os.path.exists("PANIC_BUTTON.txt"):
                    print("🚨 PANIC BUTTON DETECTED! Squaring off everything...")
                    self.broker.square_off_all_positions(reason="MANUAL_PANIC_TRIGGERED")
                    os.remove("PANIC_BUTTON.txt") # Reset
                    
            except Exception as e:
                print(f"⚠️ Monitoring Error: {e}")
            
            time.sleep(10) # Check every 10 seconds

if __name__ == "__main__":
    switch = DeadMansSwitch()
    switch.run_monitor()
