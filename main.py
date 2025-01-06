import asyncio
from solana.rpc.async_api import AsyncClient
from telegram.ext import Application, CommandHandler
import json
import logging
from datetime import datetime
import os
from solana.publickey import PublicKey
import httpx
import cachetools.func

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
TELEGRAM_BOT_TOKEN = "7643287966:AAHsQEn2r29Wplh3IhRKoC1f25jNezl8nwM"
CREATOR_WALLET = PublicKey("E88QpPXQFjyKmVK7kj5NjkAPjLTYnYoY2Dd6Po7WUJjg")
USERS_FILE = "subscribed_users.json"
TOKEN_CREATION_FILE = "token_creations.json"
cache = cachetools.func.TTLCache(maxsize=1000, ttl=600)


def load_token_creations():
    if os.path.exists(TOKEN_CREATION_FILE):
        try:
            with open(TOKEN_CREATION_FILE, "r") as f:
                return json.load(f)
        except:
            return []
    return []


def save_token_creation(token_data):
    tokens = load_token_creations()
    tokens.append(token_data)
    with open(TOKEN_CREATION_FILE, "w") as f:
        json.dump(tokens, f, indent=4)


async def get_transaction_details(signature):
    await asyncio.sleep(0.3)  # Add a delay before retrying

    if signature in cache:
        return cache[signature]
    payload_transaction_details = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
        ],
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            SOLANA_RPC_URL,
            json=payload_transaction_details,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code == 200:
            data = response.json()
            cache[signature] = data  # Store the data in cache
            return data


def is_new_token(signature):
    tokens = load_token_creations()
    return not any(token["signature"] == signature for token in tokens)


class TokenCreationTracker:
    def __init__(self):
        print("Initializing TokenCreationTracker")
        self.client = AsyncClient(SOLANA_RPC_URL)
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.last_signature = None
        self.subscribed_users = self.load_subscribed_users()
        self.processed_signatures = set()

        # Add command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))

    def load_subscribed_users(self):
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE, "r") as f:
                    return json.load(f)
            except:
                return []
        return []

    def save_subscribed_users(self):
        with open(USERS_FILE, "w") as f:
            json.dump(self.subscribed_users, f)

    async def start_command(self, update, context):
        chat_id = str(update.effective_chat.id)
        username = update.effective_user.username or "Unknown"

        await update.message.reply_text(f"Hello {username}! 👋")

        if not any(user["chat_id"] == chat_id for user in self.subscribed_users):
            user_data = {
                "chat_id": chat_id,
                "username": username,
                "subscribed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.subscribed_users.append(user_data)
            self.save_subscribed_users()
            await update.message.reply_text(
                "You have been subscribed to token creation notifications!"
            )
        else:
            await update.message.reply_text(
                "You are already subscribed to notifications!"
            )

    async def get_token_creation_details(self, signature):
        try:
            print(f"Getting token creation details for signature: {signature}")
            tx = await get_transaction_details(signature)
            print(f"Transaction details: {tx}")
            print("----------------------------------------------------😂😂")
            if tx and tx.get("result"):
                result = tx["result"]
                if result.get("meta") and result["meta"].get("innerInstructions"):
                    for inner_instructions in result["meta"]["innerInstructions"]:
                        for ix in inner_instructions["instructions"]:
                            if ix.get("programId") == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                                if "parsed" in ix and ix["parsed"].get("type") == "initializeMint":
                                    token_address = ix["parsed"]["info"]["mint"]
                                    return {
                                        "signature": signature,
                                        "token_address": token_address,
                                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    }
        except Exception as e:
            logger.error(f"Error getting token creation details: {e}")
        return None

    async def notify_subscribers(self, token_data):
        message = (
            f"🔔 New Token Created!\n\n"
            f"Token Address: {token_data['token_address']}\n"
            f"Transaction: https://solscan.io/tx/{token_data['signature']}\n"
            f"Time: {token_data['timestamp']}"
        )
        
        for user in self.subscribed_users:
            try:
                await self.application.bot.send_message(
                    chat_id=user["chat_id"],
                    text=message,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Error sending notification to user {user['chat_id']}: {e}")

    async def monitor_token_creation(self):
        while True:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        SOLANA_RPC_URL,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [str(CREATOR_WALLET), {"limit": 10}],
                        },
                        timeout=30.0,
                    )

                    if response.status_code == 429:
                        logger.warning("Rate limit hit, waiting before retry...")
                        await asyncio.sleep(60)
                        continue

                    response.raise_for_status()
                    signatures = response.json()

                    if signatures.get("result"):
                        # Load existing transactions
                        try:
                            with open("transactions.json", "r") as f:
                                saved_transactions = json.load(f)
                        except (FileNotFoundError, json.JSONDecodeError):
                            saved_transactions = []

                        # Get set of existing signatures
                        saved_signatures = {tx["signature"] for tx in saved_transactions}

                        # Add new signatures
                        for sig_data in signatures["result"]:
                            current_signature = sig_data["signature"]
                            if current_signature not in saved_signatures:
                                saved_transactions.append({
                                    "signature": current_signature,
                                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "slot": sig_data.get("slot"),
                                    "blockTime": sig_data.get("blockTime")
                                })

                        # Save updated transactions
                        with open("transactions.json", "w") as f:
                            json.dump(saved_transactions, f, indent=4)

                        # Process token creations
                        for sig_data in signatures["result"]:
                            current_signature = sig_data["signature"]
                            if current_signature not in self.processed_signatures:
                                token_data = await self.get_token_creation_details(current_signature)
                                if token_data:
                                    save_token_creation(token_data)
                                    await self.notify_subscribers(token_data)
                                    self.processed_signatures.add(current_signature)

                await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"Error monitoring token creation: {e}")


async def main():
    tracker = TokenCreationTracker()
    logger.info("Starting token creation tracker...")
    await tracker.application.initialize()
    await tracker.application.start()

    await asyncio.gather(
        tracker.monitor_token_creation(), tracker.application.updater.start_polling()
    )


if __name__ == "__main__":
    asyncio.run(main())