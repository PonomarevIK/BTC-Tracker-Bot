import logging
import asyncio
import aioschedule
import aiohttp
import re

from telebot.async_telebot import AsyncTeleBot
from telebot.asyncio_storage import StatePickleStorage
from telebot import asyncio_filters
import telebot.types


with open("token.txt", "r") as token:
    TOKEN = token.read()

logging.basicConfig(filename="logs.log", level=logging.ERROR)
logger = logging.getLogger("TeleBot")

bot = AsyncTeleBot(TOKEN, state_storage=StatePickleStorage("Storage/storage.pkl"))
bot.add_custom_filter(asyncio_filters.StateFilter(bot))
bot.add_custom_filter(asyncio_filters.TextStartsFilter())

aioscheduler = aioschedule.Scheduler()

icons = {
    "wallet": "💼",
    "cancel": "🔙",
    "start_tracking": "🔔",
    "stop_tracking": "🔕",
    "settings": "🔧",
}
btns = {
    "wallet": telebot.types.KeyboardButton("{wallet} BTC Wallet".format(**icons)),
    "cancel": telebot.types.KeyboardButton("{cancel} Cancel".format(**icons)),
    "start_tracking": telebot.types.KeyboardButton("{start_tracking} Transaction tracking on".format(**icons)),
    "stop_tracking": telebot.types.KeyboardButton("{stop_tracking} Transaction tracking off".format(**icons)),
    "settings": telebot.types.KeyboardButton("{settings} Settings".format(**icons)),
    "set_wallet": telebot.types.InlineKeyboardButton("Set new wallet", callback_data="set_new_wallet"),
    "check_balance": telebot.types.InlineKeyboardButton("Check balance", callback_data="check_balance"),
}
keyboards = {
    "menu": telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1).add(btns["wallet"], btns["start_tracking"]),
    "tracking": telebot.types.ReplyKeyboardMarkup(resize_keyboard=True).add(btns["stop_tracking"]),
    "query": telebot.types.ReplyKeyboardMarkup(resize_keyboard=True).add(btns["cancel"]),
    "wallet": telebot.types.InlineKeyboardMarkup(row_width=1).add(btns["set_wallet"], btns["check_balance"]),
    "set_wallet": telebot.types.InlineKeyboardMarkup(row_width=1).add(btns["set_wallet"]),
}


class HTTPSession:
    session = None

    @classmethod
    async def get_session(cls):
        cls.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector())

    @classmethod
    async def get_json_response(cls, url):
        if cls.session is None or cls.session.closed:
            cls.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector())
        async with cls.session.request('get', url) as response:
            if response.status == 200:
                return await response.json()
            logger.error(f"Could not reach {url}. Error code {response.status}")
        return None


# async def get_block_height() -> int:
#     """Returns index of the last block on the blockchain"""
#     wallet_request = await HTTPSession.get_json_response("https://blockchain.info/q/getblockcount")
#     print("Block height: ", wallet_request)
#     return int(wallet_request)


async def return_to_menu(chat_id, user_id, message):
    """ Sends a message and resets user's state back to 'menu' """
    await bot.send_message(chat_id, text=message, reply_markup=keyboards["menu"])
    await bot.set_state(user_id, "menu")


async def start_tracking(chat_id, user_id, wallet):
    """Looks for an unconfirmed transaction. If found schedules poke_blockchain() for every 30 seconds"""
    await bot.send_message(chat_id, text="Looking for a transaction to track...", reply_markup=keyboards["tracking"])

    if (wallet_info := await HTTPSession.get_json_response(f"https://blockchain.info/rawaddr/{wallet}?limit=1")) is None:
        await return_to_menu(chat_id, user_id, "Could not reach blockchain.info API")
        return
    if wallet_info["txs"][0]["block_height"] is not None:
        await return_to_menu(chat_id, user_id, "No unconfirmed transactions found")
        return

    tx_hash = wallet_info["txs"][0]["hash"]
    await bot.send_message(chat_id, text=f"Unconfirmed transaction {tx_hash} found")

    aioscheduler.every(30).seconds.do(poke_blockchain, chat_id, user_id, tx_hash).tag(user_id)


async def poke_blockchain(chat_id, user_id, tx_hash):
    """Is repeatedly called to check if transaction with specified tx_hash got at least one confirmation"""
    if (tx_info := await HTTPSession.get_json_response(f"https://blockchain.info/rawtx/{tx_hash}")) is None:
        return
    if tx_info["double_spend"]:
        await return_to_menu(chat_id, user_id, "Transaction invalid: double spend")
        aioscheduler.clear(user_id)
        return
    if tx_info["block_height"] is None:
        return
    aioscheduler.clear(user_id)
    await return_to_menu(chat_id, user_id, "Transaction confirmed!")


# MESSAGE HANDLERS
@bot.message_handler(commands=["start"])
async def welcome(msg):
    await bot.set_state(msg.from_user.id, "menu")
    await bot.reset_data(msg.from_user.id)
    await bot.send_message(msg.chat.id, text="Hello", reply_markup=keyboards["menu"])


@bot.message_handler(text_startswith=icons["cancel"])
async def btn_cancel(msg):
    aioscheduler.clear(msg.from_user.id)
    await bot.set_state(msg.from_user.id, "menu")
    await bot.send_message(msg.chat.id, text=icons["cancel"], reply_markup=keyboards["menu"])


# @bot.message_handler(commands=["debug"])
# async def debug(msg):
#     async with bot.retrieve_data(msg.from_user.id) as data:
#         state = await bot.get_state(msg.from_user.id)
#         print(f"DEBUG INFO\nUser state: {state}\nUser Data: {data}")
#     await bot.delete_message(msg.chat.id, msg.message_id)


@bot.message_handler(text_startswith=icons["wallet"])
async def btn_wallet(msg):
    async with bot.retrieve_data(msg.from_user.id) as data:
        if data is None or (wallet := data.get("wallet")) is None:
            response = "BTC wallet is not set"
        else:
            response = f"BTC Wallet:\n{wallet}"
    await bot.send_message(msg.chat.id, text=response, reply_markup=keyboards["wallet"])


@bot.message_handler(text_startswith=icons["start_tracking"], state="menu")
async def btn_start_tracking(msg):
    async with bot.retrieve_data(msg.from_user.id) as data:
        if data is None or (wallet := data.get("wallet")) is None:
            await bot.send_message(msg.chat.id, text="No wallet", reply_markup=keyboards["set_wallet"])
            return
    await bot.set_state(msg.from_user.id, "tracking")
    await start_tracking(msg.chat.id, msg.from_user.id, wallet)


@bot.message_handler(text_startswith=icons["stop_tracking"], state="tracking")
async def btn_stop_tracking(msg):
    aioscheduler.clear(msg.from_user.id)
    await bot.set_state(msg.from_user.id, "menu")
    await bot.send_message(msg.chat.id, text="Tracking canceled", reply_markup=keyboards["menu"])


@bot.message_handler(state="wallet_query")
async def wallet_query(msg):
    wallet_address = msg.text.strip().removeprefix("bitcoin:")
    if re.fullmatch(r"^([13]{1}[a-km-zA-HJ-NP-Z1-9]{26,33}|bc1[a-z0-9]{39,59})$", wallet_address):
        await bot.add_data(msg.from_user.id, wallet=wallet_address)
        await bot.set_state(msg.from_user.id, "menu")
        await bot.send_message(msg.chat.id, text="BTC wallet updated", reply_markup=keyboards["menu"])
    else:
        await bot.send_message(msg.chat.id, text="Not a valid BTC wallet")


@bot.callback_query_handler(func=lambda call: call.data == "set_new_wallet", state="menu")
async def btn_set_wallet(call):
    await bot.send_message(call.message.chat.id, text="Enter new wallet", reply_markup=keyboards["query"])
    await bot.set_state(call.from_user.id, "wallet_query")


@bot.callback_query_handler(func=lambda call: call.data == "check_balance", state="menu")
async def check_balance(call):
    async with bot.retrieve_data(call.from_user.id) as data:
        wallet = data.get("wallet")
    if (wallet_info := await HTTPSession.get_json_response(f"https://blockchain.info/rawaddr/{wallet}?limit=1")) is None:
        response = "Could not find wallet info online"
    else:
        response = f"Balance: {wallet_info['final_balance'] * 0.00000001}\n" \
                    f"Transactions: {wallet_info['n_tx']}"
    await bot.send_message(call.message.chat.id, text=response)


@bot.message_handler(func=lambda msg: msg.text[0] in icons.values())
async def wrong_command(msg):
    await bot.set_state(msg.from_user.id, "menu")
    await bot.send_message(msg.chat.id, text="Invalid command.\nGoing back to menu...",
                           reply_markup=keyboards["menu"])


@bot.message_handler(func=lambda msg: True)
async def delete_unrecognized(msg):
    await bot.delete_message(msg.chat.id, msg.message_id)


async def scheduler():
    while True:
        await aioscheduler.run_pending()
        await asyncio.sleep(1)


async def main():
    await asyncio.gather(bot.infinity_polling(), scheduler())


if __name__ == "__main__":
    asyncio.run(main())
