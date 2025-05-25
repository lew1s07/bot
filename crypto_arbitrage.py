import aiohttp
import asyncio
import json
import time
from datetime import datetime
from colorama import Fore, Style, init
import os
from collections import defaultdict

init(autoreset=True)

EXCHANGES = ["mexc", "gate", "bybit", "okx"]
SYMBOL_SUFFIX = "USDT"
HISTORY_FILE = "price_history.json"
ARBITRAGE_THRESHOLD = 0,01

TELEGRAM_TOKEN = "7827467641:AAFdq_Z9uQNPjbOD6fAQnXUp5lX1xInnmkY"
CHAT_IDS = [1666211153, 1399216068]


async def fetch_all_usdt_pairs():
    all_coins_info = defaultdict(dict)

    async with aiohttp.ClientSession() as session:
        # MEXC
        try:
            async with session.get("https://api.mexc.com/api/v3/exchangeInfo") as resp:
                data = await resp.json()
                for s in data["symbols"]:
                    if s["quoteAsset"] == "USDT":
                        coin = s["baseAsset"]
                        desc = s["symbol"]
                        all_coins_info[coin]["mexc"] = desc
        except:
            pass

        # GATE
        try:
            async with session.get("https://api.gate.io/api/v4/spot/currency_pairs") as resp:
                data = await resp.json()
                for p in data:
                    if p["quote"] == "USDT":
                        coin = p["id"].split("_")[0].upper()
                        desc = p.get("label", p["id"])
                        all_coins_info[coin]["gate"] = desc
        except:
            pass

        # BYBIT
        try:
            async with session.get("https://api.bybit.com/v5/market/instruments-info?category=spot") as resp:
                data = await resp.json()
                for s in data["result"]["list"]:
                    if s["symbol"].endswith("USDT"):
                        coin = s["baseCoin"]
                        desc = s["symbol"]
                        all_coins_info[coin]["bybit"] = desc
        except:
            pass

        # OKX
        try:
            async with session.get("https://www.okx.com/api/v5/public/instruments?instType=SPOT") as resp:
                data = await resp.json()
                for s in data["data"]:
                    if s["instId"].endswith("USDT"):
                        coin = s["baseCcy"]
                        desc = s["instId"]
                        all_coins_info[coin]["okx"] = desc
        except:
            pass

    # Оставляем монеты, присутствующие на 2+ биржах
    valid_coins = [coin for coin, descs in all_coins_info.items() if len(descs) >= 2]
    return valid_coins


def api_urls(coin):
    return {
        "mexc": f"https://api.mexc.com/api/v3/ticker/price?symbol={coin}{SYMBOL_SUFFIX}",
        "gate": f"https://api.gate.io/api/v4/spot/tickers?currency_pair={coin}_{SYMBOL_SUFFIX}",
        "bybit": f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={coin}{SYMBOL_SUFFIX}",
        "okx": f"https://www.okx.com/api/v5/market/ticker?instId={coin}-{SYMBOL_SUFFIX}"
    }


async def fetch_price(session, url, exchange):
    try:
        async with session.get(url, timeout=10) as resp:
            data = await resp.json()
            if exchange == "mexc":
                return float(data["price"])
            elif exchange == "gate":
                return float(data[0]["last"])
            elif exchange == "bybit":
                return float(data["result"]["list"][0]["lastPrice"])
            elif exchange == "okx":
                return float(data["data"][0]["last"])
    except:
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
            tasks = [fetch_price(session, urls[ex], ex) for ex in EXCHANGES]
            prices = await asyncio.gather(*tasks)
            price_dict = {ex: p for ex, p in zip(EXCHANGES, prices) if p is not None}

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


async def main():
    coins = await fetch_all_usdt_pairs()
    print(f"🔎 Найдено валидных монет: {len(coins)} — {coins}")

    while True:
        try:
            await compare_prices(coins)
        except Exception as e:
            print(f"{Fore.YELLOW}⚠️ Ошибка: {e}{Style.RESET_ALL}")
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
