import asyncio
import json
from typing import Any

from cli_utils import cli_command, cli_environment


class OKXClient:
    """OKX CLI wrapper — calls `okx --json` commands via subprocess."""

    PROFILE = "claude code"
    TIMEOUT = 10  # seconds per CLI call

    def __init__(self):
        self._env = cli_environment()

    async def run_cli(self, *args: str) -> dict[str, Any]:
        """Run `okx --json <args>` and return parsed JSON."""
        cmd = cli_command(
            "okx", "--profile", self.PROFILE, "--json", *args, env=self._env
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.TIMEOUT
            )
        except asyncio.TimeoutError:
            return {"error": f"Command timed out after {self.TIMEOUT}s", "cmd": " ".join(cmd)}
        except FileNotFoundError:
            return {"error": "okx CLI not found in PATH", "cmd": " ".join(cmd)}

        if proc.returncode != 0:
            return {"error": stderr.decode().strip(), "cmd": " ".join(cmd)}

        text = stdout.decode().strip()
        if not text:
            return {"error": "Empty response from CLI", "cmd": " ".join(cmd)}

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON from CLI", "raw": text[:500], "cmd": " ".join(cmd)}

    # ── Account ──────────────────────────────────────────────────────

    async def get_balance(self) -> dict[str, Any]:
        """Concurrent: trading balance + funding asset balance."""
        trading, asset = await asyncio.gather(
            self.run_cli("account", "balance"),
            self.run_cli("account", "asset-balance"),
        )
        return {"trading": trading, "asset": asset}

    async def get_positions(self) -> Any:
        return await self.run_cli("account", "positions")

    async def get_orders(self) -> dict[str, Any]:
        """Concurrent: swap + spot open orders."""
        swap, spot = await asyncio.gather(
            self.run_cli("swap", "orders"),
            self.run_cli("spot", "orders"),
        )
        return {"swap": swap, "spot": spot}

    async def get_earn(self) -> dict[str, Any]:
        """Concurrent: savings balance + fixed-term orders."""
        savings, fixed = await asyncio.gather(
            self.run_cli("earn", "savings", "balance"),
            self.run_cli("earn", "savings", "fixed-orders"),
        )
        return {"savings": savings, "fixed": fixed}

    async def get_bots(self) -> dict[str, Any]:
        """Concurrent: spot grid + contract grid + DCA bot orders."""
        spot_grid, contract_grid, dca = await asyncio.gather(
            self.run_cli("bot", "grid", "orders", "--algoOrdType", "grid"),
            self.run_cli("bot", "grid", "orders", "--algoOrdType", "contract_grid"),
            self.run_cli("bot", "dca", "orders"),
        )
        return {"spot_grid": spot_grid, "contract_grid": contract_grid, "dca": dca}

    # ── Market ───────────────────────────────────────────────────────

    async def get_ticker(self, inst_id: str) -> Any:
        return await self.run_cli("market", "ticker", inst_id)

    async def get_orderbook(self, inst_id: str, depth: int = 20) -> Any:
        return await self.run_cli("market", "orderbook", inst_id, "--sz", str(depth))

    async def get_funding_rate(self, inst_id: str) -> Any:
        return await self.run_cli("market", "funding-rate", inst_id)

    async def get_open_interest(self, inst_id: str) -> Any:
        return await self.run_cli(
            "market", "open-interest", "--instType", "SWAP", "--instId", inst_id
        )

    async def get_trades(self, inst_id: str, limit: int = 100) -> Any:
        return await self.run_cli("market", "trades", inst_id, "--limit", str(limit))

    async def get_candles(self, inst_id: str, bar: str = "1H", limit: int = 60) -> Any:
        return await self.run_cli(
            "market", "candles", inst_id, "--bar", bar, "--limit", str(limit)
        )

    async def get_indicator(
        self, indicator: str, inst_id: str, bar: str = "1H", params: list | None = None
    ) -> Any:
        args = ["market", "indicator", indicator, inst_id, "--bar", bar]
        if params:
            args += ["--params", json.dumps(params)]
        return await self.run_cli(*args)

    # ── Batch Fetch ──────────────────────────────────────────────────

    INDICATORS = [
        ("rsi", "rsi", None),           # API default [14]
        ("macd", "macd", None),         # API default [12,26,9]
        ("ema7", "ema", [7]),           # must specify, API has no default period
        ("bb", "bb", None),             # API default [20,2]
        ("supertrend", "supertrend", None),
        ("kdj", "kdj", None),
        ("adx", "adx", None),           # API default [14]
        ("atr", "atr", None),           # API default [14]
        ("obv", "obv", None),
    ]

    @staticmethod
    def _compute_ema(closes: list[float], period: int) -> float | None:
        """Compute EMA from a list of close prices (oldest first)."""
        if len(closes) < period:
            return None
        k = 2 / (period + 1)
        ema = sum(closes[:period]) / period  # SMA seed
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    def _is_empty_indicator(self, result: Any) -> bool:
        """Check if an indicator result is empty (CLI returns [] for MA-type)."""
        if not isinstance(result, list):
            return False
        d = result[0] if result else None
        if not isinstance(d, dict) or "data" not in d:
            return False
        inner = d["data"][0] if isinstance(d["data"], list) and d["data"] else None
        if not isinstance(inner, dict):
            return False
        for tf_data in inner.get("timeframes", {}).values():
            for vals in tf_data.get("indicators", {}).values():
                if isinstance(vals, list) and len(vals) == 0:
                    return True
        return False

    async def get_indicators(
        self, inst_id: str, timeframes: list[str] | None = None
    ) -> dict[str, Any]:
        """Fetch all indicators across all timeframes concurrently."""
        if timeframes is None:
            timeframes = ["1H", "4H", "1Dutc"]

        tasks = {}
        for tf in timeframes:
            for key, name, params in self.INDICATORS:
                tasks[f"{key}_{tf}"] = self.get_indicator(name, inst_id, tf, params)

        results = {}
        keys = list(tasks.keys())
        values = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for key, val in zip(keys, values):
            if isinstance(val, Exception):
                results[key] = {"error": str(val)}
            else:
                results[key] = val

        # Fallback: compute MA-type indicators from candle data when CLI returns empty
        empty_tfs = set()
        for key, val in results.items():
            if self._is_empty_indicator(val):
                for tf in timeframes:
                    if key.endswith(f"_{tf}"):
                        empty_tfs.add(tf)

        if empty_tfs:
            candle_tasks = {tf: self.get_candles(inst_id, tf, 100) for tf in empty_tfs}
            candle_results = {}
            candle_keys = list(candle_tasks.keys())
            candle_vals = await asyncio.gather(*candle_tasks.values(), return_exceptions=True)
            for k, v in zip(candle_keys, candle_vals):
                candle_results[k] = v

            for key, val in results.items():
                if not self._is_empty_indicator(val):
                    continue
                for tf in timeframes:
                    if not key.endswith(f"_{tf}"):
                        continue
                    indicator_key = key[: -(len(tf) + 1)]
                    # Find the period from INDICATORS
                    period = None
                    for ik, name, params in self.INDICATORS:
                        if ik == indicator_key and params:
                            period = params[0]
                            break
                    if period is None:
                        continue
                    candles = candle_results.get(tf)
                    if not isinstance(candles, list) or len(candles) < period:
                        continue
                    # candles are newest-first, reverse for EMA computation
                    closes = [float(c[4]) for c in reversed(candles)]
                    ema_val = self._compute_ema(closes, period)
                    if ema_val is not None:
                        api_key = indicator_key.replace(str(period), "").upper()
                        results[key] = [{
                            "data": [{"instId": inst_id, "timeframes": {
                                tf: {"indicators": {
                                    api_key: [{"ts": str(int(candles[0][0])), "values": {str(period): f"{ema_val:.1f}"}}]
                                }}
                            }}],
                            "mode": "live",
                            "timestamp": int(candles[0][0]),
                        }]

        # Reorganize by timeframe
        by_tf: dict[str, dict] = {tf: {} for tf in timeframes}
        for key, val in results.items():
            for tf in timeframes:
                if key.endswith(f"_{tf}"):
                    indicator_key = key[: -(len(tf) + 1)]
                    by_tf[tf][indicator_key] = val

        return {"instId": inst_id, "indicators": by_tf}

    async def get_full_market_data(
        self, inst_id: str, timeframes: list[str] | None = None
    ) -> dict[str, Any]:
        """Fetch everything needed for analysis concurrently."""
        if timeframes is None:
            timeframes = ["1H", "4H", "1Dutc"]

        # funding rate & open interest require -SWAP suffix
        swap_id = inst_id if inst_id.endswith("-SWAP") else f"{inst_id}-SWAP"

        results = await asyncio.gather(
            self.get_ticker(inst_id),
            self.get_indicators(inst_id, timeframes),
            self.get_orderbook(inst_id),
            self.get_funding_rate(swap_id),
            self.get_open_interest(swap_id),
            self.get_trades(inst_id),
            return_exceptions=True,
        )

        keys = ["ticker", "indicators", "orderbook", "fundingRate", "openInterest", "trades"]
        data = {}
        for key, val in zip(keys, results):
            if isinstance(val, Exception):
                data[key] = {"error": str(val)}
            else:
                data[key] = val

        data["instId"] = inst_id
        return data
