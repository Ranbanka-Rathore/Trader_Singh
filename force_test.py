import os
from dotenv import load_dotenv
from dhan_integration import DhanBroker

# Load your keys
load_dotenv()
client_id = os.getenv("DHAN_CLIENT_ID")
access_token = os.getenv("DHAN_ACCESS_TOKEN")

print("🔌 Booting Dhan Broker...")
broker = DhanBroker(client_id, access_token)

print("\n🎯 Forcing NIFTY API Call...")
# Force the system to find NIFTY
sec_id = broker.get_equity_security_id("NIFTY")

if sec_id:
    # Force the system to download the Option Chain
    spot, chain = broker.get_clean_option_chain(sec_id, "OPTIDX")
    
    if chain is not None:
        print(f"\n✅ SUCCESS! Downloaded {len(chain)} strikes.")
        print(chain.head(3)) # Show the actual data
    else:
        print("\n❌ FAILED. Look at the error message above.")