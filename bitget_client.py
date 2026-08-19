import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx


def _load_env_file():
    """Load .env file from the same directory as this module."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = value.strip('"')
            else:
                value = value.strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value


_load_env_file()


class BitgetClient:
    """Bitget REST API v2 client with HMAC-SHA256 signing and proxy support."""

    BASE_URL = "https://api.bitget.com"
    TIMEOUT = 10

    def __init__(self):
        self.api_key = os.environ.get("BITGET_API_KEY", "")
        self.secret_key = os.environ.get("BITGET_SECRET_KEY", "")
        self.passphrase = os.environ.get("BITGET_PASSPHRASE", "")
        self.proxy = os.environ.get("BITGET_PROXY", "http://127.0.0.1:7897")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key and self.passphrase)

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        message = timestamp + method.upper() + path + body
        mac = hmac.new(self.secret_key.encode(), message.encode(), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": self._sign(ts, method, path, body),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.passphrase,
            "locale": "zh-CN",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, params: dict | None = None, body: str = "") -> dict[str, Any]:
        if not self.configured:
            return {"error": "Bitget API credentials not configured"}
        query = str(httpx.QueryParams(params)) if params else ""
        request_path = path + ("?" + query if query else "")
        url = self.BASE_URL + request_path
        headers = self._headers(method, request_path, body)
        try:
            async with httpx.AsyncClient(proxy=self.proxy, timeout=self.TIMEOUT) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, headers=headers, content=body)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != "00000":
                    return {"error": data.get("msg", "Unknown error"), "code": data.get("code")}
                return data.get("data", data)
        except httpx.TimeoutException:
            return {"error": f"Bitget API timeout after {self.TIMEOUT}s"}
        except httpx.HTTPStatusError as e:
            # Surface the API-level code (e.g. 40085 for UTA mode) so callers
            # can detect and handle it gracefully.
            code = None
            try:
                code = e.response.json().get("code")
            except Exception:
                pass
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}", "code": code}
        except Exception as e:
            return {"error": f"Bitget API error: {str(e)}"}

    # ── Account ──────────────────────────────────────────────────────

    async def get_balance(self) -> dict[str, Any]:
        """
        UTA V3: unified-account assets.
        Bitget unified (UTA) accounts must use the V3 API — the classic V2
        account/spot endpoints return 40085 ("统一账户模式不支持经典账户接口").
        """
        assets_resp, tickers = await asyncio.gather(
            self._request("GET", "/api/v3/account/assets"),
            self._public_get("/api/v2/spot/market/tickers", {"productType": "SPOT"}),
        )
        if isinstance(assets_resp, dict) and assets_resp.get("error"):
            return assets_resp

        raw_assets = assets_resp.get("assets", []) if isinstance(assets_resp, dict) else []

        # 24h change map from public spot tickers (market data is mode-independent)
        change_map: dict[str, float] = {}
        if isinstance(tickers, list):
            for t in tickers:
                sym = t.get("symbol", "")
                if sym.endswith("USDT"):
                    coin = sym[:-4]
                    try:
                        change_map[coin] = float(t.get("change24h", 0)) * 100
                    except (ValueError, TypeError):
                        pass

        account_assets = []
        for a in raw_assets:
            if not isinstance(a, dict):
                continue
            coin = a.get("coin", "")
            account_assets.append({
                "coin": coin,
                "equity": a.get("balance", "0"),        # coin amount
                "available": a.get("available", "0"),
                "frozen": a.get("locked", "0"),
                "usdtEquity": a.get("usdValue", "0"),   # USD value
                "change24h": change_map.get(coin),
            })

        total_usdt = str(assets_resp.get("usdtEquity", "0") or "0") if isinstance(assets_resp, dict) else "0"

        # UTA exposes a single unified account; surface it as "spot" so the
        # existing renderers (which look for accountType=="spot") display it.
        accounts = [{
            "accountType": "spot",
            "usdtBalance": total_usdt,
            "totalEquity": total_usdt,
            "accountAssets": account_assets,
        }]
        return accounts

    async def get_positions(self) -> list[dict] | dict[str, Any]:
        """UTA V3: USDT-FUTURES + COIN-FUTURES positions (category param, not productType)."""
        usdt, coin = await asyncio.gather(
            self._request("GET", "/api/v3/position/current-position", {"category": "USDT-FUTURES"}),
            self._request("GET", "/api/v3/position/current-position", {"category": "COIN-FUTURES"}),
        )

        def extract(r):
            if isinstance(r, dict) and r.get("error"):
                return None, r
            if isinstance(r, dict) and "list" in r:
                return (r.get("list") or []), None
            if isinstance(r, list):
                return r, None
            return [], None

        usdt_list, usdt_err = extract(usdt)
        coin_list, coin_err = extract(coin)
        if usdt_err and coin_err:
            return usdt_err  # surface the error to the frontend

        raw = (usdt_list or []) + (coin_list or [])
        mapped = []
        for p in raw:
            if not isinstance(p, dict):
                continue
            mapped.append({
                "symbol": p.get("symbol", ""),
                "posSide": p.get("posSide", ""),
                "pos": p.get("total", "0"),
                "avgPx": p.get("avgPrice", "0"),
                "leverage": p.get("leverage", "0"),
                "upl": p.get("unrealisedPnl", "0"),
                "uplRatio": p.get("profitRate", "0"),
                "liqPx": p.get("liquidationPrice", ""),
            })
        return mapped

    async def get_orders(self) -> dict[str, Any]:
        """UTA V3: spot + futures unfilled orders, plus plan (strategy) orders.

        Uses the unified V3 trade endpoints with a `category` param instead of
        the classic V2 spot/mix trade endpoints (which return 40085 on UTA).
        """
        spot, fut_usdt, fut_coin, plan_usdt, plan_coin = await asyncio.gather(
            self._request("GET", "/api/v3/trade/unfilled-orders", {"category": "SPOT"}),
            self._request("GET", "/api/v3/trade/unfilled-orders", {"category": "USDT-FUTURES"}),
            self._request("GET", "/api/v3/trade/unfilled-orders", {"category": "COIN-FUTURES"}),
            self._request("GET", "/api/v3/trade/unfilled-strategy-orders", {"category": "USDT-FUTURES"}),
            self._request("GET", "/api/v3/trade/unfilled-strategy-orders", {"category": "COIN-FUTURES"}),
        )

        def extract(r):
            if isinstance(r, dict) and r.get("error"):
                return None, r
            if isinstance(r, dict) and "list" in r:
                return (r.get("list") or []), None
            if isinstance(r, list):
                return r, None
            return [], None

        spot_list, spot_err = extract(spot)
        fut_usdt_list, _ = extract(fut_usdt)
        fut_coin_list, _ = extract(fut_coin)
        plan_usdt_list, _ = extract(plan_usdt)
        plan_coin_list, _ = extract(plan_coin)

        def map_order(o):
            return {
                "symbol": o.get("symbol", ""),
                "orderType": o.get("orderType", ""),
                "side": o.get("side", ""),
                "price": o.get("price", ""),
                "size": o.get("qty", ""),
            }

        spot_orders = [map_order(o) for o in (spot_list or []) if isinstance(o, dict)]
        futures_raw = (fut_usdt_list or []) + (fut_coin_list or [])
        futures_orders = [map_order(o) for o in futures_raw if isinstance(o, dict)]

        def map_plan(o):
            pos_side = o.get("posSide", "")
            return {
                "symbol": o.get("symbol", ""),
                "side": "buy" if pos_side == "long" else "sell",
                "triggerPrice": o.get("triggerPrice", ""),
                "executePrice": o.get("triggerOrderPrice")
                                 or o.get("tpLimitPrice") or o.get("slLimitPrice") or "",
                "size": o.get("qty", ""),
            }

        plan_raw = (plan_usdt_list or []) + (plan_coin_list or [])
        plan_orders = [map_plan(o) for o in plan_raw if isinstance(o, dict)]

        # Surface errors only when every sub-request for a section failed
        spot_section = spot_err if spot_err else spot_orders
        futures_section = fut_usdt if (isinstance(fut_usdt, dict) and fut_usdt.get("error")
                                       and isinstance(fut_coin, dict) and fut_coin.get("error")) else futures_orders
        plan_section = plan_usdt if (isinstance(plan_usdt, dict) and plan_usdt.get("error")
                                     and isinstance(plan_coin, dict) and plan_coin.get("error")) else plan_orders

        return {"spot": spot_section, "futures": futures_section, "plan": plan_section}

    async def get_earn(self) -> dict[str, Any]:
        """Earn (理财) holdings.

        - 定期/锁仓 (Fixed/Locked): V3 `/api/v3/earn/elite-assets` (UTA-compatible).
        - 活期 (Flexible savings): Bitget has **no V3 endpoint** for this, and the
          classic V2 savings endpoint is blocked under UTA (40085). We still try
          V2 so classic-account users keep working, but a UTA-blocked response is
          converted into an `unsupported` marker instead of a raw error.
        """
        elite_raw, savings_raw = await asyncio.gather(
            self._request("GET", "/api/v3/earn/elite-assets"),
            self._request("GET", "/api/v2/earn/savings/assets"),
        )

        # ── 定期 / 锁仓 (elite) ───────────────────────────────────────
        if isinstance(elite_raw, dict) and elite_raw.get("error"):
            if elite_raw.get("code") == "40085":
                earn = {"unsupported": True,
                        "reason": "统一账户模式暂不支持查询定期理财"}
            else:
                earn = elite_raw
        elif isinstance(elite_raw, dict):
            earn = []
            for item in (elite_raw.get("resultList") or []):
                if not isinstance(item, dict):
                    continue
                amt = (item.get("amount") or item.get("holdingAmount")
                       or item.get("principal") or "0")
                usd = (item.get("usdtAmount") or item.get("usdValue") or amt)
                earn.append({
                    "productName": item.get("productName")
                                   or item.get("productId") or "",
                    "coin": item.get("coin") or item.get("currency") or "",
                    "amount": amt,
                    "usdtEquity": usd,
                })
        else:
            earn = elite_raw  # error passthrough

        # ── 活期 (flexible savings) ───────────────────────────────────
        if isinstance(savings_raw, dict) and savings_raw.get("error"):
            if savings_raw.get("code") == "40085":
                savings = {"unsupported": True,
                           "reason": "统一账户模式暂不支持查询活期理财（Bitget 未提供 UTA 接口）"}
            else:
                savings = savings_raw
        elif isinstance(savings_raw, list):
            savings = savings_raw
        elif isinstance(savings_raw, dict):
            inner = savings_raw.get("data", savings_raw)
            savings = inner if isinstance(inner, list) else [inner] if isinstance(inner, dict) else []
        else:
            savings = savings_raw  # error passthrough

        return {"savings": savings, "earn": earn}

    # ── Market Data (public, no auth required) ───────────────────────

    async def _public_get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """Public GET request — no API credentials needed."""
        query = str(httpx.QueryParams(params)) if params else ""
        request_path = path + ("?" + query if query else "")
        url = self.BASE_URL + request_path
        try:
            async with httpx.AsyncClient(proxy=self.proxy, timeout=self.TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != "00000":
                    return {"error": data.get("msg", "Unknown error"), "code": data.get("code")}
                return data.get("data", data)
        except httpx.TimeoutException:
            return {"error": f"Bitget API timeout after {self.TIMEOUT}s"}
        except httpx.HTTPStatusError as e:
            # Surface the API-level code (e.g. 40085 for UTA mode) so callers
            # can detect and handle it gracefully.
            code = None
            try:
                code = e.response.json().get("code")
            except Exception:
                pass
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}", "code": code}
        except Exception as e:
            return {"error": f"Bitget API error: {str(e)}"}

    async def get_candles(self, symbol: str, granularity: str = "4h", limit: int = 100) -> Any:
        """Get spot K-line/OHLCV data."""
        return await self._public_get("/api/v2/spot/market/candles", {
            "symbol": symbol, "granularity": granularity, "limit": str(limit),
        })

    async def get_depth(self, symbol: str, limit: int = 20) -> Any:
        """Get spot orderbook depth."""
        return await self._public_get("/api/v2/spot/market/depth", {
            "symbol": symbol, "limit": str(limit), "type": "step0",
        })

    async def get_trades(self, symbol: str, limit: int = 50) -> Any:
        """Get recent spot trade records."""
        return await self._public_get("/api/v2/spot/market/fills", {
            "symbol": symbol, "limit": str(limit),
        })

    # ── Combined ─────────────────────────────────────────────────────

    async def get_all(self) -> dict[str, Any]:
        """Fetch all account data concurrently."""
        balance, positions, orders, earn = await asyncio.gather(
            self.get_balance(),
            self.get_positions(),
            self.get_orders(),
            self.get_earn(),
            return_exceptions=True,
        )
        return {
            "balance": balance if not isinstance(balance, Exception) else {"error": str(balance)},
            "positions": positions if not isinstance(positions, Exception) else {"error": str(positions)},
            "orders": orders if not isinstance(orders, Exception) else {"error": str(orders)},
            "earn": earn if not isinstance(earn, Exception) else {"error": str(earn)},
        }
