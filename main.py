import asyncio
import aioschedule
from telebot.async_telebot import AsyncTeleBot
from telebot.asyncio_storage import StatePickleStorage
from telebot import asyncio_filters
import telebot.types

import httpx
import re

with open("token.txt", "r") as token:
    TOKEN = token.read()

bot = AsyncTeleBot(TOKEN, state_storage=StatePickleStorage())
# storage = StatePickleStorage("/Storage/storage.pkl")

bot.add_custom_filter(asyncio_filters.StateFilter(bot))

icons = {
    "wallet": "💼",
    "back": "🔙",
    "sound_on": "🔔",
    "sound_off": "🔕",
}

button_wallet = telebot.types.KeyboardButton("💼 BTC Wallet")
button_cancel = telebot.types.KeyboardButton("🔙 Cancel")
button_start_tracking = telebot.types.KeyboardButton("🔔 TX tracking on")
button_stop_tracking = telebot.types.KeyboardButton("🔕 TX tracking off")
il_button_set_wallet = telebot.types.InlineKeyboardButton("Set new wallet", callback_data="set_new_wallet")

kb_tracking_off = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
kb_tracking_off.add(button_wallet, button_start_tracking)
kb_tracking_on = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
kb_tracking_on.add(button_stop_tracking)
kb_cancel = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
kb_cancel.add(button_cancel)
il_kb_set_wallet = telebot.types.InlineKeyboardMarkup()
il_kb_set_wallet.add(il_button_set_wallet)


async def get_json_response(url):
    async with httpx.AsyncClient() as client:
        request = await client.get(url)
    if request.status_code == httpx.codes.OK:
        return request.json()
    return None


async def get_block_height() -> int:
    wallet_request = await get_json_response("https://blockchain.info/q/getblockcount")
    return int(wallet_request)


async def tracker(chat_id, user_id, wallet):
    wallet_info = await get_json_response(f"https://blockchain.info/rawaddr/{wallet}?limit=1")
    last_tx = wallet_info["txs"][0]
    if last_tx["block_height"] is None:
        tx_hash = last_tx["hash"]
        prev_confirmations = 0
        await bot.send_message(chat_id, text=f"Unconfirmed transaction {tx_hash} found")

        while await bot.get_state(user_id) == "tracking":
            await asyncio.sleep(20)

            if (tx_info := await get_json_response(f"https://blockchain.info/rawtx/{tx_hash}")) is None:
                continue

            if (tx_block_height := tx_info["block_height"]) is None:
                continue

            if (confirmations := await get_block_height() - tx_block_height + 1) == prev_confirmations:
                continue

            if confirmations == 2:
                await bot.send_message(chat_id, text="Transaction confirmed!", reply_markup=kb_tracking_off)
                await bot.set_state(user_id, "menu")
            else:
                prev_confirmations = confirmations
                await bot.send_message(chat_id, f"Confirmations: {confirmations}")
    else:
        await bot.send_message(chat_id, text="No unconfirmed transactions found", reply_markup=kb_tracking_off)
        await bot.set_state(user_id, "menu")


@bot.message_handler(commands=["start"])
async def welcome(msg):
    await bot.set_state(msg.from_user.id, "menu")
    await bot.reset_data(msg.from_user.id)
    await bot.send_message(msg.chat.id, text="Waddup", reply_markup=kb_tracking_off)


@bot.message_handler(func=lambda msg: msg.text == button_cancel.text)
async def btn_cancel(msg):
    await bot.set_state(msg.from_user.id, "menu")
    await bot.send_message(msg.chat.id, text="🔙", reply_markup=kb_tracking_off)


@bot.message_handler(func=lambda msg: msg.text == button_wallet.text)
async def btn_wallet(msg):
    async with bot.retrieve_data(msg.from_user.id) as data:
        if data is None or (wallet := data.get("wallet")) is None:
            await bot.send_message(msg.chat.id, text="BTC wallet is not set", reply_markup=il_kb_set_wallet)
            return
    response = f"BTC Wallet:\n{wallet}\n\n"
    async with httpx.AsyncClient() as client:
        wallet_info_request = await client.get(f"https://blockchain.info/rawaddr/{wallet}?limit=1")
        if wallet_info_request.status_code == httpx.codes.OK:
            wallet_info = wallet_info_request.json()
            response += f"Balance: {wallet_info['final_balance'] * 0.00000001}\n"
            response += f"Transactions: {wallet_info['n_tx']}"
        else:
            response += "Could not find wallet info online"

    await bot.send_message(msg.chat.id, text=response, reply_markup=il_kb_set_wallet)


@bot.message_handler(func=lambda msg: msg.text == button_start_tracking.text)
async def btn_start_tracking(msg):
    if await bot.get_state(msg.from_user.id) != "menu":
        await bot.send_message(msg.chat.id, text="Another action is currently being executed")
        return
    async with bot.retrieve_data(msg.from_user.id) as data:
        if data.get("wallet") is None:
            await bot.send_message(msg.chat.id, text="No wallet")
            return
        await bot.set_state(msg.from_user.id, "tracking")
        await bot.send_message(msg.chat.id, text="Looking for unconfirmed transactions...", reply_markup=kb_tracking_on)
        await tracker(msg.chat.id, msg.from_user.id, data["wallet"])


@bot.message_handler(func=lambda msg: msg.text == button_stop_tracking.text)
async def btn_stop_tracking(msg):
    await bot.set_state(msg.from_user.id, "menu")
    await bot.send_message(msg.chat.id, text="TX tracking cancelled", reply_markup=kb_tracking_off)


@bot.message_handler(state="wallet_query")
async def set_new_wallet(msg):
    wallet_address = msg.text.strip().removeprefix("bitcoin:")
    if re.fullmatch(r"^([13]{1}[a-km-zA-HJ-NP-Z1-9]{26,33}|bc1[a-z0-9]{39,59})$", wallet_address):
        await bot.add_data(msg.from_user.id, wallet=wallet_address)
        await bot.set_state(msg.from_user.id, "menu")
        await bot.send_message(msg.chat.id, text="BTC wallet address updated", reply_markup=kb_tracking_off)
    else:
        await bot.send_message(msg.chat.id, text="Not a valid BTC wallet")


@bot.callback_query_handler(func=lambda call: call.data == "set_new_wallet")
async def set_new_wallet_button(call):
    if await bot.get_state(call.from_user.id) != "menu":
        await bot.send_message(call.message.chat.id, text="Another action is currently being executed")
        return
    await bot.send_message(call.message.chat.id, text="Enter new wallet address", reply_markup=kb_cancel)
    await bot.set_state(call.from_user.id, "wallet_query")


@bot.message_handler(func=lambda msg: True)
async def delete_unrecognized(msg):
    await bot.delete_message(msg.chat.id, msg.message_id)


if __name__ == '__main__':
    asyncio.run(bot.polling())
