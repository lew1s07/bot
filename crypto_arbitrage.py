import aiohttp
import asyncio
import json
import time
from datetime import datetime
from colorama import Fore, Style, init
import os
from collections import defaultdict
from fastapi import FastAPI
import uvicorn

init(autoreset=True)

EXCHANGES = ["mexc", "gate", "bybit", "okx"]
SYMBOL_SUFFIX = "USDT"
HISTORY_FILE = "price_history.json"
ARBITRAGE_THRESHOLD = 3

TELEGRAM_TOKEN = "7827467641:AAFdq_Z9uQNPjbOD6fAQnXUp5lX1xInnmkY"
CHAT_IDS = [1666211153, 1399216068]

app = FastAPI()
all_coins_info = {}  # глобальное хранилище символов

@app.get("/")
async def root():
    return {"status": "ok", "message": "Arbitrage bot is running"}

@app.get("/ping")
async def ping():
    return {"message": "pong"}

async def fetch_all_usdt_pairs():
    global all_coins_info
    all_coins_info = defaultdict(dict)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get("https://api.mexc.com/api/v3/exchangeInfo") as resp:
                data = await resp.json()
                for s in data["symbols"]:
                    if s["quoteAsset"] == "USDT":
                        coin = s["baseAsset"]
                        all_coins_info[coin]["mexc"] = s["symbol"]
        except Exception as e:
            print("MEXC error:", e)

        try:
            async with session.get("https://api.gate.io/api/v4/spot/currency_pairs") as resp:
                data = await resp.json()
                for p in data:
                    if p["quote"] == "USDT":
                        coin = p["base"].upper()
                        all_coins_info[coin]["gate"] = p["id"]  # нижний регистр, например btc_usdt
        except Exception as e:
            print("Gate error:", e)

        try:
            async with session.get("https://api.bybit.com/v5/market/instruments-info?category=spot") as resp:
                data = await resp.json()
                for s in data["result"]["list"]:
                    if s["quoteCoin"] == "USDT":
                        coin = s["baseCoin"]
                        all_coins_info[coin]["bybit"] = s["symbol"]
        except Exception as e:
            print("Bybit error:", e)

        try:
            async with session.get("https://www.okx.com/api/v5/public/instruments?instType=SPOT") as resp:
                data = await resp.json()
                for s in data["data"]:
                    if s["quoteCcy"] == "USDT":
                        coin = s["baseCcy"]
                        all_coins_info[coin]["okx"] = s["instId"]
        except Exception as e:
            print("OKX error:", e)

    valid_coins = [coin for coin, descs in all_coins_info.items() if len(descs) >= 2]
    print(f"✅ Найдено {len(valid_coins)} монет на 2+ биржах")
    return valid_coins

def api_urls(coin):
    descs = all_coins_info.get(coin, {})
    urls = {}
    if "mexc" in descs:
        urls["mexc"] = f"https://api.mexc.com/api/v3/ticker/price?symbol={descs['mexc']}"
    if "gate" in descs:
        urls["gate"] = f"https://api.gate.io/api/v4/spot/tickers?currency_pair={descs['gate']}"
    if "bybit" in descs:
        urls["bybit"] = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={descs['bybit']}"
    if "okx" in descs:
        urls["okx"] = f"https://www.okx.com/api/v5/market/ticker?instId={descs['okx']}"
    return urls

async def fetch_price(session, url, exchange):
    try:
        async with session.get(url, timeout=10) as resp:
            data = await resp.json()
            if exchange == "mexc":
                return float(data["price"])
            elif exchange == "gate":
                return float(data[0]["last"])
            elif exchange == "bybit":
                if data["result"]["list"]:
                    return float(data["result"]["list"][0]["lastPrice"])
            elif exchange == "okx":
                return float(data["data"][0]["last"])
    except Exception as e:
        print(f"⚠️ Ошибка получения цены от {exchange}: {e}")
    return None

async def send_telegram_message(text):
    async with aiohttp.ClientSession() as session:
        for chat_id in CHAT_IDS:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": text}
            try:
                async with session.post(url, data=payload) as resp:
                    if resp.status == 200:
                        print(f"{Fore.GREEN}✅ Telegram -> {chat_id}{Style.RESET_ALL}")
                    else:
                        print(f"{Fore.RED}❌ Telegram ошибка: {resp.status}{Style.RESET_ALL}")
            except Exception as e:
                print(f"{Fore.RED}❌ Telegram исключение: {e}{Style.RESET_ALL}")

async def is_token_transferable(coin):
    unavailable = []
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://www.okx.com/api/v5/asset/currencies?ccy={coin}") as r:
                data = await r.json()
                if data.get("data"):
                    d = data["data"][0]
                    if d.get("canDep") != "true" or d.get("canWd") != "true":
                        unavailable.append("okx")
        except:
            pass

        try:
            async with session.get(f"https://api.bybit.com/v5/asset/coin/query-info?coin={coin}") as r:
                data = await r.json()
                if data.get("result"):
                    d = data["result"]["rows"][0]
                    if not d.get("withdrawable") or not d.get("depositable"):
                        unavailable.append("bybit")
        except:
            pass

    return unavailable

async def compare_prices(coins):
    async with aiohttp.ClientSession() as session:
        for coin in coins:
            urls = api_urls(coin)
            tasks = [fetch_price(session, urls[ex], ex) for ex in urls]
            prices = await asyncio.gather(*tasks)
            price_dict = {ex: p for ex, p in zip(urls.keys(), prices) if p is not None}

            if len(price_dict) < 2:
                continue

            min_ex = min(price_dict, key=price_dict.get)
            max_ex = max(price_dict, key=price_dict.get)
            min_price = price_dict[min_ex]
            max_price = price_dict[max_ex]

            percent_diff = ((max_price - min_price) / min_price) * 100
            abs_diff = max_price - min_price
            timestamp = datetime.now().strftime('%H:%M:%S')

            print(f"[{timestamp}] {coin}/USDT:")
            for ex, price in price_dict.items():
                pct = ((price - min_price) / min_price) * 100 if min_price else 0
                color = Fore.GREEN if price == min_price else Fore.RED if price == max_price else ""
                print(f"  - {ex.capitalize()}: {color}${price:.6f}{Style.RESET_ALL} ({pct:+.2f}%)")

            msg = (
                f"[{timestamp}] {coin}/USDT\n"
                + "\n".join([f"- {ex.capitalize()}: ${price:,.6f}" for ex, price in price_dict.items()])
                + f"\n🔹 Арбитраж: купить на {min_ex}, продать на {max_ex} "
                f"(+{percent_diff:.2f}% / +${abs_diff:.4f})"
            )

            unavailable = await is_token_transferable(coin)
            if unavailable:
                msg += f"\n⚠️ Ввод/вывод приостановлен на: {', '.join(unavailable)}"

            print(f"{Fore.CYAN}{msg}{Style.RESET_ALL}\n")

            if ARBITRAGE_THRESHOLD <= percent_diff <= 20:
                await send_telegram_message(msg)

            if not os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "w") as f:
                    json.dump({}, f)

            with open(HISTORY_FILE, "r+", encoding="utf-8") as f:
                try:
                    history = json.load(f)
                except:
                    history = {}
                history.setdefault(coin, []).append({
                    "time": timestamp,
                    "prices": price_dict,
                    "arbitrage": {
                        "buy": min_ex,
                        "sell": max_ex,
                        "percent_diff": percent_diff,
                        "abs_diff": abs_diff
                    },
                    "unavailable": unavailable
                })
                f.seek(0)
                json.dump(history, f, indent=2)

async def main_loop():
    coins = await fetch_all_usdt_pairs()
    if not coins:
        print("❌ Не найдено монет для арбитража. Завершение.")
        return

    print(f"🔁 Запуск основного цикла... Всего монет: {len(coins)}")
    while True:
        try:
            await compare_prices(coins)
        except Exception as e:
            print(f"⚠️ Ошибка в основном цикле: {e}")
        await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(main_loop())

if __name__ == "__main__":
    uvicorn.run("crypto_arbitrage:app", host="0.0.0.0", port=8000)
