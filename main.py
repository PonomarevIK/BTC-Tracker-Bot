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
bot.add_custom_filter(asyncio_filters.IsDigitFilter())

icons = {
    "wallet": "💼",
    "cancel": "🔙",
    "start_tracking": "🔔",
    "stop_tracking": "🔕",
}
btns = {
    "wallet": telebot.types.KeyboardButton("💼 BTC Wallet"),
    "cancel": telebot.types.KeyboardButton("🔙 Cancel"),
    "start_tracking": telebot.types.KeyboardButton("🔔 TX tracking on"),
    "stop_tracking": telebot.types.KeyboardButton("🔕 TX tracking off"),
    "set_wallet": telebot.types.InlineKeyboardButton("Set new wallet", callback_data="set_new_wallet"),
    "check_balance": telebot.types.InlineKeyboardButton("Check balance", callback_data="check_balance"),
}
keyboards = {
    "menu": telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1).add(btns["wallet"],
                                                                                     btns["start_tracking"]),
    "tracking": telebot.types.ReplyKeyboardMarkup(resize_keyboard=True).add(btns["stop_tracking"]),
    "wallet_query": telebot.types.ReplyKeyboardMarkup(resize_keyboard=True).add(btns["cancel"]),
    "wallet_inline": telebot.types.InlineKeyboardMarkup(row_width=1).add(btns["set_wallet"], btns["check_balance"])
}
request_urls = {
    "block count": "https://blockchain.info/q/getblockcount",
    "wallet info": "https://blockchain.info/rawaddr/{wallet}?limit=1",
    "transaction info": "https://blockchain.info/rawtx/{hash}"
}


async def get_json_response(url):
    """Asyncronously connects to a specified URL and returns a json response on success or None otherwise"""
    async with httpx.AsyncClient() as client:
        request = await client.get(url)
    if request.status_code == httpx.codes.OK:
        return request.json()
    logger.error(f"Could not reach {url}. Error code: {request.status_code}")
    return None


async def get_block_height() -> int:
    """Returns index of the newest block on the blockchain"""
    wallet_request = await get_json_response(request_urls["block count"])
    return int(wallet_request)


async def tracker(chat_id, user_id, wallet, required_confirmations):
    """Acesses blockchain.info API in a loop for updates on a specified transaction"""
    if ( wallet_info := await get_json_response(request_urls["wallet info"].format(wallet=wallet)) ) is None:
        await bot.send_message(chat_id, text="Could not reach blockchain.info API", reply_markup=keyboards["menu"])
        await bot.set_state(user_id, "menu")
        return
    last_tx = wallet_info["txs"][0]

    if last_tx["block_height"] is None:
        tx_hash = last_tx["hash"]
        prev_confirmations = 0
        await bot.send_message(chat_id, text=f"Unconfirmed transaction {tx_hash} found")

        while await bot.get_state(user_id) == "tracking":
            if ( tx_info := await get_json_response(request_urls["transaction info"].format(tx_hash=tx_hash)) ) is None:
                pass
            elif (tx_block_height := tx_info["block_height"]) is None:
                pass
            elif (confirmations := await get_block_height() - tx_block_height + 1) == prev_confirmations:
                pass
            else:
                prev_confirmations = confirmations
                if confirmations >= required_confirmations:
                    await bot.send_message(chat_id, text="Transaction confirmed!", reply_markup=keyboards["menu"])
                    await bot.set_state(user_id, "menu")
                else:
                    await bot.send_message(chat_id, f"Confirmations: {confirmations}")

            await asyncio.sleep(30)
    else:
        await bot.send_message(chat_id, text="No unconfirmed transactions found", reply_markup=keyboards["menu"])
        await bot.set_state(user_id, "menu")


# MESSAGE HANDLERS
@bot.message_handler(commands=["start"])
async def welcome(msg):
    await bot.set_state(msg.from_user.id, "menu")
    await bot.send_message(msg.chat.id, text="Hello", reply_markup=keyboards["menu"])


# @bot.message_handler(commands=["debug"])
# async def debug(msg):
#     async with bot.retrieve_data(msg.from_user.id) as data:
#         state = await bot.get_state(msg.from_user.id)
#         print(f"DEBUG INFO\nUser state: {state}\nUser Data: {data}")
#     await bot.delete_message(msg.chat.id, msg.message_id)


@bot.message_handler(text_startswith=icons["cancel"])
async def btn_cancel(msg):
    await bot.set_state(msg.from_user.id, "menu")
    await bot.send_message(msg.chat.id, text=icons["cancel"], reply_markup=keyboards["menu"])


@bot.message_handler(text_startswith=icons["wallet"], state="menu")
async def btn_wallet(msg):
    async with bot.retrieve_data(msg.from_user.id) as data:
        if data is None or (wallet := data.get("wallet")) is None:
            await bot.send_message(msg.chat.id, text="No wallet")
        else:
            await bot.send_message(msg.chat.id, text=f"BTC Wallet:\n{wallet}", reply_markup=keyboards["wallet_inline"])


@bot.message_handler(text_startswith=icons["start_tracking"], state="menu")
async def btn_start_tracking(msg):
    async with bot.retrieve_data(msg.from_user.id) as data:
        if data is None or (wallet := data.get("wallet")) is None:
            await bot.send_message(msg.chat.id, text="No wallet")
            return
        confirmations = data.get("confirmations")
    await bot.set_state(msg.from_user.id, "tracking")
    await bot.send_message(msg.chat.id, text="Looking for unconfirmed transactions...", reply_markup=keyboards["tracking"])
    if confirmations is None:
        confirmations = 2
        await bot.send_message(msg.chat.id, text="Default number of required confirmations is *2*."
                                                 "\nSend any number during tracking to change that.", parse_mode="Markdown")
    await tracker(msg.chat.id, msg.from_user.id, wallet, confirmations)


@bot.message_handler(state="tracking", text_startswith=icons["stop_tracking"])
async def btn_stop_tracking(msg):
    await bot.set_state(msg.from_user.id, "menu")
    await bot.send_message(msg.chat.id, text="TX tracking cancelled", reply_markup=keyboards["menu"])


@bot.message_handler(state="tracking", isdigit=True)
async def set_required_confirmation_count(msg):
    confirmations = int(msg.text)
    if 1 > confirmations > 10:
        await bot.send_message(msg.chat.id, text="You can't use this number")
        return
    await bot.add_data(msg.from_user.id, confirmations=confirmations)
    await bot.send_message(msg.chat.id, "Preferred confirmation count updated")


@bot.message_handler(state="wallet_query")
async def new_wallet_query(msg):
    wallet_address = msg.text.strip().removeprefix("bitcoin:")
    if re.fullmatch(r"^([13]{1}[a-km-zA-HJ-NP-Z1-9]{26,33}|bc1[a-z0-9]{39,59})$", wallet_address):
        await bot.add_data(msg.from_user.id, wallet=wallet_address)
        await bot.set_state(msg.from_user.id, "menu")
        await bot.send_message(msg.chat.id, text="BTC wallet address updated", reply_markup=keyboards["menu"])
    else:
        await bot.send_message(msg.chat.id, text="Not a valid BTC wallet")


@bot.callback_query_handler(func=lambda call: call.data == "set_new_wallet")
async def btn_set_new_wallet(call):
    await bot.send_message(call.message.chat.id, text="Enter new wallet address", reply_markup=keyboards["wallet_query"])
    await bot.set_state(call.from_user.id, "wallet_query")


@bot.callback_query_handler(func=lambda call: call.data == "check_balance")
async def btn_check_balance(call):
    async with bot.retrieve_data(call.from_user.id) as data:
        if data is None or (wallet := data.get("wallet")) is None:
            await bot.send_message(call.message.chat.id, text="No wallet")
            return
    if (wallet_info := await get_json_response(request_urls["wallet info"].format(wallet=wallet))) is None:
        response = "Could not find wallet info online"
    else:
        response = f"Balance: {wallet_info['final_balance'] * 0.00000001}\nTransactions: {wallet_info['n_tx']}"
    await bot.send_message(call.message.chat.id, text=response)


@bot.message_handler(func=lambda msg: True)
async def delete_unrecognized(msg):
    await bot.delete_message(msg.chat.id, msg.message_id)


if __name__ == '__main__':
    asyncio.run(bot.infinity_polling())
