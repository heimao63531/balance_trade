# encoding: UTF-8


import math
import os
import random
import traceback
import threading
import strategy_platform.api as zxapi

from strategy_platform.api import (register_realmd_cb, sub_realmd)
import jqdatasdk as jqsdk
import pandas as pd
import datetime
import time
import numpy as np
from DataBase.db_client import get_client

#想看列表：看 [REBALANCE][INIT]
#想看 active rebalance 汇总资金：看 [REBALANCE][SUMMARY]
#想看 active rebalance 具体是哪几只：还要看 [REBALANCE][ACTIVE]
#想看每只 active 的股数、昨收、金额：还要看 [REBALANCE][ACTIVE_DETAIL]
#想看原始 buy_list / sell_list 的资金增减：目前还要看 [REBALANCE][DEBUG]

# Mongo：策略库名；get_client(c_from=...) 选连接（与 DataBase/db_client.py 中 client_dict 键一致）
DB_STRA = 'stra_V3_1'
DB_W_BASIC_INFO = 'basic_wind'
C_FROM = 'model'


def normalize_code(code):
    """Normalize stock code to CATS format, e.g. 000001.SZ."""
    if not isinstance(code, str):
        return code
    code = code.strip().upper()
    if not code:
        return code
    if '.' in code:
        parts = code.split('.')
        if len(parts) == 2 and len(parts[0]) == 6 and parts[1] in ('SZ', 'SH'):
            return code
        return code
    if len(code) == 8 and code[:2] in ('SZ', 'SH') and code[2:].isdigit():
        return code[2:] + '.' + code[:2]
    if len(code) == 8 and code[:6].isdigit() and code[6:] in ('SZ', 'SH'):
        return code[:6] + '.' + code[6:]
    return code


# 禁买、禁卖、不参与调仓与 rebalance 目标池；手动填写，写法可与 normalize_code 一致（如 000001.SZ）
FORBIDDEN_STOCKS = (
    "688244.SH",
)

forbidden_set = frozenset(
    normalize_code(x) for x in FORBIDDEN_STOCKS if x is not None and str(x).strip()
)


jqsdk.auth('18621833878', 'Ah123456')

today = datetime.datetime.strftime(datetime.datetime.now(), "%Y-%m-%d")
today_ = datetime.datetime.strftime(datetime.datetime.now(), "%Y%m%d")
last_day = str(jqsdk.get_trade_days(count=10)[-2])  # YYYY-MM-DD，与 Mongo stocks_list.date 对齐

# 导入框架提供的函数，我们这里先引入订阅股票回调函数，订阅股票，下单
from strategy_platform.api import (sub_realmd, register_realmd_cb)
from strategy_platform.api import (sub_order, register_order_cb)
from strategy_platform.api import (submit_order)
from strategy_platform.api import (add_argument)

"""
    策略示例：一个简单的订阅行情，根据行情最新价格下单的策略示例
    1: 订阅中信证券(60003.SH）的标的信息
    2：每次收到行情之后，如果最新价格大于XX，用以"最新价格"作为买入价，每次买入200股票
"""


# 账户类型和账户名称， 在策略启动时输入
acct_type = "FEQD"
acct = "2672764"

# xt_account = (r'C:\长江证券CQT极速量化交易系统\userdata_mini',   "131620591", 'CREDIT')

start_time = "09:00:00"
end_time = "15:00:00"  # 程序结束时间
MARKET_WARMUP_SECONDS = 60
REBALANCE_TIME = "15:50:00"
REBALANCE_TIME_CHECK_INTERVAL = 5

trade_time = '09:30'
# trade_time = '14:25'
leverage_time = '09:30'
purchase_money = 0
redeem_money = 0
watch_order_interval = 5  # 监控单子的时间间隔，单位秒
new_order_interval = 1.0    # 每次下单的时间间隔,单位分钟
order_not_filled_timeout = 50  # 订单未全部成交超时时间，启动和运行是都可以配置和修改
POSITION_LOG_TIME_EOD = '15:27'  # 收盘前打印全量持仓（每日一次）

#query_account_list enabledBalance
# pos_size = 240
pos_size = 168
limit_line = 40
# rebalance：当前股数与目标股数相对偏差在此比例内不调仓（3% → 0.97~1.03）
REBALANCE_QTY_TOLERANCE = 0.015
#保证金 = 100000

# to do  rmb or dollar
# AccountData currentBalance
# adjust leverage
# 2.8
# get_pre_order_money
# target_pos_size+limit_line

# Mongo 与候选池在 on_init 里读：CATS 的 log 通常在回调里才注入，模块顶层 log.info 可能不生效或报错
candidate_target_position = []

asset_file = 'D:\\zx\\asset\\'+last_day+'.csv' # to do 用户资产文件 
chg_file = rf'D:\trade_data\data\chg\{today}.csv'


log.info(f'[IO][READ] 读取asset_file: {asset_file}')
with open(asset_file, 'r') as f:
    data = f.readlines()[-1]
    data = data.split(',')
    assert data[0] == last_day
    nav = float(data[3]) # to do  净值
    total_asset = float(data[1])
    start_leverage = 1.0
    if not math.isclose(nav, total_asset, rel_tol=1e-4, abs_tol=1.0):
        raise ValueError(
            f'asset_file校验失败: nav({nav}) 与 total_asset({total_asset}) 不近似相等, asset_file={asset_file}'
        )

nowtime = datetime.datetime.now()
if (
    datetime.datetime.strptime(
        str(nowtime.date()) + trade_time, '%Y-%m-%d%H:%M')
    < nowtime
):
    trade_time = datetime.datetime.strftime(
        nowtime + datetime.timedelta(minutes=1), "%H:%M")
if (
    datetime.datetime.strptime(str(nowtime.date()) +
                               leverage_time, '%Y-%m-%d%H:%M')
    < nowtime
):
    leverage_time = datetime.datetime.strftime(
        nowtime + datetime.timedelta(minutes=2), "%H:%M")


# cats设置参数
zxapi.add_argument('trade_time', str, 0, trade_time)  # 开始交易的时间
zxapi.add_argument('leverage_time', str, 0, leverage_time)  # 开始交易的时间
zxapi.add_argument('purchase_money', int, 2, purchase_money)  # 申购金额
zxapi.add_argument('redeem_money', int, 2, redeem_money)  # 赎回金额
#zxapi.add_argument('total_asset', float, 2, total_asset)  # 总头寸

### 
db_w_basic_info = get_client(c_from=C_FROM)[DB_W_BASIC_INFO]
paused_li_df = pd.DataFrame(db_w_basic_info['w_basic_info'].find({'date': last_day}, {'_id': 0, 'code': 1, 'trade_status': 1}))
if paused_li_df.empty:
    raise ValueError(f'MongoDB中w_basic_info无数据，date={last_day}')

# 停盘/非正常交易：排除「交易中」；trade_status 为 1 或 '交易' 视为可交易
paused_li = paused_li_df[~paused_li_df['trade_status'].isin(['交易', 1])].copy()
paused_li["code"] = paused_li["code"].map(normalize_code)
print("paused_li (停盘等): ", paused_li)

### stocks_list
db_stra = get_client(c_from=C_FROM)[DB_STRA]
log.info(f'[IO][READ] 读取Mongo stocks_list, date={last_day}')
stocks_list_df = pd.DataFrame(
    db_stra['stocks_list'].find({'date': last_day}, {'_id': 0, 'code': 1, 'F1': 1})
)
if stocks_list_df.empty:
    raise ValueError(f'MongoDB中stocks_list无数据，date={last_day}')
stocks_list_df['F1'] = pd.to_numeric(stocks_list_df['F1'], errors='coerce')
stocks_list_df = stocks_list_df.dropna(subset=['code', 'F1']).sort_values(by='F1', ascending=False)
candidate_target_position = stocks_list_df.code.map(normalize_code).tolist()[:(pos_size + limit_line)]
_n_cand = len(candidate_target_position)
candidate_target_position = [c for c in candidate_target_position if c not in forbidden_set]
if _n_cand != len(candidate_target_position):
    log.info(
        f'[FORBIDDEN] 候选池剔除 {_n_cand - len(candidate_target_position)} 只，'
        f'remaining={len(candidate_target_position)}'
    )
preview = candidate_target_position[:20]
log.info(f"candidate_target_position count={len(candidate_target_position)}, preview(20)={preview}")
print(f"[candidate_target_position] count={len(candidate_target_position)}, preview={preview}")

def filter_stock_positions(positions, require_positive_qty=True, tag=''):
    if positions is None:
        return []
    filtered = []
    zero_qty = []
    for pos in positions:
        symbol = getattr(pos, 'symbol', '')
        if not (symbol.endswith('.SH') or symbol.endswith('.SZ')):
            continue
        qty = getattr(pos, 'currentQty', 0) or 0
        if require_positive_qty and qty <= 0:
            zero_qty.append(symbol)
            continue
        filtered.append(pos)
    if zero_qty:
        log.info(
            f'[POSITION][FILTER_ZERO_QTY] tag={tag}, removed={len(zero_qty)}, symbols={zero_qty}'
        )
    return filtered


def query_stock_positions(tag='', require_positive_qty=True):
    positions = zxapi.query_position(acct_type, acct)
    if positions is None:
        log.warning(
            f'query_position 返回 None，按空仓处理 tag={tag} acct_type={acct_type} acct={acct}'
        )
        positions = []
    return filter_stock_positions(
        positions,
        require_positive_qty=require_positive_qty,
        tag=tag,
    )

pos_dt = pd.DataFrame(columns=['证券代码', '当前价格(交易货币)', '标的数量'])

hold_position_list = query_stock_positions(tag='STARTUP')

real_hold_position = [pos.symbol for pos in hold_position_list]

fb_in_hold = sorted(set(real_hold_position) & forbidden_set)
if fb_in_hold:
    log.info(f'[FORBIDDEN] 当前持仓不参与买卖/调仓: {fb_in_hold}')

pos_dt = pd.DataFrame(
    [
        {
            '证券代码': pos.symbol,
            '当前价格(交易货币)': pos.lLastPrice,
            '标的数量': pos.currentQty
        }
        for pos in hold_position_list
    ],
    columns=['证券代码', '当前价格(交易货币)', '标的数量'],
)

pos_dt['市值(交易货币)'] = pos_dt['当前价格(交易货币)'] * pos_dt['标的数量']

log.info(
    f"[POSITION][PRICE_CHECK] "
    f"lLastPrice_zero={(pos_dt['当前价格(交易货币)'] == 0).sum()}, "
    f"qty_zero={(pos_dt['标的数量'] == 0).sum()}, "
    f"rows={len(pos_dt)}, "
    f"sample={pos_dt.head(20).to_dict('records')}"
)


def log_full_holdings(tag, positions=None):
    """打印全量 A 股持仓代码（启动与 15:27 各打一次）。"""
    if positions is None:
        positions = zxapi.query_position(acct_type, acct)
    if positions is None:
        log.warning(
            f'[POSITION][{tag}] query_position 返回 None, acct_type={acct_type}, acct={acct}'
        )
        positions = []
    stock_positions = filter_stock_positions(positions, tag=tag)
    symbols = sorted(pos.symbol for pos in stock_positions)
    total_mv = sum(getattr(pos, 'marketValue', 0) or 0 for pos in stock_positions)
    log.info(
        f'[POSITION][{tag}] count={len(symbols)}, total_market_value={total_mv}, symbols={symbols}'
    )


log_full_holdings('STARTUP', hold_position_list)

data_book = pd.DataFrame(columns=['code', 'lastPrice', 'open', 'high', 'low', 'lastClose', 'amount', 'volume', 'pvolume',
                         'stockStatus', 'openInt', 'lastSettlementPrice', 'askPrice', 'bidPrice', 'askVol', 'bidVol', 'transactionNum', 'high_limit', 'low_limit', 'limitStatus'])
# data_book['code'] = real_hold_position + \
#     real_hold_position_cj + candidate_target_position
data_book['code'] = real_hold_position + candidate_target_position
data_book.drop_duplicates(keep='last', inplace=True)
data_book['high_limit'] = -1
data_book['low_limit'] = -1
data_book['limitStatus'] = 0
data_book.set_index('code', inplace=True)
data_book[['askPrice', 'bidPrice', 'askVol', 'bidVol']] = data_book[[
    'askPrice', 'bidPrice', 'askVol', 'bidVol']].astype(object)

cols = ['code', 'direction', 'amount', 'target_amount',
        'wait_amount', 'correct_price', 'type', 'child', 'start_amount', 'allow_trade']

trade_book = pd.DataFrame(
    columns=cols)

CONSTANT_DIRECTION_BUY = 1
CONSTANT_DIRECTION_SELL = -1

if not os.path.exists(rf'D:\trade_data\data\leverage\{today}'):
    os.mkdir(rf'D:\trade_data\data\leverage\{today}')

def noon_pass():
    """
    判断是否未中午休市时间，即是否是在11：30到1点之间，在此时间段，则跳过定时器中函数
    :return: True or False
    """
    now_ = datetime.datetime.now()
    hour_ = now_.hour
    minute_ = now_.minute
    # return False
    if 12 <= hour_ < 13 or (hour_ == 11 and minute_ >= 30):
        return True
    else:
        return False

CONSTANT_LIMIT_HIGH = 1  # 涨停
CONSTANT_LIMIT_LOW = 2  # 跌停
CONSTANT_LIMIT_UNKNOWN = 999  # 未知交易状态
CONSTANT_LIMIT_STOP = 4  # 未知交易状态
CONSTANT_LIMIT_LOCK = -1  # 被其他票锁死
# CONSTANT_LIMIT_LOCK_CJ = -2  # 被其他票锁死
CONSTANT_LIMIT_NORMAL = 0  # 被其他票锁死

def _get_attr(obj, name, default=None):
    return getattr(obj, name, default)


def _get_first_attr(obj, names, default=None):
    for name in names:
        value = _get_attr(obj, name, None)
        if value is not None:
            return value
    return default


def _status_to_char(status):
    if status is None or pd.isna(status):
        return None
    if isinstance(status, str):
        return status if len(status) == 1 else status.strip()
    try:
        status_int = int(status)
        if status_int in (0, 1, 2, 3):
            return status_int
        return chr(status_int)
    except (TypeError, ValueError):
        return status


def _level_values(obj, prefix):
    """
    RealMKData 兼容取值：
    - 优先取类似 bidPrice1..5 / askPrice1..5
    - 若存在 bidPrice/askPrice 且为列表，则直接使用
    """
    direct = _get_attr(obj, prefix, None)
    if isinstance(direct, (list, tuple)):
        return list(direct)
    values = []
    for i in range(1, 6):
        v = _get_attr(obj, f"{prefix}{i}", None)
        if v is not None:
            values.append(v)
    if not values and direct is not None:
        values = [direct]
    return values

md_count = 0
def data_callback(realmk_obj, cb_arg):
    """
    CATS 实时行情回调：RealMKData 单对象。
    """
    global data_book, md_count
    md_count += 1
    stock_code = _get_attr(realmk_obj, 'symbol', None)
    if not stock_code or stock_code not in data_book.index:
        return

    bid_price = _level_values(realmk_obj, 'bidPrice')
    ask_price = _level_values(realmk_obj, 'askPrice')
    bid_vol = _level_values(realmk_obj, 'bidVolume') or _level_values(realmk_obj, 'bidVol')
    ask_vol = _level_values(realmk_obj, 'askVolume') or _level_values(realmk_obj, 'askVol')
    status = _status_to_char(_get_attr(realmk_obj, 'status', None))

    data_book.loc[stock_code, 'lastPrice'] = _get_attr(realmk_obj, 'lastPrice', np.nan)
    data_book.loc[stock_code, 'open'] = _get_attr(realmk_obj, 'openPrice', np.nan)
    data_book.loc[stock_code, 'high'] = _get_attr(realmk_obj, 'highPrice', np.nan)
    data_book.loc[stock_code, 'low'] = _get_attr(realmk_obj, 'lowPrice', np.nan)
    data_book.loc[stock_code, 'lastClose'] = _get_attr(realmk_obj, 'preClosePrice', np.nan)
    data_book.loc[stock_code, 'amount'] = _get_attr(realmk_obj, 'turnover', np.nan)
    data_book.loc[stock_code, 'volume'] = _get_attr(realmk_obj, 'volume', np.nan)
    data_book.loc[stock_code, 'pvolume'] = _get_attr(realmk_obj, 'curVolume', _get_attr(realmk_obj, 'volume', np.nan))
    data_book.loc[stock_code, 'stockStatus'] = status
    data_book.loc[stock_code, 'openInt'] = _get_first_attr(realmk_obj, ['openInterest', 'openInt'], 0)
    data_book.loc[stock_code, 'lastSettlementPrice'] = _get_first_attr(
        realmk_obj, ['settlement', 'lastSettlementPrice'], np.nan)
    data_book.at[stock_code, 'askPrice'] = ask_price
    data_book.at[stock_code, 'bidPrice'] = bid_price
    data_book.at[stock_code, 'askVol'] = ask_vol
    data_book.at[stock_code, 'bidVol'] = bid_vol
    data_book.loc[stock_code, 'transactionNum'] = _get_first_attr(realmk_obj, ['ncjbs', 'transactionNum'], 0)

    up_stop = _get_first_attr(realmk_obj, ['upperLimit', 'upStopPrice'], None)
    down_stop = _get_first_attr(realmk_obj, ['lowerLimit', 'downStopPrice'], None)
    if up_stop is not None:
        data_book.loc[stock_code, 'high_limit'] = up_stop
    if down_stop is not None:
        data_book.loc[stock_code, 'low_limit'] = down_stop

    last_price = data_book.loc[stock_code, 'lastPrice']
    high_limit = data_book.loc[stock_code, 'high_limit']
    low_limit = data_book.loc[stock_code, 'low_limit']
    if status in {'A', 'B', 'C', 'D', 'G', 'H', 'P', 'Q', 'S', 'V', 'W', 'X', 'Z', 'q'}:
        data_book.loc[stock_code, 'limitStatus'] = CONSTANT_LIMIT_STOP
    elif bid_price and ask_price and bid_price[0] == ask_price[0] == 0:
        data_book.loc[stock_code, 'limitStatus'] = CONSTANT_LIMIT_STOP
    elif pd.notna(high_limit) and high_limit != -1 and abs(last_price - high_limit) < 0.001:
        data_book.loc[stock_code, 'limitStatus'] = CONSTANT_LIMIT_HIGH
    elif pd.notna(low_limit) and low_limit != -1 and abs(last_price - low_limit) < 0.001:
        data_book.loc[stock_code, 'limitStatus'] = CONSTANT_LIMIT_LOW
    elif data_book.loc[stock_code, 'limitStatus'] != -1 and data_book.loc[stock_code, 'limitStatus'] < 100:
        data_book.loc[stock_code, 'limitStatus'] = CONSTANT_LIMIT_NORMAL

    if md_count <= 300 or md_count % 1000 == 0:
        bid1 = bid_price[0] if bid_price else None
        ask1 = ask_price[0] if ask_price else None
        bid_vol1 = bid_vol[0] if bid_vol else None
        ask_vol1 = ask_vol[0] if ask_vol else None
        log.info(
            f'[MK_CB] n={md_count} stk={stock_code} last={last_price} '
            f'open={data_book.loc[stock_code, "open"]} high={data_book.loc[stock_code, "high"]} '
            f'low={data_book.loc[stock_code, "low"]} lastClose={data_book.loc[stock_code, "lastClose"]} '
            f'amount={data_book.loc[stock_code, "amount"]} volume={data_book.loc[stock_code, "volume"]} '
            f'pvolume={data_book.loc[stock_code, "pvolume"]} status={status} '
            f'openInt={data_book.loc[stock_code, "openInt"]} '
            f'lastSettlementPrice={data_book.loc[stock_code, "lastSettlementPrice"]} '
            f'bid1={bid1} bidVol1={bid_vol1} ask1={ask1} askVol1={ask_vol1} '
            f'transactionNum={data_book.loc[stock_code, "transactionNum"]} '
            f'high_limit={data_book.loc[stock_code, "high_limit"]} '
            f'low_limit={data_book.loc[stock_code, "low_limit"]} '
            f'limitStatus={data_book.loc[stock_code, "limitStatus"]}'
        )

market_symbols = list(set(real_hold_position + candidate_target_position))

# CATS 单策略实时行情订阅上限
MAX_REALMD_SUBS = 2000


def _symbols_for_realmd(max_n=MAX_REALMD_SUBS):
    """先持仓再候选，去重，最多 max_n 只。"""
    seen = set()
    out = []
    for sym in list(real_hold_position) + list(candidate_target_position):
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
        if len(out) >= max_n:
            break
    return out


realmd_sub_symbols = _symbols_for_realmd(MAX_REALMD_SUBS)


def _sub_realmd_with_log(symbols, label='RUN'):
    """调用 sub_realmd 前打清本次传入数量，便于对照 CATS 200 上限类报错。"""
    syms = [symbols] if isinstance(symbols, str) else list(symbols)
    n = len(syms)
    nu = len(set(syms))
    if n != nu:
        log.warning(f'[MK_SUB] {label}: 入参含重复 code, 条数={n} 去重后={nu}')
    log.info(
        f'[MK_SUB] {label}: 即将 sub_realmd — 传入条数={n}, 去重后 symbol 数={nu}, '
        f'data_book 行数={len(data_book.index)}; 本策略本次请求量如上（CATS 总名额 200 含其它订阅）'
    )
    log.info(f'[MK_SUB] {label} 全列表={syms}')
    sub_realmd(syms)

order_book = {}
start_reb = False
rebalance_active_list_logged = False
rebalance_time_last_state = None
shdq_index = None
trade_book_ready = False
invalid_correct_price_warned = set()

def warn_invalid_correct_price_once(stock, action):
    if stock in invalid_correct_price_warned:
        return
    invalid_correct_price_warned.add(stock)
    log.warning(f'{stock} correct_price 无效，后续SHDQ指数{action}将跳过该股票且不再重复提示')

class SHDQ_index:
    def __init__(self, stock_list) -> None:
        self.stock_list = stock_list
        self.base_value = 0.0
        self.curr_value = 0.0
        self.filepath = os.path.join(
            # r'C:\Users\a\Desktop\算法交易CATS\index',
            r'D:\trade_data\index\进取1-中信-股衍',
            datetime.datetime.strftime(
                datetime.datetime.now(), '%Y%m%d') + '-SHDQ_index.csv',
        )
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        log.info(f'[IO][WRITE][INIT] 创建SHDQ指数文件: {self.filepath}')
        with open(self.filepath, 'w+', encoding='utf-8') as f:
            f.write('time,base_value,curr_value,curr_point\n')
        sum = 0
        if len(self.stock_list):
            for stock in self.stock_list:
                correct_price = trade_book.loc[
                    trade_book['code'] == stock, 'correct_price'
                ]
                if correct_price.empty or not valid_number(correct_price.values[0]):
                    warn_invalid_correct_price_once(stock, '初始化/刷新')
                    continue
                sum += 1
        self.base_value = self.curr_value = sum
        self.curr_point = 1.0
        self.sav_local()

    def sav_local(self):
        # log.info(f'[IO][WRITE] 追加SHDQ指数: {self.filepath}, curr_point={self.curr_point:.6f}')
        with open(self.filepath, 'a+', encoding='utf-8') as f:
            f.write(
                f'{datetime.datetime.now()},{self.base_value},{self.curr_value},{self.curr_point}\n'
            )

    def refresh(self):
        sum = 0
        if len(self.stock_list):
            for stock in self.stock_list:
                correct_price = trade_book.loc[
                    trade_book['code'] == stock, 'correct_price'
                ]
                if correct_price.empty or not valid_number(correct_price.values[0]):
                    warn_invalid_correct_price_once(stock, '刷新')
                    continue
                sum += (
                    data_book.loc[stock, 'lastPrice']
                    / correct_price.values[0]
                )
            self.curr_value = sum
            if valid_number(self.base_value):
                self.curr_point = self.curr_value / self.base_value
        self.sav_local()

def config_init_argument(argument_dict):
    for k, v in argument_dict.items():
        print("开始配置参数：argument: [key: {}, value: {}]".format(k, v))
        if k == "trade_time":
            global trade_time
            trade_time = v
        # elif k == "purchase_money":
        #     global purchase_money
        #     purchase_money = v
        # elif k == "redeem_money":
        #     global redeem_money
        #     redeem_money = v
        elif k == "total_asset":
            global total_asset
            total_asset = v
        else:
            # 可以在这里进行配置项的设置
            pass

def start_rebalance():
    log.info(f'开始rebalance')
    global buy_list, sell_list, rebalance_list, start_reb, rebalance_active_list_logged

    start_reb = True
    rebalance_active_list_logged = False

def get_paused(code_li):
    """
    输入股票代码列表，返回其中停牌票代码列表
    :param code_li: 输入待选择的股票代码列表
    :return: 返回其中停牌票的代码列表
    """
    code_set = {normalize_code(code) for code in code_li}
    realtime_paused_set = set(
        data_book[
            data_book['stockStatus'].isin({'A', 'B', 'C', 'D', 'G', 'H', 'P', 'Q', 'S', 'V', 'W', 'X', 'Z', 'q'})
        ].index.map(normalize_code)
    )
    return list(code_set & realtime_paused_set)

def init_trade_book():
    log.info(f'初始化交易信息表')
    if trade_time == '09:30':
        time.sleep(30)
    try:
        global trade_book, data_book, last_asset_time, trade_book_ready
        global buy_list, sell_list, rebalance_list, target_hold_position, buy_list_raw, sell_list_raw, rebalance_list_raw
        trade_book_ready = False

        paused_position = get_paused(
            real_hold_position)
        log.info(f'paused_position:{paused_position}')

        lastnight_pos = []
        if pos_dt.empty:
            log.info('当前账户空仓，按空仓路径初始化交易信息表')
        else:
            avg = pos_dt['市值(交易货币)'].mean()
            if pd.isna(avg) or avg <= 0:
                log.warning(
                    f'持仓均值异常，跳过昨夜持仓筛选 avg={avg}, rows={len(pos_dt)}'
                )
            else:
                for _, row in pos_dt.iterrows():
                    if row['市值(交易货币)'] > 0.1 * avg:
                        lastnight_pos.append(row['证券代码'])

        # candidate_target_position是全局候选池， target_hold_position 是剔除停牌和前target_pos_size的股票池
        buy_list, sell_list, rebalance_list, target_hold_position = get_trade_stk_list(
            lastnight_pos, candidate_target_position, paused_position, real_hold_position)

        import copy
        buy_list_raw, sell_list_raw, rebalance_list_raw = copy.deepcopy(
            buy_list), copy.deepcopy(sell_list), copy.deepcopy(rebalance_list)

        log.info(
            f'[REBALANCE][INIT] target_hold={len(target_hold_position)}, buy={len(buy_list)}, '
            f'sell={len(sell_list)}, rebalance={len(rebalance_list)}, total_asset={total_asset}, pos_size={pos_size}, '
            f'buy_list={buy_list}, sell_list={sell_list}, rebalance_list={rebalance_list}'
        )

        trade_book['code'] = candidate_target_position + real_hold_position

        trade_book.drop_duplicates(subset=['code'], keep='last', inplace=True)

        trade_book['child'] = ''
        # trade_book['child_cj'] = ''
        trade_book['correct_price'] = 0
        trade_book['type'] = 'no_trade'

        trade_book['wait_amount'] = 0
        trade_book['start_amount'] = 0
        trade_book['amount'] = 0
        trade_book['direction'] = 0
        trade_book['allow_trade'] = True

        for index, row in trade_book.iterrows():
            curr_stk = row['code']
            trade_book.loc[trade_book['code'] == curr_stk,
                           'correct_price'] = data_book.loc[curr_stk, 'lastPrice']
            if curr_stk in pos_dt['证券代码'].unique().tolist():
                trade_book.loc[trade_book['code'] == curr_stk,
                               'start_amount'] = pos_dt.loc[pos_dt['证券代码'] == curr_stk, '标的数量'].values[0]

        last_asset_time = time.time()
        init_positions = query_stock_positions(tag='INIT_TRADE_BOOK')
        for pos_obj in init_positions:

            trade_book.loc[trade_book['code'] ==
                           pos_obj.symbol, 'amount'] = pos_obj.currentQty

            trade_book.loc[trade_book['code'] ==
                           pos_obj.symbol, 'direction'] = 0

        for stk in sell_list:
            trade_book.loc[trade_book['code'] == stk, 'type'] = 'turn'

            trade_book.loc[trade_book['code'] == stk,
                           'direction'] = CONSTANT_DIRECTION_SELL

            trade_book.loc[trade_book['code'] == stk, 'target_amount'] = 0

        target_value = total_asset / pos_size # to do

        for stk in buy_list:
            trade_book.loc[trade_book['code'] == stk, 'type'] = 'turn'

            trade_book.loc[trade_book['code'] == stk,
                           'direction'] = CONSTANT_DIRECTION_BUY

            price_for_target = target_price_for(stk)
            trade_book.loc[trade_book['code'] == stk,
                           'target_amount'] = int(target_value / price_for_target//100)*100

        for stk in rebalance_list:
            trade_book.loc[trade_book['code'] == stk, 'type'] = 'rebalance'

            price_for_target = target_price_for(stk)
            trade_book.loc[trade_book['code'] == stk,
                           'target_amount'] = int(target_value / price_for_target//100)*100

            amount = trade_book.loc[trade_book['code']
                                    == stk, 'amount'].values[0]
            target_amount = trade_book.loc[trade_book['code']
                                           == stk, 'target_amount'].values[0]

            if amount > 0 and target_amount/amount > 2:
                trade_book.loc[trade_book['code'] == stk, 'type'] = 'turn'

            if amount > target_amount:
                trade_book.loc[trade_book['code'] == stk,
                                'direction'] = CONSTANT_DIRECTION_SELL

            elif amount < target_amount:

                trade_book.loc[trade_book['code'] == stk,
                                'direction'] = CONSTANT_DIRECTION_BUY

            else:

                trade_book.loc[trade_book['code'] == stk, 'direction'] = 0

        debug_symbols = list(dict.fromkeys(sell_list + buy_list + rebalance_list))
        log.info(
            f'[REBALANCE][DEBUG_SUMMARY] count={len(debug_symbols)}, '
            f'sell={len(sell_list)}, buy={len(buy_list)}, rebalance={len(rebalance_list)}, '
            f'target_value={target_value}'
        )
        for debug_stk in debug_symbols:
            if debug_stk not in trade_book['code'].values:
                log.warning(f'[REBALANCE][DEBUG] stk={debug_stk}, missing_in_trade_book=True')
                continue

            tb_row = trade_book.loc[trade_book['code'] == debug_stk].iloc[0]
            db_row = data_book.loc[debug_stk] if debug_stk in data_book.index else None
            last_close = db_row['lastClose'] if db_row is not None else np.nan
            last_price = db_row['lastPrice'] if db_row is not None else np.nan
            correct_price = tb_row['correct_price']
            target_price = last_close if valid_number(last_close) else (
                last_price if valid_number(last_price) else correct_price
            )
            start_amount = tb_row['start_amount']
            amount = tb_row['amount']
            target_amount = tb_row['target_amount']
            wait_amount = tb_row['wait_amount']
            direction = tb_row['direction']
            target_value_actual = (
                target_amount * target_price if valid_number(target_amount) and valid_number(target_price) else np.nan
            )
            current_value = (
                amount * last_price if valid_number(amount) and valid_number(last_price) else np.nan
            )

            log.info(
                f'[REBALANCE][DEBUG] stk={debug_stk}, type={tb_row["type"]}, direction={direction}, '
                f'in_sell={debug_stk in sell_list}, in_buy={debug_stk in buy_list}, '
                f'in_rebalance={debug_stk in rebalance_list}, '
                f'start_amount={start_amount}, amount={amount}, wait_amount={wait_amount}, '
                f'target_amount={target_amount}, gap={target_amount - amount}, '
                f'target_price={target_price}, lastClose={last_close}, lastPrice={last_price}, '
                f'correct_price={correct_price}, current_value={current_value}, '
                f'target_value_actual={target_value_actual}'
            )

        rebalance_active, rebalance_active_details = get_rebalance_active_details(update_direction=False)
        log_rebalance_active_details(rebalance_active, rebalance_active_details, 'INIT')

        global shdq_index
        shdq_index = SHDQ_index(sell_list + buy_list)

        trade_book_ready = True
        log.info(f'初始化交易信息表完成')

        check_rebalance_time(run_new_order_on_trigger=False)
        log.info('行情预热和交易信息表初始化完成，buy/sell立即运行，rebalance等待时间触发')

        new_order()  # 这种按时间间隔的定时器启动第一次时是不运行的，所以启动定时器前，先运行一遍该函数
        new_order_timer = zxapi.minute_timer(
            new_order_interval,
            new_order,
        )
    except Exception as e:
        trade_book_ready = False
        log.exception(f'初始化交易信息表异常: {e}')
        traceback.print_exc()
        if '[TARGET_PRICE]' in str(e):
            log.error('target_amount计算依赖的lastPrice无效，策略进程退出')
            os._exit(1)


candidate_buy_order = []
# candidate_buy_order_cj = []
order_count = 9
reb_leverage_tag = 0


def is_trade_allowed(stk):
    rows = trade_book.loc[trade_book['code'] == stk]
    if rows.empty:
        return False
    row = rows.iloc[0]
    return bool(row.get('allow_trade', True)) and pd.notna(row.get('target_amount'))


def get_rebalance_active_details(update_direction=False):
    rebalance_active = []
    rebalance_active_details = []
    for stk in rebalance_list:
        if not is_trade_allowed(stk):
            continue
        target_amount = trade_book.loc[trade_book['code']
                                       == stk, 'target_amount'].values[0]
        direction = trade_book.loc[trade_book['code']
                                   == stk, 'direction'].values[0]
        amount = trade_book.loc[trade_book['code']
                                == stk, 'amount'].values[0]
        wait_amount = trade_book.loc[trade_book['code']
                                     == stk, 'wait_amount'].values[0]
        curr_amount = trade_book.loc[trade_book['code']
                                     == stk, 'amount'].values[0]+direction*trade_book.loc[trade_book['code'] == stk, 'wait_amount'].values[0]
        _reb_low = 1 - REBALANCE_QTY_TOLERANCE
        _reb_high = 1 + REBALANCE_QTY_TOLERANCE
        lower_bound = _reb_low * target_amount
        upper_bound = _reb_high * target_amount
        reason = None
        if _reb_low * target_amount > curr_amount and direction == CONSTANT_DIRECTION_BUY:
            reason = 'BUY_UNDER_TARGET'
        elif curr_amount > _reb_high * target_amount and direction == CONSTANT_DIRECTION_SELL:
            reason = 'SELL_OVER_TARGET'
        elif direction == 0 and not (_reb_low * target_amount < curr_amount < _reb_high * target_amount):
            if target_amount > curr_amount:
                reason = 'DIR0_TO_BUY'
                if update_direction:
                    trade_book.loc[trade_book['code'] == stk,
                                   'direction'] = CONSTANT_DIRECTION_BUY
            elif target_amount < curr_amount:
                reason = 'DIR0_TO_SELL'
                if update_direction:
                    trade_book.loc[trade_book['code'] == stk,
                                   'direction'] = CONSTANT_DIRECTION_SELL
        if reason is None:
            continue
        rebalance_active.append(stk)
        rebalance_active_details.append(
            (stk, reason, amount, wait_amount, curr_amount, target_amount, direction, lower_bound, upper_bound)
        )
    return rebalance_active, rebalance_active_details


def log_rebalance_active_details(rebalance_active, rebalance_active_details, stage):
    log.info(
        f'[REBALANCE][ACTIVE] tolerance={REBALANCE_QTY_TOLERANCE}, '
        f'count={len(rebalance_active)}, list={rebalance_active}, stage={stage}'
    )
    rebalance_active_detail_rows = []
    active_buy_count = 0
    active_sell_count = 0
    active_buy_qty = 0
    active_sell_qty = 0
    active_buy_money = 0.0
    active_sell_money = 0.0
    active_buy_missing_last_close = []
    active_sell_missing_last_close = []
    for (
        stk,
        reason,
        amount,
        wait_amount,
        curr_amount,
        target_amount,
        direction,
        lower_bound,
        upper_bound,
    ) in rebalance_active_details:
        db_row = data_book.loc[stk] if stk in data_book.index else None
        last_close = db_row['lastClose'] if db_row is not None else np.nan
        last_price = db_row['lastPrice'] if db_row is not None else np.nan
        gap = target_amount - curr_amount
        try:
            last_close_value = float(last_close)
        except (TypeError, ValueError):
            last_close_value = np.nan
        valid_last_close = math.isfinite(last_close_value) and last_close_value > 0
        if gap > 0:
            active_buy_count += 1
            active_buy_qty += gap
            if valid_last_close:
                active_buy_money += gap * last_close_value
            else:
                active_buy_missing_last_close.append(stk)
        elif gap < 0:
            active_sell_count += 1
            active_sell_qty += abs(gap)
            if valid_last_close:
                active_sell_money += abs(gap) * last_close_value
            else:
                active_sell_missing_last_close.append(stk)
        rebalance_active_detail_rows.append(
            (
                stk,
                reason,
                direction,
                amount,
                wait_amount,
                curr_amount,
                target_amount,
                lower_bound,
                upper_bound,
                gap,
                last_close,
                last_price,
            )
        )
    log.info(
        f'[REBALANCE][SUMMARY] active_buy_stocks={active_buy_count}, '
        f'active_sell_stocks={active_sell_count}, active_buy_qty={active_buy_qty}, '
        f'active_sell_qty={active_sell_qty}, active_buy_money={active_buy_money:.2f}, '
        f'active_sell_money={active_sell_money:.2f}, '
        f'active_net_money={active_buy_money - active_sell_money:.2f}, '
        f'active_buy_missing_lastClose={active_buy_missing_last_close}, '
        f'active_sell_missing_lastClose={active_sell_missing_last_close}, stage={stage}'
    )
    for (
        stk,
        reason,
        direction,
        amount,
        wait_amount,
        curr_amount,
        target_amount,
        lower_bound,
        upper_bound,
        gap,
        last_close,
        last_price,
    ) in rebalance_active_detail_rows:
        log.info(
            f'[REBALANCE][ACTIVE_DETAIL] stk={stk}, reason={reason}, direction={direction}, '
            f'amount={amount}, wait_amount={wait_amount}, curr_amount={curr_amount}, '
            f'target_amount={target_amount}, lower_bound={lower_bound}, upper_bound={upper_bound}, '
            f'gap={gap}, lastClose={last_close}, lastPrice={last_price}, '
            f'len rebalance_active_details={len(rebalance_active_details)}, stage={stage}'
        )


def new_order():
    global candidate_buy_order, trade_book, data_book, order_count, rebalance_active_list_logged
    if noon_pass():
        return
    # 分成买入卖出列表。先运行卖出的，再运行买入的
    sell_target_list = []
    buy_target_list = []

    for stk in buy_list:
        if not is_trade_allowed(stk):
            log.warning(f'[ORDER][SKIP] stk={stk}, allow_trade=False，跳过买入目标')
            continue
        buy_target_list.append(stk)

    for stk in sell_list:
        if not is_trade_allowed(stk):
            log.warning(f'[ORDER][SKIP] stk={stk}, allow_trade=False，跳过卖出目标')
            continue
        sell_target_list.append(stk)

    if start_reb:
        rebalance_active, rebalance_active_details = get_rebalance_active_details(update_direction=True)
        for (
            stk,
            reason,
            amount,
            wait_amount,
            curr_amount,
            target_amount,
            direction,
            lower_bound,
            upper_bound,
        ) in rebalance_active_details:
            if reason in ('BUY_UNDER_TARGET', 'DIR0_TO_BUY'):
                buy_target_list.append(stk)
            elif reason in ('SELL_OVER_TARGET', 'DIR0_TO_SELL'):
                sell_target_list.append(stk)
        if not rebalance_active_list_logged:
            log_rebalance_active_details(rebalance_active, rebalance_active_details, 'TRIGGER')
            rebalance_active_list_logged = True

    if sell_target_list or buy_target_list or candidate_buy_order:
        log.info(
            f'[ORDER][NEW] start_reb={start_reb}, '
            f'sell_targets={len(sell_target_list)} preview={sell_target_list[:10]}, '
            f'buy_targets={len(buy_target_list)} preview={buy_target_list[:10]}, '
            f'pending_buy_before={len(candidate_buy_order)} preview={candidate_buy_order[:10]}'
        )

    time.sleep(random.randint(0, 10))

    for stk in sell_target_list:
        new_sell_order(stk)

    for stk in buy_target_list:
        candidate_buy_order.append(stk)

    new_buy_order()
    order_count += 1


def new_buy_order():
    """
    对于买单：
        如果涨停，不买，并选择下一个股票进行买入
        如果跌停，正常买入
        如果出现成交量受限的情况，按照快启动思路买入
        如果没有涨停和跌停，那正常操作。
    """
    # now = datetime.datetime.now().strftime("%H:%M")
    # if now < "09:45":
    #     return

    global candidate_buy_order, buy_list, rebalance_list, sell_list_raw
    if not candidate_buy_order:
        return
    candidate_buy_order = [stk for stk in candidate_buy_order if is_trade_allowed(stk)]
    if not candidate_buy_order:
        return
    log.info(f'[BUY] candidates={candidate_buy_order}')
    shdq_index.refresh()
    pre_order_money = get_pre_order_money('gy')
    new_candidate_buy_order = []
    for stk in candidate_buy_order:
        if normalize_code(stk) in forbidden_set:
            log.info(f'[FORBIDDEN] 跳过买入 stk={stk}')
            continue
        # log.info(stk)
        min_limit = 200 if stk.startswith('688') else 100

        target_amount = trade_book.loc[trade_book['code']
                                       == stk, 'target_amount'].values[0]
        curr_amount = trade_book.loc[trade_book['code'] == stk, 'amount'].values[0] + \
            trade_book.loc[trade_book['code']
                           == stk, 'wait_amount'].values[0]

        try:
            price = data_book.loc[stk, 'bidPrice'][0]

            rand_factor = random.uniform(0.9, 1.1)

            try:
                qty = max(pre_order_money*rand_factor /
                          price // 100 * 100, min_limit)
            except:
                price = data_book.loc[stk, 'lastPrice']
                qty = max(pre_order_money*rand_factor /
                          price // 100 * 100, min_limit)

            if price / trade_book.loc[trade_book['code'] == stk, 'correct_price'].values[0] < shdq_index.curr_point:
                qty *= 2
                log.info(
                    f'{stk}买单优势，订单翻倍！target={target_amount}, curr={curr_amount}, '
                    f'gap={target_amount-curr_amount}, qty={qty}, min_limit={min_limit}'
                )

            if qty > target_amount - curr_amount:
                qty = (target_amount - curr_amount)//min_limit * min_limit
                if qty <= 0:
                    log.info(
                        f'跳过买入，stk:{stk}, target={target_amount}, curr={curr_amount}, '
                        f'gap={target_amount-curr_amount}, min_limit={min_limit}'
                    )
                    if stk in buy_list:
                        buy_list.remove(stk)
                        rebalance_list.append(stk)
                    continue

            is_limit = data_book.loc[stk, 'limitStatus']
            if 100 <= is_limit <= qty:
                log.info(
                    f'[BUY] volume_limited_order stk={stk}, price={price}, qty={is_limit}, '
                    f'target={target_amount}, curr={curr_amount}, gap={target_amount-curr_amount}'
                )
                zxapi.submit_order(
                    acct_type,
                    acct,
                    stk,
                    "1",
                    "0",
                    price,
                    is_limit,
                    None,
                    on_order,
                    [stk, "1", price, is_limit],
                )
                data_book.loc[stk, 'limitStatus'] *= 2
            elif is_limit in (CONSTANT_LIMIT_LOCK, CONSTANT_LIMIT_UNKNOWN,):
                log.info(f'[BUY] skip status_unknown stk={stk}, limitStatus={is_limit}')
            elif is_limit in (CONSTANT_LIMIT_HIGH,):

                child = trade_book.loc[trade_book['code']
                                       == stk, 'child'].values[0]
                if child == '':
                    for stk_ in candidate_target_position:
                        if stk_ not in target_hold_position+sell_list_raw:
                            if trade_book.loc[trade_book['child'] == stk_].shape[0] == 0:
                                trade_book.loc[trade_book['code']
                                               == stk, 'child'] = stk_
                                child = stk_
                                # 'code', 'direction', 'amount', 'target_amount', 'wait_amount', 'type', 'child'

                                trade_book.loc[trade_book['code'] ==
                                               child, 'direction'] = CONSTANT_DIRECTION_BUY
                                trade_book.loc[trade_book['code'] ==
                                               child, 'type'] = trade_book.loc[trade_book['code'] == stk, 'type'].values[0]
                                trade_book.loc[trade_book['code'] ==
                                               child, 'target_amount'] = 0

                                break

                child_price = data_book.loc[child, 'bidPrice'][0]
                child_limit = 200 if child.startswith('688') else 100
                child_qty = max(qty *
                                data_book.loc[stk, 'bidPrice'][0] /
                                child_price//100 * 100, child_limit)

                trade_book.loc[trade_book['code'] ==
                               child, 'target_amount'] += child_qty

                trade_book.loc[trade_book['code']
                               == stk, 'target_amount'] -= qty

                if child not in buy_list:
                    buy_list.append(child)

                # new_candidate_buy_order.append(child)
            else:
                enabled_account_balance_shdq = zxapi.query_account(
                    acct_type, acct
                ).currentBalance

                if enabled_account_balance_shdq < price*qty:
                    log.info(
                        f'跳过买入，资金不足，stk:{stk}, price:{price}, qty:{qty}, '
                        f'need={price*qty}, enableBailBalance={enabled_account_balance_shdq}, '
                        f'target={target_amount}, curr={curr_amount}, gap={target_amount-curr_amount}'
                    )
                    new_candidate_buy_order.append(stk)
                else:
                    log.info(
                        f'[BUY] submit stk:{stk}, price:{price}, qty:{qty}, cash={enabled_account_balance_shdq}, '
                        f'target={target_amount}, curr={curr_amount}, gap={target_amount-curr_amount}, '
                        f'limitStatus={is_limit}'
                    )
                    zxapi.submit_order(
                        acct_type,
                        acct,
                        stk,
                        "1",
                        "0",
                        price,
                        int(qty),
                        None,
                        on_order,
                        [stk, "1", price, qty],
                    )

        except Exception as e:
            traceback.print_exc()
    candidate_buy_order = new_candidate_buy_order



def new_sell_order(stk):
    """
    对于卖单：
        如果涨停，不卖，并同时取消一个买单
        如果跌停，卖单挂上，并同时取消一个买单
        如果出现成交量受限的情况，按照快启动思路买入
        如果没有涨停和跌停，那正常操作。
    """
    if normalize_code(stk) in forbidden_set:
        # log.info(f'[FORBIDDEN] 跳过卖出 stk={stk}')
        return
    if not is_trade_allowed(stk):
        log.warning(f'[SELL][SKIP] stk={stk}, allow_trade=False，跳过卖出')
        return
    global buy_list
    pre_order_money = get_pre_order_money('gy')

    try:
        shdq_index.refresh()

        global buy_list, sell_list, rebalance_list, child_list
        min_limit = 100
        if stk.startswith('688'):
            min_limit = 200
        target_amount = trade_book.loc[trade_book['code']
                                       == stk, 'target_amount'].values[0]
        curr_amount = trade_book.loc[trade_book['code'] == stk, 'amount'].values[0] - \
            trade_book.loc[trade_book['code']
                           == stk, 'wait_amount'].values[0]

        price = data_book.loc[stk, 'askPrice'][0]
        # price = data_book.loc[stk, 'bidPrice'][0]
        rand_factor = random.random()*0.6+0.7

        try:
            qty = max(pre_order_money*rand_factor /
                      price // 100 * 100, min_limit)
        except:
            price = data_book.loc[stk, 'lastPrice']
            qty = max(pre_order_money*rand_factor /
                      price // 100 * 100, min_limit)

        if price / trade_book.loc[trade_book['code'] == stk, 'correct_price'].values[0] > shdq_index.curr_point:
            qty *= 2
            log.info(f'{stk}卖单优势，订单翻倍！')

        if qty > curr_amount - target_amount:
            qty = (curr_amount-target_amount)//min_limit * min_limit
            if qty <= 0:
                if target_amount <= 0.1 and curr_amount > 0.1:
                    qty = curr_amount - target_amount
                elif target_amount <= 0.1 and curr_amount <= 0.1:
                    sell_list.remove(stk)
                if qty <= 0:
                    return

        is_limit = data_book.loc[stk, 'limitStatus']
        if 100 <= is_limit <= qty:
            log.info(f'{stk} 成交量太小了,按照{is_limit}来卖')
            zxapi.submit_order(
                acct_type,
                acct,
                stk,
                "2",
                "0",
                price,
                is_limit,
                None,
                on_order,
                [stk, "2", price, is_limit],
            )
            log.info(f'下单，stk:{stk}, price:{price}, qty:{is_limit}')
            data_book.loc[stk, 'limitStatus'] *= 2
        elif is_limit in (CONSTANT_LIMIT_LOW, CONSTANT_LIMIT_HIGH,):
            log.info(f'{stk} 不能交易{data_book.loc[stk]}')
            if is_limit == CONSTANT_LIMIT_LOW:
                zxapi.submit_order(
                    acct_type,
                    acct,
                    stk,
                    "2",
                    "0",
                    price,
                    int(qty),
                    None,
                    on_order,
                    [stk, "2", price, qty],
                )
            if stk in sell_list_raw:
                child = trade_book.loc[trade_book['code']
                                       == stk, 'child'].values[0]
                if child == '':
                    for stk_ in candidate_target_position[::-1]:
                        if stk_ in buy_list_raw:
                            if trade_book.loc[trade_book['child'] == stk_].shape[0] == 0:
                                trade_book.loc[trade_book['code']
                                               == stk, 'child'] = stk_
                                child = stk_
                                break

                data_book.loc[child, 'limitStatus'] = CONSTANT_LIMIT_LOCK
        else:
            child = trade_book.loc[trade_book['code']
                                   == stk, 'child'].values[0]

            if child != '':

                data_book.loc[child, 'limitStatus'] = CONSTANT_LIMIT_NORMAL

            zxapi.submit_order(
                acct_type,
                acct,
                stk,
                "2",
                "0",
                price,
                int(qty),
                None,
                on_order,
                [stk, "2", price, qty],
            )
            log.info(f'下单，stk:{stk}, price:{price}, qty:{qty}')
    except Exception as e:
        traceback.print_exc()

ORDER_STATUS_DONE = 0
ORDER_STATUS_READY = -1
ORDER_STATUS_RUN = 1

def on_order(result, cb_arg):
    """
    下单回调函数
    :param result: 下单结果
    :param cb_arg: 下单参数
    :return:
    """
    stk, direction, price, qty = cb_arg
    if result.rc == '0':
        # log.info(f'下单回调函数，下单结果为{result.resp}')
        trade_book.loc[trade_book['code'] == stk, 'wait_amount'] += qty
    else:
        log.info(f'下单失败，失败原因为{result.resp}，下单参数为{cb_arg}')

def on_order_update(order_obj, cb_arg):
    """
    订单状态变化的回调函数，在此处修改变量可用现金
    对于卖单，如果有成交则加上成交部分市值
    对于卖单，如果有撤单则加上撤单部分市值
    :param order_obj:
    :param cb_arg:
    :return:
    """
    # log.info('订单状态已变更')
    order_id = order_obj.orderNo

    if order_id not in order_book:
        保证金 = nav
        名义本金 = get_total_asset()
        保证金占用率 = 保证金/名义本金
        order_book[order_id] = {
            "order_object": order_obj, "status": ORDER_STATUS_RUN, '保证金': 保证金, '名义本金': 名义本金, '保证金占用率': 保证金占用率, 'written': False}
    else:
        order_book[order_id]['order_object'] = order_obj

def is_notfullfilled_order_timeout(order_obj, time_out):
    """
    输入订单对象和认为超时时间，用来判断该订单是否超时
    :param one_order: 订单对象
    :param time_out: 超时时间
    :return: True or False 是否超时
    """
    full_date_str = order_obj.orderDate + ' ' + order_obj.orderTime
    time_array = time.strptime(full_date_str, '%Y%m%d %H%M%S')
    time_stamp = time.mktime(time_array)
    if time.time() - time_stamp > time_out:
        return True
    return False

order_file = rf'D:\trade_data\order\{today_}-order.csv' # 存的是成交订单

if not os.path.exists(order_file):
    log.info(f'[IO][WRITE][INIT] 创建order_file: {order_file}')
    with open(order_file, 'w', encoding='utf-8') as f:
        f.write(
            'date,time,order_no,stock_code,order_side,order_price,order_volume,rebalance_or_turn,保证金,名义本金,保证金占用率\n')

成交量风控 = "HTS_ERR_EXCEED_MATCHAMOUNT_RATIO"

def on_cancle(result, cb_arg):
    """
    下单回调函数
    :param result: 下单结果
    :param cb_arg: 下单参数
    :return:
    """
    if result.rc == '0':
        pass
    else:
        log.info(f'撤单失败，失败原因为{result.resp}')


def watch_order():
    global order_book
    try:
        if noon_pass():
            return
        sorted_order_book = sorted(
            order_book, key=lambda k: order_book[k]['order_object'].orderTime)
        running_order_count = sum(
            1 for order_no in sorted_order_book
            if order_book[order_no]['status'] != ORDER_STATUS_DONE
        )
        if running_order_count > 0:
            log.info(f'[ORDER][WATCH] running={running_order_count}, total={len(order_book)}')
        # for order_no in list(order_book.keys()):
        for order_no in sorted_order_book:
            status = order_book[order_no]['status']
            if status != ORDER_STATUS_DONE:

                order_obj = order_book[order_no]['order_object']
                order_no = order_obj.orderNo
                stock_code = order_obj.symbol
                order_side = order_obj.side
                order_price = order_obj.avgPrice
                order_volume = order_obj.filledQty
                order_status = order_obj.status
                order_order_price = order_obj.price
                curr_date = datetime.datetime.strftime(
                    datetime.datetime.now(), '%Y-%m-%d')
                curr_time = datetime.datetime.strftime(
                    datetime.datetime.now(), '%H:%M:%S:%f')

                if order_status in (0, 1):
                    if not is_notfullfilled_order_timeout(order_obj, order_not_filled_timeout):
                        continue
                    elif data_book.loc[stock_code, 'limitStatus'] in (CONSTANT_LIMIT_LOW, CONSTANT_LIMIT_HIGH):
                        continue
                    elif data_book.loc[stock_code, 'bidPrice'][1] <= order_order_price <= data_book.loc[stock_code, 'askPrice'][1]:
                        continue
                    elif curr_time < '14:57:00':
                        time.sleep(0.1)
                        zxapi.cancel_order(
                            acct_type, acct, order_no, None, None)
                elif order_status in (2, 3, 4,):
                    if not order_book[order_no]['written']:
                        保证金 = order_book[order_no]['保证金']
                        名义本金 = order_book[order_no]['名义本金']
                        保证金占用率 = order_book[order_no]['保证金占用率']

                        if order_volume > 0:
                            reb_turn_flag = 1 if stock_code in buy_list_raw+sell_list_raw else 0
                            str = f'{curr_date},{curr_time},{order_no},{stock_code},{"sell" if order_side=="2" else "buy"},{order_price},{order_volume},{reb_turn_flag},{保证金},{名义本金},{保证金占用率}\n'

                            order_book[order_no]['written'] = True
                            log.info(f'[IO][WRITE] 追加订单记录: {order_file}, order_no={order_no}, stock={stock_code}, qty={order_volume}')
                            with open(order_file, 'a', encoding='utf-8') as f:
                                f.write(str)

                    if order_status == 2:
                        # log.info('订单状态为2，即订单全成，删除')

                        order_book[order_no]['status'] = ORDER_STATUS_DONE
                        trade_book.loc[trade_book['code'] ==
                                       order_obj.symbol, 'wait_amount'] -= order_obj.qty

                        if order_obj.side == '1':
                            trade_book.loc[trade_book['code'] ==
                                           order_obj.symbol, 'amount'] += order_obj.qty
                        else:
                            trade_book.loc[trade_book['code'] ==
                                           order_obj.symbol, 'amount'] -= order_obj.qty

                    elif order_status in (3, 4):
                        # log.info('订单状态为3，4，即订单部分撤单或全撤，删除后补偿下单！！')

                        order_book[order_no]['status'] = ORDER_STATUS_DONE
                        compensate_order(order_obj)
                elif order_status in (5,):
                    order_book[order_no]['status'] = ORDER_STATUS_DONE
                    trade_book.loc[trade_book['code'] ==
                                   order_obj.symbol, 'wait_amount'] -= order_obj.qty
                    if 成交量风控 in order_obj.text and data_book.loc[order_obj.symbol, 'limitStatus'] not in (CONSTANT_LIMIT_LOW, CONSTANT_LIMIT_HIGH):
                        data_book.loc[order_obj.symbol, 'limitStatus'] = 200 if order_obj.symbol.startswith(
                            '688') else 100
    except Exception as e:
        traceback.print_exc()




def compensate_order(order_obj):
    stk = order_obj.symbol
    if normalize_code(stk) in forbidden_set:
        log.info(f'[FORBIDDEN] 跳过补偿下单 stk={stk}')
        return
    need_filled_qty = order_obj.qty - order_obj.filledQty

    if order_obj.side == '1':
        last_price = data_book.loc[stk, 'bidPrice'][0]
    else:
        last_price = data_book.loc[stk, 'askPrice'][0]

    min_limit = 200 if stk.startswith('688') else 100

    qty = max(need_filled_qty//100 * 100, min_limit)

    enabled_account_balance_shdq = zxapi.query_account(
        acct_type, acct).currentBalance

    if order_obj.side == '1':
        if enabled_account_balance_shdq < last_price*qty: # 保证金不足 to do
            trade_book.loc[trade_book['code'] ==
                           stk, 'wait_amount'] -= order_obj.qty
            trade_book.loc[trade_book['code'] ==
                           stk, 'amount'] += order_obj.filledQty
            return

    trade_book.loc[trade_book['code'] == stk, 'wait_amount'] -= order_obj.qty

    if order_obj.side == '1':
        trade_book.loc[trade_book['code'] ==
                       stk, 'amount'] += order_obj.filledQty
    else:
        trade_book.loc[trade_book['code'] ==
                       stk, 'amount'] -= order_obj.filledQty

    log.info(f'下单，stk:{stk}, price:{last_price}, qty:{qty}, currentBalance: {enabled_account_balance_shdq}')
    zxapi.submit_order(
        acct_type,
        acct,
        stk,
        order_obj.side,
        "0",
        last_price,
        int(qty),
        None,
        on_order,
        [stk, order_obj.side, last_price, qty],
    )


def get_trade_stk_list(
    hold_position, Candidate_target_position, Holded_limited_paused_position, real_hold_position, type='gy'
):

    sell_list = []
    buy_list = []
    rebalance_list = []
    target_hold_position = []
    target_pos_size = pos_size - len(Holded_limited_paused_position)

    real_hold_position_with_num = [
        (
            stk,
            Candidate_target_position.index(stk)
            if stk in Candidate_target_position
            else 999,
        )
        for stk in hold_position
        if stk not in Holded_limited_paused_position and stk not in forbidden_set
    ]
    real_hold_position_with_num.sort(key=lambda a: a[1])

    for i in range(len(real_hold_position_with_num)):
        if real_hold_position_with_num[i][1] < (target_pos_size+limit_line) and len(target_hold_position) < target_pos_size:
            target_hold_position.append(real_hold_position_with_num[i][0])

    index = 0
    while len(target_hold_position) < (target_pos_size):
        stk = Candidate_target_position[index]
        if (
            stk not in target_hold_position
            and stk not in Holded_limited_paused_position
            and stk not in forbidden_set
        ):
            target_hold_position.append(stk)
        index += 1

    sell_list = list(
        set(real_hold_position)
        - set(target_hold_position)
        - set(Holded_limited_paused_position)
        - forbidden_set
    )
    buy_list = list(set(target_hold_position) - set(hold_position))
    rebalance_list = list(set(target_hold_position) - set(buy_list))
    log.info(
        f'[REBALANCE][LIST] target={len(target_hold_position)}, buy={len(buy_list)}, '
        f'sell={len(sell_list)}, rebalance={len(rebalance_list)}'
    )


    return buy_list, sell_list, rebalance_list, target_hold_position


def get_chg():
    # 通过订单计算涨跌幅
    try:
        log.info(f'[IO][READ] 读取order_file用于计算chg: {order_file}')
        order_df = pd.read_csv(order_file, header=0, encoding='utf-8')

        order_df = order_df[order_df['order_volume'] != 0]

        delta = 0

        trade_book['clean_amount'] = trade_book['amount']

        for index, row in order_df.iterrows():
            order_price = row['order_price']
            order_amount = row['order_volume']
            cur_stk = row['stock_code']

            cur_price = data_book.loc[cur_stk, 'lastPrice']
            cur_close = data_book.loc[cur_stk, 'lastClose']

            side = row['order_side']

            if side == 'sell':
                delta += (order_price-cur_close)*order_amount - \
                    0.00062*order_amount * order_price

            elif side == 'buy':
                delta += (cur_price-order_price)*order_amount - \
                    0.00012*order_amount * order_price
                trade_book.loc[trade_book['code'] ==
                               cur_stk, 'clean_amount'] -= order_amount

        for index, row in trade_book.iterrows():
            cur_stk = row['code']

            cur_price = data_book.loc[cur_stk, 'lastPrice']
            cur_close = data_book.loc[cur_stk, 'lastClose']

            clean_amount = trade_book.loc[trade_book['code'] ==
                                          cur_stk, 'clean_amount'].values[0]

            if not valid_number(abs(clean_amount)):
                continue
            if not valid_number(cur_price) or not valid_number(cur_close):
                continue

            delta += clean_amount*(cur_price-cur_close)

        cur_cash = zxapi.query_account(acct_type, acct).currentBalance
        log.info(f'chg:{delta}，现金：{cur_cash}')
        return delta
    except Exception as e:
        traceback.print_exc()


def rebalance_leverage(chg, cj_current):
    return

check_chg_timer = None

def start_rebalance_leverage():
    global check_chg_timer
    if check_chg_timer is None:
        check_chg_timer = zxapi.minute_timer(1, check_chg)
    check_chg()


start_dic = {'14:30': -1, '13:30': 0.05,
             '13:45': 0.04, '14:00': 0.03, '14:15': 0.02, '09:00': 100}


def check_chg():
    global check_chg_timer, curr_extrem_volality, extrem_volality_check_timer, start_dic
    if noon_pass():
        return
    if not trade_book_ready:
        log.info('trade_book尚未初始化完成，跳过本次check_chg，等待下一次定时检查')
        return
    now = datetime.datetime.now().strftime("%H:%M")
    log.info('=================check_chg============')

    key = [i for i in list(start_dic.keys()) if now >= i]
    key.sort()

    chg = get_chg()
    chg_r = (chg)/(total_asset)

    cj_current = 0

    log.info(
        f'{zxapi.query_account(acct_type, acct).currentBalance},{cj_current}')
    if chg_r >= start_dic[key[-1]]:
        log.info(f'现在时间：{now}, chg:{chg_r},启动2倍调仓')
        # rebalance_leverage(chg, cj_current)
        if now < '14:30':
            start_dic = {'14:30': -1, '09:00': 100}
        else:
            zxapi.cancel_timer(check_chg_timer)
    elif chg_r < -0.05:
        log.info(f'现在时间：{now}, chg:{chg_r},启动2倍调仓')
        # rebalance_leverage(chg, cj_current)
        zxapi.cancel_timer(check_chg_timer)
        curr_extrem_volality = chg_r
        extrem_volality_check_timer = zxapi.minute_timer(
            1, extrem_volality_check)
    else:
        log.info(f'现在时间：{now}, chg:{chg_r},不启动2倍调仓')



def extrem_volality_check():
    global curr_extrem_volality

    now = datetime.datetime.now().strftime("%H:%M")

    chg = get_chg()
    chg_r = (chg)/(total_asset)

    cj_current = 0

    if abs(chg_r-curr_extrem_volality) >= 0.01:
        log.info(
            f'现在时间：{now}, chg:{chg_r},上次波动{curr_extrem_volality},波动超过1%，再次reb')
        # rebalance_leverage(chg, cj_current)
        curr_extrem_volality = chg_r
    elif now >= '14:30':
        log.info(
            f'现在时间：{now}, chg:{chg_r},2点半必须再次reb')
        # rebalance_leverage(chg, cj_current)
        curr_extrem_volality = chg_r

    else:
        log.info(f'现在时间：{now}, chg:{chg_r},波动未超过1%')


def get_pre_order_money(type):

    now = datetime.datetime.now().strftime("%H:%M")

    # to do 用户预下单金额
    pre_order_money_dic = {
        'gy': {'09:30': 3500}} # 'gy': {'09:30': 10000}, 'cj': {'09:30': 5000}}
    key = [i for i in list(pre_order_money_dic[type].keys()) if now >= i]
    key.sort()

    pre_order_money = pre_order_money_dic[type][key[-1]]

    return pre_order_money


def run_or_schedule_at(target_time, callback):
    now = datetime.datetime.now().strftime("%H:%M")
    if now >= target_time:
        callback()
    else:
        zxapi.at_day_timer(target_time, callback)


def parse_rebalance_time(value):
    value = str(value).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise ValueError(f'不支持的调仓时间格式: {value}，请写 HH:MM 或 HH:MM:SS')


def _log_rebalance_time_state(state, message, level='info'):
    global rebalance_time_last_state
    if rebalance_time_last_state == state:
        return
    getattr(log, level)(message)
    rebalance_time_last_state = state


def check_rebalance_time(run_new_order_on_trigger=True):
    if start_reb:
        return
    try:
        trigger_time = parse_rebalance_time(REBALANCE_TIME)
    except Exception as e:
        _log_rebalance_time_state(
            f'invalid:{e}',
            f'[REBALANCE][TIME_TRIGGER] REBALANCE_TIME配置无效: {REBALANCE_TIME}, error={e}',
            level='warning'
        )
        return

    now = datetime.datetime.now()
    trigger_dt = datetime.datetime.combine(now.date(), trigger_time)
    if now < trigger_dt:
        _log_rebalance_time_state(
            f'waiting:{trigger_time.strftime("%H:%M:%S")}',
            f'[REBALANCE][TIME_TRIGGER] rebalance等待时间触发: REBALANCE_TIME={REBALANCE_TIME}, '
            f'trigger_time={trigger_time.strftime("%H:%M:%S")}'
        )
        return

    if not trade_book_ready:
        _log_rebalance_time_state(
            f'ready_wait:{trigger_time.strftime("%H:%M:%S")}',
            f'[REBALANCE][TIME_TRIGGER] 调仓时间已到但交易表未初始化完成，等待初始化: '
            f'trigger_time={trigger_time.strftime("%H:%M:%S")}'
        )
        return

    log.info(
        f'[REBALANCE][TIME_TRIGGER] 调仓时间已触发: REBALANCE_TIME={REBALANCE_TIME}, '
        f'trigger_time={trigger_time.strftime("%H:%M:%S")}, now={now.strftime("%H:%M:%S")}'
    )
    start_rebalance()
    if run_new_order_on_trigger:
        new_order()


def schedule_init_trade_book_after_warmup():
    now = datetime.datetime.now()
    trade_dt = datetime.datetime.combine(
        now.date(),
        datetime.datetime.strptime(trade_time, "%H:%M").time(),
    )
    if now >= trade_dt:
        init_dt = now + datetime.timedelta(seconds=MARKET_WARMUP_SECONDS)
    else:
        init_dt = trade_dt + datetime.timedelta(seconds=MARKET_WARMUP_SECONDS)

    delay = max(0, (init_dt - now).total_seconds())
    log.info(
        f'[INIT][WARMUP] trade_time={trade_time}, warmup_seconds={MARKET_WARMUP_SECONDS}, '
        f'init_at={init_dt.strftime("%H:%M:%S")}, delay={delay:.1f}s'
    )
    timer = threading.Timer(delay, init_trade_book)
    timer.daemon = True
    timer.start()


def log_full_holdings_eod():
    log_full_holdings('1527')


def valid_number(value):
    try:
        return pd.notna(value) and float(value) > 0
    except (TypeError, ValueError):
        return False


def target_price_for(stk):
    last_price = data_book.loc[stk, 'lastPrice'] if stk in data_book.index else np.nan
    if valid_number(last_price):
        return float(last_price)

    msg = f'[TARGET_PRICE] {stk} lastPrice无效，无法计算target_amount，lastPrice={last_price}'
    log.error(msg)
    raise ValueError(msg)


def write_asset():
    """
    定时器中的函数，每隔1min读取一次账户总资产信息，并写入相关文件
    """
    global last_asset_time
    try:
        # 如果是中午休市期间，跳过
        if noon_pass():
            return
        # 获取当前日期和时间，将日期作为保存文件名，将时间作为数据asset_df的一列
        now = datetime.datetime.now()
        now_date = now.strftime('%Y-%m-%d')
        now_time = now.strftime('%H:%M:%S')
        now_time_ = now.strftime('%H%M%S')

        # 获取总资产，并准备数据，为DataFrame格式
        名义本金 = get_total_correct_asset()
        asset_df = pd.DataFrame([now_time, 名义本金]).T
        asset_df.columns = ['time', 'total_asset']
        path = rf'D:\trade_data\index\进取1-中信-股衍\{now_date}.csv'
        flag = os.path.exists(path)
        if not flag:
            pass
            # asset_df.to_csv(
            #     path,
            #     mode='w',
            #     index=False,
            #     header=True,
            #     encoding='utf-8-sig',
            # )
        else:
            pass
            # asset_df.to_csv(
            #     path,
            #     mode='a',
            #     index=False,
            #     header=False,
            #     encoding='utf-8-sig',
            # )

        last_asset_time = time.time()

        pos_list = query_stock_positions(tag='WRITE_ASSET')
        pos_df = pd.DataFrame()
        pos_path = rf'D:\trade_data\data\{today}-gy.csv'
        if os.path.exists(pos_path):
            pos_df = pd.read_csv(
                pos_path, index_col=0)
        for pos in pos_list:
            pos_df.loc[now_time, pos.symbol] = pos.marketValue

        # pos_df.to_csv(pos_path)


        trade_path = rf'D:\trade_data\data\{today}-gy-trade.csv'
        data_path = rf'D:\trade_data\data\{today}-gy-data.csv'
        # trade_book.to_csv(trade_path, index=False)
        # data_book.to_csv(data_path)
        # order_book.to_csv(
        #     rf'C:\Users\a\Desktop\xtquant\asset\realtime\{today}-gy-order.csv')

        pos_snap = rf'D:\trade_data\data\{today}\{now_time_}-gy.csv'
        trade_snap = rf'D:\trade_data\data\{today}\{now_time_}-gy-trade.csv'
        data_snap = rf'D:\trade_data\data\{today}\{now_time_}-gy-data.csv'
        # pos_df.to_csv(pos_snap)
        # trade_book.to_csv(trade_snap, index=False)
        # data_book.to_csv(data_snap)

        chg = get_chg()

        # chg_r = (chg)/(total_asset+cj_last)
        chg_r = (chg)/(total_asset)

        cj_current = 0
        with open(chg_file, 'a+') as f:
            f.write(
                f'{now_time},{chg_r},{(名义本金+cj_current)/(chg+nav)}\n')
        log.info(
            f'[IO][WRITE] asset_snapshot time={now_time}, total_asset={名义本金}, '
            f'pos_count={len(pos_list)}, chg_r={chg_r:.8f}, '
            f'asset_file={path}, pos_file={pos_path}, trade_file={trade_path}, data_file={data_path}'
        )

    except Exception as e:
        log.exception(f'定时执行函数write_asset异常: {e}')

_curr_positions = zxapi.query_position(acct_type, acct)
if _curr_positions is None:
    log.warning(
        f'query_position 返回 None，curr_total_asset 按 0 初始化 acct_type={acct_type} acct={acct}'
    )
    _curr_positions = []
curr_total_asset = np.array(
    [pos.marketValue for pos in _curr_positions if pos.symbol.endswith('.SH') or pos.symbol.endswith('.SZ')]
).sum()
last_asset_time = time.time()


def get_total_trade_asset():

    total_trade_asset = 0

    for index, row in trade_book.iterrows():

        cur_stk = row['code']
        target_amount = trade_book.loc[trade_book['code'] ==
                                       cur_stk, 'target_amount'].values[0]
        last_price = data_book.loc[cur_stk, 'lastPrice']

        import math

        if math.isnan(target_amount):
            target_amount = 0
        if math.isnan(last_price):
            last_price = 0

        total_trade_asset += last_price*target_amount

    return total_trade_asset

def get_total_correct_asset():
    total_correct_asset = 0

    for index, row in trade_book.iterrows():

        cur_stk = row['code']
        target_amount = trade_book.loc[trade_book['code'] ==
                                       cur_stk, 'amount'].values[0]
        last_price = data_book.loc[cur_stk, 'lastPrice']

        import math

        if math.isnan(target_amount):
            target_amount = 0
        if math.isnan(last_price):
            last_price = 0

        total_correct_asset += last_price*target_amount

    return total_correct_asset

def get_total_asset():
    """
    总资产 = 持仓总市值+可用资金+冻结资金
    """
    global curr_total_asset, last_asset_time

    cur_time = time.time()
    if cur_time - last_asset_time < 10:
        return curr_total_asset
    else:
        pos_value = np.array(
            [pos.marketValue for pos in zxapi.query_position(
                acct_type, acct) if pos.symbol.endswith('.SH') or pos.symbol.endswith('.SZ')]
        ).sum()  # 查询持仓总市值

        curr_total_asset = pos_value
        last_asset_time = time.time()
        return pos_value

def on_init(argument_dict):
    '''
    策略启动入口函数，如果不实现，则该策略立马退出

    :param argument_dict: 为一个dict，是策略启动时可能需要的参数，该参数可以通过add_argument进行增加
    :return: 无返回值，该函数执行失败时，需要抛出异常
    '''
    global candidate_target_position

    log.info("yyyyyyyyyyyyyyyyyyyyyyy")
    # 配置初始化时参数字典
    # config_init_argument(argument_dict)

    register_realmd_cb(data_callback, None)
    if len(realmd_sub_symbols) < len(market_symbols):
        log.warning(
            f'[MK] 行情订阅裁剪: 全表 {len(market_symbols)} 只 -> 订阅 {len(realmd_sub_symbols)} 只（上限 {MAX_REALMD_SUBS}）'
        )
    _sub_realmd_with_log(realmd_sub_symbols, 'RUN')

    # 注册订单变更的回调函数
    zxapi.register_order_cb(on_order_update, None)
    
    # 订阅订单，当订单状态变更时，会调用on_order_update
    zxapi.sub_order(acct_type, acct)
	
    write_asset()
    zxapi.second_timer(30, write_asset)

    # trade_time时间点初始化交易信息表
    zxapi.second_timer(
        watch_order_interval,
        watch_order,
    )

    # trade_time时间点初始化交易信息表；如果启动时已过目标时间，等待行情预热后执行一次
    schedule_init_trade_book_after_warmup()
    zxapi.second_timer(REBALANCE_TIME_CHECK_INTERVAL, check_rebalance_time)

    # 每天只在1430调整一次杠杆
    run_or_schedule_at(leverage_time, start_rebalance_leverage)

    # 15:27 打印全量持仓（每日定时一次；若启动已过该时刻则立即打一次）
    run_or_schedule_at(POSITION_LOG_TIME_EOD, log_full_holdings_eod)

    # 订阅标的
    #sub_realmd(undiverse)

    return


def on_fini():
    '''
    策略退出时调用该函数， 如果不实现则默认函数只打印一条日志
    :return: 无返回值，如果失败需要抛出异常
    '''
    log.info("on_fini called ......")


def on_update(dict):
    '''
    更新策略时调用该函数；
    如果不实现该函数，则系统提供一个默认的空函数
    start_time和end_time在系统内部默认会处理，用户函数也可以处理
    :param dict: 参数为dict，value见add_argument函数说明
    :return: 需要2个返回值，第一个返回值表示成功或失败，第二个表示原因
       True, "", 成功
       False, "because xxx" 失败
    '''
    log.info("on_update called ......")
    return True, ""

