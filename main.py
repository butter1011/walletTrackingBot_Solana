import asyncio
from solana.rpc.async_api import AsyncClient
from telegram.ext import Application, CommandHandler
import json
import logging
from datetime import datetime
import os
from solana.publickey import PublicKey
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
TELEGRAM_BOT_TOKEN = "7643287966:AAHsQEn2r29Wplh3IhRKoC1f25jNezl8nwM"
CREATOR_WALLET = PublicKey("E88QpPXQFjyKmVK7kj5NjkAPjLTYnYoY2Dd6Po7WUJjg")
USERS_FILE = "subscribed_users.json"
TOKEN_CREATION_FILE = "token_creations.json"


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

    async def process_token_creation(self, token_data):
        if is_new_token(token_data["signature"]):
            tx_details = await self.client.get_transaction(token_data["signature"])
            if (
                tx_details
                and tx_details["result"]
                and tx_details["result"]["blockTime"]
            ):
                block_time = tx_details["result"]["blockTime"]
                timestamp = datetime.fromtimestamp(block_time).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            else:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            token_data["timestamp"] = timestamp
            save_token_creation(token_data)

            message = f"🪙 *New Token Creation Detected*\n\n"
            message += f"👨‍💼 *Creator:* `{CREATOR_WALLET}`\n"
            message += f"🕒 *Timestamp:* {timestamp}\n"
            message += f"🔗 *Signature:* `{token_data['signature'][:20]}...`\n"
            message += f"💎 *Token Address:* `{token_data['token_address']}`\n"
            message += f"📝 *Token Name:* {token_data['token_name']}\n"
            message += f"💫 *Token Symbol:* {token_data['token_symbol']}\n"
            message += f"🔢 *Decimals:* {token_data['decimals']}\n\n"
            message += f"🔍 [View on Solana Explorer](https://explorer.solana.com/tx/{token_data['signature']})"

            for user in self.subscribed_users:
                try:
                    await self.application.bot.send_message(
                        chat_id=user["chat_id"],
                        text=message,
                        parse_mode="Markdown",
                        disable_web_page_preview=True,
                    )
                except Exception as e:
                    logger.error(f"Error sending Telegram message to {user}: {e}")

    async def get_token_creation_details(self, signature):
        try:
            tx = await self.client.get_transaction(signature)
            if tx and tx["result"]:
                # Check if transaction contains token creation instruction
                for instruction in tx["result"]["meta"]["innerInstructions"]:
                    if (
                        "tokenProgram" in instruction
                        and instruction["programId"]
                        == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
                    ):
                        # Extract token details from the transaction
                        token_address = instruction["accounts"][
                            1
                        ]  # Usually the token mint address
                        token_info = await self.client.get_token_supply(token_address)

                        return {
                            "signature": signature,
                            "token_address": token_address,
                            "token_name": token_info.get("name", "Unknown"),
                            "token_symbol": token_info.get("symbol", "Unknown"),
                            "decimals": token_info.get("decimals", 0),
                        }
        except Exception as e:
            logger.error(f"Error getting token creation details: {e}")
        return None

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
                            "params": [str(CREATOR_WALLET)],
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
                        # Load processed signatures from transactions.json
                        try:
                            with open("transactions.json", "r") as f:
                                saved_transactions = json.load(f)
                                saved_signatures = {
                                    tx["signature"] for tx in saved_transactions
                                }
                        except (FileNotFoundError, json.JSONDecodeError):
                            saved_signatures = set()

                        # Process all new signatures in reverse order (newest first)
                        for sig_data in signatures["result"]:
                            current_signature = sig_data["signature"]

                            if (
                                current_signature not in saved_signatures
                                and current_signature not in self.processed_signatures
                            ):
                                token_data = await self.get_token_creation_details(
                                    current_signature
                                )
                                if token_data:
                                    await self.process_token_creation(token_data)
                                    self.processed_signatures.add(current_signature)
                                    self.last_signature = current_signature

                await asyncio.sleep(10)  # Check every 10 seconds

            except Exception as e:
                logger.error(f"Error monitoring token creation: {e}")
                await asyncio.sleep(30)


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
