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
import random
import threading
import queue
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Update the constants to use environment variables
SOLANA_RPC_URL = os.getenv('SOLANA_RPC_URL')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CREATOR_WALLET = PublicKey(os.getenv('CREATOR_WALLET'))
USERS_FILE = os.getenv('USERS_FILE')
TOKEN_CREATION_FILE = os.getenv('TOKEN_CREATION_FILE')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

cache = cachetools.func.TTLCache(maxsize=1000, ttl=600)

PROXIES = [
    {"http://": "http://1.in.p.apiroute.net:12324/"},
    {"http://": "http://2.in.p.apiroute.net:12324/"},
    {"http://": "http://3.in.p.apiroute.net:12324/"},
    {"http://": "http://4.in.p.apiroute.net:12324/"},
    {"http://": "http://5.in.p.apiroute.net:12324/"},
    {"http://": "http://6.in.p.apiroute.net:12324/"},
    {"http://": "http://7.in.p.apiroute.net:12324/"},
    {"http://": "http://8.in.p.apiroute.net:12324/"},
    {"http://": "http://9.in.p.apiroute.net:12324/"},
    {"http://": "http://10.in.p.apiroute.net:12324/"},
    {"http://": "http://11.in.p.apiroute.net:12324/"},
    {"http://": "http://12.in.p.apiroute.net:12324/"},
    {"http://": "http://13.in.p.apiroute.net:12324/"},
    {"http://": "http://14.in.p.apiroute.net:12324/"},
    {"http://": "http://15.in.p.apiroute.net:12324/"},
    {"http://": "http://16.in.p.apiroute.net:12324/"},
    {"http://": "http://17.in.p.apiroute.net:12324/"},
    {"http://": "http://18.in.p.apiroute.net:12324/"},
    {"http://": "http://19.in.p.apiroute.net:12324/"},
    {"http://": "http://20.in.p.apiroute.net:12324/"},
    {"http://": "http://21.in.p.apiroute.net:12324/"},
]

PROXIES_EXTRA = [
    {"http://": "http://22.in.p.apiroute.net:12324/"},
    {"http://": "http://23.in.p.apiroute.net:12324/"},
    {"http://": "http://24.in.p.apiroute.net:12324/"},
    {"http://": "http://25.in.p.apiroute.net:12324/"},
    {"http://": "http://26.in.p.apiroute.net:12324/"},
    {"http://": "http://27.in.p.apiroute.net:12324/"},
    {"http://": "http://28.in.p.apiroute.net:12324/"},
    {"http://": "http://29.in.p.apiroute.net:12324/"},
    {"http://": "http://30.in.p.apiroute.net:12324/"},
]

proxy = PROXIES[20]
api_success_count = 0
api_rate_limit_count = 0


def random_proxy():
    return random.choice(PROXIES_EXTRA)


def process_transactions_with_threads(signatures, num_threads=20):
    signature_queue = queue.Queue()
    for signature in signatures:
        signature_queue.put(signature["signature"])

    results = []
    results_lock = threading.Lock()

    def worker():
        while True:
            try:
                signature = signature_queue.get(timeout=1)
                thread_proxy = PROXIES[
                    int(threading.current_thread().name.split("-")[1])
                ]
                result = asyncio.run(get_transaction_details(signature, thread_proxy))

                if result:
                    with results_lock:
                        results.append(result)

                signature_queue.task_done()
            except queue.Empty:
                break
            except Exception as e:
                logger.error(f"Error processing signature: {e}")
                signature_queue.task_done()

    threads = []

    for i in range(num_threads):
        t = threading.Thread(target=worker, name=f"Worker-{i}")
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results


async def get_transaction_details(signature, thread_proxy):
    global api_success_count, api_rate_limit_count
    print(f"Using proxy: {thread_proxy}")
    max_retries = 10
    retry_count = 0

    payload_transaction_details = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
        ],
    }

    while retry_count < max_retries:
        try:
            async with httpx.AsyncClient(proxies=thread_proxy) as client:
                response = await client.post(
                    SOLANA_RPC_URL,
                    json=payload_transaction_details,
                    headers={"Content-Type": "application/json"},
                    timeout=30.0,
                )

                if response.status_code == 429:
                    logger.warning(
                        f"Rate limit exceeded. Retry attempt {retry_count + 1}/{max_retries}"
                    )
                    api_rate_limit_count += 1
                    thread_proxy = random_proxy()
                    retry_count += 1
                    continue

                if response.status_code == 200:
                    api_success_count += 1
                    data = response.json()
                    logger.info(
                        f"API Stats - Successful: {api_success_count}, Rate Limited: {api_rate_limit_count}"
                    )
                    return data

        except Exception as e:
            logger.error(f"Error with proxy {thread_proxy}: {e}")
            retry_count += 1
            thread_proxy = random_proxy()

    return None


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

    async def notify_subscribers(self, token_data):
        print(f"Notifying subscribers about token creation: {token_data}")
        
        # Create inline keyboard with button
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        keyboard = [[
            InlineKeyboardButton(
                "View on NeoBullx 🔍", 
                url=f"https://neobullx.com/token/{token_data['token_address']}"
            )
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

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
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(
                    f"Error sending notification to user {user['chat_id']}: {e}"
                )


    async def monitor_token_creation(self):
        while True:
            try:
                async with httpx.AsyncClient(proxies=proxy, timeout=30.0) as client:
                    response = await client.post(
                        SOLANA_RPC_URL,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [str(CREATOR_WALLET), {"limit": 10}],
                        },
                    )

                    response.raise_for_status()
                    signatures = response.json()

                    if signatures.get("result"):
                        results = process_transactions_with_threads(
                            signatures["result"]
                        )
                        for result in results:
                            if result and result.get("result"):
                                transaction = result["result"]
                                if transaction:
                                    signature = transaction.get("transaction", {}).get(
                                        "signatures", [""]
                                    )[0]

                                    try:
                                        with open(
                                            "processed_transactions.json", "r"
                                        ) as f:
                                            processed_txs = json.load(f)
                                    except:
                                        processed_txs = []

                                    if signature not in [
                                        tx["signature"] for tx in processed_txs
                                    ]:
                                        if self.is_token_creation_transaction(
                                            transaction
                                        ):
                                            token_data = {
                                                "token_address": str(CREATOR_WALLET),
                                                "signature": signature,
                                                "timestamp": datetime.fromtimestamp(
                                                    transaction.get("blockTime", 0)
                                                ).strftime("%Y-%m-%d %H:%M:%S"),
                                            }

                                            processed_txs.append(token_data)
                                            with open(
                                                "processed_transactions.json", "w"
                                            ) as f:
                                                json.dump(processed_txs, f)

                                            await self.notify_subscribers(token_data)

                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error monitoring token creation: {e}")
                await asyncio.sleep(5)

    def is_token_creation_transaction(self, transaction):
        try:
            program_ids = [
                account.get("program")
                for account in transaction.get("transaction", {})
                .get("message", {})
                .get("accountKeys", [])
            ]
            return "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA" in program_ids
        except:
            return False


async def main():
    tracker = TokenCreationTracker()
    logger.info("Starting token creation tracker...")
    await tracker.application.initialize()
    await tracker.application.start()
    await asyncio.gather(
        tracker.monitor_token_creation(), tracker.application.updater.start_polling()
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
