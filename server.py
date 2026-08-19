import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import uuid4

import html2text

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from okx_client import OKXClient
from bitget_client import BitgetClient
from analyzer import Analyzer
from cli_utils import cli_available
from stock_analyzer import StockAnalyzer
from security import (
    REPORT_ID_PATTERN,
    SYMBOL_PATTERN,
    sanitize_report_html,
    validate_secret,
    validate_symbol,
)

app = FastAPI(title="Trade Dashboard")
allowed_hosts = [
    host.strip()
    for host in os.environ.get(
        "DASHBOARD_ALLOWED_HOSTS", "127.0.0.1,localhost"
    ).split(",")
    if host.strip()
]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
okx = OKXClient()
bitget = BitgetClient()
analyzer = Analyzer()
stock_analyzer = StockAnalyzer()
analysis_lock = asyncio.Lock()

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

MarketSymbol = Annotated[str, PathParam(pattern=SYMBOL_PATTERN.pattern)]
ReportID = Annotated[str, PathParam(pattern=REPORT_ID_PATTERN.pattern)]


async def _analysis_slot():
    """Reject overlapping expensive analyses instead of queueing unbounded work."""
    if analysis_lock.locked():
        raise HTTPException(status_code=409, detail="An analysis is already running")
    await analysis_lock.acquire()
    try:
        yield
    finally:
        analysis_lock.release()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Enforce same-origin mutations and safe defaults for sensitive responses."""
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            same_origin = (
                parsed.scheme in {"http", "https"}
                and parsed.netloc.lower() == request.headers.get("host", "").lower()
            )
            if not same_origin:
                return JSONResponse(
                    status_code=403, content={"detail": "Cross-origin request rejected"}
                )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    sensitive_prefixes = ("/api/account", "/api/analyze", "/api/reports", "/api/market/cache")
    if request.url.path.startswith(sensitive_prefixes):
        response.headers["Cache-Control"] = "no-store"
    return response


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


def _write_private_text(path: Path, content: str) -> None:
    """Atomically replace a local sensitive file with owner-only permissions."""
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def save_report(symbol: str, html: str, timestamp: str) -> str:
    """Save report to disk (JSON + Markdown). Returns the report ID (filename stem)."""
    symbol = validate_symbol(symbol)
    dt = datetime.fromisoformat(timestamp)
    html = sanitize_report_html(html)
    report_id = f'{dt.strftime("%Y%m%d_%H%M%S_%f")}_{symbol}_{uuid4().hex[:8]}'
    report_data = {
        "id": report_id,
        "symbol": symbol,
        "html": html,
        "timestamp": timestamp,
    }
    report_file = REPORTS_DIR / f"{report_id}.json"
    _write_private_text(report_file, json.dumps(report_data, ensure_ascii=False, indent=2))

    # Auto-generate Markdown version for Claude Code
    md_file = REPORTS_DIR / f"{report_id}.md"
    _write_private_text(md_file, _html_to_markdown(html, symbol, timestamp))

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
async def market_ticker(inst_id: MarketSymbol):
    return await okx.get_ticker(inst_id)


@app.get("/api/bitget/ticker/{symbol}")
async def bitget_ticker(symbol: MarketSymbol):
    """Get Bitget spot ticker (for stock tokens like QQQONUSDT)."""
    result = await bitget._public_get(
        "/api/v2/spot/market/tickers", {"symbol": symbol}
    )
    if isinstance(result, list) and result:
        return result[0]
    return result


@app.get("/api/bitget/candles/{symbol}")
async def bitget_candles(
    symbol: MarketSymbol,
    granularity: Annotated[str, Query(pattern=r"^(?:[1-9]\d?(?:min|h)|[1-9]\d?day)$")] = "4h",
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    return await bitget.get_candles(symbol, granularity, limit)


@app.get("/api/bitget/depth/{symbol}")
async def bitget_depth(
    symbol: MarketSymbol, limit: Annotated[int, Query(ge=1, le=150)] = 20
):
    return await bitget.get_depth(symbol, limit)


@app.get("/api/bitget/trades/{symbol}")
async def bitget_trades(
    symbol: MarketSymbol, limit: Annotated[int, Query(ge=1, le=500)] = 50
):
    return await bitget.get_trades(symbol, limit)


@app.get("/api/market/indicators/{inst_id:path}")
async def market_indicators(
    inst_id: MarketSymbol,
    timeframes: Annotated[str, Query(min_length=1, max_length=64)] = "1H,4H,1Dutc",
):
    tfs = [t.strip() for t in timeframes.split(",") if t.strip()]
    allowed = {"1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6Hutc", "12Hutc", "1Dutc", "2Dutc", "3Dutc", "1Wutc"}
    if (
        not tfs
        or len(tfs) > 3
        or len(set(tfs)) != len(tfs)
        or any(tf not in allowed for tf in tfs)
    ):
        raise HTTPException(status_code=400, detail="Unsupported timeframes")
    return await okx.get_indicators(inst_id, tfs)


@app.get("/api/market/orderbook/{inst_id:path}")
async def market_orderbook(
    inst_id: MarketSymbol, depth: Annotated[int, Query(ge=1, le=400)] = 20
):
    return await okx.get_orderbook(inst_id, depth)


@app.get("/api/market/funding-rate/{inst_id:path}")
async def market_funding_rate(inst_id: MarketSymbol):
    return await okx.get_funding_rate(inst_id)


@app.get("/api/market/open-interest/{inst_id:path}")
async def market_open_interest(inst_id: MarketSymbol):
    return await okx.get_open_interest(inst_id)


@app.get("/api/market/trades/{inst_id:path}")
async def market_trades(
    inst_id: MarketSymbol, limit: Annotated[int, Query(ge=1, le=500)] = 100
):
    return await okx.get_trades(inst_id, limit)


@app.get("/api/market/candles/{inst_id:path}")
async def market_candles(
    inst_id: MarketSymbol,
    bar: Annotated[str, Query(pattern=r"^(?:[1-9]\d?m|[1-9]\d?H(?:utc)?|[1-9]\d?[DWM](?:utc)?)$")] = "1H",
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    return await okx.get_candles(inst_id, bar, limit)


# ── Analysis endpoint ────────────────────────────────────────────────

class BitgetCredentials(BaseModel):
    api_key: str
    secret_key: str
    passphrase: str

    @field_validator("api_key", "secret_key", "passphrase")
    @classmethod
    def safe_credential(cls, value: str) -> str:
        return validate_secret(value)


def _update_env_file(values: dict[str, str]) -> None:
    """Update selected keys without destroying unrelated local settings."""
    env_path = Path(__file__).parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(values)
    updated: list[str] = []
    for line in lines:
        key = line.partition("=")[0].strip() if "=" in line else ""
        if key in values:
            if key in remaining:
                updated.append(f"{key}={json.dumps(remaining.pop(key))}")
            continue
        updated.append(line)
    updated.extend(f"{key}={json.dumps(value)}" for key, value in remaining.items())
    _write_private_text(env_path, "\n".join(updated) + "\n")


@app.post("/api/account/bitget/config")
async def update_bitget_credentials(creds: BitgetCredentials):
    """Update Bitget API credentials at runtime."""
    _update_env_file({
        "BITGET_API_KEY": creds.api_key,
        "BITGET_SECRET_KEY": creds.secret_key,
        "BITGET_PASSPHRASE": creds.passphrase,
    })
    bitget.api_key = creds.api_key
    bitget.secret_key = creds.secret_key
    bitget.passphrase = creds.passphrase
    return {"status": "ok", "configured": bitget.configured}


class AnalyzeRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=1)
    include_positions: bool = True

    @field_validator("symbols")
    @classmethod
    def safe_symbols(cls, values: list[str]) -> list[str]:
        return [validate_symbol(value) for value in values]


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, _slot=Depends(_analysis_slot)):
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
async def list_reports(limit: Annotated[int, Query(ge=1, le=100)] = 20):
    """List saved reports, newest first."""
    reports = []
    for f in sorted(REPORTS_DIR.glob("*.json"), reverse=True):
        if len(reports) >= limit:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reports.append({
                "id": f.stem,
                "symbol": data["symbol"],
                "timestamp": data["timestamp"],
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return reports


@app.get("/api/reports/{report_id}")
async def get_report(report_id: ReportID):
    """Get a single report by ID."""
    report_file = REPORTS_DIR / f"{report_id}.json"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        data = json.loads(report_file.read_text(encoding="utf-8"))
        data["html"] = sanitize_report_html(data.get("html", ""))
        return data
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Corrupt report file")


# ── Report save (for Claude Code semi-automatic flow) ─────────────────

class ReportSaveRequest(BaseModel):
    symbol: str
    html: str = Field(min_length=1, max_length=2_000_000)
    timestamp: str | None = Field(default=None, max_length=64)

    @field_validator("symbol")
    @classmethod
    def safe_symbol(cls, value: str) -> str:
        return validate_symbol(value)

    @field_validator("timestamp")
    @classmethod
    def valid_timestamp(cls, value: str | None) -> str | None:
        if value is not None:
            datetime.fromisoformat(value)
        return value


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
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to save report") from exc
    return {"status": "saved", "id": report_id, "symbol": req.symbol}


# ── Market data cache (for Claude Code semi-automatic flow) ────────────

@app.post("/api/market/cache/{inst_id:path}")
async def cache_market_data(inst_id: MarketSymbol, include_account: bool = True):
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

    cache_file = CACHE_DIR / f"analysis_data_{inst_id}.json"
    _write_private_text(cache_file, json.dumps(cache_entry, ensure_ascii=False, indent=2))

    return {
        "status": "cached",
        "file": cache_file.relative_to(Path(__file__).parent).as_posix(),
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
