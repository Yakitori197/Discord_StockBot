"""
Discord 股票資訊機器人
功能：查詢股票開盤/收盤價、交易量、漲跌幅等資訊
支援：純數字台股代碼、股票名稱搜尋（不限大小寫）
"""

import sys

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
import asyncio
from typing import Optional, Tuple
import os
from threading import Thread
import signal
import logging
from flask import Flask

import yolab_quote as yq
from yolab_quote import QuoteClient

import reliability

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("discord_stockbot")

# 就緒狀態：區分 liveness（行程存活）與 readiness（Discord 連線完成）
readiness = reliability.ReadinessState()

# 行情來源：yfinance 優先、Yahoo JSON 端點備援（兩者無共用程式路徑，
# 其中一個掛掉不會連帶失效）。30 秒報價快取讓 !market 這類一次查多檔的
# 指令不會重複打同一個端點。
_quotes = QuoteClient(ttl=30, max_workers=8)

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
# 中文名稱對照表已移入 yolab-quote。該套件的內建表合併了本專案原本的
# TW_STOCK_NAMES / STOCK_NAME_MAP 與 LINE bot 那一份（合併時零衝突，只是
# 涵蓋範圍不同），查詢一律走 yq.get_name() 與 yq.resolve()，不再各自維護。
# 要補自家代號請用 yolab_quote.names.register()。


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
    """獲取股票的中文名稱。

    改用 yolab-quote 的內建對照表：該表已合併本專案原有的 TW_STOCK_NAMES
    與 LINE bot 那一份，涵蓋範圍比原本更廣，也不必再各自維護一份。
    交易所後綴由套件處理（2330.TW 與 2330 都可查）。
    """
    return yq.get_name(symbol)


def search_stock_by_name(query: str) -> Optional[str]:
    """線上搜尋股票，返回最匹配的代碼。

    改用 yolab-quote 的 search_symbols()，行為與原本相同（同一個 Yahoo
    端點），但逾時與錯誤處理由套件統一負責。
    """
    try:
        results = yq.search_symbols(query, limit=1)
    except yq.QuoteError as exc:
        print(f"Search error: {exc}")
        return None
    return results[0].symbol if results else None


def resolve_stock_symbol(user_input: str) -> Tuple[str, str]:
    """
    解析使用者輸入，返回 (股票代碼, 解析說明)
    
    支援格式：
    1. 純數字（如 2330）-> 視為台股，自動加 .TW
    2. 股票名稱（如 nvidia）-> 從對照表或搜尋找代碼
    3. 完整代碼（如 AAPL, 2330.TW）-> 直接使用
    """
    user_input = user_input.strip()
    if not user_input:
        return user_input, None

    # 交給 yolab-quote：它涵蓋台股代碼正規化、中文名稱與英文別名（含拼錯
    # 變體）。原本這裡用 `^\d{4,6}$` 判斷台股，會漏掉 00631L / 00632R
    # 這類帶字尾的槓桿／反向 ETF，導致它們被當成美股查詢。
    resolved = yq.resolve(user_input)
    if resolved:
        return resolved, None

    # 對照表查不到才走線上搜尋。
    print(f"Searching online for: {user_input}")
    search_result = search_stock_by_name(user_input)
    if search_result:
        return search_result, None

    return user_input.upper(), None


def _load_history(symbol: str, days: int) -> Tuple[list, Optional[str], str]:
    """取得日 K，連同顯示用的名稱與幣別。

    回傳 (bars, name, currency)；查不到時 bars 為空 list。
    名稱與幣別需另查一次報價（走 30 秒快取，通常不會多打一次網路）；
    拿不到就退回代碼與 USD，不讓它擋住歷史資料本身。
    """
    try:
        bars = _quotes.get_bars(symbol, days)
    except yq.QuoteError:
        return [], None, 'USD'

    name = symbol
    currency = 'USD'
    try:
        quote = _quotes.get_quote(symbol)
        name = yq.get_name(quote.symbol) or quote.name or symbol
        currency = quote.currency or 'USD'
    except yq.QuoteError:
        pass
    return bars, name, currency


def get_stock_info(symbol: str) -> dict:
    """獲取股票資訊"""
    try:
        quote = _quotes.get_quote(symbol)

        # 三個月高低與均量：用套件的日 K 計算（約 63 個交易日）。
        three_month_high = None
        three_month_low = None
        avg_volume = None
        try:
            bars = _quotes.get_bars(symbol, 63)
        except yq.QuoteError:
            bars = []          # 拿不到歷史不影響即時報價，欄位留 None
        if bars:
            three_month_high = max(bar.high for bar in bars)
            three_month_low = min(bar.low for bar in bars)
            volumes = [bar.volume for bar in bars if bar.volume is not None]
            if volumes:
                avg_volume = sum(volumes) / len(volumes)

        # 優先顯示中文名稱；套件的對照表同時涵蓋台股與美股。
        name = yq.get_name(quote.symbol) or quote.name or quote.symbol

        return {
            'symbol': quote.symbol,
            'name': name,
            'currency': quote.currency or 'USD',
            'open': quote.open,
            'high': quote.high,
            'low': quote.low,
            'close': quote.price,
            'volume': quote.volume,
            'change': quote.change,
            'change_percent': quote.change_percent,
            'market_cap': quote.extra.get('market_cap'),
            'pe_ratio': quote.extra.get('pe_ratio'),
            'three_month_high': three_month_high,
            'three_month_low': three_month_low,
            'avg_volume': avg_volume,
            # 已是百分比（套件負責換算），顯示時不可再乘 100。
            'dividend_yield': quote.extra.get('dividend_yield'),
            'sector': quote.extra.get('sector', 'N/A'),
            'industry': quote.extra.get('industry', 'N/A'),
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
    
    # yolab-quote 已把殖利率換算成百分比，這裡不能再乘 100
    # （原本乘的是 yfinance 回傳的小數比例）。
    dividend_display = f"{data['dividend_yield']:.2f}%" if data['dividend_yield'] else 'N/A'
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
            # 改用套件的搜尋，並丟到 worker thread：原本是在 async 函式裡
            # 直接跑同步的 requests.get，會卡住 event loop。
            matches = await asyncio.to_thread(_quotes.search, query, 10)

            if not matches:
                await ctx.send(f"❌ 找不到與 `{query}` 相關的股票")
                return

            embed = discord.Embed(
                title=f"🔍 搜尋結果: {query}",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )

            results = []
            for i, match in enumerate(matches, 1):
                results.append(
                    f"`{i}.` **{match.symbol}** - {match.name}\n"
                    f"    📍 {match.exchange or 'N/A'} | {match.quote_type or 'N/A'}"
                )

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
            bars, name, currency = await asyncio.to_thread(_load_history, symbol, days)

            if not bars:
                # 對照表解不出來，改走線上搜尋
                search_result = await asyncio.to_thread(search_stock_by_name, query)
                if search_result:
                    symbol = search_result
                    bars, name, currency = await asyncio.to_thread(_load_history, symbol, days)

            if not bars:
                await ctx.send(f"❌ 找不到 `{query}` 的歷史資料")
                return

            embed = discord.Embed(
                title=f"📅 {symbol.upper()} - {name} 歷史價格",
                description=f"最近 {len(bars)} 個交易日",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )

            # 顯示每日資料（最多10筆）
            display_data = bars[-10:]
            
            history_text = "```\n"
            history_text += f"{'日期':<12} {'收盤價':>10} {'漲跌%':>8} {'成交量':>12}\n"
            history_text += "-" * 44 + "\n"
            
            prev_close = None
            for bar in display_data:
                if prev_close:
                    change_pct = ((bar.close - prev_close) / prev_close) * 100
                    change_str = f"{'+' if change_pct >= 0 else ''}{change_pct:.2f}%"
                else:
                    change_str = "N/A"

                history_text += (
                    f"{bar.date:<12} {bar.close:>10.2f} {change_str:>8} "
                    f"{format_number(bar.volume or 0, 0):>12}\n"
                )
                prev_close = bar.close
            
            history_text += "```"
            
            embed.add_field(name="📊 歷史數據", value=history_text, inline=False)
            
            # 統計資訊
            volumes = [bar.volume for bar in bars if bar.volume is not None]
            embed.add_field(
                name="📈 期間最高",
                value=f"{max(bar.high for bar in bars):.2f} {currency}",
                inline=True
            )
            embed.add_field(
                name="📉 期間最低",
                value=f"{min(bar.low for bar in bars):.2f} {currency}",
                inline=True
            )
            embed.add_field(
                name="📦 平均成交量",
                value=format_number(sum(volumes) / len(volumes), 0) if volumes else "N/A",
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
