"""
identity.py — BNBAgent ERC-8004 on-chain identity registration
Registers LEORIX Edge as a discoverable AI agent on BSC Testnet.
Run once to get your agentId — save it.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def register_agent():
    from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider

    wallet = EVMWalletProvider(
        password=os.getenv("WALLET_PASSWORD"),
        private_key=os.getenv("PRIVATE_KEY"),
    )

    sdk = ERC8004Agent(network="bsc-testnet", wallet_provider=wallet)

    agent_uri = sdk.generate_agent_uri(
        name="leorix-edge",
        description=(
            "AI-powered crypto trading agent using Smart Money Concepts + EMA momentum. "
            "Reads CMC signals, detects BOS/liquidity sweeps/order blocks, "
            "executes on BSC via Trust Wallet AgentKit. Built by LEORIX."
        ),
        endpoints=[
            AgentEndpoint(
                name="A2A",
                endpoint="https://leorix.co.in/leorix-edge",
                version="1.0.0",
            ),
        ],
    )

    print("Registering LEORIX Edge agent on BSC Testnet...")
    print(f"Agent URI: {agent_uri[:80]}...")

    result = sdk.register_agent(agent_uri=agent_uri)
    print(f"\n✅ Agent registered!")
    print(f"   Agent ID : {result['agentId']}")
    print(f"   TX Hash  : {result['transactionHash']}")
    print(f"\nSave these — you'll need the TX hash for the hackathon submission.")
    return result


def get_agent_info(agent_id: int):
    from bnbagent import ERC8004Agent, EVMWalletProvider

    wallet = EVMWalletProvider(
        password=os.getenv("WALLET_PASSWORD"),
        private_key=os.getenv("PRIVATE_KEY"),
    )
    sdk = ERC8004Agent(network="bsc-testnet", wallet_provider=wallet)
    info = sdk.get_agent_info(agent_id=agent_id)
    print(f"Agent #{agent_id}: {info}")
    return info


if __name__ == "__main__":
    import sys
    if "--info" in sys.argv:
        idx = sys.argv.index("--info")
        agent_id = int(sys.argv[idx + 1])
        get_agent_info(agent_id)
    else:
        register_agent()