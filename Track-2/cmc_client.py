"""
cmc_client.py — CoinMarketCap API wrapper for LEORIX Edge (Track 2)
- CMC: quotes, global metrics, market regime
- Binance public API: OHLCV historical (no auth needed)
"""

import requests

BASE_URL = "https://pro-api.coinmarketcap.com"

COIN_IDS = {
    "BTC": 1,
    "ETH": 1027,
    "BNB": 1839,
}


class CMCClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "X-CMC_PRO_API_KEY": api_key,
            "Accept": "application/json",
        })

    def _get(self, path: str, params: dict) -> dict:
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status", {}).get("error_code", 0) != 0:
            raise ValueError(f"CMC API error: {data['status']['error_message']}")
        return data

    def get_key_info(self) -> dict:
        """Check API key usage and plan tier."""
        data = self._get("/v1/key/info", {})
        plan = data["data"]["plan"]
        usage = data["data"]["usage"]
        return {
            "credit_limit_monthly": plan["credit_limit_monthly"],
            "rate_limit_per_minute": plan["rate_limit_minute"],
            "credits_used_today": usage["current_day"]["credits_used"],
            "credits_used_month": usage["current_month"]["credits_used"],
            "credits_left_month": usage["current_month"]["credits_left"],
        }

    def get_quotes(self, symbols: list) -> dict:
        """Latest price, market cap, volume, % changes for given symbols."""
        data = self._get("/v2/cryptocurrency/quotes/latest", {
            "symbol": ",".join(symbols),
            "convert": "USD",
        })
        result = {}
        for symbol in symbols:
            entries = data["data"].get(symbol)
            if entries:
                coin = entries[0]
                q = coin["quote"]["USD"]
                result[symbol] = {
                    "id": coin["id"],
                    "name": coin["name"],
                    "price": q["price"],
                    "volume_24h": q["volume_24h"],
                    "market_cap": q["market_cap"],
                    "pct_1h": q["percent_change_1h"],
                    "pct_24h": q["percent_change_24h"],
                    "pct_7d": q["percent_change_7d"],
                }
        return result

    def get_global_metrics(self) -> dict:
        """Total market cap, BTC dominance, ETH dominance, 24h volume."""
        data = self._get("/v1/global-metrics/quotes/latest", {"convert": "USD"})
        d = data["data"]
        q = d["quote"]["USD"]
        return {
            "total_market_cap": q["total_market_cap"],
            "total_volume_24h": q["total_volume_24h"],
            "btc_dominance": d["btc_dominance"],
            "eth_dominance": d["eth_dominance"],
            "active_cryptocurrencies": d["active_cryptocurrencies"],
        }

    def get_market_regime(self) -> str:
        """
        Derive market regime from BTC dominance + 7d price change.
        Proxy for Fear & Greed (not available on basic tier).
        Returns: BULL | BEAR | NEUTRAL
        """
        metrics = self.get_global_metrics()
        quotes = self.get_quotes(["BTC"])
        btc_7d = quotes["BTC"]["pct_7d"]
        btc_dom = metrics["btc_dominance"]

        if btc_7d > 5 and btc_dom < 55:
            return "BULL"
        elif btc_7d < -5 or btc_dom > 60:
            return "BEAR"
        else:
            return "NEUTRAL"

    def get_ohlcv_historical(self, symbol: str, interval: str = "1d", limit: int = 90) -> list:
        """
        OHLCV candles via Binance public API — no API key needed.
        symbol: 'BTC', 'ETH', 'BNB'
        interval: 1m | 5m | 15m | 1h | 4h | 1d
        limit: number of candles (max 1000)
        """
        binance_symbol = f"{symbol}USDT"
        resp = requests.get("https://api.binance.com/api/v3/klines", params={
            "symbol": binance_symbol,
            "interval": interval,
            "limit": limit,
        }, timeout=10)
        resp.raise_for_status()
        candles = []
        for k in resp.json():
            candles.append({
                "time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return candles


# ── Quick validation ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os, json

    API_KEY = os.getenv("CMC_API_KEY", "2d25132563a8497893597470798e861e")
    client = CMCClient(API_KEY)

    print("── Key Info ──")
    print(json.dumps(client.get_key_info(), indent=2))

    print("\n── Quotes: BTC, ETH, BNB ──")
    print(json.dumps(client.get_quotes(["BTC", "ETH", "BNB"]), indent=2))

    print("\n── Global Metrics ──")
    print(json.dumps(client.get_global_metrics(), indent=2))

    print("\n── Market Regime ──")
    print(client.get_market_regime())

    print("\n── BTC OHLCV (last 7 daily candles via Binance) ──")
    candles = client.get_ohlcv_historical("BTC", interval="1d", limit=7)
    print(json.dumps(candles, indent=2))