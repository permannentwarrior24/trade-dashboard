// ── State ───────────────────────────────────────────────────────────

let currentSymbol = "BTC-USDT";
let accountData = null;
let marketData = null;

// ── K-Line Chart State ─────────────────────────────────────────────
let klineChart = null;
let activeBar = "1H";
let activeIndicators = new Set(["MA"]);
let chartInitialized = false;
const indicatorPaneMap = {};  // name → paneId (for sub-pane indicators)

// ── Init ────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    // Market page: bind tab clicks + auto-load
    const tabs = document.querySelectorAll(".tab");
    if (tabs.length) {
        tabs.forEach(tab => {
            tab.addEventListener("click", () => {
                tabs.forEach(t => t.classList.remove("active"));
                tab.classList.add("active");
                currentSymbol = tab.dataset.symbol;
                if (tab.dataset.source === "bitget") {
                    loadBitgetMarket(currentSymbol);
                } else {
                    loadMarket(currentSymbol);
                }
            });
        });
        const activeTab = document.querySelector(".tab.active");
        if (activeTab?.dataset.source === "bitget") {
            loadBitgetMarket(currentSymbol);
        } else {
            loadMarket(currentSymbol);
        }
        loadHistory();
    }

    // Account page: auto-load account data
    if (document.getElementById("valuation-content")) {
        refreshAccount();
    }
});

// ── API Helpers ─────────────────────────────────────────────────────

async function api(url, options = {}) {
    try {
        const resp = await fetch(url, options);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        return await resp.json();
    } catch (e) {
        console.error(`API error: ${url}`, e);
        throw e;
    }
}

// ── Account ─────────────────────────────────────────────────────────

async function refreshAccount() {
    setStatus("loading");
    try {
        // Check Bitget health in parallel
        const healthPromise = api("/api/health").catch(() => null);
        const data = await api("/api/account/all");
        accountData = data;

        // Show Bitget configuration status
        const health = await healthPromise;
        const bgStatusEl = document.getElementById("bitget-status");
        const bgConfigEl = document.getElementById("bitget-config");
        if (bgStatusEl) {
            if (!health?.bitget) {
                bgStatusEl.innerHTML = '<span style="color:var(--red)">(未配置 API Key)</span>';
                if (bgConfigEl) bgConfigEl.style.display = "block";
            } else {
                const hasBgError = data.bitget?.balance?.error || data.bitget?.error;
                if (hasBgError) {
                    const errMsg = data.bitget?.balance?.error || data.bitget?.error || "";
                    if (errMsg.includes("40037") || errMsg.includes("Apikey")) {
                        bgStatusEl.innerHTML = '<span style="color:var(--red)">(API Key 无效)</span>';
                        if (bgConfigEl) bgConfigEl.style.display = "block";
                    } else {
                        bgStatusEl.innerHTML = '<span style="color:var(--yellow)">(连接异常)</span>';
                    }
                } else {
                    bgStatusEl.innerHTML = '';
                    if (bgConfigEl) bgConfigEl.style.display = "none";
                }
            }
        }

        const okx = data.okx || {};
        const bitget = data.bitget || {};

        // Total valuation (combined)
        renderTotalValuation(okx, bitget);

        // OKX columns (5 cards)
        renderPositions("okx-positions", okx.positions);
        renderBots("okx-bots", okx.bots);
        renderOrders("okx-orders", okx.orders);
        renderEarn("okx-earn", okx.earn);
        renderBalance("okx-balance", okx.balance);

        // Bitget columns (6 cards)
        renderPositions("bitget-positions", bitget.positions);
        renderBitgetInvestments("bitget-investments", bitget.balance);
        renderPlanOrders("bitget-bots", bitget.orders?.plan);
        renderOrdersBitget("bitget-orders", bitget.orders);
        renderEarnBitget("bitget-earn", bitget.earn);
        renderBalanceBitget("bitget-balance", bitget.balance);

        setStatus("ok");
    } catch (e) {
        setStatus("error");
        console.error("Account load error:", e);
        document.querySelectorAll(".card-content").forEach(el => {
            el.innerHTML = `<span style="color:var(--red)">错误: ${escHtml(e.message)}</span>`;
        });
    }
}

// ── Valuation helpers ──────────────────────────────────────────────

function calcOkxValuation(okx) {
    let trading = 0, funding = 0, earn = 0;
    const b = okx?.balance;
    if (b && !b.error) {
        const t = b.trading;
        if (t && !t.error) {
            const d = Array.isArray(t) ? t[0] : t;
            trading = parseFloat(d?.totalEq || 0);
        }
        const a = b.asset;
        if (a && !a.error) {
            const arr = Array.isArray(a) ? a : (a?.data || []);
            arr.forEach(x => { funding += parseFloat(x.bal || 0); });
        }
    }
    const e = okx?.earn;
    if (e && !e.error) {
        const s = e.savings;
        if (s && !s.error) {
            const arr = Array.isArray(s) ? s : (s?.data || []);
            arr.forEach(x => { earn += parseFloat(x.amt || 0); });
        }
    }
    return { trading, funding, earn, total: trading + funding + earn };
}

function calcBitgetValuation(bitget) {
    let trading = 0, funding = 0, earn = 0;
    const b = bitget?.balance;
    if (Array.isArray(b)) {
        b.forEach(acc => {
            const val = parseFloat(acc.usdtBalance || acc.totalEquity || 0);
            if (acc.accountType === "earn") {
                earn += val;
            } else if (acc.accountType === "funding") {
                funding += val;
            } else {
                // spot, futures, bots, margin → trading bucket
                trading += val;
            }
        });
    }
    return { trading, funding, earn, total: trading + funding + earn };
}

function renderTotalValuation(okx, bitget) {
    const el = document.getElementById("valuation-content");
    const okxVal = calcOkxValuation(okx);
    const bgVal = calcBitgetValuation(bitget);
    const grandTotal = okxVal.total + bgVal.total;

    el.innerHTML = `
        <div class="ticker-grid">
            <div class="ticker-item">
                <div class="label">两所总资产</div>
                <div class="value">$${grandTotal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
            </div>
            <div class="ticker-item">
                <div class="label">OKX 总计</div>
                <div class="value">$${okxVal.total.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
            </div>
            <div class="ticker-item">
                <div class="label">Bitget 总计</div>
                <div class="value">$${bgVal.total.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
            </div>
            <div class="ticker-item">
                <div class="label">OKX 交易/资金/赚币</div>
                <div class="value" style="font-size:13px">$${fmtNum(okxVal.trading)} / $${fmtNum(okxVal.funding)} / $${fmtNum(okxVal.earn)}</div>
            </div>
            <div class="ticker-item">
                <div class="label">Bitget 交易/资金/赚币</div>
                <div class="value" style="font-size:13px">$${fmtNum(bgVal.trading)} / $${fmtNum(bgVal.funding)} / $${fmtNum(bgVal.earn)}</div>
            </div>
        </div>`;
}

// ── OKX Renderers ──────────────────────────────────────────────────

function renderBalance(containerId, data) {
    const el = document.getElementById(containerId)?.querySelector(".card-content") || document.getElementById(containerId);
    const trading = data?.trading;
    if (!trading || trading.error) {
        el.innerHTML = `<span class="loading">${trading?.error || "无数据"}</span>`;
        return;
    }

    let details = [];
    const tData = Array.isArray(trading) ? trading[0] : trading;
    if (tData?.details) {
        details = tData.details;
    } else if (Array.isArray(tData)) {
        details = tData;
    }

    if (!details.length) {
        el.innerHTML = '<span class="loading">无余额</span>';
        return;
    }

    let html = `<table><thead><tr><th>币种</th><th>权益</th><th>可用</th><th>冻结</th><th>USD值</th></tr></thead><tbody>`;
    details.forEach((d, i) => {
        const eq = parseFloat(d.eq || 0);
        if (eq < 0.01) return;
        const cls = i % 2 === 1 ? ' class="even"' : '';
        html += `<tr${cls}>
            <td>${d.ccy}</td>
            <td class="num">${fmtNum(d.eq)}</td>
            <td class="num">${fmtNum(d.availBal)}</td>
            <td class="num">${fmtNum(d.frozenBal)}</td>
            <td class="num">$${fmtNum(d.eqUsd)}</td>
        </tr>`;
    });
    html += "</tbody></table>";
    el.innerHTML = html;
}

function renderPositions(containerId, data) {
    const el = document.getElementById(containerId)?.querySelector(".card-content") || document.getElementById(containerId);
    if (!data || data.error) {
        const msg = typeof data?.error === 'string' ? data.error : JSON.stringify(data?.error) || "无持仓";
        el.innerHTML = `<span style="color:var(--red);font-size:13px">${escHtml(msg)}</span>`;
        return;
    }

    const positions = Array.isArray(data) ? data : (data?.data || []);
    if (!positions.length) {
        el.innerHTML = '<span class="loading">无持仓</span>';
        return;
    }

    let html = `<table><thead><tr><th>合约</th><th>方向</th><th>数量</th><th>均价</th><th>杠杆</th><th>未实现盈亏</th><th>收益率</th><th>强平价</th></tr></thead><tbody>`;
    positions.forEach((p, i) => {
        const upl = parseFloat(p.upl || p.unrealizedPL || 0);
        const uplRatio = parseFloat(p.uplRatio || p.unrealizedPLRate || 0) * (p.uplRatio ? 100 : 1);
        const cls = i % 2 === 1 ? ' class="even"' : '';
        const side = p.posSide || p.holdSide || "";
        const dirClass = side === "long" ? "bullish" : "bearish";
        const uplClass = upl >= 0 ? "positive" : "negative";
        const lever = p.lever || p.leverage || "—";
        html += `<tr${cls}>
            <td>${p.instId || p.symbol}</td>
            <td class="${dirClass}">${side === "long" ? "做多" : "做空"}</td>
            <td class="num">${p.pos || p.openSize || "—"}</td>
            <td class="num">${fmtNum(p.avgPx || p.openAvgPrice)}</td>
            <td class="num">${lever}x</td>
            <td class="num ${uplClass}">${fmtNum(upl)}</td>
            <td class="num ${uplClass}">${uplRatio.toFixed(2)}%</td>
            <td class="num">${fmtNum(p.liqPx || p.liqPrice)}</td>
        </tr>`;
    });
    html += "</tbody></table>";
    el.innerHTML = html;
}

function renderOrders(containerId, data) {
    const el = document.getElementById(containerId)?.querySelector(".card-content") || document.getElementById(containerId);
    if (!data) {
        el.innerHTML = '<span class="loading">无挂单</span>';
        return;
    }

    let allOrders = [];
    for (const key of ["swap", "spot"]) {
        const d = data[key];
        if (d && !d.error) {
            const arr = Array.isArray(d) ? d : (d?.data || []);
            allOrders = allOrders.concat(arr.map(o => ({ ...o, type: key })));
        }
    }

    if (!allOrders.length) {
        el.innerHTML = '<span class="loading">无挂单</span>';
        return;
    }

    let html = `<table><thead><tr><th>合约</th><th>类型</th><th>方向</th><th>价格</th><th>数量</th></tr></thead><tbody>`;
    allOrders.forEach((o, i) => {
        const cls = i % 2 === 1 ? ' class="even"' : '';
        const sideClass = o.side === "buy" ? "bullish" : "bearish";
        html += `<tr${cls}>
            <td>${o.instId}</td>
            <td>${o.ordType}</td>
            <td class="${sideClass}">${o.side === "buy" ? "买入" : "卖出"}</td>
            <td class="num">${fmtNum(o.px)}</td>
            <td class="num">${o.sz}</td>
        </tr>`;
    });
    html += "</tbody></table>";
    el.innerHTML = html;
}

function renderEarn(containerId, data) {
    const el = document.getElementById(containerId)?.querySelector(".card-content") || document.getElementById(containerId);
    if (!data || data.error) {
        el.innerHTML = `<span class="loading">${data?.error || "无数据"}</span>`;
        return;
    }

    const savings = data?.savings;
    const fixed = data?.fixed;
    const hasSavings = savings && !savings.error && (Array.isArray(savings) ? savings : (savings?.data || [])).length > 0;
    const hasFixed = fixed && !fixed.error && (Array.isArray(fixed) ? fixed : (fixed?.data || [])).length > 0;

    if (!hasSavings && !hasFixed) {
        el.innerHTML = '<span class="loading">无赚币持仓</span>';
        return;
    }

    let html = "";

    if (hasSavings) {
        const arr = Array.isArray(savings) ? savings : (savings?.data || []);
        html += `<div style="margin-bottom:10px"><strong style="color:var(--text-muted);font-size:11px;text-transform:uppercase">活期理财</strong></div>`;
        html += `<table><thead><tr><th>币种</th><th>总额</th><th>收益</th><th>利率</th></tr></thead><tbody>`;
        arr.forEach((s, i) => {
            const amt = parseFloat(s.amt || 0);
            if (amt < 0.01) return;
            const cls = i % 2 === 1 ? ' class="even"' : '';
            const rate = s.rate ? (parseFloat(s.rate) * 100).toFixed(2) + "%" : "—";
            html += `<tr${cls}>
                <td>${s.ccy}</td>
                <td class="num">${fmtNum(s.amt)}</td>
                <td class="num positive">${fmtNum(s.earnings)}</td>
                <td class="num">${rate}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
    }

    if (hasFixed) {
        const arr = Array.isArray(fixed) ? fixed : (fixed?.data || []);
        html += `<div style="margin:${hasSavings ? '14px' : '0'} 0 10px"><strong style="color:var(--text-muted);font-size:11px;text-transform:uppercase">定期理财</strong></div>`;
        html += `<table><thead><tr><th>币种</th><th>金额</th><th>状态</th><th>到期</th></tr></thead><tbody>`;
        arr.forEach((f, i) => {
            const cls = i % 2 === 1 ? ' class="even"' : '';
            const stateMap = { pending: "匹配中", earning: "赚币中", expired: "逾期", settled: "已结算", cancelled: "已撤销" };
            const state = stateMap[f.state] || f.state;
            const stateClass = f.state === "earning" ? "bullish" : f.state === "pending" ? "neutral" : "";
            const maturity = f.maturityTime ? new Date(parseInt(f.maturityTime)).toLocaleDateString("zh-CN") : "—";
            html += `<tr${cls}>
                <td>${f.ccy}</td>
                <td class="num">${fmtNum(f.amt)}</td>
                <td class="${stateClass}">${state}</td>
                <td>${maturity}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
    }

    el.innerHTML = html;
}

function renderBots(containerId, data) {
    const el = document.getElementById(containerId)?.querySelector(".card-content") || document.getElementById(containerId);
    if (!data || data.error) {
        el.innerHTML = `<span class="loading">${data?.error || "无数据"}</span>`;
        return;
    }

    const contractGrid = data?.contract_grid;
    const spotGrid = data?.spot_grid;
    const dca = data?.dca;
    const hasContract = contractGrid && !contractGrid.error && (Array.isArray(contractGrid) ? contractGrid : (contractGrid?.data || [])).length > 0;
    const hasSpot = spotGrid && !spotGrid.error && (Array.isArray(spotGrid) ? spotGrid : (spotGrid?.data || [])).length > 0;
    const hasDca = dca && !dca.error && (Array.isArray(dca) ? dca : (dca?.data || [])).length > 0;

    if (!hasContract && !hasSpot && !hasDca) {
        el.innerHTML = '<span class="loading">无运行中的机器人</span>';
        return;
    }

    let html = "";

    function renderGridTable(arr, label) {
        let h = `<div style="margin:${html ? '14px' : '0'} 0 10px"><strong style="color:var(--text-muted);font-size:11px;text-transform:uppercase">${label}</strong></div>`;
        h += `<table><thead><tr><th>合约</th><th>方向</th><th>投资</th><th>浮动盈亏</th><th>总盈亏</th><th>网格数</th><th>杠杆</th></tr></thead><tbody>`;
        arr.forEach((b, i) => {
            const floatPnl = parseFloat(b.floatProfit || 0);
            const totalPnl = parseFloat(b.totalPnl || 0);
            const pnlRatio = parseFloat(b.pnlRatio || 0) * 100;
            const cls = i % 2 === 1 ? ' class="even"' : '';
            const dirMap = { long: "做多", short: "做空", neutral: "中性" };
            const dirClass = b.direction === "long" ? "bullish" : b.direction === "short" ? "bearish" : "neutral";
            const floatClass = floatPnl >= 0 ? "positive" : "negative";
            const totalClass = totalPnl >= 0 ? "positive" : "negative";
            h += `<tr${cls}>
                <td>${b.instId}</td>
                <td class="${dirClass}">${dirMap[b.direction] || b.direction}</td>
                <td class="num">$${fmtNum(b.investment || b.sz)}</td>
                <td class="num ${floatClass}">${fmtNum(b.floatProfit)} (${pnlRatio.toFixed(2)}%)</td>
                <td class="num ${totalClass}">${fmtNum(b.totalPnl)}</td>
                <td class="num">${b.gridNum}</td>
                <td class="num">${b.actualLever ? parseFloat(b.actualLever).toFixed(1) + "x" : b.lever + "x"}</td>
            </tr>`;
        });
        h += `</tbody></table>`;
        return h;
    }

    if (hasContract) {
        const arr = Array.isArray(contractGrid) ? contractGrid : (contractGrid?.data || []);
        html += renderGridTable(arr, "合约网格");
    }

    if (hasSpot) {
        const arr = Array.isArray(spotGrid) ? spotGrid : (spotGrid?.data || []);
        html += renderGridTable(arr, "现货网格");
    }

    if (hasDca) {
        const arr = Array.isArray(dca) ? dca : (dca?.data || []);
        html += `<div style="margin:${html ? '14px' : '0'} 0 10px"><strong style="color:var(--text-muted);font-size:11px;text-transform:uppercase">DCA 定投</strong></div>`;
        html += `<table><thead><tr><th>合约</th><th>方向</th><th>投资</th><th>浮动盈亏</th><th>总盈亏</th></tr></thead><tbody>`;
        arr.forEach((b, i) => {
            const floatPnl = parseFloat(b.floatProfit || 0);
            const totalPnl = parseFloat(b.totalPnl || 0);
            const cls = i % 2 === 1 ? ' class="even"' : '';
            const dirClass = b.direction === "long" ? "bullish" : "bearish";
            const floatClass = floatPnl >= 0 ? "positive" : "negative";
            const totalClass = totalPnl >= 0 ? "positive" : "negative";
            html += `<tr${cls}>
                <td>${b.instId}</td>
                <td class="${dirClass}">${b.direction === "long" ? "做多" : "做空"}</td>
                <td class="num">$${fmtNum(b.investment || b.sz)}</td>
                <td class="num ${floatClass}">${fmtNum(b.floatProfit)}</td>
                <td class="num ${totalClass}">${fmtNum(b.totalPnl)}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
    }

    el.innerHTML = html;
}

// ── Bitget Config ─────────────────────────────────────────────────

async function saveBitgetCreds() {
    const apiKey = document.getElementById("bg-api-key").value.trim();
    const secret = document.getElementById("bg-secret").value.trim();
    const passphrase = document.getElementById("bg-passphrase").value.trim();
    const msgEl = document.getElementById("bg-config-msg");

    if (!apiKey || !secret || !passphrase) {
        msgEl.innerHTML = '<span style="color:var(--red)">请填写所有字段</span>';
        return;
    }

    msgEl.innerHTML = '<span style="color:var(--text-muted)">保存中...</span>';
    try {
        const result = await api("/api/account/bitget/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_key: apiKey, secret_key: secret, passphrase: passphrase }),
        });
        if (result.configured) {
            msgEl.innerHTML = '<span style="color:var(--green)">保存成功，正在刷新数据...</span>';
            // Clear inputs
            document.getElementById("bg-api-key").value = "";
            document.getElementById("bg-secret").value = "";
            document.getElementById("bg-passphrase").value = "";
            // Refresh data
            setTimeout(() => refreshAccount(), 500);
        } else {
            msgEl.innerHTML = '<span style="color:var(--red)">保存失败</span>';
        }
    } catch (e) {
        msgEl.innerHTML = `<span style="color:var(--red)">错误: ${escHtml(e.message)}</span>`;
    }
}

// ── Bitget Renderers ───────────────────────────────────────────────

function renderBalanceBitget(containerId, data) {
    const el = document.getElementById(containerId)?.querySelector(".card-content") || document.getElementById(containerId);
    if (!data || data.error) {
        el.innerHTML = `<span style="color:var(--red);font-size:13px">${escHtml(typeof data?.error === 'string' ? data.error : JSON.stringify(data?.error) || "无数据")}</span>`;
        return;
    }

    // data is array of account types
    const accounts = Array.isArray(data) ? data : [data];
    let allAssets = [];
    accounts.forEach(acc => {
        const accountVal = parseFloat(acc.usdtBalance || 0);
        const assets = acc.accountAssets || acc.assets || [];
        if (Array.isArray(assets) && assets.length) {
            // Spot account with per-coin detail: pass account total for filtering
            assets.forEach(a => allAssets.push({ ...a, accountType: acc.accountType, _accountUsdt: accountVal }));
        } else if (accountVal >= 0.01) {
            // Summary format: accountType + usdtBalance (no per-coin detail)
            allAssets.push({ coin: acc.accountType === "earn" ? "赚币" : acc.accountType.toUpperCase(), usdtEquity: acc.usdtBalance, accountType: acc.accountType });
        }
    });

    if (!allAssets.length) {
        el.innerHTML = '<span class="loading">无余额</span>';
        return;
    }

    // Sort by USD value descending
    allAssets.sort((a, b) => parseFloat(b.usdtEquity || 0) - parseFloat(a.usdtEquity || 0));

    let html = `<table><thead><tr><th>币种</th><th>账户</th><th>权益</th><th>可用</th><th>冻结</th><th>USD值</th></tr></thead><tbody>`;
    allAssets.forEach((d, i) => {
        const eq = parseFloat(d.usdtEquity || d.equity || d._accountUsdt || 0);
        if (eq < 0.01) return;
        const cls = i % 2 === 1 ? ' class="even"' : '';
        const typeMap = { spot: "现货", futures: "合约", funding: "资金", earn: "赚币", bots: "策略", margin: "杠杆" };
        const usdVal = parseFloat(d.usdtEquity || 0);
        html += `<tr${cls}>
            <td>${d.coin}</td>
            <td>${typeMap[d.accountType] || d.accountType}</td>
            <td class="num">${fmtNum(d.equity || d.usdtEquity)}</td>
            <td class="num">${fmtNum(d.available)}</td>
            <td class="num">${fmtNum(d.frozen)}</td>
            <td class="num">${usdVal >= 0.01 ? '$' + fmtNum(d.usdtEquity) : '—'}</td>
        </tr>`;
    });
    html += "</tbody></table>";
    el.innerHTML = html;
}

function renderBitgetInvestments(containerId, data) {
    const el = document.getElementById(containerId)?.querySelector(".card-content") || document.getElementById(containerId);
    if (!data || data.error) {
        el.innerHTML = `<span style="color:var(--red);font-size:13px">${escHtml(typeof data?.error === 'string' ? data.error : JSON.stringify(data?.error) || "无数据")}</span>`;
        return;
    }
    const STABLECOINS = new Set(["USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDG"]);
    const accounts = Array.isArray(data) ? data : [data];
    let investments = [];
    accounts.forEach(acc => {
        if (acc.accountType !== "spot") return;
        const assets = acc.accountAssets || acc.assets || [];
        if (!Array.isArray(assets)) return;
        assets.forEach(a => {
            const coin = a.coin || "";
            if (STABLECOINS.has(coin)) return;
            const usdVal = parseFloat(a.usdtEquity || 0);
            if (usdVal < 0.01) return;
            investments.push({
                coin,
                equity: a.equity || "—",
                usdtEquity: usdVal,
                change24h: a.change24h != null ? parseFloat(a.change24h) : null,
            });
        });
    });
    if (!investments.length) {
        el.innerHTML = '<span class="loading">无投资持仓</span>';
        return;
    }
    investments.sort((a, b) => b.usdtEquity - a.usdtEquity);
    let html = `<table><thead><tr><th>币种</th><th>数量</th><th>USD值</th><th>24h涨跌</th></tr></thead><tbody>`;
    investments.forEach((d, i) => {
        const cls = i % 2 === 1 ? ' class="even"' : '';
        const chgClass = d.change24h != null ? (d.change24h >= 0 ? "positive" : "negative") : "";
        const chgText = d.change24h != null ? `${d.change24h >= 0 ? "+" : ""}${d.change24h.toFixed(2)}%` : "—";
        html += `<tr${cls}>
            <td><strong>${d.coin}</strong></td>
            <td class="num">${fmtNum(d.equity)}</td>
            <td class="num">$${fmtNum(d.usdtEquity)}</td>
            <td class="num ${chgClass}">${chgText}</td>
        </tr>`;
    });
    html += "</tbody></table>";
    el.innerHTML = html;
}

function renderOrdersBitget(containerId, data) {
    const el = document.getElementById(containerId)?.querySelector(".card-content") || document.getElementById(containerId);
    if (!data) {
        el.innerHTML = '<span class="loading">无挂单</span>';
        return;
    }
    // Show error if spot/futures/plan all errored
    if (data.error) {
        el.innerHTML = `<span style="color:var(--red);font-size:13px">${escHtml(data.error)}</span>`;
        return;
    }

    let allOrders = [];
    const orderErrors = [];
    // Spot orders
    const spot = data.spot;
    if (Array.isArray(spot)) {
        spot.forEach(o => allOrders.push({ ...o, type: "现货" }));
    } else if (spot && Array.isArray(spot.data)) {
        spot.data.forEach(o => allOrders.push({ ...o, type: "现货" }));
    } else if (spot?.error) {
        orderErrors.push(`现货: ${spot.error}`);
    }
    // Futures orders
    const futures = data.futures;
    if (Array.isArray(futures)) {
        futures.forEach(o => allOrders.push({ ...o, type: "合约" }));
    } else if (futures && Array.isArray(futures.data)) {
        futures.data.forEach(o => allOrders.push({ ...o, type: "合约" }));
    } else if (futures?.error) {
        orderErrors.push(`合约: ${futures.error}`);
    }

    if (!allOrders.length) {
        if (orderErrors.length) {
            el.innerHTML = `<span style="color:var(--red);font-size:13px">${escHtml(orderErrors.join(" | "))}</span>`;
        } else {
            el.innerHTML = '<span class="loading">无挂单</span>';
        }
        return;
    }

    let html = `<table><thead><tr><th>合约</th><th>类型</th><th>方向</th><th>价格</th><th>数量</th></tr></thead><tbody>`;
    allOrders.forEach((o, i) => {
        const cls = i % 2 === 1 ? ' class="even"' : '';
        const sideClass = o.side === "buy" ? "bullish" : "bearish";
        html += `<tr${cls}>
            <td>${o.symbol || o.instId}</td>
            <td>${o.orderType || o.ordType || "—"}</td>
            <td class="${sideClass}">${o.side === "buy" ? "买入" : "卖出"}</td>
            <td class="num">${fmtNum(o.price || o.px)}</td>
            <td class="num">${o.size || o.sz}</td>
        </tr>`;
    });
    html += "</tbody></table>";
    el.innerHTML = html;
}

function renderEarnBitget(containerId, data) {
    const el = document.getElementById(containerId)?.querySelector(".card-content") || document.getElementById(containerId);
    if (!data || data.error) {
        el.innerHTML = `<span style="color:var(--red);font-size:13px">${escHtml(typeof data?.error === 'string' ? data.error : JSON.stringify(data?.error) || "无数据")}</span>`;
        return;
    }

    const savings = data.savings;
    const earn = data.earn;

    // Show nested errors (but NOT the "unsupported in UTA" markers)
    const errors = [];
    if (savings?.error && !savings?.unsupported) errors.push(`活期: ${savings.error}`);
    if (earn?.error && !earn?.unsupported) errors.push(`定期: ${earn.error}`);
    if (errors.length && !Array.isArray(savings) && !Array.isArray(earn)) {
        el.innerHTML = `<span style="color:var(--red);font-size:13px">${escHtml(errors.join(" | "))}</span>`;
        return;
    }

    const hasSavings = Array.isArray(savings) && savings.length > 0;
    const hasEarn = Array.isArray(earn) && earn.length > 0;

    const notes = [];
    if (savings?.unsupported) notes.push(`活期：${savings.reason || "暂不支持"}`);
    if (earn?.unsupported) notes.push(`定期：${earn.reason || "暂不支持"}`);

    if (!hasSavings && !hasEarn) {
        if (notes.length) {
            el.innerHTML = `<span style="color:var(--text-muted);font-size:12px">${escHtml(notes.join("；"))}</span>`;
        } else {
            el.innerHTML = '<span class="loading">无赚币持仓</span>';
        }
        return;
    }

    let html = "";

    if (hasSavings) {
        html += `<div style="margin-bottom:10px"><strong style="color:var(--text-muted);font-size:11px;text-transform:uppercase">活期理财</strong></div>`;
        html += `<table><thead><tr><th>币种</th><th>总额</th><th>USD值</th></tr></thead><tbody>`;
        savings.forEach((s, i) => {
            const amt = parseFloat(s.amount || s.holdingAmount || 0);
            if (amt < 0.01) return;
            const cls = i % 2 === 1 ? ' class="even"' : '';
            html += `<tr${cls}>
                <td>${s.coin || s.ccy}</td>
                <td class="num">${fmtNum(s.amount || s.holdingAmount)}</td>
                <td class="num">$${fmtNum(s.usdtEquity || s.amount)}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
    }

    if (hasEarn) {
        html += `<div style="margin:${hasSavings ? '14px' : '0'} 0 10px"><strong style="color:var(--text-muted);font-size:11px;text-transform:uppercase">定期/其他理财</strong></div>`;
        html += `<table><thead><tr><th>产品</th><th>币种</th><th>金额</th><th>USD值</th></tr></thead><tbody>`;
        earn.forEach((e, i) => {
            const cls = i % 2 === 1 ? ' class="even"' : '';
            html += `<tr${cls}>
                <td>${e.productName || e.productId || "—"}</td>
                <td>${e.coin || e.ccy}</td>
                <td class="num">${fmtNum(e.amount || e.holdingAmount)}</td>
                <td class="num">$${fmtNum(e.usdtEquity || e.amount)}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
    }

    el.innerHTML = html;
}

function renderPlanOrders(containerId, data) {
    const el = document.getElementById(containerId)?.querySelector(".card-content") || document.getElementById(containerId);
    if (!data || data.error) {
        el.innerHTML = `<span style="color:var(--red);font-size:13px">${escHtml(typeof data?.error === 'string' ? data.error : JSON.stringify(data?.error) || "无策略单")}</span>`;
        return;
    }

    const orders = Array.isArray(data) ? data : (data?.data || []);
    if (!orders.length) {
        el.innerHTML = '<span class="loading">无策略单</span>';
        return;
    }

    let html = `<table><thead><tr><th>合约</th><th>触发价</th><th>方向</th><th>委托价</th><th>数量</th></tr></thead><tbody>`;
    orders.forEach((o, i) => {
        const cls = i % 2 === 1 ? ' class="even"' : '';
        const sideClass = o.side === "buy" ? "bullish" : "bearish";
        html += `<tr${cls}>
            <td>${o.symbol || o.instId}</td>
            <td class="num">${fmtNum(o.triggerPrice || o.triggerPx)}</td>
            <td class="${sideClass}">${o.side === "buy" ? "买入" : "卖出"}</td>
            <td class="num">${fmtNum(o.executePrice || o.ordPx || o.price)}</td>
            <td class="num">${o.size || o.sz}</td>
        </tr>`;
    });
    html += "</tbody></table>";
    el.innerHTML = html;
}

// ── Market ──────────────────────────────────────────────────────────

async function loadMarket(symbol) {
    currentSymbol = symbol;
    // Clear stale data immediately on tab switch
    document.getElementById("ticker-content").innerHTML = '<span class="loading">加载中...</span>';
    document.getElementById("indicators-content").innerHTML = '<span class="loading">加载中...</span>';
    document.getElementById("orderflow-content").innerHTML = '<span class="loading">加载中...</span>';
    const derivEl = document.getElementById("derivatives-content");
    if (derivEl) {
        derivEl.innerHTML = '<span class="loading">加载中...</span>';
        const card = derivEl.closest(".card");
        if (card) card.querySelector("h3").textContent = "衍生品数据";
    }
    const indCard = document.getElementById("indicators-content")?.closest(".card");
    if (indCard) indCard.querySelector("h3").textContent = "技术指标（多时间框架）";
    try {
        const swapId = symbol.endsWith("-SWAP") ? symbol : `${symbol}-SWAP`;
        const [ticker, indicators, orderbook, fundingRate, openInterest] = await Promise.all([
            api(`/api/market/ticker/${symbol}`),
            api(`/api/market/indicators/${symbol}`),
            api(`/api/market/orderbook/${symbol}`),
            api(`/api/market/funding-rate/${swapId}`).catch(() => null),
            api(`/api/market/open-interest/${swapId}`).catch(() => null),
        ]);
        marketData = { ticker, indicators, orderbook, fundingRate, openInterest };
        renderTicker(ticker);
        renderDerivatives(fundingRate, openInterest);
        renderIndicators(indicators);
        renderOrderflow(orderbook);
        initKlineChart();
        loadChartCandles(symbol, activeBar);

        // Auto-cache market + account data for Claude Code consumption (fire-and-forget)
        fetch(`/api/market/cache/${encodeURIComponent(symbol)}?include_account=true`, {
            method: "POST"
        }).catch(e => console.warn("Cache save failed (non-critical):", e));
    } catch (e) {
        showError("ticker-content", e.message);
    }
}

async function loadBitgetMarket(symbol) {
    currentSymbol = symbol;
    document.getElementById("ticker-content").innerHTML = '<span class="loading">加载中...</span>';
    document.getElementById("indicators-content").innerHTML = '<span class="loading">加载中...</span>';
    document.getElementById("orderflow-content").innerHTML = '<span class="loading">加载中...</span>';
    const derivEl = document.getElementById("derivatives-content");
    if (derivEl) {
        derivEl.innerHTML = '<span class="loading">加载中...</span>';
        // Update card title for spot context
        const card = derivEl.closest(".card");
        if (card) card.querySelector("h3").textContent = "现货市场数据";
    }
    // Update indicators card title
    const indCard = document.getElementById("indicators-content")?.closest(".card");
    if (indCard) indCard.querySelector("h3").textContent = "技术指标（4H K线计算）";
    try {
        const [ticker, candles, depth, trades] = await Promise.all([
            api(`/api/bitget/ticker/${symbol}`),
            api(`/api/bitget/candles/${symbol}?granularity=4h&limit=100`),
            api(`/api/bitget/depth/${symbol}?limit=20`),
            api(`/api/bitget/trades/${symbol}?limit=50`),
        ]);
        marketData = { ticker, candles, depth, trades };
        renderBitgetTicker(ticker);
        renderBitgetSpotData(ticker, depth);
        renderBitgetIndicators(candles);
        renderBitgetOrderflow(depth, trades);
        initKlineChart();
        loadChartCandles(symbol, activeBar);

        // Auto-cache for Claude Code consumption (fire-and-forget)
        fetch(`/api/market/cache/${encodeURIComponent(symbol)}?include_account=true`, {
            method: "POST"
        }).catch(e => console.warn("Cache save failed (non-critical):", e));
    } catch (e) {
        showError("ticker-content", e.message);
    }
}

// ── K-Line Chart ─────────────────────────────────────────────────

function initKlineChart() {
    if (chartInitialized) return;
    const container = document.getElementById("kline-container");
    if (!container) return;

    if (typeof klinecharts === "undefined") {
        container.innerHTML = '<div style="padding:40px;text-align:center;color:#999;">' +
            '<p>⚠️ KLineChart 库加载失败</p>' +
            '<p style="font-size:12px;margin-top:8px;">请检查网络连接或刷新页面重试</p></div>';
        return;
    }

    klineChart = klinecharts.init("kline-container", {
        styles: {
            grid: {
                show: true,
                horizontal: { color: "#f0f0f0" },
                vertical: { color: "#f0f0f0" },
            },
            candle: {
                type: "candle_solid",
                bar: {
                    upColor: "#10b981",
                    downColor: "#ef4444",
                    upBorderColor: "#10b981",
                    downBorderColor: "#ef4444",
                    noChangeColor: "#999",
                },
                priceMark: {
                    high: { textFamily: "SF Mono, Fira Code, monospace", textSize: 11 },
                    low: { textFamily: "SF Mono, Fira Code, monospace", textSize: 11 },
                    last: {
                        upColor: "#10b981",
                        downColor: "#ef4444",
                        textFamily: "SF Mono, Fira Code, monospace",
                        textSize: 11,
                    },
                },
            },
            indicator: {
                ohlc: { upColor: "#10b981", downColor: "#ef4444" },
                bars: [
                    { upColor: "rgba(16,185,129,0.6)", downColor: "rgba(239,68,68,0.6)" },
                ],
            },
            xAxis: {
                axisLine: { color: "#dce3ea" },
                tickLine: { color: "#dce3ea" },
                tickText: { color: "#374151", size: 11 },
            },
            yAxis: {
                axisLine: { color: "#dce3ea" },
                tickLine: { color: "#dce3ea" },
                tickText: { color: "#374151", size: 11 },
            },
            separator: { color: "#dce3ea" },
        },
    });

    // Default MA overlay
    klineChart.createIndicator("MA", false, { id: "candle_pane" });

    // Bind timeframe tabs
    document.querySelectorAll(".tf-tab").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tf-tab").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            activeBar = btn.dataset.bar;
            loadChartCandles(currentSymbol, activeBar);
        });
    });

    // Bind indicator toggles
    document.querySelectorAll(".ind-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const indicator = btn.dataset.indicator;
            if (activeIndicators.has(indicator)) {
                activeIndicators.delete(indicator);
                btn.classList.remove("active");
                removeChartIndicator(indicator);
            } else {
                activeIndicators.add(indicator);
                btn.classList.add("active");
                addChartIndicator(indicator);
            }
        });
    });

    // Responsive resize
    const ro = new ResizeObserver(() => {
        if (klineChart) {
            const rect = container.getBoundingClientRect();
            klineChart.resize(rect.width, rect.height);
        }
    });
    ro.observe(container);

    chartInitialized = true;
}

function addChartIndicator(name) {
    if (!klineChart) return;
    if (name === "MA" || name === "BOLL") {
        klineChart.createIndicator(name, false, { id: "candle_pane" });
    } else {
        const paneId = klineChart.createIndicator(name, true);
        if (paneId) indicatorPaneMap[name] = paneId;
    }
}

function removeChartIndicator(name) {
    if (!klineChart) return;
    if (name === "MA" || name === "BOLL") {
        klineChart.removeIndicator("candle_pane", name);
    } else {
        const paneId = indicatorPaneMap[name];
        if (paneId) {
            klineChart.removeIndicator(paneId, name);
            delete indicatorPaneMap[name];
        }
    }
}

async function loadChartCandles(symbol, bar) {
    if (!klineChart) return;

    const tab = document.querySelector(`.tab[data-symbol="${symbol}"]`);
    const isBitget = tab?.dataset.source === "bitget";

    try {
        let raw;
        if (isBitget) {
            const barMap = { "1m":"1min", "5m":"5min", "15m":"15min", "30m":"30min", "1H":"1h", "4H":"4h", "1D":"1day" };
            const bitgetBar = barMap[bar] || "1h";
            raw = await api(`/api/bitget/candles/${symbol}?granularity=${bitgetBar}&limit=200`);
        } else {
            raw = await api(`/api/market/candles/${symbol}?bar=${bar}&limit=200`);
        }
        const arr = Array.isArray(raw) ? raw : (raw?.data || []);
        // Both exchanges: [ts, o, h, l, c, vol, ...], newest-first → reverse for KLineChart
        const data = [...arr].reverse().map(c => ({
            timestamp: parseInt(c[0]),
            open: parseFloat(c[1]),
            high: parseFloat(c[2]),
            low: parseFloat(c[3]),
            close: parseFloat(c[4]),
            volume: parseFloat(c[5]),
        }));
        klineChart.applyNewData(data);
    } catch (e) {
        console.error("Chart data load error:", e);
    }
}

function renderTicker(data) {
    const el = document.getElementById("ticker-content");
    const t = Array.isArray(data) ? data[0] : data;
    if (!t || t.error) {
        el.innerHTML = `<span class="loading">${t?.error || "无数据"}</span>`;
        return;
    }

    const last = parseFloat(t.last || 0);
    const open = parseFloat(t.open24h || 0);
    const change = open ? ((last - open) / open * 100) : 0;
    const changeClass = change >= 0 ? "positive" : "negative";

    el.innerHTML = `
        <div class="ticker-grid">
            <div class="ticker-item">
                <div class="label">当前价</div>
                <div class="value">${fmtNum(t.last)}</div>
            </div>
            <div class="ticker-item">
                <div class="label">24h 涨跌</div>
                <div class="value ${changeClass}">${change >= 0 ? "+" : ""}${change.toFixed(2)}%</div>
            </div>
            <div class="ticker-item">
                <div class="label">24h 最高</div>
                <div class="value">${fmtNum(t.high24h)}</div>
            </div>
            <div class="ticker-item">
                <div class="label">24h 最低</div>
                <div class="value">${fmtNum(t.low24h)}</div>
            </div>
            <div class="ticker-item">
                <div class="label">24h 成交量</div>
                <div class="value">${fmtVol(t.volCcy24h)}</div>
            </div>
        </div>`;
}

function renderBitgetTicker(data) {
    const el = document.getElementById("ticker-content");
    const t = Array.isArray(data) ? data[0] : data;
    if (!t || t.error) {
        el.innerHTML = `<span class="loading">${t?.error || "无数据"}</span>`;
        return;
    }

    const last = parseFloat(t.lastPr || 0);
    const change = parseFloat(t.change24h || 0) * 100;
    const changeClass = change >= 0 ? "positive" : "negative";

    el.innerHTML = `
        <div class="ticker-grid">
            <div class="ticker-item">
                <div class="label">当前价</div>
                <div class="value">${fmtNum(t.lastPr)}</div>
            </div>
            <div class="ticker-item">
                <div class="label">24h 涨跌</div>
                <div class="value ${changeClass}">${change >= 0 ? "+" : ""}${change.toFixed(2)}%</div>
            </div>
            <div class="ticker-item">
                <div class="label">24h 最高</div>
                <div class="value">${fmtNum(t.high24h)}</div>
            </div>
            <div class="ticker-item">
                <div class="label">24h 最低</div>
                <div class="value">${fmtNum(t.low24h)}</div>
            </div>
            <div class="ticker-item">
                <div class="label">24h 成交量</div>
                <div class="value">${fmtVol(t.usdtVolume)}</div>
            </div>
        </div>`;
}

// ── Bitget Market Data Renderers ─────────────────────────────────

function renderBitgetSpotData(ticker, depth) {
    const el = document.getElementById("derivatives-content");
    if (!el) return;
    const t = Array.isArray(ticker) ? ticker[0] : ticker;
    const d = Array.isArray(depth) ? depth[0] : depth;
    if ((!t || t.error) && (!d || d.error)) {
        el.innerHTML = '<span class="loading">无数据</span>';
        return;
    }
    let html = '<div class="ticker-grid">';
    if (t && !t.error) {
        html += `
            <div class="ticker-item">
                <div class="label">24h 成交量</div>
                <div class="value">${fmtVol(t.usdtVolume)}</div>
            </div>`;
    }
    if (d && !d.error) {
        const bids = d.bids || [];
        const asks = d.asks || [];
        if (bids.length && asks.length) {
            const bestBid = parseFloat(bids[0][0]);
            const bestAsk = parseFloat(asks[0][0]);
            const spread = bestAsk - bestBid;
            const spreadPct = bestBid ? (spread / bestBid * 100) : 0;
            const bidVol = bids.slice(0, 10).reduce((s, b) => s + parseFloat(b[1] || 0), 0);
            const askVol = asks.slice(0, 10).reduce((s, a) => s + parseFloat(a[1] || 0), 0);
            const imbalance = askVol > 0 ? (bidVol / askVol) : 0;
            html += `
                <div class="ticker-item">
                    <div class="label">最优买/卖</div>
                    <div class="value">${fmtNum(bestBid)} / ${fmtNum(bestAsk)}</div>
                </div>
                <div class="ticker-item">
                    <div class="label">价差</div>
                    <div class="value">${fmtNum(spread)} (${spreadPct.toFixed(3)}%)</div>
                </div>
                <div class="ticker-item">
                    <div class="label">买卖失衡(10档)</div>
                    <div class="value ${imbalance > 1.2 ? 'positive' : imbalance < 0.8 ? 'negative' : ''}">${imbalance.toFixed(2)}</div>
                </div>`;
        }
    }
    html += '</div>';
    el.innerHTML = html;
}

function renderBitgetIndicators(candles) {
    const el = document.getElementById("indicators-content");
    if (!el) return;
    if (!candles || candles.error) {
        el.innerHTML = `<span class="loading">${candles?.error || "无数据"}</span>`;
        return;
    }
    const raw = Array.isArray(candles) ? candles : (candles.data || []);
    if (!raw.length) {
        el.innerHTML = '<span class="loading">无K线数据</span>';
        return;
    }
    // Bitget candles: [ts, open, high, low, close, vol, quoteVol] — newest first
    const bars = [...raw].reverse().map(c => ({
        ts: parseInt(c[0]), open: parseFloat(c[1]), high: parseFloat(c[2]),
        low: parseFloat(c[3]), close: parseFloat(c[4]), vol: parseFloat(c[5]),
    }));
    const closes = bars.map(b => b.close);
    const highs = bars.map(b => b.high);
    const lows = bars.map(b => b.low);

    // Compute indicators
    const rsi = computeRSI(closes, 14);
    const macd = computeMACD(closes, 12, 26, 9);
    const ema7 = computeEMA(closes, 7);
    const bb = computeBB(closes, 20, 2);

    const rows = [
        { name: "RSI(14)", value: rsi != null ? rsi.toFixed(1) : "—" },
        { name: "MACD", value: macd ? `${macd.dif.toFixed(4)} / ${macd.dea.toFixed(4)} / ${macd.hist.toFixed(4)}` : "—" },
        { name: "EMA(7)", value: ema7 != null ? fmtNum(ema7) : "—" },
        { name: "BB(20,2)", value: bb ? `${fmtNum(bb.upper)} / ${fmtNum(bb.mid)} / ${fmtNum(bb.lower)}` : "—" },
    ];

    let html = `<table><thead><tr><th>指标</th><th>4H 数值</th><th>信号</th></tr></thead><tbody>`;
    rows.forEach((r, i) => {
        const cls = i % 2 === 1 ? ' class="even"' : '';
        let signal = "";
        if (r.name === "RSI(14)" && rsi != null) {
            signal = rsi > 70 ? '<span class="bearish">超买</span>' : rsi < 30 ? '<span class="bullish">超卖</span>' : "中性";
        } else if (r.name === "MACD" && macd) {
            signal = macd.hist > 0 ? '<span class="bullish">多头</span>' : '<span class="bearish">空头</span>';
        }
        html += `<tr${cls}><td>${r.name}</td><td class="num">${r.value}</td><td>${signal}</td></tr>`;
    });
    html += "</tbody></table>";
    el.innerHTML = html;
}

function renderBitgetOrderflow(depth, trades) {
    const el = document.getElementById("orderflow-content");
    if (!el) return;
    const d = Array.isArray(depth) ? depth[0] : depth;
    const t = Array.isArray(trades) ? trades : (trades || []);

    if ((!d || d.error) && !t.length) {
        el.innerHTML = '<span class="loading">无数据</span>';
        return;
    }

    let html = "";
    // Orderbook summary
    if (d && !d.error) {
        const bids = d.bids || [];
        const asks = d.asks || [];
        if (bids.length && asks.length) {
            const bestBid = parseFloat(bids[0][0]);
            const bestAsk = parseFloat(asks[0][0]);
            const spread = bestAsk - bestBid;
            const spreadPct = bestBid ? (spread / bestBid * 100) : 0;
            html += `<div style="margin-bottom:10px;font-size:13px">
                <strong>买一:</strong> ${fmtNum(bestBid)} | <strong>卖一:</strong> ${fmtNum(bestAsk)} |
                <strong>价差:</strong> ${fmtNum(spread)} (${spreadPct.toFixed(3)}%)
            </div>`;
            // Orderbook table
            html += `<table><thead><tr><th>买量</th><th>买价</th><th></th><th>卖价</th><th>卖量</th></tr></thead><tbody>`;
            const rows = Math.min(5, Math.max(bids.length, asks.length));
            for (let i = 0; i < rows; i++) {
                const bid = bids[i] || [];
                const ask = asks[i] || [];
                const cls = i % 2 === 1 ? ' class="even"' : '';
                html += `<tr${cls}>
                    <td class="num positive">${bid[1] ? parseFloat(bid[1]).toFixed(4) : ""}</td>
                    <td class="num">${bid[0] ? fmtNum(bid[0]) : ""}</td>
                    <td style="text-align:center;color:var(--text-muted)">|</td>
                    <td class="num">${ask[0] ? fmtNum(ask[0]) : ""}</td>
                    <td class="num negative">${ask[1] ? parseFloat(ask[1]).toFixed(4) : ""}</td>
                </tr>`;
            }
            html += "</tbody></table>";
        }
    }
    // Recent trades
    if (t.length) {
        html += `<div style="margin-top:12px"><strong style="font-size:13px">最近成交</strong></div>`;
        html += `<table><thead><tr><th>时间</th><th>方向</th><th>价格</th><th>数量</th></tr></thead><tbody>`;
        t.slice(0, 15).forEach((trade, i) => {
            const side = trade.side || "";
            const cls = i % 2 === 1 ? ' class="even"' : '';
            const dirClass = side === "buy" ? "positive" : "negative";
            const ts = trade.ts || trade.cTime || "";
            const time = ts ? new Date(parseInt(ts)).toLocaleTimeString("zh-CN") : "—";
            html += `<tr${cls}>
                <td>${time}</td>
                <td class="${dirClass}">${side === "buy" ? "买入" : "卖出"}</td>
                <td class="num">${fmtNum(trade.price)}</td>
                <td class="num">${parseFloat(trade.size || trade.quantity || 0).toFixed(4)}</td>
            </tr>`;
        });
        html += "</tbody></table>";
    }
    el.innerHTML = html;
}

// ── Indicator Computation Helpers ────────────────────────────────

function computeEMA(data, period) {
    if (data.length < period) return null;
    const k = 2 / (period + 1);
    let ema = data.slice(0, period).reduce((s, v) => s + v, 0) / period;
    for (let i = period; i < data.length; i++) {
        ema = data[i] * k + ema * (1 - k);
    }
    return ema;
}

function computeRSI(closes, period = 14) {
    if (closes.length < period + 1) return null;
    let gains = 0, losses = 0;
    for (let i = 1; i <= period; i++) {
        const diff = closes[i] - closes[i - 1];
        if (diff > 0) gains += diff; else losses -= diff;
    }
    let avgGain = gains / period;
    let avgLoss = losses / period;
    for (let i = period + 1; i < closes.length; i++) {
        const diff = closes[i] - closes[i - 1];
        avgGain = (avgGain * (period - 1) + (diff > 0 ? diff : 0)) / period;
        avgLoss = (avgLoss * (period - 1) + (diff < 0 ? -diff : 0)) / period;
    }
    if (avgLoss === 0) return 100;
    const rs = avgGain / avgLoss;
    return 100 - 100 / (1 + rs);
}

function computeMACD(closes, fast = 12, slow = 26, signal = 9) {
    if (closes.length < slow) return null;
    const emaFast = computeEMA(closes, fast);
    const emaSlow = computeEMA(closes, slow);
    if (emaFast == null || emaSlow == null) return null;
    // Compute DIF series for signal EMA
    const kFast = 2 / (fast + 1), kSlow = 2 / (slow + 1), kSig = 2 / (signal + 1);
    let ef = closes.slice(0, fast).reduce((s, v) => s + v, 0) / fast;
    let es = closes.slice(0, slow).reduce((s, v) => s + v, 0) / slow;
    const difSeries = [];
    for (let i = 0; i < closes.length; i++) {
        if (i >= fast) ef = closes[i] * kFast + ef * (1 - kFast);
        if (i >= slow) {
            es = closes[i] * kSlow + es * (1 - kSlow);
            difSeries.push(ef - es);
        }
    }
    if (difSeries.length < signal) return null;
    let dea = difSeries.slice(0, signal).reduce((s, v) => s + v, 0) / signal;
    for (let i = signal; i < difSeries.length; i++) {
        dea = difSeries[i] * kSig + dea * (1 - kSig);
    }
    const dif = difSeries[difSeries.length - 1];
    return { dif, dea, hist: 2 * (dif - dea) };
}

function computeBB(closes, period = 20, stdDev = 2) {
    if (closes.length < period) return null;
    const slice = closes.slice(-period);
    const mid = slice.reduce((s, v) => s + v, 0) / period;
    const variance = slice.reduce((s, v) => s + (v - mid) ** 2, 0) / period;
    const sd = Math.sqrt(variance);
    return { upper: mid + stdDev * sd, mid, lower: mid - stdDev * sd };
}

function renderDerivatives(fundingRate, openInterest) {
    const el = document.getElementById("derivatives-content");
    if (!el) return;
    const fr = Array.isArray(fundingRate) ? fundingRate[0] : fundingRate;
    const oi = Array.isArray(openInterest) ? openInterest[0] : openInterest;
    if ((!fr || fr.error) && (!oi || oi.error)) {
        el.innerHTML = '<span class="loading">无衍生品数据</span>';
        return;
    }
    let html = '<div class="ticker-grid">';
    if (fr && !fr.error) {
        const ratePct = (parseFloat(fr.fundingRate) * 100).toFixed(4);
        const nextTime = fr.nextFundingTime ? new Date(Number(fr.nextFundingTime)).toLocaleString("zh-CN") : "—";
        html += `
            <div class="ticker-item">
                <div class="label">资金费率</div>
                <div class="value">${ratePct}%</div>
            </div>
            <div class="ticker-item">
                <div class="label">下次结算</div>
                <div class="value">${nextTime}</div>
            </div>`;
    }
    if (oi && !oi.error) {
        const oiUsd = fmtVol(oi.oiUsd);
        const oiCcy = parseFloat(oi.oiCcy).toLocaleString("en", { maximumFractionDigits: 2 });
        html += `
            <div class="ticker-item">
                <div class="label">持仓量(USD)</div>
                <div class="value">${oiUsd}</div>
            </div>
            <div class="ticker-item">
                <div class="label">持仓量(币)</div>
                <div class="value">${oiCcy}</div>
            </div>`;
    }
    html += '</div>';
    el.innerHTML = html;
}

function renderIndicators(data) {
    const el = document.getElementById("indicators-content");
    if (!data || data.error) {
        el.innerHTML = `<span class="loading">${data?.error || "无数据"}</span>`;
        return;
    }

    const indData = data.indicators || data;
    const timeframes = Object.keys(indData).filter(k => k !== "instId");
    const tfLabel = tf => tf === "1Dutc" ? "1D" : tf;

    if (!timeframes.length) {
        el.innerHTML = '<span class="loading">无指标数据</span>';
        return;
    }

    const indicatorNames = {
        rsi: "RSI(14)", macd: "MACD", ema7: "EMA(7)", bb: "BB(20,2)",
        supertrend: "Supertrend", kdj: "KDJ", adx: "ADX(14)", atr: "ATR(14)", obv: "OBV"
    };

    let html = `<table><thead><tr><th>指标</th>`;
    timeframes.forEach(tf => { html += `<th>${tfLabel(tf)}</th>`; });
    html += `</tr></thead><tbody>`;

    for (const [key, name] of Object.entries(indicatorNames)) {
        html += `<tr><td>${name}</td>`;
        timeframes.forEach((tf, i) => {
            const tfData = indData[tf];
            const val = tfData?.[key];
            if (!val || val.error) {
                html += `<td class="num">—</td>`;
            } else {
                html += `<td class="num">${formatIndicator(key, val)}</td>`;
            }
        });
        html += `</tr>`;
    }

    html += `</tbody></table>`;
    el.innerHTML = html;
}

// Multi-component indicator display formatters
const MULTI_FMT = {
    macd:  v => [v.dif, v.dea, v.macd].map(n => parseFloat(n).toFixed(2)).join("/"),
    bb:    v => [v.upper, v.middle, v.lower].map(n => parseFloat(n).toFixed(2)).join("/"),
    kdj:   v => [v.k, v.d, v.j].map(n => parseFloat(n).toFixed(2)).join("/"),
    adx:   v => [v.adx, v.diPlus, v.diMinus].map(n => parseFloat(n).toFixed(2)).join("/"),
};

function formatIndicator(key, data) {
    const d = Array.isArray(data) ? data[0] : data;
    if (!d) return "—";

    // 嵌套结构: d.data -> timeframes -> indicators -> key -> values
    if (d.data) {
        const inner = Array.isArray(d.data) ? d.data[0] : d.data;
        if (inner?.timeframes) {
            let foundKey = false;
            for (const tf of Object.values(inner.timeframes)) {
                const indicators = tf.indicators || tf;
                const normalizedKey = key.replace(/\d+$/, "").toUpperCase();
                const vals = indicators[key] || indicators[key.toUpperCase()] || indicators[normalizedKey];
                if (vals !== undefined) foundKey = true;
                if (vals && (!Array.isArray(vals) || vals.length > 0)) {
                    const entry = Array.isArray(vals) ? vals[0] : vals;
                    const v = entry?.values || entry;
                    if (typeof v === "object") {
                        // Multi-component: try known formatters first
                        const fmt = MULTI_FMT[key];
                        if (fmt) { try { return fmt(v); } catch(_) {} }
                        // Fallback: return first numeric value
                        for (const val of Object.values(v)) {
                            if (!isNaN(parseFloat(val))) return parseFloat(val).toFixed(2);
                        }
                    }
                    if (typeof v === "number" || typeof v === "string") {
                        const n = parseFloat(v);
                        if (!isNaN(n)) return n.toFixed(2);
                    }
                }
            }
            if (foundKey) return "—";
        }
    }

    if (d.value !== undefined) return parseFloat(d.value).toFixed(2);
    const normalizedKey = key.replace(/\d+$/, "").toUpperCase();
    if (d[key] !== undefined) return parseFloat(d[key]).toFixed(2);
    if (d[key.toUpperCase()] !== undefined) return parseFloat(d[key.toUpperCase()]).toFixed(2);
    if (d[normalizedKey] !== undefined) return parseFloat(d[normalizedKey]).toFixed(2);

    return JSON.stringify(d).substring(0, 30);
}

function renderOrderflow(data) {
    const el = document.getElementById("orderflow-content");
    if (!data || data.error) {
        el.innerHTML = `<span class="loading">${data?.error || "无数据"}</span>`;
        return;
    }

    const d = Array.isArray(data) ? data[0] : data;
    if (!d?.asks || !d?.bids) {
        el.innerHTML = '<span class="loading">无订单簿数据</span>';
        return;
    }

    const bestAsk = parseFloat(d.asks[0]?.[0] || 0);
    const bestBid = parseFloat(d.bids[0]?.[0] || 0);
    const spread = bestAsk - bestBid;
    const spreadPct = bestBid ? (spread / bestBid * 100) : 0;

    // Calculate imbalance
    let bidTotal = 0, askTotal = 0;
    d.bids.slice(0, 10).forEach(b => { bidTotal += parseFloat(b[1] || 0); });
    d.asks.slice(0, 10).forEach(a => { askTotal += parseFloat(a[1] || 0); });
    const imbalance = askTotal ? (bidTotal / askTotal) : 0;

    let html = `
        <div class="ticker-grid">
            <div class="ticker-item">
                <div class="label">Best Bid</div>
                <div class="value bullish">${fmtNum(bestBid)}</div>
            </div>
            <div class="ticker-item">
                <div class="label">Best Ask</div>
                <div class="value bearish">${fmtNum(bestAsk)}</div>
            </div>
            <div class="ticker-item">
                <div class="label">Spread</div>
                <div class="value">${spread.toFixed(2)} (${spreadPct.toFixed(3)}%)</div>
            </div>
            <div class="ticker-item">
                <div class="label">Bid/Ask Imbalance (10L)</div>
                <div class="value ${imbalance > 1.2 ? 'bullish' : imbalance < 0.8 ? 'bearish' : ''}">${imbalance.toFixed(2)}x</div>
            </div>
        </div>`;

    // Top 5 bid/ask table
    html += `<table style="margin-top:10px"><thead><tr><th>Bid Qty</th><th>Bid Price</th><th></th><th>Ask Price</th><th>Ask Qty</th></tr></thead><tbody>`;
    for (let i = 0; i < 5; i++) {
        const bid = d.bids[i] || [];
        const ask = d.asks[i] || [];
        const cls = i % 2 === 1 ? ' class="even"' : '';
        html += `<tr${cls}>
            <td class="num bullish">${fmtNum(bid[1])}</td>
            <td class="num">${fmtNum(bid[0])}</td>
            <td style="text-align:center;color:var(--text-muted)">|</td>
            <td class="num">${fmtNum(ask[0])}</td>
            <td class="num bearish">${fmtNum(ask[1])}</td>
        </tr>`;
    }
    html += `</tbody></table>`;

    el.innerHTML = html;
}

// ── Analysis ────────────────────────────────────────────────────────

let analysisAbort = null;
let analysisTimer = null;

async function startAnalysis() {
    if (!document.getElementById("analysis-result")) return;
    const btn = document.getElementById("btn-analyze");
    const container = document.getElementById("analysis-result");
    const empty = document.getElementById("analysis-empty");
    const content = document.getElementById("analysis-content");

    btn.disabled = true;
    btn.textContent = "分析中...";
    container.style.display = "block";
    if (empty) empty.style.display = "none";

    const startTime = Date.now();
    content.innerHTML = `
        <div class="analysis-loading">
            <div class="spinner"></div>
            <p>正在分析 ${currentSymbol}，Claude Code 生成报告中...</p>
            <p class="analysis-timer">已用时 <span id="elapsed">0</span> 秒</p>
            <button id="btn-cancel-analysis" class="btn-cancel">取消分析</button>
        </div>`;

    const elapsedEl = document.getElementById("elapsed");
    analysisTimer = setInterval(() => {
        elapsedEl.textContent = Math.floor((Date.now() - startTime) / 1000);
    }, 1000);

    const cancelBtn = document.getElementById("btn-cancel-analysis");
    analysisAbort = new AbortController();
    cancelBtn.onclick = async () => {
        cancelBtn.disabled = true;
        cancelBtn.textContent = "取消中...";
        try { await fetch("/api/analyze/cancel", { method: "POST" }); } catch (_) {}
        analysisAbort.abort();
    };

    try {
        const result = await api("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbols: [currentSymbol], include_positions: true }),
            signal: analysisAbort.signal,
        });
        renderAnalysis(result);
        saveToHistory(result);
    } catch (e) {
        if (e.name === "AbortError") {
            showError("analysis-content", "分析已取消");
        } else {
            showError("analysis-content", "分析失败: " + e.message);
        }
    } finally {
        clearInterval(analysisTimer);
        analysisTimer = null;
        analysisAbort = null;
        btn.disabled = false;
        btn.textContent = "开始分析";
    }
}

async function cacheForAnalysis() {
    const btn = document.getElementById("btn-cache");
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = "缓存中...";
    try {
        const result = await api(
            `/api/market/cache/${encodeURIComponent(currentSymbol)}?include_account=true`,
            { method: "POST" }
        );
        btn.textContent = "已缓存 ✓";
        console.log("Cache saved:", result.file);
    } catch (e) {
        btn.textContent = "缓存失败";
        console.error("Cache failed:", e);
    }
    setTimeout(() => {
        btn.disabled = false;
        btn.textContent = "缓存数据";
    }, 2000);
}

function renderAnalysis(result) {
    const el = document.getElementById("analysis-content");
    const container = document.getElementById("analysis-result");
    const empty = document.getElementById("analysis-empty");

    container.style.display = "block";
    if (empty) empty.style.display = "none";

    const html = result.html || result.error || "无分析结果";
    el.innerHTML = html;

    container.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ── History ─────────────────────────────────────────────────────────

function saveToHistory(result) {
    const history = JSON.parse(localStorage.getItem("analysis_history") || "[]");
    history.unshift({
        id: result.id,
        symbol: result.symbol,
        html: result.html,
        timestamp: result.timestamp,
    });
    if (history.length > 10) history.length = 10;
    localStorage.setItem("analysis_history", JSON.stringify(history));
    loadHistory();
}

async function loadHistory() {
    const el = document.getElementById("history-list");
    try {
        const reports = await api("/api/reports");
        if (!reports.length) {
            el.innerHTML = '<p class="loading">暂无分析历史</p>';
            return;
        }
        el.innerHTML = reports.map(r => `
            <div class="history-item" onclick="loadReport('${r.id}')">
                <div class="meta">${r.symbol} — ${new Date(r.timestamp).toLocaleString("zh-CN")}</div>
            </div>
        `).join("");
    } catch (e) {
        const history = JSON.parse(localStorage.getItem("analysis_history") || "[]");
        if (!history.length) {
            el.innerHTML = '<p class="loading">暂无分析历史</p>';
            return;
        }
        el.innerHTML = history.map((h, i) => `
            <div class="history-item" onclick="showHistoryItem(${i})">
                <div class="meta">${h.symbol} — ${new Date(h.timestamp).toLocaleString("zh-CN")}</div>
            </div>
        `).join("");
    }
}

function showHistoryItem(index) {
    const history = JSON.parse(localStorage.getItem("analysis_history") || "[]");
    if (history[index]) {
        renderAnalysis(history[index]);
    }
}

async function loadReport(id) {
    try {
        const result = await api(`/api/reports/${id}`);
        renderAnalysis(result);
    } catch (e) {
        showError("analysis-content", "加载报告失败: " + e.message);
    }
}

function toggleHistory() {
    const el = document.getElementById("history-list");
    el.classList.toggle("expanded");
    el.classList.toggle("collapsed");
}

// ── Refresh All ─────────────────────────────────────────────────────

async function refreshAll() {
    const btn = document.getElementById("btn-refresh");
    btn.disabled = true;
    try {
        const tasks = [];
        if (document.getElementById("valuation-content")) tasks.push(refreshAccount());
        if (document.getElementById("ticker-content")) tasks.push(loadMarket(currentSymbol));
        await Promise.all(tasks);
    } finally {
        btn.disabled = false;
    }
}

// ── UI Helpers ──────────────────────────────────────────────────────

function setStatus(state) {
    const dot = document.getElementById("status-indicator");
    dot.className = "status-dot";
    if (state === "ok") dot.classList.add("ok");
    else if (state === "error") dot.classList.add("error");
}

function showLoading(text) {
    document.getElementById("loading-text").textContent = text;
    document.getElementById("loading-overlay").style.display = "flex";
}

function hideLoading() {
    document.getElementById("loading-overlay").style.display = "none";
}

function showError(elementId, message) {
    const el = document.getElementById(elementId);
    if (el) el.innerHTML = `<span style="color:var(--red)">错误: ${escHtml(message)}</span>`;
}

function fmtNum(val) {
    if (val === null || val === undefined || val === "") return "—";
    const n = parseFloat(val);
    if (isNaN(n)) return String(val);
    if (Math.abs(n) >= 1000) return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (Math.abs(n) >= 1) return n.toFixed(2);
    return n.toFixed(4);
}

function fmtVol(val) {
    const n = parseFloat(val);
    if (isNaN(n)) return "—";
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
    if (n >= 1e3) return `$${(n / 1e3).toFixed(2)}K`;
    return `$${n.toFixed(2)}`;
}

function escHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}
