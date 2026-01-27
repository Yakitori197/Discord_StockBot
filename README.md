# 📈 Discord 股票資訊機器人

一個功能完整的 Discord 股票查詢機器人，**智慧識別**股票代碼與名稱，支援查詢全球股票的開盤價、收盤價、交易量、漲跌幅等即時資訊。

## ✨ 功能特色

- 🎯 **智慧識別**: 自動識別台股代碼、英文名稱、中文名稱
- 🔍 **股票查詢**: 查詢任何股票的完整資訊
- 📊 **股票比較**: 同時比較多檔股票的表現
- 📅 **歷史價格**: 查詢過去 30 天內的歷史價格走勢
- 🌍 **市場指數**: 一鍵查詢全球主要市場指數
- 🎨 **美觀介面**: 使用 Discord Embed 呈現精美的資訊卡片

## 🎯 智慧識別功能

使用者可以用多種方式查詢股票，機器人會自動識別：

| 輸入方式 | 範例 | 自動轉換 |
|----------|------|----------|
| 純數字（台股） | `2330` | → `2330.TW` (台積電) |
| 英文名稱 | `nvidia` | → `NVDA` |
| 中文名稱 | `台積電` | → `2330.TW` |
| 完整代碼 | `AAPL` | → `AAPL` |

### 支援的名稱對照（部分）

**美股：**
- `nvidia` / `apple` / `microsoft` / `google` / `amazon` / `meta` / `tesla`
- `amd` / `intel` / `netflix` / `uber` / `airbnb` / `disney` ...

**台股（中文）：**
- `台積電` / `鴻海` / `聯發科` / `台達電` / `大立光`
- `富邦金` / `國泰金` / `中信金` / `長榮` / `陽明` ...

**台股（英文）：**
- `tsmc` / `foxconn` / `mediatek` / `asus` / `acer` ...

**港股：**
- `騰訊` / `tencent` / `阿里巴巴` / `alibaba` / `小米` ...

## 🚀 快速開始

### 1. 建立 Discord 機器人

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)
2. 點擊 **New Application** 建立新應用程式
3. 在左側選單選擇 **Bot**
4. 點擊 **Reset Token** 取得機器人 Token

### 2. 設定機器人權限

在 **OAuth2 > URL Generator** 中勾選：
- **Scopes**: `bot`, `applications.commands`
- **Bot Permissions**: `Send Messages`, `Embed Links`, `Read Message History`, `Use Slash Commands`

### 3. 安裝與執行

```bash
# 安裝依賴
pip install -r requirements.txt

# 設定環境變數
cp .env.example .env
# 編輯 .env 填入 Discord Bot Token

# 啟動機器人
python bot.py
```

## 📖 指令說明

### 股票查詢

```
!stock 2330          # 查詢台積電（純數字自動加 .TW）
!stock nvidia        # 查詢 NVIDIA（自動轉換為 NVDA）
!stock 台積電        # 查詢台積電（中文名稱）
!s apple            # 簡寫指令
```

### 快速查價

```
!price 2330         # 快速查詢台積電股價
!p nvidia           # 簡寫指令
```

### 股票比較

```
!compare 2330 nvidia apple    # 比較多檔股票
!c 台積電 鴻海 聯發科          # 支援中文
```

### 搜尋股票

```
!search semiconductor         # 搜尋半導體相關股票
!find tech                    # 搜尋科技股
```

### 歷史價格

```
!history 2330 14              # 查詢 14 天歷史
!h nvidia 7                   # 查詢 7 天歷史
```

### 市場指數

```
!market                       # 查詢全球主要市場指數
!m                            # 簡寫
```

## 📊 顯示資訊範例

```
📈 2330.TW - Taiwan Semiconductor Manufacturing Company Limited
🇹🇼 自動識別為台股代碼

💰 當前價格: 1,085.00 TWD
📊 漲跌幅: +15.00 (+1.40%)

🔓 開盤價: 1,070.00    ⬆️ 最高價: 1,090.00    ⬇️ 最低價: 1,065.00
📦 成交量: 28.5M       📈 平均成交量: 32.1M
📅 52週最高: 1,125.00  📅 52週最低: 543.00
🏢 市值: 28.13T        📊 本益比: 32.45
🏭 產業: Technology / Semiconductors
```

## 🐳 Docker 部署

```bash
docker build -t discord-stock-bot .
docker run -d --env-file .env discord-stock-bot
```

或使用 docker-compose:

```bash
docker-compose up -d
```

## ⚙️ 自訂名稱對照表

編輯 `bot.py` 中的 `STOCK_NAME_MAP` 字典來新增更多名稱對照：

```python
STOCK_NAME_MAP = {
    # 新增自訂對照
    '你的名稱': '股票代碼',
    'custom_name': 'SYMBOL',
    ...
}
```

## ⚠️ 注意事項

1. **資料延遲**: 股票資料來自 Yahoo Finance，可能有 15-20 分鐘的延遲
2. **API 限制**: 請避免過於頻繁的查詢
3. **投資風險**: 本機器人僅供資訊參考，不構成投資建議
4. **Token 安全**: 請勿將 Discord Bot Token 公開

## 📄 授權條款

MIT License

---

**⚠️ 免責聲明**: 本機器人提供的所有資訊僅供參考，不構成投資建議。投資有風險，請謹慎評估。
