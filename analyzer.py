import asyncio
import json
import os
from datetime import datetime


class Analyzer:
    """Constructs analysis prompts and calls Claude Code CLI."""

    TIMEOUT = 300  # seconds

    def __init__(self):
        self._env = os.environ.copy()
        npm_global = os.path.expanduser("~/.npm-global/bin")
        if npm_global not in self._env.get("PATH", ""):
            self._env["PATH"] = f"{npm_global}:{self._env.get('PATH', '')}"
        self._current_proc: asyncio.subprocess.Process | None = None
        self._cancelled = False

    def cancel(self):
        """Terminate the running Claude process."""
        self._cancelled = True
        if self._current_proc and self._current_proc.returncode is None:
            self._current_proc.terminate()

    @staticmethod
    def _extract_base_asset(symbol: str) -> str:
        """Extract base asset from symbol: BTC-USDT -> BTC, XAU-USDT-SWAP -> XAU."""
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

    def build_prompt(
        self, market_data: dict, account_data: dict | None = None
    ) -> str:
        """Build analysis prompt aligned with kline-indicator 3-pillar framework."""
        symbol = market_data.get("instId", "UNKNOWN")
        now = datetime.now().strftime("%Y-%m-%d %H:%M UTC+8")

        sections = [
            "你是专业的加密货币交易分析师，使用 K-Line Indicator Engine 的三支柱分析框架。",
            "以下是当前市场数据（由代码从 OKX API 精确获取，所有数字均为字符串类型的精确值）。\n",
            f"**分析时间**: {now}",
            f"**标的**: {symbol}\n",
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

        # Market data sections
        for key, label in [
            ("ticker", "价格快照"),
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
            # Summarize trades to save tokens
            sections.append(self._summarize_trades(trades))
        else:
            sections.append(self._fmt(trades))
        sections.append("")

        # Analysis instructions
        sections.append(self._analysis_instructions())

        return "\n".join(sections)

    def _fmt(self, data) -> str:
        """Format data as JSON string."""
        if isinstance(data, (dict, list)):
            return f"```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"
        return str(data)

    def _summarize_trades(self, trades: list) -> str:
        """Summarize recent trades into aggregated stats to save tokens."""
        if not trades:
            return "无成交数据"

        buy_vol = 0.0
        sell_vol = 0.0
        buy_count = 0
        sell_count = 0
        total_vol = 0.0

        for t in trades[:100]:
            # OKX trades format: [instId, tradeId, px, sz, side, ts]
            if isinstance(t, list) and len(t) >= 5:
                try:
                    sz = float(t[3])
                    side = t[4]  # buy or sell
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

        # Include first 5 raw trades for pattern detection
        raw_sample = trades[:5]

        return (
            f"```json\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n```\n\n"
            f"成交样本（前 5 笔）:\n```json\n{json.dumps(raw_sample, ensure_ascii=False)}\n```"
        )

    def _analysis_instructions(self) -> str:
        """The analysis framework instructions."""
        return """
---

请严格按照以下框架进行分析，全部输出为 HTML（不要 Markdown）：

## 1. 市场快照
用 <table> 展示：当前价、24h 涨跌幅、24h 高低、24h 成交量、持仓量、资金费率。

## 2. 多时间框架技术指标解读
用 <table> 展示 1H/4H/1D 的 RSI、MACD、BB、Supertrend、KDJ、ADX、ATR。
每个指标附简要解读（1-2 句话）。

## 3. 三支柱评分

### Pillar 1: 宏观周期（30%）
基于 BTC 的 Rainbow Chart 位置、AHR999、市场阶段分类。评分 0-100。
- 0-20: Deep Value / Capitulation → Maximum accumulation
- 20-40: Recovery / Early Bull → Gradual accumulation
- 40-60: Mid-Cycle / Neutral → Selective positioning
- 60-80: Late Bull / Overheating → Risk reduction
- 80-100: Mania / Distribution → Maximum caution

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

### 综合评分
Composite = Pillar1 × 0.30 + Pillar2 × 0.40 + Pillar3 × 0.30

## 4. 订单流分析
基于最近 100 笔成交数据的 Delta（买量-卖量）、买卖盘不平衡比、大单检测。
给出订单流综合评分（0-100）。

## 5. 趋势判断
短期（1-3天）/ 中期（1-2周）/ 长期（1-3月），每个给出方向和置信度。

## 6. 持仓分析与交易建议

### 6a. 现有持仓处置建议（优先级最高）
**重要**：提示词中的持仓数据已经按当前分析标的过滤，只包含与 {symbol} 相关的持仓。其他品种的持仓（如黄金、ETH 等）不在本分析范围内，**绝对不要分析其他品种的持仓**。

如果账户状态中存在与当前标的相关的持仓（包括 SWAP/FUTURES 持仓和交易机器人持仓），**必须**对每个持仓给出处置建议：
- 分析当前持仓方向（多/空）与技术面、趋势判断是否一致
- 结合三支柱评分和趋势判断，给出明确建议：**加仓** / **减仓** / **持仓观望** / **平仓离场**
- 如果是做空持仓，同样需要分析：空头趋势是否延续、是否应该加空、减空或平空
- 每个持仓建议需附上理由（引用具体指标数据）
- 如果持仓处于亏损状态，重点评估是否需要止损或减仓
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

## 8. 关键监控指标

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

<h3>SYMBOL 三支柱技术分析报告</h3>
<p><strong>分析时间</strong>：TIME &nbsp;|&nbsp; <strong>数据源</strong>：OKX API 实时</p>
<hr>

<h4>1. 市场快照</h4>
<table><thead><tr><th>指标</th><th>数值</th><th>备注</th></tr></thead><tbody>
<tr><td>当前价</td><td>73,822.10</td><td>Bid 73,822.1 / Ask 73,822.2</td></tr>
<tr class="even"><td>24h 涨跌幅</td><td class="positive">+0.70%</td><td>开盘 → 现价</td></tr>
</tbody></table>
<hr>

<h4>2. 多时间框架技术指标解读</h4>
<table><thead><tr><th>指标</th><th>1H</th><th>4H</th><th>1D</th></tr></thead><tbody>
<tr><td><strong>RSI(14)</strong></td><td>52.52</td><td>37.92</td><td>35.96</td></tr>
<tr class="even"><td><em>解读</em></td><td>中性偏多</td><td>偏弱</td><td>偏弱</td></tr>
</tbody></table>
<hr>

<h4>3. 三支柱评分</h4>
<h4>Pillar 1: 宏观周期（权重 30%）</h4>
<div class="pillar-score">
  <p><strong>评分：28 / 100</strong>（Recovery / Early Bull 区间下沿）</p>
  <div class="score-bar" style="--score: 28"></div>
</div>
<p>评分依据文字说明...</p>

<h4>Pillar 2: 量价因子（权重 40%）</h4>
<table><thead><tr><th>信号</th><th>1H (权重 30%)</th><th>4H (权重 60%)</th><th>1D (权重 10%)</th></tr></thead><tbody>
<tr><td>Price > EMA7</td><td class="positive">+1</td><td class="positive">+1</td><td class="negative">0</td></tr>
<tr class="even"><td><strong>周期小计</strong></td><td><strong>+4</strong></td><td><strong>-2</strong></td><td><strong>-1</strong></td></tr>
</tbody></table>

<div class="pillar-score">
  <p><strong>评分：49 / 100</strong>（中性）</p>
  <div class="score-bar" style="--score: 49"></div>
</div>

<h4>综合评分</h4>
<div class="pillar-score">
  <p><strong>Composite = 41.5</strong></p>
  <div class="score-bar" style="--score: 41.5"></div>
</div>
<p>偏空震荡 · 观望为主</p>
<hr>

<h4>4. 订单流分析</h4>
<table>...</table>

<h4>5. 趋势判断</h4>
<table><thead><tr><th>时间框架</th><th>方向</th><th>置信度</th><th>依据</th></tr></thead><tbody>...</tbody></table>
<hr>

<h4>6. 持仓分析与交易建议</h4>

<h5>现有持仓处置</h5>
<div class="trade-setup">
  <table><thead><tr><th>持仓</th><th>方向</th><th>盈亏</th><th>建议</th><th>理由</th></tr></thead><tbody>
  <tr><td>BTC-USDT-SWAP</td><td class="bearish">做空</td><td class="positive">+2.5%</td><td><strong>减仓</strong></td><td>RSI 超卖反弹信号，建议减仓 50%</td></tr>
  </tbody></table>
</div>

<h5>新开仓建议</h5>
<span class="signal-grade grade-c">C 级 — 观望</span>

<div class="trade-setup">
  <table>...</table>
</div>
<hr>

<h4>7. 风险提示</h4>
<div class="risk-warning">
  <ul><li>风险点 1...</li><li>风险点 2...</li></ul>
</div>
<hr>

<h4>8. 关键监控指标</h4>
<table>...</table>
"""

    async def analyze(
        self, market_data: dict, account_data: dict | None = None
    ) -> dict:
        """Call Claude Code CLI with the constructed prompt. Returns {html, timestamp}."""
        prompt = self.build_prompt(market_data, account_data)
        self._cancelled = False
        returncode = None

        try:
            self._current_proc = await asyncio.create_subprocess_exec(
                "claude", "--print", "--output-format", "text",
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
        """Clean up generated HTML for consistent styling.

        - Remove <script> tags
        - Strip ALL inline style attributes (styles come from CSS classes)
        - Ensure alternating row classes on table rows
        """
        import re

        # Remove <script> tags
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)

        # Strip ALL inline style attributes — CSS classes handle styling
        # Handle both single and double quoted style values, and unquoted
        html = re.sub(r"""\s+style\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", "", html, flags=re.IGNORECASE)

        # Add class="even" to alternating <tr> rows inside each <tbody>
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
