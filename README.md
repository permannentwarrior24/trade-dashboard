# Trade Dashboard

OKX 交易仪表盘 — 账户概览 + AI 行情分析的 Web 应用。

## 功能

### 账户概览（`/`）

- 总资产估值（交易账户 + 资金账户 + 赚币账户）
- 当前持仓（合约方向、均价、杠杆、未实现盈亏、强平价）
- 交易机器人（合约网格、现货网格、DCA 定投）
- 挂单（现货 + 合约）
- 赚币理财（活期 + 定期）
- 余额明细（过滤灰尘）

### 行情分析（`/market`）

- 市场快照：当前价、24h 涨跌、成交量
- 衍生品数据：资金费率、持仓量（USD/币本位）
- 多时间框架技术指标：RSI、MACD、EMA(7)、BB、Supertrend、KDJ、ADX、ATR、OBV（1H/4H/1D）
- 订单簿深度：Best Bid/Ask、Spread、买卖盘不平衡比
- **AI 分析报告**：调用 Claude Code CLI，基于 K-Line Indicator Engine 三支柱框架生成 HTML 报告

### AI 分析框架

三支柱评分体系（0-100）：

| Pillar | 权重 | 数据来源 |
|--------|------|----------|
| 宏观周期 | 30% | BTC Rainbow Chart、AHR999、市场阶段 |
| 量价因子 | 40% | 多时间框架 RSI/MACD/BB/EMA 信号加权 |
| 衍生品 | 30% | 资金费率、持仓量趋势、订单簿深度 |

信号等级：A+ / A / B+ / B / C / D，仅 A/B+ 级给出具体交易方案（入场、止损、目标、R/R ≥ 1.5:1）。

## 架构

```
static/
├── index.html      # 账户概览页
├── market.html     # 行情分析页
├── app.js          # 前端逻辑
└── style.css       # 样式

server.py           # FastAPI 服务端
├── /api/account/*  # 账户数据（balance/positions/orders/earn/bots）
├── /api/market/*   # 行情数据（ticker/indicators/orderbook/funding-rate/open-interest/trades）
├── /api/analyze    # POST — 触发 AI 分析
└── /api/reports    # 报告存档

okx_client.py       # OKX CLI 封装（subprocess 调用 `okx --json`）
analyzer.py         # 构建分析 prompt → 调用 Claude Code CLI
report2md.py        # 报告格式转换工具（JSON → Markdown）
```

**数据流**：前端 → FastAPI → `okx` CLI（市场/账户数据）→ `claude` CLI（AI 分析）→ HTML 报告 → 保存到 `reports/`

## 依赖

- Python 3.10+
- [okx CLI](https://www.npmjs.com/package/@okx_ai/okx-trade-cli)：`npm install -g @okx_ai/okx-trade-cli`
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)：`npm install -g @anthropic-ai/claude-code`
- Python 包：fastapi、uvicorn、html2text

## 启动

```bash
# 一键启动（自动检查依赖、安装 Python 包）
cd trade-dashboard
bash start.sh

# 或手动
pip install -r requirements.txt
uvicorn server:app --host 127.0.0.1 --port 8501 --reload
```

访问 http://127.0.0.1:8501

## 使用

1. **账户概览**：打开首页，自动加载账户数据
2. **行情分析**：切换到「行情分析」页，选择币种（BTC/ETH/XAU），查看实时行情
3. **AI 报告**：点击「开始分析」，等待 Claude Code 生成报告（通常 30-120 秒）
4. **报告存档**：分析报告自动保存到 `reports/`（JSON + Markdown 双格式）

## 配置

- OKX API Key 通过 `okx` CLI 的 `claude code` profile 配置
- 预设币种标签可在 `market.html` 的 `.symbol-tabs` 中修改
- 技术指标列表在 `okx_client.py` 的 `INDICATORS` 常量中定义
