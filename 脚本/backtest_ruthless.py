#!/usr/bin/env python3
"""
无情操盘手策略回测
对持仓个股用 B1/B2/B砖 买点 + S1~S5 卖点 + 死叉/黄线止损 进行回测
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from fetch_wave_data import fetch_kline, calc_ema, calc_sma

# ── 指标计算 ──────────────────────────────────────────────

def calc_dema(closes, period=10):
    ema1 = calc_ema(closes, period)
    ema2 = calc_ema(ema1, period)
    return ema2  # DEMA = EMA(EMA(close, N), N)

def calc_quad_ma(closes):
    ma14 = calc_sma(closes, 14)
    ma28 = calc_sma(closes, 28)
    ma57 = calc_sma(closes, 57)
    ma114 = calc_sma(closes, 114)
    result = [None] * len(closes)
    for i in range(len(closes)):
        if all(x is not None and x[i] is not None for x in [ma14, ma28, ma57, ma114]):
            result[i] = (ma14[i] + ma28[i] + ma57[i] + ma114[i]) / 4
        else:
            result[i] = None
    return result

def calc_kdj(klines, n=9, m1=3, m2=3):
    """KDJ指标"""
    length = len(klines)
    k_vals = [50.0] * length
    d_vals = [50.0] * length
    j_vals = [50.0] * length
    for i in range(n - 1, length):
        window = klines[max(0, i - n + 1):i + 1]
        highest = max(bar['high'] for bar in window)
        lowest = min(bar['low'] for bar in window)
        if highest == lowest:
            rsv = 50.0
        else:
            rsv = (klines[i]['close'] - lowest) / (highest - lowest) * 100
        if i == n - 1:
            k_vals[i] = rsv
            d_vals[i] = rsv
        else:
            k_vals[i] = (m1 - 1) / m1 * k_vals[i - 1] + 1 / m1 * rsv
            d_vals[i] = (m2 - 1) / m2 * d_vals[i - 1] + 1 / m2 * k_vals[i]
        j_vals[i] = 3 * k_vals[i] - 2 * d_vals[i]
    return k_vals, d_vals, j_vals

def calc_brick(white, yellow):
    """砖柱: DIF=白-黄, DEA=EMA(DIF,9), brick=(DIF-DEA)*2"""
    length = len(white)
    dif = [None] * length
    for i in range(length):
        if white[i] is not None and yellow[i] is not None:
            dif[i] = white[i] - yellow[i]
    # EMA of DIF with period 9
    dif_values = [v if v is not None else 0 for v in dif]
    dea = calc_ema(dif_values, 9)
    bricks = [None] * length
    colors = [None] * length
    for i in range(length):
        if dif[i] is not None and dea[i] is not None:
            bricks[i] = (dif[i] - dea[i]) * 2
            if i > 0 and bricks[i - 1] is not None:
                if bricks[i] > 0 and bricks[i] > bricks[i - 1]:
                    colors[i] = 'red'
                elif bricks[i] > 0 and bricks[i] <= bricks[i - 1]:
                    colors[i] = 'yellow'
                elif bricks[i] < 0 and bricks[i] < bricks[i - 1]:
                    colors[i] = 'green'
                elif bricks[i] < 0 and bricks[i] >= bricks[i - 1]:
                    colors[i] = 'blue'
                elif bricks[i] == 0:
                    colors[i] = 'yellow' if bricks[i - 1] >= 0 else 'blue'
    return bricks, colors

def is_death_cross(white, yellow, i):
    """白线下穿黄线"""
    if i < 1: return False
    if any(v is None for v in [white[i], white[i-1], yellow[i], yellow[i-1]]):
        return False
    return white[i-1] >= yellow[i-1] and white[i] < yellow[i]

def is_golden_cross(white, yellow, i):
    """白线上穿黄线"""
    if i < 1: return False
    if any(v is None for v in [white[i], white[i-1], yellow[i], yellow[i-1]]):
        return False
    return white[i-1] <= yellow[i-1] and white[i] > yellow[i]

# ── 信号检测 ──────────────────────────────────────────────

def detect_b1(klines, j_vals, yellow, i):
    """B1: J<15 + close >= yellow*0.97"""
    if yellow[i] is None: return False
    return j_vals[i] < 15 and klines[i]['close'] >= yellow[i] * 0.97

def detect_b2_after(klines, b1_idx, i):
    """B2: B1之后第一根中长阳(实体>=3%)"""
    if i <= b1_idx: return False
    bar = klines[i]
    body_pct = (bar['close'] - bar['open']) / bar['open'] * 100 if bar['open'] > 0 else 0
    return body_pct >= 3.0

def detect_b_brick(bricks, colors, klines, i):
    """B砖: 连续3~4根绿砖 → 过渡 → 黄砖 + B2"""
    if i < 8 or colors[i] != 'yellow':
        return False
    # 往回找: 黄砖之前应有过渡(蓝/红), 再之前应有3~4根连续绿砖
    j = i - 1
    # 跳过过渡砖(蓝/红)
    transition = 0
    while j >= 0 and colors[j] in ('blue', 'red'):
        transition += 1
        j -= 1
    if transition == 0:
        return False
    # 数绿砖
    green_count = 0
    while j >= 0 and colors[j] == 'green':
        green_count += 1
        j -= 1
    if green_count < 3 or green_count > 4:
        return False
    # 过渡期间需有B2(实体>=3%的阳线)
    has_b2 = False
    for k in range(j + green_count + 1, i + 1):
        bar = klines[k]
        body_pct = (bar['close'] - bar['open']) / bar['open'] * 100 if bar['open'] > 0 else 0
        if body_pct >= 3.0:
            has_b2 = True
            break
    return has_b2

def detect_s1(klines, white, i):
    """S1断头铡: 高位+天量大阴"""
    if i < 5 or white[i] is None: return False
    bar = klines[i]
    if bar['close'] < white[i] * 0.97: return False  # 非高位
    # 前3日阳线居多
    bull_days = sum(1 for k in range(i-3, i) if klines[k]['close'] > klines[k]['open'])
    if bull_days < 2: return False
    # 天量: >= 前期阳量均值 * 2
    bull_vols = [klines[k]['volume'] for k in range(max(0,i-20), i) if klines[k]['close'] > klines[k]['open']]
    if not bull_vols: return False
    avg_bull_vol = sum(bull_vols) / len(bull_vols)
    if bar['volume'] < avg_bull_vol * 2: return False
    # 大阴: 跌>3%
    drop_pct = (bar['open'] - bar['close']) / bar['open'] * 100
    return drop_pct > 3.0

def detect_s2(klines, white, i):
    """S2次高点反杀: 前日缩量近高+今日巨量长阴"""
    if i < 21 or white[i] is None: return False
    bar = klines[i]
    prev = klines[i-1]
    if bar['close'] < white[i] * 0.97: return False
    # 前日缩量创近20日高
    is_near_high = prev['high'] >= max(klines[k]['high'] for k in range(i-20, i))
    prev_vol_low = prev['volume'] < klines[i-2]['volume']
    if not (is_near_high and prev_vol_low): return False
    # 今日巨量长阴
    bull_vols = [klines[k]['volume'] for k in range(max(0,i-20), i) if klines[k]['close'] > klines[k]['open']]
    if not bull_vols: return False
    avg_bull_vol = sum(bull_vols) / len(bull_vols)
    drop_pct = (bar['open'] - bar['close']) / bar['open'] * 100
    return bar['volume'] >= avg_bull_vol * 2 and drop_pct > 3.0

def detect_s5(klines, white, i):
    """S5温水青蛙: 近5根K线阴肥阳瘦"""
    if i < 5 or white[i] is None: return False
    if klines[i]['close'] < white[i] * 0.97: return False
    window = klines[i-4:i+1]
    bear_bars = [b for b in window if b['close'] < b['open']]
    bull_bars = [b for b in window if b['close'] >= b['open']]
    if len(bear_bars) < 2 or len(bull_bars) < 2: return False
    avg_bear_vol = sum(b['volume'] for b in bear_bars) / len(bear_bars)
    avg_bull_vol = sum(b['volume'] for b in bull_bars) / len(bull_bars)
    avg_bear_body = sum(abs(b['open']-b['close']) for b in bear_bars) / len(bear_bars)
    avg_bull_body = sum(abs(b['open']-b['close']) for b in bull_bars) / len(bull_bars)
    return avg_bear_vol > avg_bull_vol * 1.5 and avg_bear_body > avg_bull_body * 1.5

# ── 回测引擎 ──────────────────────────────────────────────

def backtest_ruthless(code, name, capital=10000, years=1):
    """
    无情操盘手策略回测
    买入: B1 / B砖
    卖出: 死叉清仓 / 跌破黄线清仓 / S1减半 / S2清仓 / S5减仓 / 止损(买入日低点)
    """
    extra = 200  # 预热
    count = int(years * 250) + extra
    result = fetch_kline(code, count)
    if not result:
        return None
    stock_name, klines = result
    if len(klines) < extra + 50:
        return None
    closes = [bar['close'] for bar in klines]

    # 计算指标
    white = calc_dema(closes, 10)
    yellow = calc_quad_ma(closes)
    _, _, j_vals = calc_kdj(klines)
    bricks, brick_colors = calc_brick(white, yellow)

    # 回测区间: 去掉预热
    start_idx = extra
    
    # 状态
    cash = capital
    shares = 0
    buy_price = 0
    buy_low = 0  # 买入日最低点(止损位)
    trades = []
    position = 'empty'  # empty / holding
    last_b1_idx = -999

    for i in range(start_idx, len(klines)):
        bar = klines[i]
        price = bar['close']

        if yellow[i] is None or white[i] is None:
            continue

        # ══ 持仓状态: 检查卖出信号 ══
        if position == 'holding':
            sell_reason = None
            sell_shares = shares

            # 最高优先: 死叉清仓
            if is_death_cross(white, yellow, i):
                sell_reason = '☠️死叉清仓'
            # 跌破黄线清仓
            elif price < yellow[i]:
                sell_reason = '❌跌破黄线'
            # 止损: 跌破买入日低点
            elif buy_low > 0 and price < buy_low:
                sell_reason = '⚠️止损(买入日低点)'
            # S2: 无条件清仓
            elif detect_s2(klines, white, i):
                sell_reason = 'S2次高点反杀'
            # S1: 砍50%
            elif detect_s1(klines, white, i):
                sell_reason = 'S1断头铡(减半)'
                sell_shares = shares // 2
            # S5: 分批减仓(卖1/3)
            elif detect_s5(klines, white, i):
                sell_reason = 'S5温水青蛙(减仓)'
                sell_shares = shares // 3
            # 盈转亏: 盈利回撤至成本
            elif buy_price > 0 and price < buy_price and shares * buy_price > capital * 0.02:
                # 曾经盈利超过3%，现在回到成本以下
                pass  # 简化: 不实现盈转亏检测

            if sell_reason and sell_shares > 0:
                proceeds = sell_shares * price
                cash += proceeds
                pnl = (price - buy_price) * sell_shares
                trades.append({
                    'date': bar['date'], 'action': 'sell', 'reason': sell_reason,
                    'price': price, 'shares': sell_shares, 'pnl': round(pnl, 2),
                    'cash_after': round(cash, 2)
                })
                shares -= sell_shares
                if shares <= 0:
                    shares = 0
                    position = 'empty'
                    buy_price = 0
                    buy_low = 0

        # ══ 空仓状态: 检查买入信号 ══
        if position == 'empty':
            buy_reason = None

            # 禁区: 价格在黄线下方 → 不操作(标准战法)
            if price < yellow[i]:
                continue

            # B1买点
            if detect_b1(klines, j_vals, yellow, i):
                buy_reason = 'B1超卖低吸'
                last_b1_idx = i
            # B砖买点(不限位置，独立于B1)
            elif detect_b_brick(bricks, brick_colors, klines, i):
                buy_reason = 'B砖底部反转'

            if buy_reason:
                shares_to_buy = int(cash // price // 100) * 100
                if shares_to_buy >= 100:
                    cost = shares_to_buy * price
                    cash -= cost
                    shares = shares_to_buy
                    buy_price = price
                    buy_low = bar['low']
                    position = 'holding'
                    trades.append({
                        'date': bar['date'], 'action': 'buy', 'reason': buy_reason,
                        'price': price, 'shares': shares,
                        'cash_after': round(cash, 2)
                    })

    # 结算: 如果期末仍持仓，按最后收盘价计算
    final_price = klines[-1]['close']
    portfolio_value = cash + shares * final_price
    total_return = portfolio_value - capital
    return_pct = total_return / capital * 100

    wins = sum(1 for t in trades if t['action'] == 'sell' and t.get('pnl', 0) > 0)
    losses = sum(1 for t in trades if t['action'] == 'sell' and t.get('pnl', 0) <= 0)
    total_sells = wins + losses
    win_rate = wins / total_sells * 100 if total_sells > 0 else 0

    return {
        'code': code,
        'name': name,
        'capital': capital,
        'years': years,
        'final_value': round(portfolio_value, 2),
        'total_return': round(total_return, 2),
        'return_pct': round(return_pct, 2),
        'trades': trades,
        'total_trades': len(trades),
        'sell_count': total_sells,
        'wins': wins, 'losses': losses,
        'win_rate': round(win_rate, 1),
        'final_shares': shares,
        'final_price': final_price,
    }

# ── 主程序 ──────────────────────────────────────────────

def main():
    holdings = [
        ('002452', '长高电新'),
        ('600744', '华银电力'),
        ('600742', '富维股份'),
        ('000422', '湖北宜化'),
        ('002202', '金风科技'),
        ('002498', '汉缆股份'),
        ('600522', '中天科技'),
    ]

    print("=" * 72)
    print("无情操盘手策略回测 — 持仓个股（1年 + 2年）")
    print("策略: B1超卖低吸 + B砖底部反转 | 死叉/黄线清仓 + S1~S5出货信号")
    print("每只投入 ¥10,000")
    print("=" * 72)

    for years in [1, 2]:
        print(f"\n{'─' * 72}")
        print(f"  回测周期: {years}年")
        print(f"{'─' * 72}")

        total_capital = 0
        total_final = 0
        all_results = []

        for code, name in holdings:
            r = backtest_ruthless(code, name, capital=10000, years=years)
            if r is None:
                print(f"  {name}({code}): 数据不足，跳过")
                continue

            all_results.append(r)
            total_capital += r['capital']
            total_final += r['final_value']

            # 打印摘要
            sign = '+' if r['total_return'] >= 0 else ''
            holding_note = f" (持仓{r['final_shares']}股@{r['final_price']:.2f})" if r['final_shares'] > 0 else ''
            print(f"\n  {name}({code})")
            print(f"    收益: {sign}¥{r['total_return']:,.0f} ({sign}{r['return_pct']:.1f}%)")
            print(f"    交易: {r['total_trades']}笔 | 卖出{r['sell_count']}笔 | 胜率{r['win_rate']:.0f}%({r['wins']}胜{r['losses']}负){holding_note}")

            # 打印交易明细
            for t in r['trades']:
                if t['action'] == 'buy':
                    print(f"      {t['date']} 买入 {t['shares']}股@{t['price']:.2f}  [{t['reason']}]")
                else:
                    pnl_sign = '+' if t['pnl'] >= 0 else ''
                    print(f"      {t['date']} 卖出 {t['shares']}股@{t['price']:.2f}  {pnl_sign}{t['pnl']:.0f}  [{t['reason']}]")

        # 汇总
        if all_results:
            total_return = total_final - total_capital
            total_pct = total_return / total_capital * 100
            sign = '+' if total_return >= 0 else ''
            print(f"\n  {'═' * 60}")
            print(f"  {years}年汇总: ¥{total_capital:,.0f} → ¥{total_final:,.0f} ({sign}{total_pct:.1f}%)")
            print(f"  {sign}¥{total_return:,.0f}")
            print(f"  {'═' * 60}")

if __name__ == '__main__':
    os.environ['no_proxy'] = '*'
    os.environ['NO_PROXY'] = '*'
    main()
