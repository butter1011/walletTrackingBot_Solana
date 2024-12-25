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
WALLET_ADDRESSES = [
    PublicKey("JAgzgU2uSzHJxZJqYRpPDk2vJpZrL876YtxLQWkKWJeV"),
    PublicKey("AWaNwuTjfYMaprz2KebNe5E2K2WfcXjhawdvwoiFkjJ"),
]
USERS_FILE = "subscribed_users.json"
TRANSACTIONS_FILE = "transactions.json"


def load_transactions():
    if os.path.exists(TRANSACTIONS_FILE):
        try:
            with open(TRANSACTIONS_FILE, "r") as f:
                return json.load(f)
        except:
            return []
    return []


def save_transaction(tx_data):
    transactions = load_transactions()
    transactions.append(tx_data)
    with open(TRANSACTIONS_FILE, "w") as f:
        json.dump(transactions, f, indent=4)


def is_new_transaction(signature):
    transactions = load_transactions()
    return not any(tx["signature"] == signature for tx in transactions)


class SolanaWalletTracker:
    def __init__(self):
        print("Initializing SolanaWalletTracker")
        self.client = AsyncClient(SOLANA_RPC_URL)
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.last_signatures = {str(addr): None for addr in WALLET_ADDRESSES}
        self.subscribed_users = self.load_subscribed_users()
        self.processed_signatures = set()
        self.last_notification_time = None

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
                f"You have been subscribed to Solana wallet notifications!"
            )
        else:
            await update.message.reply_text(
                f"You are already subscribed to notifications!"
            )

    async def process_transaction(self, tx_data, wallet_address):
        if is_new_transaction(tx_data["signature"]):
            # Get transaction timestamp from Solana
            tx_details = await self.client.get_transaction(tx_data["signature"])
            if tx_details and tx_details["result"] and tx_details["result"]["blockTime"]:
                block_time = tx_details["result"]["blockTime"]
                timestamp = datetime.fromtimestamp(block_time).strftime("%Y-%m-%d %H:%M:%S")
            else:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # Fallback
                
            tx_data["timestamp"] = timestamp
            tx_data["wallet_address"] = str(wallet_address)
            save_transaction(tx_data)

            message = f"💫 *New Solana Transaction Detected*\n\n"
            message += f"👛 *Wallet:* `{wallet_address}`\n"
            message += f"🕒 *Timestamp:* {timestamp}\n"
            message += f"🔗 *Signature:* `{tx_data['signature'][:20]}...`\n"
            message += f"💰 *Amount:* {tx_data['amount']:.6f} SOL\n"
            message += f"📝 *Transaction Type:* {tx_data['type']}\n\n"
            message += f"🔍 [View on Solana Explorer](https://explorer.solana.com/tx/{tx_data['signature']})"

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

    async def get_transaction_details(self, signature):
        try:
            tx = await self.client.get_transaction(signature)
            if tx and tx["result"]:
                # Calculate actual transaction amount from pre and post balances
                pre_balance = tx["result"]["meta"]["preBalances"][0]
                post_balance = tx["result"]["meta"]["postBalances"][0]
                amount = (
                    abs(post_balance - pre_balance) / 1e9
                )  # Convert lamports to SOL

                return {
                    "signature": signature,
                    "amount": amount,
                    "type": "Transfer" if amount > 0 else "Other",
                }
        except Exception as e:
            logger.error(f"Error getting transaction details: {e}")
        return None

    async def monitor_wallet(self):
        while True:
            try:
                async with httpx.AsyncClient() as client:
                    for wallet_address in WALLET_ADDRESSES:
                        try:
                            response = await client.post(
                                SOLANA_RPC_URL,
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "getSignaturesForAddress",
                                    "params": [str(wallet_address)],
                                },
                                timeout=30.0,
                            )

                            if response.status_code == 429:
                                logger.warning(
                                    "Rate limit hit, waiting before retry..."
                                )
                                await asyncio.sleep(60)  # Wait 1 minute before retrying
                                continue

                            response.raise_for_status()
                            signatures = response.json()

                            if signatures.get("result"):
                                newest_signature = signatures["result"][0]["signature"]
                                wallet_key = str(wallet_address)

                                if (
                                    newest_signature != self.last_signatures[wallet_key]
                                    and newest_signature
                                    not in self.processed_signatures
                                ):
                                    tx_data = await self.get_transaction_details(
                                        newest_signature
                                    )
                                    if tx_data:
                                        await self.process_transaction(
                                            tx_data, wallet_key
                                        )
                                        self.processed_signatures.add(newest_signature)
                                        self.last_signatures[wallet_key] = (
                                            newest_signature
                                        )

                        except httpx.RequestError as e:
                            logger.error(
                                f"Request error for wallet {wallet_address}: {e}"
                            )
                            continue

                    await asyncio.sleep(10)  # Check every 10 seconds

            except Exception as e:
                logger.error(f"Error monitoring wallets: {e}")
                await asyncio.sleep(30)  # Wait longer on error


async def main():
    tracker = SolanaWalletTracker()
    logger.info("Starting Solana wallet tracker...")
    await tracker.application.initialize()
    await tracker.application.start()

    # Run both the monitoring and the bot polling together
    await asyncio.gather(
        tracker.monitor_wallet(), tracker.application.updater.start_polling()
    )


if __name__ == "__main__":
    asyncio.run(main())
