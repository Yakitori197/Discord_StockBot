"""
Discord 股票資訊機器人
功能：查詢股票開盤/收盤價、交易量、漲跌幅等資訊
支援：純數字台股代碼、股票名稱搜尋（不限大小寫）
"""

import sys

import discord
from discord.ext import commands
from discord import app_commands
import yfinance as yf
from datetime import datetime, timedelta
import asyncio
from typing import Optional, Tuple
import os
import re
import requests
from threading import Thread
import signal
import logging
from flask import Flask

import reliability

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("discord_stockbot")

# 就緒狀態：區分 liveness（行程存活）與 readiness（Discord 連線完成）
readiness = reliability.ReadinessState()

# ===== Flask 保活 / 健康檢查 =====
app = Flask(__name__)

@app.route('/')
def home():
    return "Discord Stock Bot is running!"

@app.route('/health')
def health():
    """readiness：只有 bot 真正連上 Discord 才回 200，否則 503。
    此端點供監控 Discord 連線狀態；Render 行程健康檢查使用 /live。"""
    if readiness.is_ready():
        return "READY", 200
    return "NOT_READY", 503

@app.route('/live')
def live():
    """liveness：行程存活即回 200（Flask 能回應代表行程還活著）。"""
    return "OK", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# 資料層在 setup_hook 中非同步初始化
import database

# 機器人設定
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # 歡迎系統需要

bot = commands.Bot(command_prefix='!', intents=intents)


# ===== Cog 載入 =====
INITIAL_COGS = [
    'cogs.leveling',
    'cogs.welcome',
]


async def load_cogs():
    """載入所有 Cog 模組。"""
    for cog in INITIAL_COGS:
        try:
            await bot.load_extension(cog)
        except Exception as exc:
            logger.error("Cog 載入失敗（%s）", type(exc).__name__)
            raise


@bot.event
async def setup_hook():
    """初始化持久化資料層，再載入依賴它的 cogs。"""
    try:
        await database.initialize()
        logger.info("資料層初始化完成（backend=%s）", database.backend_name())
    except Exception as exc:
        logger.error("資料層初始化失敗（%s）", type(exc).__name__)
        raise
    await load_cogs()

# ===== 台股代碼對應中文名稱 =====
TW_STOCK_NAMES = {
    '2330': '台積電',
    '2317': '鴻海',
    '2454': '聯發科',
    '2308': '台達電',
    '2412': '中華電',
    '2881': '富邦金',
    '2882': '國泰金',
    '2891': '中信金',
    '2884': '玉山金',
    '1301': '台塑',
    '1303': '南亞',
    '1326': '台化',
    '1216': '統一',
    '3008': '大立光',
    '2382': '廣達',
    '2357': '華碩',
    '2353': '宏碁',
    '3231': '緯創',
    '4938': '和碩',
    '3711': '日月光投控',
    '2303': '聯電',
    '2379': '瑞昱',
    '6415': '矽力-KY',
    '2603': '長榮',
    '2609': '陽明',
    '2615': '萬海',
    '2618': '長榮航',
    '2610': '華航',
    '2002': '中鋼',
    '1101': '台泥',
    '1102': '亞泥',
    '2886': '兆豐金',
    '2887': '台新金',
    '2880': '華南金',
    '2883': '開發金',
    '2885': '元大金',
    '2892': '第一金',
    '2912': '統一超',
    '2207': '和泰車',
    '2301': '光寶科',
    '2345': '智邦',
    '2395': '研華',
    '2408': '南亞科',
    '2474': '可成',
    '2492': '華新科',
    '3034': '聯詠',
    '3037': '欣興',
    '3045': '台灣大',
    '3481': '群創',
    '3661': '世芯-KY',
    '3665': '貿聯-KY',
    '4904': '遠傳',
    '5871': '中租-KY',
    '5876': '上海商銀',
    '5880': '合庫金',
    '6505': '台塑化',
    '6669': '緯穎',
    '2327': '國巨',
    '2347': '聯強',
    '2352': '佳世達',
    '2356': '英業達',
    '2360': '致茂',
    '2376': '技嘉',
    '2377': '微星',
    '2383': '台光電',
    '2385': '群光',
    '2388': '威盛',
    '2401': '凌陽',
    '2409': '友達',
    '2449': '京元電子',
    '2451': '創見',
    '2498': '宏達電',
    '3017': '奇鋐',
    '3023': '信邦',
    '3044': '健鼎',
    '3189': '景碩',
    '3443': '創意',
    '3515': '華擎',
    '3533': '嘉澤',
    '3596': '智易',
    '3617': '碩天',
    '3653': '健策',
    '3702': '大聯大',
    '4919': '新唐',
    '4966': '譜瑞-KY',
    '5269': '祥碩',
    '6239': '力成',
    '6271': '同欣電',
    '6285': '啟碁',
    '6409': '旭隼',
    '6446': '藥華藥',
    '6488': '環球晶',
    '6515': '穎崴',
    '6531': '愛普',
    '6547': '高端疫苗',
    '6552': '易華電',
    '6592': '和潤企業',
    '6756': '威鍇',
    '6770': '力積電',
    '8046': '南電',
    '8454': '富邦媒',
}


# ===== 常用股票名稱對照表 =====
# 可以根據需求擴充
STOCK_NAME_MAP = {
    # 美股科技巨頭（含常見拼寫變體）
    'nvidia': 'NVDA',
    'nvida': 'NVDA',      # 常見拼錯
    'nVidia': 'NVDA',
    'geforce': 'NVDA',    # 產品名
    'apple': 'AAPL',
    'iphone': 'AAPL',     # 產品名
    'microsoft': 'MSFT',
    'msft': 'MSFT',
    'windows': 'MSFT',    # 產品名
    'google': 'GOOGL',
    'alphabet': 'GOOGL',
    'youtube': 'GOOGL',   # 子公司
    'amazon': 'AMZN',
    'amzn': 'AMZN',
    'aws': 'AMZN',        # 服務名
    'meta': 'META',
    'facebook': 'META',
    'fb': 'META',
    'instagram': 'META',  # 子公司
    'whatsapp': 'META',   # 子公司
    'tesla': 'TSLA',
    'tsla': 'TSLA',
    'netflix': 'NFLX',
    'nflx': 'NFLX',
    'amd': 'AMD',
    'ryzen': 'AMD',       # 產品名
    'radeon': 'AMD',      # 產品名
    'intel': 'INTC',
    'intc': 'INTC',
    'qualcomm': 'QCOM',
    'qcom': 'QCOM',
    'snapdragon': 'QCOM', # 產品名
    'broadcom': 'AVGO',
    'avgo': 'AVGO',
    'adobe': 'ADBE',
    'adbe': 'ADBE',
    'photoshop': 'ADBE',  # 產品名
    'salesforce': 'CRM',
    'crm': 'CRM',
    'oracle': 'ORCL',
    'orcl': 'ORCL',
    'ibm': 'IBM',
    'cisco': 'CSCO',
    'csco': 'CSCO',
    'paypal': 'PYPL',
    'pypl': 'PYPL',
    'uber': 'UBER',
    'airbnb': 'ABNB',
    'abnb': 'ABNB',
    'spotify': 'SPOT',
    'spot': 'SPOT',
    'zoom': 'ZM',
    'shopify': 'SHOP',
    'shop': 'SHOP',
    'snowflake': 'SNOW',
    'snow': 'SNOW',
    'palantir': 'PLTR',
    'pltr': 'PLTR',
    'coinbase': 'COIN',
    'robinhood': 'HOOD',
    'hood': 'HOOD',
    
    # 美股其他知名公司
    'berkshire': 'BRK-B',
    'jpmorgan': 'JPM',
    'visa': 'V',
    'mastercard': 'MA',
    'walmart': 'WMT',
    'costco': 'COST',
    'nike': 'NKE',
    'disney': 'DIS',
    'cocacola': 'KO',
    'coca-cola': 'KO',
    'pepsi': 'PEP',
    'mcdonalds': 'MCD',
    "mcdonald's": 'MCD',
    'starbucks': 'SBUX',
    'boeing': 'BA',
    'lockheed': 'LMT',
    'exxon': 'XOM',
    'chevron': 'CVX',
    'pfizer': 'PFE',
    'johnson': 'JNJ',
    'procter': 'PG',
    'pg': 'PG',
    
    # ETF
    'qqq': 'QQQ',
    'spy': 'SPY',
    'voo': 'VOO',
    'vti': 'VTI',
    'arkk': 'ARKK',
    'soxx': 'SOXX',
    'smh': 'SMH',
    
    # 台股熱門（中文名稱）
    '台積電': '2330.TW',
    '鴻海': '2317.TW',
    '聯發科': '2454.TW',
    '台達電': '2308.TW',
    '中華電': '2412.TW',
    '富邦金': '2881.TW',
    '國泰金': '2882.TW',
    '中信金': '2891.TW',
    '玉山金': '2884.TW',
    '台塑': '1301.TW',
    '南亞': '1303.TW',
    '台化': '1326.TW',
    '統一': '1216.TW',
    '大立光': '3008.TW',
    '廣達': '2382.TW',
    '華碩': '2357.TW',
    '宏碁': '2353.TW',
    '緯創': '3231.TW',
    '和碩': '4938.TW',
    '日月光': '3711.TW',
    '聯電': '2303.TW',
    '瑞昱': '2379.TW',
    '矽力': '6415.TW',
    '長榮': '2603.TW',
    '陽明': '2609.TW',
    '萬海': '2615.TW',
    '長榮航': '2618.TW',
    '華航': '2610.TW',
    
    # 台股英文簡稱
    'tsmc': '2330.TW',
    'foxconn': '2317.TW',
    'mediatek': '2454.TW',
    'delta': '2308.TW',
    'asus': '2357.TW',
    'acer': '2353.TW',
    'umc': '2303.TW',
    
    # 港股
    '騰訊': '0700.HK',
    'tencent': '0700.HK',
    '阿里巴巴': '9988.HK',
    'alibaba': '9988.HK',
    '美團': '3690.HK',
    'meituan': '3690.HK',
    '小米': '1810.HK',
    'xiaomi': '1810.HK',
    '京東': '9618.HK',
    'jd': '9618.HK',
    '百度': '9888.HK',
    'baidu': '9888.HK',
    '網易': '9999.HK',
    'netease': '9999.HK',
    'bilibili': '9626.HK',
    '比亞迪': '1211.HK',
    'byd': '1211.HK',
    
    # 指數
    'sp500': '^GSPC',
    's&p500': '^GSPC',
    's&p': '^GSPC',
    'dow': '^DJI',
    'dowjones': '^DJI',
    'nasdaq': '^IXIC',
    '那斯達克': '^IXIC',
    '道瓊': '^DJI',
    '台股': '^TWII',
    '加權': '^TWII',
    '加權指數': '^TWII',
    '恆生': '^HSI',
    'hangseng': '^HSI',
    '日經': '^N225',
    'nikkei': '^N225',
}

# 自動將 TW_STOCK_NAMES 中的中文名稱加入 STOCK_NAME_MAP
for code, name in TW_STOCK_NAMES.items():
    STOCK_NAME_MAP[name] = f"{code}.TW"


def format_number(num: float, decimal: int = 2) -> str:
    """格式化數字，加入千分位"""
    if num is None:
        return "N/A"
    if abs(num) >= 1e9:
        return f"{num/1e9:.2f}B"
    elif abs(num) >= 1e6:
        return f"{num/1e6:.2f}M"
    elif abs(num) >= 1e3:
        return f"{num/1e3:.2f}K"
    return f"{num:,.{decimal}f}"


def get_change_emoji(change: float) -> str:
    """根據漲跌返回對應的表情符號"""
    if change > 0:
        return "📈"
    elif change < 0:
        return "📉"
    return "➖"


def get_tw_stock_chinese_name(symbol: str) -> Optional[str]:
    """獲取台股的中文名稱"""
    # 從 symbol 提取數字代碼 (例如 2330.TW -> 2330)
    code = symbol.replace('.TW', '').replace('.TWO', '')
    return TW_STOCK_NAMES.get(code)


def search_stock_by_name(query: str) -> Optional[str]:
    """
    使用 Yahoo Finance 搜尋股票
    返回最匹配的股票代碼
    """
    try:
        # 使用 yfinance 的搜尋功能
        url = f"https://query2.finance.yahoo.com/v1/finance/search"
        params = {
            'q': query,
            'quotesCount': 5,
            'newsCount': 0,
            'listsCount': 0,
            'enableFuzzyQuery': True,
            'quotesQueryId': 'tss_match_phrase_query'
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        data = response.json()
        
        if 'quotes' in data and len(data['quotes']) > 0:
            # 返回第一個結果的代碼
            return data['quotes'][0]['symbol']
        
        return None
    except Exception as e:
        print(f"Search error: {e}")
        return None


def resolve_stock_symbol(user_input: str) -> Tuple[str, str]:
    """
    解析使用者輸入，返回 (股票代碼, 解析說明)
    
    支援格式：
    1. 純數字（如 2330）-> 視為台股，自動加 .TW
    2. 股票名稱（如 nvidia）-> 從對照表或搜尋找代碼
    3. 完整代碼（如 AAPL, 2330.TW）-> 直接使用
    """
    user_input = user_input.strip()
    original_input = user_input
    
    # 1. 檢查是否為純數字（台股代碼）
    if re.match(r'^\d{4,6}$', user_input):
        symbol = f"{user_input}.TW"
        return symbol, None  # 不顯示識別訊息
    
    # 2. 檢查是否已經是完整的代碼格式（含後綴）
    if re.match(r'^[\w\-\.]+\.(TW|HK|T|L|PA|DE|SS|SZ)$', user_input.upper()):
        return user_input.upper(), None
    
    # 3. 檢查是否在名稱對照表中（不分大小寫）
    lookup_key = user_input.lower()
    if lookup_key in STOCK_NAME_MAP:
        symbol = STOCK_NAME_MAP[lookup_key]
        return symbol, None
    
    # 4. 如果看起來像美股代碼（1-5個英文字母），直接使用
    if re.match(r'^[A-Za-z]{1,5}$', user_input):
        return user_input.upper(), None  # 不顯示識別訊息
    
    # 5. 嘗試線上搜尋
    print(f"Searching online for: {user_input}")
    search_result = search_stock_by_name(user_input)
    if search_result:
        return search_result, None
    
    # 6. 都找不到，返回原始輸入嘗試
    return user_input.upper(), None


def get_stock_info(symbol: str) -> dict:
    """獲取股票資訊"""
    try:
        stock = yf.Ticker(symbol)
        info = stock.info
        hist = stock.history(period="5d")
        
        if hist.empty:
            return None
        
        # 取得最新交易日資料
        latest = hist.iloc[-1]
        
        # 計算漲跌
        if len(hist) >= 2:
            prev_close = hist.iloc[-2]['Close']
            change = latest['Close'] - prev_close
            change_percent = (change / prev_close) * 100
        else:
            change = 0
            change_percent = 0
        
        # 獲取三個月歷史資料來計算三個月最高/最低
        hist_3m = stock.history(period="3mo")
        if not hist_3m.empty:
            three_month_high = hist_3m['High'].max()
            three_month_low = hist_3m['Low'].min()
        else:
            three_month_high = None
            three_month_low = None
        
        # 獲取貨幣資訊
        currency = info.get('currency', 'USD')
        
        # 獲取名稱 - 如果是台股，優先使用中文名稱
        name = info.get('longName', info.get('shortName', symbol))
        if '.TW' in symbol.upper() or '.TWO' in symbol.upper():
            chinese_name = get_tw_stock_chinese_name(symbol.upper())
            if chinese_name:
                name = chinese_name
        
        return {
            'symbol': symbol.upper(),
            'name': name,
            'currency': currency,
            'open': latest['Open'],
            'high': latest['High'],
            'low': latest['Low'],
            'close': latest['Close'],
            'volume': latest['Volume'],
            'change': change,
            'change_percent': change_percent,
            'market_cap': info.get('marketCap'),
            'pe_ratio': info.get('trailingPE'),
            'three_month_high': three_month_high,
            'three_month_low': three_month_low,
            'avg_volume': info.get('averageVolume'),
            'dividend_yield': info.get('dividendYield'),
            'sector': info.get('sector', 'N/A'),
            'industry': info.get('industry', 'N/A'),
        }
    except Exception as e:
        print(f"Error fetching stock info: {e}")
        return None


def create_stock_embed(data: dict, resolve_msg: str = None) -> discord.Embed:
    """創建股票資訊嵌入訊息"""
    change_emoji = get_change_emoji(data['change'])
    currency = data['currency']
    
    # 根據漲跌設定顏色
    if data['change'] > 0:
        color = discord.Color.green()
    elif data['change'] < 0:
        color = discord.Color.red()
    else:
        color = discord.Color.grey()
    
    embed = discord.Embed(
        title=f"{change_emoji} {data['symbol']} - {data['name']}",
        color=color,
        timestamp=datetime.now()
    )
    
    # 不顯示解析說明
    
    # 價格資訊 - 所有價格後面加上貨幣
    embed.add_field(
        name="💰 當前價格",
        value=f"**{format_number(data['close'])} {currency}**",
        inline=True
    )
    
    # 漲跌幅也加上貨幣
    embed.add_field(
        name="📊 漲跌幅",
        value=f"{'+' if data['change'] >= 0 else ''}{format_number(data['change'])} {currency} ({'+' if data['change_percent'] >= 0 else ''}{data['change_percent']:.2f}%)",
        inline=True
    )
    
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # 空白欄位
    
    # OHLC 資訊 - 加上貨幣
    embed.add_field(
        name="🔓 開盤價",
        value=f"{format_number(data['open'])} {currency}",
        inline=True
    )
    
    embed.add_field(
        name="⬆️ 最高價",
        value=f"{format_number(data['high'])} {currency}",
        inline=True
    )
    
    embed.add_field(
        name="⬇️ 最低價",
        value=f"{format_number(data['low'])} {currency}",
        inline=True
    )
    
    # 交易量資訊
    embed.add_field(
        name="📦 成交量",
        value=f"{format_number(data['volume'], 0)}",
        inline=True
    )
    
    embed.add_field(
        name="📈 平均成交量",
        value=f"{format_number(data['avg_volume'], 0) if data['avg_volume'] else 'N/A'}",
        inline=True
    )
    
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    # 三個月高低資訊（取代52週）
    embed.add_field(
        name="📅 三個月最高",
        value=f"{format_number(data['three_month_high']) if data['three_month_high'] else 'N/A'}",
        inline=True
    )
    
    embed.add_field(
        name="📅 三個月最低",
        value=f"{format_number(data['three_month_low']) if data['three_month_low'] else 'N/A'}",
        inline=True
    )
    
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    # 本益比與股息殖利率（不顯示市值）
    embed.add_field(
        name="📊 本益比 (P/E)",
        value=f"{format_number(data['pe_ratio']) if data['pe_ratio'] else 'N/A'}",
        inline=True
    )
    
    dividend_display = f"{data['dividend_yield']*100:.2f}%" if data['dividend_yield'] else 'N/A'
    embed.add_field(
        name="💵 股息殖利率",
        value=dividend_display,
        inline=True
    )
    
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    # 不顯示產業資訊
    # 不顯示資料來源，只顯示查詢日期
    embed.set_footer(text=f"查詢日期：{datetime.now().strftime('%Y-%m-%d')}")
    
    return embed


# ===== 指令定義 =====

@bot.event
async def on_ready():
    """機器人啟動 / 重連完成時執行"""
    logger.info('Discord 連線已就緒')
    readiness.set_ready()  # readiness：Discord 連線完成

    # 只有在明確要求時才同步全域斜線指令。
    # 一般重啟不同步，避免對 Discord 全域指令 API 反覆呼叫而觸發 429。
    if reliability.should_sync_commands(os.getenv('SYNC_COMMANDS_ON_START')):
        try:
            synced = await bot.tree.sync()
            logger.info(f'已同步 {len(synced)} 個全域斜線指令（SYNC_COMMANDS_ON_START 已啟用）')
        except discord.HTTPException as e:
            logger.warning(f'同步指令失敗（{reliability.classify_startup_error(e)}）')
    else:
        logger.info('略過全域指令同步（一般重啟）。需同步請設 SYNC_COMMANDS_ON_START=true 或用管理指令 !sync')

    try:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="股市行情 | !stock"
            )
        )
    except discord.HTTPException as e:
        logger.warning(f'設定狀態失敗（{type(e).__name__}）')


@bot.event
async def on_disconnect():
    """與 Discord 斷線：標記 not-ready（/health 會回 503）。"""
    readiness.set_not_ready()


@bot.event
async def on_resumed():
    """重連成功：標記 ready。"""
    readiness.set_ready()


@bot.command(name='sync')
@commands.is_owner()
async def sync_command(ctx):
    """（限機器人擁有者）手動同步全域斜線指令；一般重啟不會自動同步。"""
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ 已同步 {len(synced)} 個斜線指令")
    except discord.HTTPException as e:
        kind = reliability.classify_startup_error(e)
        if kind == "rate_limited":
            await ctx.send("⚠️ 被 Discord 限速（429），請稍後再試")
        else:
            await ctx.send("❌ 同步失敗")
        logger.warning(f'手動同步失敗（{kind}）')


@bot.command(name='stock', aliases=['s', '股票', 'q', '查'])
async def stock_command(ctx, *, query: str):
    """
    查詢股票資訊
    用法: !stock 2330 或 !stock nvidia 或 !stock 台積電
    """
    async with ctx.typing():
        # 解析使用者輸入
        symbol, resolve_msg = resolve_stock_symbol(query)
        
        data = await asyncio.to_thread(get_stock_info, symbol)
        
        if data is None:
            # 如果第一次找不到，嘗試線上搜尋
            search_result = await asyncio.to_thread(search_stock_by_name, query)
            if search_result and search_result != symbol:
                symbol = search_result
                resolve_msg = None
                data = await asyncio.to_thread(get_stock_info, symbol)
        
        if data is None:
            await ctx.send(
                f"❌ 找不到 `{query}` 對應的股票\n\n"
                f"💡 **提示：**\n"
                f"• 台股可直接輸入數字代碼，如 `2330`\n"
                f"• 美股可輸入代碼或名稱，如 `AAPL` 或 `apple`\n"
                f"• 支援中文名稱，如 `台積電`、`鴻海`"
            )
            return
        
        embed = create_stock_embed(data, resolve_msg)
        await ctx.send(embed=embed)


@bot.tree.command(name="stock", description="查詢股票即時資訊（支援代碼、名稱、中文）")
@app_commands.describe(query="股票代碼或名稱 (例如: 2330, nvidia, 台積電)")
async def stock_slash(interaction: discord.Interaction, query: str):
    """斜線命令：查詢股票"""
    await interaction.response.defer()
    
    # 解析使用者輸入
    symbol, resolve_msg = resolve_stock_symbol(query)
    
    data = await asyncio.to_thread(get_stock_info, symbol)
    
    if data is None:
        # 如果第一次找不到，嘗試線上搜尋
        search_result = await asyncio.to_thread(search_stock_by_name, query)
        if search_result and search_result != symbol:
            symbol = search_result
            resolve_msg = None
            data = await asyncio.to_thread(get_stock_info, symbol)
    
    if data is None:
        await interaction.followup.send(
            f"❌ 找不到 `{query}` 對應的股票\n\n"
            f"💡 **提示：**\n"
            f"• 台股可直接輸入數字代碼，如 `2330`\n"
            f"• 美股可輸入代碼或名稱，如 `AAPL` 或 `apple`\n"
            f"• 支援中文名稱，如 `台積電`、`鴻海`"
        )
        return
    
    embed = create_stock_embed(data, resolve_msg)
    await interaction.followup.send(embed=embed)


@bot.command(name='compare', aliases=['c', '比較', 'vs'])
async def compare_command(ctx, *queries: str):
    """
    比較多檔股票
    用法: !compare 2330 nvidia apple
    """
    if len(queries) < 2:
        await ctx.send("❌ 請至少輸入兩個股票進行比較\n範例: `!compare 2330 nvidia`")
        return
    
    if len(queries) > 5:
        await ctx.send("❌ 最多只能比較 5 檔股票")
        return
    
    async with ctx.typing():
        embed = discord.Embed(
            title="📊 股票比較",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        for query in queries:
            symbol, _ = resolve_stock_symbol(query)
            data = await asyncio.to_thread(get_stock_info, symbol)
            
            if data is None:
                # 嘗試線上搜尋
                search_result = await asyncio.to_thread(search_stock_by_name, query)
                if search_result:
                    data = await asyncio.to_thread(get_stock_info, search_result)
            
            if data:
                change_emoji = get_change_emoji(data['change'])
                currency = data['currency']
                value = (
                    f"💰 價格: **{format_number(data['close'])} {currency}**\n"
                    f"{change_emoji} 漲跌: {'+' if data['change'] >= 0 else ''}{format_number(data['change'])} {currency} "
                    f"({'+' if data['change_percent'] >= 0 else ''}{data['change_percent']:.2f}%)\n"
                    f"📦 成交量: {format_number(data['volume'], 0)}\n"
                    f"📊 本益比: {format_number(data['pe_ratio']) if data['pe_ratio'] else 'N/A'}"
                )
                embed.add_field(
                    name=f"{data['symbol']} - {data['name'][:20]}",
                    value=value,
                    inline=True
                )
            else:
                embed.add_field(
                    name=f"❌ {query}",
                    value="找不到此股票",
                    inline=True
                )
        
        embed.set_footer(text=f"查詢日期：{datetime.now().strftime('%Y-%m-%d')}")
        await ctx.send(embed=embed)


@bot.tree.command(name="compare", description="比較多檔股票")
@app_commands.describe(
    stock1="第一檔股票（代碼或名稱）",
    stock2="第二檔股票（代碼或名稱）",
    stock3="第三檔股票（選填）",
    stock4="第四檔股票（選填）",
    stock5="第五檔股票（選填）"
)
async def compare_slash(
    interaction: discord.Interaction,
    stock1: str,
    stock2: str,
    stock3: Optional[str] = None,
    stock4: Optional[str] = None,
    stock5: Optional[str] = None
):
    """斜線命令：比較股票"""
    await interaction.response.defer()
    
    queries = [s for s in [stock1, stock2, stock3, stock4, stock5] if s]
    
    embed = discord.Embed(
        title="📊 股票比較",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    
    for query in queries:
        symbol, _ = resolve_stock_symbol(query)
        data = await asyncio.to_thread(get_stock_info, symbol)
        
        if data is None:
            search_result = await asyncio.to_thread(search_stock_by_name, query)
            if search_result:
                data = await asyncio.to_thread(get_stock_info, search_result)
        
        if data:
            change_emoji = get_change_emoji(data['change'])
            currency = data['currency']
            value = (
                f"💰 價格: **{format_number(data['close'])} {currency}**\n"
                f"{change_emoji} 漲跌: {'+' if data['change'] >= 0 else ''}{format_number(data['change'])} {currency} "
                f"({'+' if data['change_percent'] >= 0 else ''}{data['change_percent']:.2f}%)\n"
                f"📦 成交量: {format_number(data['volume'], 0)}\n"
                f"📊 本益比: {format_number(data['pe_ratio']) if data['pe_ratio'] else 'N/A'}"
            )
            embed.add_field(
                name=f"{data['symbol']} - {data['name'][:20]}",
                value=value,
                inline=True
            )
        else:
            embed.add_field(
                name=f"❌ {query}",
                value="找不到此股票",
                inline=True
            )
    
    embed.set_footer(text=f"查詢日期：{datetime.now().strftime('%Y-%m-%d')}")
    await interaction.followup.send(embed=embed)


@bot.command(name='price', aliases=['p', '價格'])
async def price_command(ctx, *, query: str):
    """
    快速查詢股價
    用法: !price 2330 或 !p nvidia
    """
    async with ctx.typing():
        symbol, resolve_msg = resolve_stock_symbol(query)
        data = await asyncio.to_thread(get_stock_info, symbol)
        
        if data is None:
            search_result = await asyncio.to_thread(search_stock_by_name, query)
            if search_result:
                data = await asyncio.to_thread(get_stock_info, search_result)
        
        if data is None:
            await ctx.send(f"❌ 找不到 `{query}` 對應的股票")
            return
        
        change_emoji = get_change_emoji(data['change'])
        currency = data['currency']
        msg = (
            f"{change_emoji} **{data['symbol']}** ({data['name'][:30]})\n"
            f"💰 {format_number(data['close'])} {currency} | "
            f"漲跌: {'+' if data['change'] >= 0 else ''}{format_number(data['change'])} {currency} "
            f"({'+' if data['change_percent'] >= 0 else ''}{data['change_percent']:.2f}%)"
        )
        await ctx.send(msg)


@bot.command(name='search', aliases=['find', '搜尋', '找'])
async def search_command(ctx, *, query: str):
    """
    搜尋股票
    用法: !search nvidia
    """
    async with ctx.typing():
        try:
            url = f"https://query2.finance.yahoo.com/v1/finance/search"
            params = {
                'q': query,
                'quotesCount': 10,
                'newsCount': 0,
                'listsCount': 0,
                'enableFuzzyQuery': True,
            }
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            data = response.json()
            
            if 'quotes' not in data or len(data['quotes']) == 0:
                await ctx.send(f"❌ 找不到與 `{query}` 相關的股票")
                return
            
            embed = discord.Embed(
                title=f"🔍 搜尋結果: {query}",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            results = []
            for i, quote in enumerate(data['quotes'][:10], 1):
                symbol = quote.get('symbol', 'N/A')
                name = quote.get('shortname') or quote.get('longname') or 'N/A'
                exchange = quote.get('exchange', 'N/A')
                quote_type = quote.get('quoteType', 'N/A')
                
                results.append(f"`{i}.` **{symbol}** - {name}\n    📍 {exchange} | {quote_type}")
            
            embed.description = "\n\n".join(results)
            embed.set_footer(text=f"使用 !stock <代碼> 查詢詳細資訊 | 查詢日期：{datetime.now().strftime('%Y-%m-%d')}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ 搜尋失敗: {str(e)}")


@bot.command(name='history', aliases=['h', '歷史'])
async def history_command(ctx, query: str, days: int = 7):
    """
    查詢歷史價格
    用法: !history 2330 7 或 !h nvidia 14
    """
    if days > 30:
        days = 30
        await ctx.send("⚠️ 最多只能查詢 30 天的歷史資料")
    
    async with ctx.typing():
        try:
            symbol, _ = resolve_stock_symbol(query)
            stock = yf.Ticker(symbol)
            hist = stock.history(period=f"{days}d")
            
            if hist.empty:
                # 嘗試線上搜尋
                search_result = await asyncio.to_thread(search_stock_by_name, query)
                if search_result:
                    symbol = search_result
                    stock = yf.Ticker(symbol)
                    hist = stock.history(period=f"{days}d")
            
            if hist.empty:
                await ctx.send(f"❌ 找不到 `{query}` 的歷史資料")
                return
            
            info = stock.info
            name = info.get('longName', info.get('shortName', symbol))
            currency = info.get('currency', 'USD')
            
            # 如果是台股，優先使用中文名稱
            if '.TW' in symbol.upper() or '.TWO' in symbol.upper():
                chinese_name = get_tw_stock_chinese_name(symbol.upper())
                if chinese_name:
                    name = chinese_name
            
            embed = discord.Embed(
                title=f"📅 {symbol.upper()} - {name} 歷史價格",
                description=f"最近 {len(hist)} 個交易日",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            # 顯示每日資料（最多10筆）
            display_data = hist.tail(10)
            
            history_text = "```\n"
            history_text += f"{'日期':<12} {'收盤價':>10} {'漲跌%':>8} {'成交量':>12}\n"
            history_text += "-" * 44 + "\n"
            
            prev_close = None
            for date, row in display_data.iterrows():
                date_str = date.strftime('%Y-%m-%d')
                close = row['Close']
                volume = row['Volume']
                
                if prev_close:
                    change_pct = ((close - prev_close) / prev_close) * 100
                    change_str = f"{'+' if change_pct >= 0 else ''}{change_pct:.2f}%"
                else:
                    change_str = "N/A"
                
                history_text += f"{date_str:<12} {close:>10.2f} {change_str:>8} {format_number(volume, 0):>12}\n"
                prev_close = close
            
            history_text += "```"
            
            embed.add_field(name="📊 歷史數據", value=history_text, inline=False)
            
            # 統計資訊
            embed.add_field(
                name="📈 期間最高",
                value=f"{hist['High'].max():.2f} {currency}",
                inline=True
            )
            embed.add_field(
                name="📉 期間最低",
                value=f"{hist['Low'].min():.2f} {currency}",
                inline=True
            )
            embed.add_field(
                name="📦 平均成交量",
                value=format_number(hist['Volume'].mean(), 0),
                inline=True
            )
            
            embed.set_footer(text=f"查詢日期：{datetime.now().strftime('%Y-%m-%d')}")
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ 查詢失敗: {str(e)}")


@bot.command(name='market', aliases=['m', '大盤', '指數'])
async def market_command(ctx):
    """
    查詢主要市場指數
    用法: !market
    """
    indices = [
        ('^GSPC', 'S&P 500'),
        ('^DJI', '道瓊工業'),
        ('^IXIC', '那斯達克'),
        ('^TWII', '台灣加權'),
        ('^HSI', '恆生指數'),
        ('^N225', '日經 225'),
    ]
    
    async with ctx.typing():
        embed = discord.Embed(
            title="🌍 全球主要市場指數",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        
        for symbol, name in indices:
            data = await asyncio.to_thread(get_stock_info, symbol)
            if data:
                change_emoji = get_change_emoji(data['change'])
                value = (
                    f"💰 **{format_number(data['close'])}**\n"
                    f"{change_emoji} {'+' if data['change'] >= 0 else ''}{format_number(data['change'])} "
                    f"({'+' if data['change_percent'] >= 0 else ''}{data['change_percent']:.2f}%)"
                )
                embed.add_field(name=f"📊 {name}", value=value, inline=True)
        
        embed.set_footer(text=f"查詢日期：{datetime.now().strftime('%Y-%m-%d')}")
        await ctx.send(embed=embed)


@bot.tree.command(name="market", description="查詢全球主要市場指數")
async def market_slash(interaction: discord.Interaction):
    """斜線命令：查詢市場指數"""
    await interaction.response.defer()
    
    indices = [
        ('^GSPC', 'S&P 500'),
        ('^DJI', '道瓊工業'),
        ('^IXIC', '那斯達克'),
        ('^TWII', '台灣加權'),
        ('^HSI', '恆生指數'),
        ('^N225', '日經 225'),
    ]
    
    embed = discord.Embed(
        title="🌍 全球主要市場指數",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )
    
    for symbol, name in indices:
        data = await asyncio.to_thread(get_stock_info, symbol)
        if data:
            change_emoji = get_change_emoji(data['change'])
            value = (
                f"💰 **{format_number(data['close'])}**\n"
                f"{change_emoji} {'+' if data['change'] >= 0 else ''}{format_number(data['change'])} "
                f"({'+' if data['change_percent'] >= 0 else ''}{data['change_percent']:.2f}%)"
            )
            embed.add_field(name=f"📊 {name}", value=value, inline=True)
    
    embed.set_footer(text=f"查詢日期：{datetime.now().strftime('%Y-%m-%d')}")
    await interaction.followup.send(embed=embed)


@bot.command(name='help_stock', aliases=['hs', '股票幫助', '說明'])
async def help_stock_command(ctx):
    """
    顯示股票機器人使用說明
    """
    embed = discord.Embed(
        title="📖 股票機器人使用說明",
        description="🎯 **智慧識別** - 自動識別股票代碼與名稱！",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="📌 `!stock <查詢>` 或 `!s`",
        value=(
            "查詢股票完整資訊\n"
            "範例:\n"
            "• `!stock 2330` → 台積電\n"
            "• `!stock nvidia` → NVIDIA\n"
            "• `!stock 台積電` → 台積電\n"
            "• `!stock apple` → Apple"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📌 `!price <查詢>` 或 `!p`",
        value="快速查詢股價\n範例: `!p 2330`、`!p tesla`",
        inline=False
    )
    
    embed.add_field(
        name="📌 `!compare <股票1> <股票2> ...` 或 `!c`",
        value="比較多檔股票（最多5檔）\n範例: `!c 2330 nvidia apple`",
        inline=False
    )
    
    embed.add_field(
        name="📌 `!search <關鍵字>` 或 `!find`",
        value="搜尋股票\n範例: `!search semiconductor`",
        inline=False
    )
    
    embed.add_field(
        name="📌 `!history <查詢> [天數]` 或 `!h`",
        value="查詢歷史價格（預設7天，最多30天）\n範例: `!h 2330 14`",
        inline=False
    )
    
    embed.add_field(
        name="📌 `!market` 或 `!m`",
        value="查詢全球主要市場指數",
        inline=False
    )
    
    embed.add_field(
        name="💡 智慧識別支援",
        value=(
            "• **純數字** → 自動識別為台股 (`2330` → `2330.TW`)\n"
            "• **英文名稱** → 自動搜尋 (`nvidia` → `NVDA`)\n"
            "• **中文名稱** → 從資料庫查詢 (`台積電` → `2330.TW`)\n"
            "• **完整代碼** → 直接使用 (`AAPL`、`2330.TW`)"
        ),
        inline=False
    )
    
    embed.set_footer(text=f"查詢日期：{datetime.now().strftime('%Y-%m-%d')}")
    await ctx.send(embed=embed)


# 錯誤處理
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ 缺少必要參數，請使用 `!help` 查看使用說明。")
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        logger.error("指令執行失敗（%s）", type(error).__name__)
        await ctx.send("❌ 指令執行失敗，請稍後再試。")

# ===== 啟動 =====
def _install_signal_handlers(loop, shutdown_event: asyncio.Event):
    """將 Render 的關機訊號轉成非同步乾淨關閉。"""
    def _graceful():
        if shutdown_event.is_set():
            return
        readiness.set_not_ready()
        shutdown_event.set()
        logger.info("收到關機訊號，正在乾淨關閉")
        loop.create_task(bot.close())

    for signame in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _graceful)
        except (NotImplementedError, RuntimeError):
            pass


async def _wait_for_retry(delay: float, shutdown_event: asyncio.Event) -> bool:
    """Wait without blocking; return True when shutdown interrupts the wait."""
    if delay <= 0:
        await asyncio.sleep(0)
        return shutdown_event.is_set()
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=delay)
        return True
    except TimeoutError:
        return False


async def _run_bot(token: str, backoff=None):
    """Run Discord with bounded in-process startup retries and clean shutdown."""
    retry_policy = backoff or reliability.StartupBackoff(
        base=2.0,
        factor=2.0,
        cap=300.0,
        max_retries=6,
        jitter=0.5,
        cooldown=60.0,
    )
    shutdown_event = asyncio.Event()
    _install_signal_handlers(asyncio.get_running_loop(), shutdown_event)

    try:
        async with bot:
            while not shutdown_event.is_set():
                try:
                    logger.info("正在啟動 Discord Bot")
                    await bot.start(token, reconnect=True)
                    logger.info("Bot 已乾淨關閉")
                    return
                except discord.LoginFailure:
                    logger.error("啟動驗證失敗；請檢查部署端機密設定")
                    raise SystemExit(1)
                except discord.HTTPException as exc:
                    readiness.set_not_ready()
                    kind = reliability.classify_startup_error(exc)
                    retry_after = reliability.parse_retry_after(exc)
                    delay = retry_policy.next_delay(retry_after=retry_after)
                    exhausted = delay is None
                    if exhausted:
                        delay = retry_policy.cooldown
                    logger.warning(
                        "啟動失敗（kind=%s status=%s）；%.1f 秒後%s",
                        kind,
                        getattr(exc, "status", "unknown"),
                        delay,
                        "退出" if exhausted else "重試",
                    )
                    if await _wait_for_retry(delay, shutdown_event):
                        return
                    if exhausted:
                        raise SystemExit(1)
                except (OSError, TimeoutError) as exc:
                    readiness.set_not_ready()
                    delay = retry_policy.next_delay()
                    exhausted = delay is None
                    if exhausted:
                        delay = retry_policy.cooldown
                    logger.warning(
                        "啟動網路錯誤（%s）；%.1f 秒後%s",
                        type(exc).__name__,
                        delay,
                        "退出" if exhausted else "重試",
                    )
                    if await _wait_for_retry(delay, shutdown_event):
                        return
                    if exhausted:
                        raise SystemExit(1)
                except Exception as exc:
                    logger.error("啟動失敗（%s）", type(exc).__name__)
                    raise SystemExit(1)
    finally:
        readiness.set_not_ready()
        await database.close()


if __name__ == '__main__':
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        logger.error("啟動所需的部署端機密設定不存在")
        sys.exit(1)

    keep_alive()

    try:
        asyncio.run(_run_bot(token))
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logger.info("手動中斷")
