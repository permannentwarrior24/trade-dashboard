import asyncio
import json
from datetime import datetime
from pathlib import Path

import html2text

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from okx_client import OKXClient
from bitget_client import BitgetClient
from analyzer import Analyzer
from cli_utils import cli_available
from stock_analyzer import StockAnalyzer

app = FastAPI(title="Trade Dashboard")
okx = OKXClient()
bitget = BitgetClient()
analyzer = Analyzer()
stock_analyzer = StockAnalyzer()

# Symbol mapping: frontend symbol → OKX instrument ID for stock tokens
SYMBOL_MAP = {
    "QQQONUSDT": "QQQ-USDT-SWAP",
}
STOCK_TOKENS = {"QQQONUSDT", "QQQ-USDT-SWAP"}

STATIC_DIR = Path(__file__).parent / "static"
REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


# ── Startup check ────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    issues = []
    if not cli_available("okx"):
        issues.append("okx CLI not found. Install: npm install -g @okx_ai/okx-trade-cli")
    if not cli_available("claude"):
        issues.append("claude CLI not found. Install: npm install -g @anthropic-ai/claude-code")
    if not bitget.configured:
        issues.append("Bitget API credentials not set (BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE)")
    if issues:
        print("WARNING: " + " | ".join(issues))


# ── Report persistence ───────────────────────────────────────────────

def _html_to_markdown(html: str, symbol: str, timestamp: str) -> str:
    """Convert report HTML to clean Markdown for Claude Code consumption."""
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_images = True
    h.protect_links = True
    body = h.handle(html)
    return f"# {symbol} 技术分析报告\n\n> 生成时间: {timestamp}\n\n{body}"


def save_report(symbol: str, html: str, timestamp: str) -> str:
    """Save report to disk (JSON + Markdown). Returns the report ID (filename stem)."""
    dt = datetime.fromisoformat(timestamp)
    report_id = dt.strftime("%Y%m%d_%H%M%S") + "_" + symbol.replace("/", "-")
    report_data = {
        "id": report_id,
        "symbol": symbol,
        "html": html,
        "timestamp": timestamp,
    }
    report_file = REPORTS_DIR / f"{report_id}.json"
    report_file.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Auto-generate Markdown version for Claude Code
    md_file = REPORTS_DIR / f"{report_id}.md"
    md_file.write_text(_html_to_markdown(html, symbol, timestamp), encoding="utf-8")

    return report_id


# ── Static files ─────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/market")
async def market_page():
    return FileResponse(STATIC_DIR / "market.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Account endpoints ────────────────────────────────────────────────

@app.get("/api/account/balance")
async def account_balance():
    return await okx.get_balance()


@app.get("/api/account/positions")
async def account_positions():
    return await okx.get_positions()


@app.get("/api/account/orders")
async def account_orders():
    return await okx.get_orders()


@app.get("/api/account/earn")
async def account_earn():
    return await okx.get_earn()


@app.get("/api/account/bots")
async def account_bots():
    return await okx.get_bots()


@app.get("/api/account/all")
async def account_all():
    """Combined account data: OKX + Bitget, fetched concurrently."""
    okx_data, bitget_data = await asyncio.gather(
        _fetch_okx_all(),
        bitget.get_all(),
        return_exceptions=True,
    )
    return {
        "okx": okx_data if not isinstance(okx_data, Exception) else {"error": str(okx_data)},
        "bitget": bitget_data if not isinstance(bitget_data, Exception) else {"error": str(bitget_data)},
    }


async def _fetch_okx_all():
    balance, positions, orders, earn, bots = await asyncio.gather(
        okx.get_balance(),
        okx.get_positions(),
        okx.get_orders(),
        okx.get_earn(),
        okx.get_bots(),
    )
    return {"balance": balance, "positions": positions, "orders": orders, "earn": earn, "bots": bots}


# ── Market endpoints ─────────────────────────────────────────────────

@app.get("/api/market/ticker/{inst_id:path}")
async def market_ticker(inst_id: str):
    return await okx.get_ticker(inst_id)


@app.get("/api/bitget/ticker/{symbol}")
async def bitget_ticker(symbol: str):
    """Get Bitget spot ticker (for stock tokens like QQQONUSDT)."""
    result = await bitget._request("GET", "/api/v2/spot/market/tickers", {"symbol": symbol})
    if isinstance(result, list) and result:
        return result[0]
    return result


@app.get("/api/bitget/candles/{symbol}")
async def bitget_candles(symbol: str, granularity: str = "4h", limit: int = 100):
    return await bitget.get_candles(symbol, granularity, limit)


@app.get("/api/bitget/depth/{symbol}")
async def bitget_depth(symbol: str, limit: int = 20):
    return await bitget.get_depth(symbol, limit)


@app.get("/api/bitget/trades/{symbol}")
async def bitget_trades(symbol: str, limit: int = 50):
    return await bitget.get_trades(symbol, limit)


@app.get("/api/market/indicators/{inst_id:path}")
async def market_indicators(inst_id: str, timeframes: str = "1H,4H,1Dutc"):
    tfs = [t.strip() for t in timeframes.split(",") if t.strip()]
    return await okx.get_indicators(inst_id, tfs)


@app.get("/api/market/orderbook/{inst_id:path}")
async def market_orderbook(inst_id: str, depth: int = 20):
    return await okx.get_orderbook(inst_id, depth)


@app.get("/api/market/funding-rate/{inst_id:path}")
async def market_funding_rate(inst_id: str):
    return await okx.get_funding_rate(inst_id)


@app.get("/api/market/open-interest/{inst_id:path}")
async def market_open_interest(inst_id: str):
    return await okx.get_open_interest(inst_id)


@app.get("/api/market/trades/{inst_id:path}")
async def market_trades(inst_id: str, limit: int = 100):
    return await okx.get_trades(inst_id, limit)


@app.get("/api/market/candles/{inst_id:path}")
async def market_candles(inst_id: str, bar: str = "1H", limit: int = 100):
    return await okx.get_candles(inst_id, bar, limit)


# ── Analysis endpoint ────────────────────────────────────────────────

class BitgetCredentials(BaseModel):
    api_key: str
    secret_key: str
    passphrase: str


@app.post("/api/account/bitget/config")
async def update_bitget_credentials(creds: BitgetCredentials):
    """Update Bitget API credentials at runtime."""
    bitget.api_key = creds.api_key
    bitget.secret_key = creds.secret_key
    bitget.passphrase = creds.passphrase
    # Also persist to .env file
    env_path = Path(__file__).parent / ".env"
    env_lines = [
        f'BITGET_API_KEY="{creds.api_key}"',
        f'BITGET_SECRET_KEY="{creds.secret_key}"',
        f'BITGET_PASSPHRASE="{creds.passphrase}"',
    ]
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    return {"status": "ok", "configured": bitget.configured}


class AnalyzeRequest(BaseModel):
    symbols: list[str]
    include_positions: bool = True


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    if not req.symbols:
        raise HTTPException(status_code=400, detail="No symbols provided")

    symbol = req.symbols[0]  # v1: analyze first symbol
    is_stock = symbol in STOCK_TOKENS
    okx_symbol = SYMBOL_MAP.get(symbol, symbol)

    # Fetch market data and optionally account data concurrently
    tasks = [okx.get_full_market_data(okx_symbol)]
    if req.include_positions:
        tasks.append(okx.get_balance())
        tasks.append(okx.get_positions())
        tasks.append(okx.get_bots())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    market_data = results[0] if not isinstance(results[0], Exception) else {"error": str(results[0])}
    account_data = None

    if req.include_positions and len(results) >= 4:
        account_data = {
            "balance": results[1] if not isinstance(results[1], Exception) else {"error": str(results[1])},
            "positions": results[2] if not isinstance(results[2], Exception) else {"error": str(results[2])},
            "bots": results[3] if not isinstance(results[3], Exception) else {"error": str(results[3])},
        }

    if is_stock:
        macro_data = await stock_analyzer.fetch_macro_data()
        result = await stock_analyzer.analyze(market_data, macro_data, account_data)
    else:
        result = await analyzer.analyze(market_data, account_data)

    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])

    # Save report to disk
    report_id = save_report(symbol, result["html"], result["timestamp"])
    result["id"] = report_id

    return result


@app.post("/api/analyze/cancel")
async def cancel_analysis():
    """Cancel the running analysis."""
    analyzer.cancel()
    stock_analyzer.cancel()
    return {"status": "cancelled"}


# ── Report endpoints ──────────────────────────────────────────────────

@app.get("/api/reports")
async def list_reports(limit: int = 20):
    """List saved reports, newest first."""
    reports = []
    for f in sorted(REPORTS_DIR.glob("*.json"), reverse=True):
        if len(reports) >= limit:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reports.append({
                "id": data["id"],
                "symbol": data["symbol"],
                "timestamp": data["timestamp"],
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return reports


@app.get("/api/reports/{report_id}")
async def get_report(report_id: str):
    """Get a single report by ID."""
    report_file = REPORTS_DIR / f"{report_id}.json"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        data = json.loads(report_file.read_text(encoding="utf-8"))
        return data
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Corrupt report file")


# ── Report save (for Claude Code semi-automatic flow) ─────────────────

class ReportSaveRequest(BaseModel):
    symbol: str
    html: str
    timestamp: str | None = None


@app.post("/api/reports/save")
async def save_report_from_claude(req: ReportSaveRequest):
    """
    Accept an HTML report generated by Claude Code in conversation.
    Saves to reports/ directory and returns the report ID.
    Frontend can fetch it via GET /api/reports/{id}.
    """
    timestamp = req.timestamp or datetime.now().isoformat()
    try:
        report_id = save_report(req.symbol, req.html, timestamp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save report: {e}")
    return {"status": "saved", "id": report_id, "symbol": req.symbol}


# ── Market data cache (for Claude Code semi-automatic flow) ────────────

@app.post("/api/market/cache/{inst_id:path}")
async def cache_market_data(inst_id: str, include_account: bool = True):
    """
    Fetch full market data + optionally account data, save to cache file.
    Called by frontend to prepare data for Claude Code consumption.
    Returns the cache file path and a summary.
    """
    tasks = [okx.get_full_market_data(inst_id)]
    if include_account:
        tasks.append(okx.get_balance())
        tasks.append(okx.get_positions())
        tasks.append(okx.get_bots())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    market_data = results[0] if not isinstance(results[0], Exception) else {"error": str(results[0])}

    cache_entry = {
        "instId": inst_id,
        "cached_at": datetime.now().isoformat(),
        "market_data": market_data,
    }

    if include_account and len(results) >= 4:
        cache_entry["account_data"] = {
            "balance": results[1] if not isinstance(results[1], Exception) else {"error": str(results[1])},
            "positions": results[2] if not isinstance(results[2], Exception) else {"error": str(results[2])},
            "bots": results[3] if not isinstance(results[3], Exception) else {"error": str(results[3])},
        }

    safe_name = inst_id.replace("/", "-")
    cache_file = CACHE_DIR / f"analysis_data_{safe_name}.json"
    cache_file.write_text(json.dumps(cache_entry, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": "cached",
        "file": str(cache_file),
        "fields": list(cache_entry.keys()),
    }


# ── Health check ─────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    okx_ok = cli_available("okx")
    claude_ok = cli_available("claude")
    bitget_ok = bitget.configured
    return {"okx": okx_ok, "claude": claude_ok, "bitget": bitget_ok}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8501)
