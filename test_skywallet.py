
import os
from dotenv import load_dotenv
from controllers.skywallet_gateway import SkyWalletGatewayClient

load_dotenv()

print("Testing SkyWallet Gateway Client")
print(f"SKYWALLET_GATEWAY_URL: {os.getenv('SKYWALLET_GATEWAY_URL')}")
print(f"SKYWALLET_SERVICE_NAME: {os.getenv('SKYWALLET_SERVICE_NAME')}")
print(f"SKYWALLET_API_KEY: {'*' * len(os.getenv('SKYWALLET_API_KEY', ''))}")
print(f"SKYWALLET_SIGNING_SECRET: {'*' * len(os.getenv('SKYWALLET_SIGNING_SECRET', ''))}")

# Test client initialization
client = SkyWalletGatewayClient()
print("\nClient initialized successfully!")
