# Discord 股票資訊機器人

以 Discord 指令查詢全球股票、台股與市場指數，並提供等級、歡迎訊息和角色獎勵功能。

## 功能

- 自動辨識台股代碼、英文或中文股票名稱
- 股票現價、歷史價格、市場指數與多股票比較
- Discord prefix commands 與 slash commands
- 非同步網路請求，不阻塞 Discord event loop
- 會員等級、排行榜、角色獎勵與歡迎設定
- PostgreSQL 正式儲存；本機開發可 fallback 至 SQLite
- liveness、readiness、啟動退避與乾淨 SIGTERM 關閉

## 系統需求

- Python 3.13.14
- Discord application 與 Bot token
- 正式部署需要 PostgreSQL

## 本機快速開始

建立並啟用虛擬環境後安裝依賴：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
```

只在目前的 shell 設定必要值，再啟動 Bot：

```powershell
$env:DISCORD_BOT_TOKEN = "<set-locally>"
python bot.py
```

未設定 `DATABASE_URL` 時，本機會使用 SQLite。這個 fallback 只適合開發與測試。

## Render 部署

`render.yaml` 使用 Python web service，啟動指令為 `python bot.py`。

部署前在 Render Dashboard 設定：

- `DISCORD_BOT_TOKEN`：Discord Bot 的秘密憑證
- `DATABASE_URL`：Render PostgreSQL 提供的 managed connection value
- `REQUIRE_DURABLE_STORAGE=true`：禁止正式環境退回臨時 SQLite
- `SYNC_COMMANDS_ON_START=false`：一般重啟不重複同步全域指令

Render 平台 health check 使用 `/live`。`/health` 是 Discord readiness：未連線回 503，連線完成回 200。

首次切換 PostgreSQL 前，請依照 [PostgreSQL migration runbook](docs/postgres-migration.md) 執行 dry-run、原子遷移及筆數驗證。

## Docker

先在主機 shell 設定必要環境變數，再傳入容器；不要將實際值寫進 image、compose file 或命令歷史：

```powershell
docker build -t discord-stock-bot .
docker run --rm -e DISCORD_BOT_TOKEN -e DATABASE_URL discord-stock-bot
```

## 常用指令

```text
!stock 2330
!stock nvidia
!price AAPL
!compare 2330 AAPL NVDA
!history 2330 14
!market
!level
!rank
```

全域 slash commands 預設不會在每次重啟時同步。Bot 擁有者可在需要時執行 `!sync`。

## 測試

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

測試涵蓋啟動退避、指令同步閘門、健康端點、SIGTERM、SQLite 非同步 contract、併發 XP 更新，以及遷移 snapshot 的完整性與隱私輸出。

## 安全與資料

- 不要把憑證、真實連線字串、資料庫快照或私人紀錄提交到 Git。
- 正式環境不允許使用 Render 臨時檔案系統保存 Bot 狀態。
- 遷移工具不輸出資料列、使用者名稱、Discord ID 或連線資訊。
- Discord 使用者看到固定友善錯誤；內部日誌只記錄錯誤類型。
- 股票資料可能延遲，本專案不構成投資建議。

## 授權

MIT License