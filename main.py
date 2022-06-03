import logging
import asyncio
import httpx
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

icons = {
    "wallet": "💼",
    "cancel": "🔙",
    "start_tracking": "🔔",
    "stop_tracking": "🔕",
    "settings": "🔧"
}
btns = {
    "wallet": telebot.types.KeyboardButton("💼 BTC Wallet"),
    "cancel": telebot.types.KeyboardButton("🔙 Cancel"),
    "start_tracking": telebot.types.KeyboardButton("🔔 Transaction tracking on"),
    "stop_tracking": telebot.types.KeyboardButton("🔕 Transaction tracking off"),
    "settings": telebot.types.KeyboardButton("🔧 Settings"),
    "set_wallet": telebot.types.InlineKeyboardButton("Set new wallet", callback_data="set_new_wallet"),
    "check_balance": telebot.types.InlineKeyboardButton("Check balance", callback_data="check_balance"),
}
keyboards = {
    "menu": telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2).add(btns["wallet"], btns["settings"], btns["start_tracking"]),
    "tracking": telebot.types.ReplyKeyboardMarkup(resize_keyboard=True).add(btns["stop_tracking"]),
    "query": telebot.types.ReplyKeyboardMarkup(resize_keyboard=True).add(btns["cancel"]),
    "set_wallet": telebot.types.InlineKeyboardMarkup(row_width=1).add(btns["set_wallet"], btns["check_balance"])
}


async def get_json_response(url):
    """Connects to a specified URL and returns a json response on success or None otherwise"""
    async with httpx.AsyncClient() as client:
        request = await client.get(url)
    if request.status_code == httpx.codes.OK:
        return request.json()
    logger.error(f"Could not reach {url}. Error code {request.status_code}")
    return None


async def get_block_height() -> int:
    """Returns index of the newest block on the blockchain"""
    wallet_request = await get_json_response("https://blockchain.info/q/getblockcount")
    return int(wallet_request)


async def tracker(chat_id, user_id, wallet, required_confirmations):
    """Accesses blockchain.info API in a loop to check for updates on the last transaction on user's wallet"""
    if (wallet_info := await get_json_response(f"https://blockchain.info/rawaddr/{wallet}?limit=1")) is None:
        await bot.send_message(chat_id, text="Could not reach blockchain.info API", reply_markup=keyboards["menu"])
        await bot.set_state(user_id, "menu")
        return
    last_tx = wallet_info["txs"][0]

    if last_tx["block_height"] is not None:
        await bot.send_message(chat_id, text="No unconfirmed transactions found", reply_markup=keyboards["menu"])
        await bot.set_state(user_id, "menu")
        return
    tx_hash = last_tx["hash"]
    prev_confirmations = 0
    await bot.send_message(chat_id, text=f"Unconfirmed transaction {tx_hash} found")

    while await bot.get_state(user_id) == "tracking":
        await asyncio.sleep(20)

        if (tx_info := await get_json_response(f"https://blockchain.info/rawtx/{tx_hash}")) is None:
            continue
        if (tx_block_height := tx_info["block_height"]) is None:
            continue
        await asyncio.sleep(10)
        if (confirmations := await get_block_height() - tx_block_height + 1) == prev_confirmations:
            continue

        prev_confirmations = confirmations
        if confirmations >= required_confirmations:
            await bot.send_message(chat_id, text="Transaction confirmed!", reply_markup=keyboards["menu"])
            await bot.set_state(user_id, "menu")
            return
        else:
            await bot.send_message(chat_id, f"Confirmations: {confirmations}")



    await bot.send_message(chat_id, text="Transaction tracking cancelled", reply_markup=keyboards["menu"])


# MESSAGE HANDLERS
@bot.message_handler(commands=["start"])
async def welcome(msg):
    await bot.set_state(msg.from_user.id, "menu")
    await bot.reset_data(msg.from_user.id)
    await bot.send_message(msg.chat.id, text="Hello", reply_markup=keyboards["menu"])


@bot.message_handler(text_startswith=icons["cancel"])
async def btn_cancel(msg):
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
    await bot.send_message(msg.chat.id, text=response, reply_markup=keyboards["set_wallet"])


@bot.message_handler(text_startswith=icons["settings"])
async def btn_settings(msg):
    async with bot.retrieve_data(msg.from_user.id) as data:
        confirmations = data.get("confirmations") or 2
    message = f"Confirmations needed before transactions is considered confirmed: *{confirmations}*." \
              f"\nSend a number between 1 and 10 to change that."
    await bot.send_message(msg.chat.id, text=message, reply_markup=keyboards["query"], parse_mode="Markdown")
    await bot.set_state(msg.from_user.id, "settings_query")


@bot.message_handler(text_startswith=icons["start_tracking"], state="menu")
async def btn_start_tracking(msg):
    async with bot.retrieve_data(msg.from_user.id) as data:
        if data is None or (wallet := data.get("wallet")) is None:
            await bot.send_message(msg.chat.id, text="No wallet")
            return
        confirmations = data.get("confirmations") or 2
    await bot.set_state(msg.from_user.id, "tracking")
    await bot.send_message(msg.chat.id, text="Looking for unconfirmed transactions...", reply_markup=keyboards["tracking"])
    await tracker(msg.chat.id, msg.from_user.id, wallet, confirmations)


@bot.message_handler(text_startswith=icons["stop_tracking"], state="tracking")
async def btn_stop_tracking(msg):
    await bot.set_state(msg.from_user.id, "menu")
    await bot.send_message(msg.chat.id, text="Please wait...", reply_markup=telebot.types.ReplyKeyboardRemove())


@bot.message_handler(state="wallet_query")
async def wallet_query(msg):
    wallet_address = msg.text.strip().removeprefix("bitcoin:")
    if re.fullmatch(r"^([13]{1}[a-km-zA-HJ-NP-Z1-9]{26,33}|bc1[a-z0-9]{39,59})$", wallet_address):
        await bot.add_data(msg.from_user.id, wallet=wallet_address)
        await bot.set_state(msg.from_user.id, "menu")
        await bot.send_message(msg.chat.id, text="BTC wallet updated", reply_markup=keyboards["menu"])
    else:
        await bot.send_message(msg.chat.id, text="Not a valid BTC wallet")


@bot.message_handler(state="settings_query")
async def settings_query(msg):
    if not msg.text.isdigit():
        await bot.send_message(msg.chat.id, "Invalid input")
        return
    confirmations = int(msg.text)
    if confirmations not in range(1, 11):
        await bot.send_message(msg.chat.id, "Number must be between 1 and 10")
        return
    await bot.add_data(msg.from_user.id, confirmations=confirmations)
    await bot.set_state(msg.from_user.id, "menu")
    await bot.send_message(msg.chat.id, "Success", reply_markup=keyboards["menu"])


@bot.callback_query_handler(func=lambda call: call.data == "set_new_wallet", state="menu")
async def btn_set_wallet(call):
    await bot.send_message(call.message.chat.id, text="Enter new wallet", reply_markup=keyboards["query"])
    await bot.set_state(call.from_user.id, "wallet_query")


@bot.callback_query_handler(func=lambda call: call.data == "check_balance", state="menu")
async def check_balance(call):
    async with bot.retrieve_data(call.from_user.id) as data:
        wallet = data.get("wallet")
    if (wallet_info := await get_json_response(f"https://blockchain.info/rawaddr/{wallet}?limit=1")) is None:
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


if __name__ == '__main__':
    asyncio.run(bot.infinity_polling())
