import asyncio
import json
from datetime import datetime

import yfinance as yf

from cli_utils import cli_command, cli_environment


class StockAnalyzer:
    """Stock-token specific analyzer using 3-pillar framework with US macro data."""

    TIMEOUT = 300  # seconds

    MACRO_SYMBOLS = {
        "^VIX": "vix",
        "DX-Y.NYB": "dxy",
        "^TNX": "tnx_10y",
        "QQQ": "qqq_underlying",
    }

    def __init__(self):
        self._env = cli_environment()
        self._current_proc = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        if self._current_proc and self._current_proc.returncode is None:
            self._current_proc.terminate()

    @staticmethod
    def _extract_base_asset(symbol: str) -> str:
        """Extract base asset from symbol: BTC-USDT -> BTC, QQQ-USDT-SWAP -> QQQ."""
        return symbol.split("-")[0] if "-" in symbol else symbol

    @staticmethod
    def _filter_related(items, base_asset: str):
        """Filter positions list to only those whose instId starts with base_asset."""
        if not items or not isinstance(items, list):
            return items
        base_upper = base_asset.upper()
        return [
            item for item in items
            if isinstance(item, dict)
            and (item.get("instId", "") or "").upper().startswith(base_upper)
        ]

    @staticmethod
    def _filter_bots(bots: dict, base_asset: str):
        """Filter bots dict (keys: contract_grid, spot_grid, dca) to only related symbols."""
        if not isinstance(bots, dict):
            return bots
        result = {}
        base_upper = base_asset.upper()
        for bot_type, bot_list in bots.items():
            if isinstance(bot_list, list):
                result[bot_type] = [
                    b for b in bot_list
                    if isinstance(b, dict)
                    and (b.get("instId", "") or "").upper().startswith(base_upper)
                ]
            elif isinstance(bot_list, dict) and "error" in bot_list:
                result[bot_type] = bot_list
            else:
                result[bot_type] = bot_list
        return result

    async def fetch_macro_data(self) -> dict:
        """Fetch US macro indicators from Yahoo Finance."""
        try:
            return await asyncio.to_thread(self._fetch_macro_sync)
        except Exception as e:
            return {"error": str(e)}

    def _fetch_macro_sync(self) -> dict:
        tickers = yf.Tickers(" ".join(self.MACRO_SYMBOLS.keys()))
        result = {}
        for yf_sym, key in self.MACRO_SYMBOLS.items():
            try:
                t = tickers.tickers.get(yf_sym)
                if t is None:
                    result[key] = {"error": f"ticker {yf_sym} not found"}
                    continue
                info = t.fast_info
                hist = t.history(period="5d")
                if hist.empty:
                    result[key] = {"error": f"no history for {yf_sym}"}
                    continue
                last_row = hist.iloc[-1]
                prev_close = hist.iloc[-2]["Close"] if len(hist) >= 2 else last_row["Close"]
                result[key] = {
                    "symbol": yf_sym,
                    "price": round(float(last_row["Close"]), 2),
                    "prev_close": round(float(prev_close), 2),
                    "change_pct": round((float(last_row["Close"]) / float(prev_close) - 1) * 100, 2),
                    "high": round(float(last_row["High"]), 2),
                    "low": round(float(last_row["Low"]), 2),
                    "date": str(hist.index[-1].date()),
                }
            except Exception as e:
                result[key] = {"error": str(e)}
        return result

    def build_prompt(
        self, market_data: dict, macro_data: dict, account_data: dict | None = None
    ) -> str:
        symbol = market_data.get("instId", "UNKNOWN")
        now = datetime.now().strftime("%Y-%m-%d %H:%M UTC+8")

        sections = [
            "你是专业的美股股票代币分析师，使用三支柱分析框架分析 OKX 上的股票代币永续合约。",
            "以下是当前市场数据（由代码从 OKX API 和 Yahoo Finance 精确获取）。\n",
            f"**分析时间**: {now}",
            f"**标的**: {symbol}（QQQ 美股代币永续合约）\n",
        ]

        # Account data — filter positions/bots to only those related to current symbol
        if account_data:
            base_asset = self._extract_base_asset(symbol)
            sections.append("## 账户状态")
            sections.append("### 余额")
            sections.append(self._fmt(account_data.get("balance", {})))

            raw_positions = account_data.get("positions", [])
            filtered_positions = self._filter_related(raw_positions, base_asset)
            sections.append(f"\n### 当前持仓（SWAP/FUTURES）— 仅 {base_asset} 相关")
            sections.append(self._fmt(filtered_positions) if filtered_positions else "无相关持仓")

            bots = account_data.get("bots")
            if bots and not (isinstance(bots, dict) and "error" in bots):
                filtered_bots = self._filter_bots(bots, base_asset)
                sections.append(f"\n### 交易机器人持仓（DCA/网格）— 仅 {base_asset} 相关")
                sections.append(self._fmt(filtered_bots))
            sections.append("")

        # Macro data from Yahoo Finance
        sections.append("## 美股宏观数据（Yahoo Finance）")
        sections.append(self._fmt(macro_data))
        sections.append("")

        # Market data from OKX
        for key, label in [
            ("ticker", "价格快照（OKX QQQ-USDT-SWAP）"),
            ("fundingRate", "资金费率"),
            ("openInterest", "持仓量（SWAP）"),
        ]:
            sections.append(f"## {label}")
            sections.append(self._fmt(market_data.get(key, {})))
            sections.append("")

        # Indicators by timeframe
        indicators = market_data.get("indicators", {})
        ind_data = indicators.get("indicators", indicators)
        for tf in ["1H", "4H", "1Dutc"]:
            tf_data = ind_data.get(tf, {})
            if tf_data:
                tf_label = "1D" if tf == "1Dutc" else tf
                sections.append(f"## 技术指标 — {tf_label}")
                for ind_key in [
                    "rsi", "macd", "ema7", "bb", "supertrend", "kdj", "adx", "atr", "obv"
                ]:
                    val = tf_data.get(ind_key, {})
                    if val and "error" not in val:
                        sections.append(f"### {ind_key.upper()}")
                        sections.append(self._fmt(val))
                sections.append("")

        # Order book
        sections.append("## 订单簿（±20 档）")
        sections.append(self._fmt(market_data.get("orderbook", {})))
        sections.append("")

        # Recent trades
        sections.append("## 最近成交（100 笔）")
        trades = market_data.get("trades", {})
        if isinstance(trades, list):
            sections.append(self._summarize_trades(trades))
        else:
            sections.append(self._fmt(trades))
        sections.append("")

        # Analysis instructions
        sections.append(self._analysis_instructions())

        return "\n".join(sections)

    def _fmt(self, data) -> str:
        if isinstance(data, (dict, list)):
            return f"```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"
        return str(data)

    def _summarize_trades(self, trades: list) -> str:
        if not trades:
            return "无成交数据"

        buy_vol = sell_vol = 0.0
        buy_count = sell_count = 0
        total_vol = 0.0

        for t in trades[:100]:
            if isinstance(t, list) and len(t) >= 5:
                try:
                    sz = float(t[3])
                    side = t[4]
                    total_vol += sz
                    if side == "buy":
                        buy_vol += sz
                        buy_count += 1
                    else:
                        sell_vol += sz
                        sell_count += 1
                except (ValueError, IndexError):
                    continue
            elif isinstance(t, dict):
                try:
                    sz = float(t.get("sz", 0))
                    side = t.get("side", "")
                    total_vol += sz
                    if side == "buy":
                        buy_vol += sz
                        buy_count += 1
                    else:
                        sell_vol += sz
                        sell_count += 1
                except ValueError:
                    continue

        ratio = buy_vol / sell_vol if sell_vol > 0 else float("inf")
        delta = buy_vol - sell_vol

        summary = {
            "total_trades": len(trades[:100]),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "buy_volume": round(buy_vol, 4),
            "sell_volume": round(sell_vol, 4),
            "total_volume": round(total_vol, 4),
            "buy_sell_ratio": round(ratio, 3),
            "delta": round(delta, 4),
            "delta_direction": "accumulation" if delta > 0 else "distribution",
        }
        raw_sample = trades[:5]
        return (
            f"```json\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n```\n\n"
            f"成交样本（前 5 笔）:\n```json\n{json.dumps(raw_sample, ensure_ascii=False)}\n```"
        )

    def _analysis_instructions(self) -> str:
        return """
---

请严格按照以下框架进行分析，全部输出为 HTML（不要 Markdown）：

## 1. 市场快照
用 <table> 展示：当前价、24h 涨跌幅、24h 高低、24h 成交量、持仓量、资金费率。
同时展示底层 ETF（QQQ）价格和溢价/折价情况。

## 2. 多时间框架技术指标解读
用 <table> 展示 1H/4H/1D 的 RSI、MACD、BB、Supertrend、KDJ、ADX、ATR。
每个指标附简要解读（1-2 句话）。

## 3. 三支柱评分

### Pillar 1: 宏观周期（30%）
基于美股宏观数据分析（替代加密货币的 BTC Rainbow Chart / AHR999）：
- **VIX 恐慌指数**：< 15 低波动看涨，15-25 中性，> 25 高波动看跌，> 30 恐慌
- **美元指数 DXY**：走强 → 压制美股，走弱 → 利好美股
- **10Y 国债收益率 TNX**：上升 → 成长股承压，下降 → 成长股受益
- **QQQ 底层 ETF 价格趋势**：与代币价格对比判断溢价率
- **Fed 政策周期**：加息/降息预期、缩表/扩表
- 评分 0-100：
  - 0-20: 极度恐慌 / 超卖 → Maximum accumulation
  - 20-40: 恐慌消退 / 早期恢复 → Gradual accumulation
  - 40-60: 中性 / 均衡 → Selective positioning
  - 60-80: 乐观 / 超买 → Risk reduction
  - 80-100: 极度贪婪 / 泡沫 → Maximum caution

### Pillar 2: 量价因子（40%）
对每个时间框架计算信号得分（-5 到 +5，归一化到 0-100）：
- Price > MA7: +1 / Price > MA25: +1 / Price > MA50: +1
- MACD 金叉: +3（持续多头: +1）
- RSI < 30: +2（30-45: +1，> 70: -2，55-70: -1）
- BB %B < 20%: +2（> 80%: -2）
- 成交量比 > 1.5: +1 / 动量为正: +1
多周期加权：主时间框架 60%，上一级 ±10%，上两级 ±5%。

### Pillar 3: 衍生品（30%）
基于资金费率、持仓量趋势、订单簿深度评分 0-100。
- 资金费率为负（空头付费）→ 看涨
- 持仓量随价格上涨 → 看涨
- 订单簿买盘 > 卖盘 20%+ → 看涨
- 股票代币的资金费率反映 crypto 市场对美股方向的预期

### 综合评分
Composite = Pillar1 × 0.30 + Pillar2 × 0.40 + Pillar3 × 0.30

## 4. 订单流分析
基于最近 100 笔成交数据的 Delta（买量-卖量）、买卖盘不平衡比、大单检测。
给出订单流综合评分（0-100）。

## 5. 趋势判断
短期（1-3天）/ 中期（1-2周）/ 长期（1-3月），每个给出方向和置信度。
美股代币需额外考虑：美股盘前/盘后走势、重大经济数据发布、财报季影响。

## 6. 持仓分析与交易建议

### 6a. 现有持仓处置建议（优先级最高）
**重要**：提示词中的持仓数据已经按当前分析标的过滤，只包含与当前标的相关的持仓。其他品种的持仓不在本分析范围内，**绝对不要分析其他品种的持仓**。

如果账户状态中存在与当前标的相关的持仓，**必须**对每个持仓给出处置建议：
- 分析当前持仓方向（多/空）与技术面、趋势判断是否一致
- 结合三支柱评分和趋势判断，给出明确建议：**加仓** / **减仓** / **持仓观望** / **平仓离场**
- 每个持仓建议需附上理由（引用具体指标数据）
- **只分析与当前标的相关的持仓**，不要分析其他品种

### 6b. 新开仓建议
如果综合评分在 30-70 区间且存在交易机会，给出具体方案：

信号等级（A+/A/B+/B/C/D）：
- A+: MACD金叉 + RSI<45 + Price>MA25 + Vol>1.5x + 正Delta + 宏观30-50
- A: 3+ 信号汇聚 + 宏观确认
- B+: 2-3 信号对齐 + 宏观中性
- B: 单一强信号 + 背离确认
- C/D: 忽略

每个方案包含：方向、入场价、止损价（附理由）、目标1/目标2、R/R比（需≥1.5:1）、建议仓位、盈利因子。

入场条件检查清单（全部 YES 才建议）：
1. 宏观评分 30-70？
2. 信号加权评分 ≥ 65？
3. R/R ≥ 1.5:1？
4. 仓位 ≤ 2% 风险？
5. 订单簿健康？
6. Delta 方向一致？

## 7. 风险提示
特别关注：
- 美股交易时间（9:30-16:00 ET）与加密市场 24/7 的差异
- 股票代币流动性风险（交易量可能远低于底层 ETF）
- 监管风险（股票代币合规性）
- 汇率风险（USDT 计价 vs USD 底层资产）

## 8. 关键监控指标
重点监控：VIX 变化、Fed 利率决议日期、QQQ 财报季、美国 CPI/非农数据发布日期。

---

输出格式要求（严格遵守，不要自行发挥）：
- 全部使用 HTML 标签（h3, h4, table, div, span, p, ul, li, strong, em, hr），不要 Markdown
- 数字精确到小数点后 2 位
- 绝对禁止使用 style="..." 属性（所有样式由 CSS class 控制，inline style 会被自动清除）
- 涨跌/盈亏正数用 <span class="positive"> 或 <td class="positive">
- 涨跌/盈亏负数用 <span class="negative"> 或 <td class="negative">
- 做多方向用 class="bullish"，做空方向用 class="bearish"
- 表格必须用 <table><thead><tr><th>...</th></tr></thead><tbody>...</tbody></table> 结构
- 表格交替行用 <tr class="even"> 增加可读性
- 三支柱评分用以下结构：
    <div class="pillar-score">
      <p><strong>评分：N / 100</strong>（阶段描述）</p>
      <div class="score-bar" style="--score: N"></div>
    </div>
- 信号等级用 <span class="signal-grade grade-x"> 展示（grade-a-plus / grade-a / grade-b-plus / grade-b / grade-c / grade-d）
- 交易建议用 <div class="trade-setup"> 包裹
- 风险提示用 <div class="risk-warning"> 包裹

### HTML 结构模板（严格按此格式输出）：

<h3>QQQON 三支柱技术分析报告</h3>
<p><strong>分析时间</strong>：TIME &nbsp;|&nbsp; <strong>数据源</strong>：OKX API + Yahoo Finance</p>
<hr>

<h4>1. 市场快照</h4>
<table><thead><tr><th>指标</th><th>数值</th><th>备注</th></tr></thead><tbody>
<tr><td>OKX 代币价格</td><td>XXX.XX</td><td>Bid/Ask</td></tr>
<tr class="even"><td>底层 ETF (QQQ)</td><td>XXX.XX</td><td>Yahoo Finance</td></tr>
<tr><td>溢价率</td><td class="positive">+0.XX%</td><td>代币 vs ETF</td></tr>
</tbody></table>
<hr>

<h4>2. 多时间框架技术指标解读</h4>
<table><thead><tr><th>指标</th><th>1H</th><th>4H</th><th>1D</th></tr></thead><tbody>
<tr><td><strong>RSI(14)</strong></td><td>XX.XX</td><td>XX.XX</td><td>XX.XX</td></tr>
<tr class="even"><td><em>解读</em></td><td>解读文字</td><td>解读文字</td><td>解读文字</td></tr>
</tbody></table>
<hr>

<h4>3. 三支柱评分</h4>
（同 crypto 报告格式，但 Pillar 1 使用美股宏观数据）

<h4>4. 订单流分析</h4>
<table>...</table>

<h4>5. 趋势判断</h4>
<table><thead><tr><th>时间框架</th><th>方向</th><th>置信度</th><th>依据</th></tr></thead><tbody>...</tbody></table>
<hr>

<h4>6. 持仓分析与交易建议</h4>
（同 crypto 报告格式）

<h4>7. 风险提示</h4>
<div class="risk-warning">
  <ul><li>风险点 1...</li><li>风险点 2...</li></ul>
</div>
<hr>

<h4>8. 关键监控指标</h4>
<table>...</table>
"""

    async def analyze(
        self, market_data: dict, macro_data: dict, account_data: dict | None = None
    ) -> dict:
        prompt = self.build_prompt(market_data, macro_data, account_data)
        self._cancelled = False

        try:
            cmd = cli_command(
                "claude", "--print", "--output-format", "text", env=self._env
            )
            self._current_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
            stdout, stderr = await asyncio.wait_for(
                self._current_proc.communicate(input=prompt.encode()),
                timeout=self.TIMEOUT,
            )
            returncode = self._current_proc.returncode
        except asyncio.TimeoutError:
            return {"error": f"Analysis timed out after {self.TIMEOUT}s"}
        except FileNotFoundError:
            return {"error": "claude CLI not found in PATH"}
        finally:
            self._current_proc = None

        if self._cancelled:
            return {"error": "分析已取消"}

        if returncode != 0:
            return {"error": stderr.decode().strip()}

        html = stdout.decode().strip()
        html = self._sanitize_html(html)

        return {
            "html": html,
            "timestamp": datetime.now().isoformat(),
            "symbol": market_data.get("instId", "UNKNOWN"),
        }

    @staticmethod
    def _sanitize_html(html: str) -> str:
        import re
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"""\s+style\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", "", html, flags=re.IGNORECASE)

        def _add_even_rows(m):
            tbody_content = m.group(1)
            rows = re.findall(r'(<tr(?:\s[^>]*)?>.*?</tr>)', tbody_content, re.DOTALL | re.IGNORECASE)
            if not rows:
                return m.group(0)
            new_rows = []
            for i, row in enumerate(rows):
                if i % 2 == 1 and 'class=' not in row[:row.index('>')]:
                    row = re.sub(r'^<tr', '<tr class="even"', row, count=1)
                new_rows.append(row)
            rebuilt = '\n'.join(new_rows)
            return f'<tbody>{rebuilt}</tbody>'

        html = re.sub(
            r'<tbody>(.*?)</tbody>',
            _add_even_rows,
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        return html
