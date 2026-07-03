import os
from dotenv import load_dotenv
from dhan_integration import DhanBroker
from backend.app.services.redis_service import redis_service

load_dotenv()

class BrokerService:
    def __init__(self):
        client_id = os.getenv("DHAN_CLIENT_ID")
        access_token = os.getenv("DHAN_ACCESS_TOKEN")
        # Inject redis client for shared caching across microservices
        self.broker = DhanBroker(client_id, access_token, redis_client=redis_service.client)

    def get_broker(self):
        return self.broker

# Singleton instance
broker_service = BrokerService()
