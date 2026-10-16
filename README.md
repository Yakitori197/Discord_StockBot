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
- Git（安裝固定版本的 yolab-quote 時需要；`git --version` 應可執行）
- Discord application 與 Bot token
- 正式部署需要 PostgreSQL

## 本機快速開始

建立並啟用虛擬環境後安裝依賴：

```powershell
$env:PYTHONUTF8 = "1"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
```

Windows 的預設編碼可能是 cp950。`PYTHONUTF8=1` 讓 Python 使用 UTF-8，避免讀取含中文註解的依賴檔時發生解碼錯誤；每次開新終端機都要重新設定。若使用 cmd，請先執行 `set PYTHONUTF8=1`。也可單次使用 `python -X utf8 -m pip install -r requirements.lock`。只改 `chcp 65001` 不等於啟用 Python UTF-8 模式。

只在目前的 shell 設定必要值，再啟動 Bot：

```powershell
$env:DISCORD_BOT_TOKEN = "<set-locally>"
python bot.py
```

未設定 `DATABASE_URL` 時，本機會使用 SQLite。這個 fallback 只適合開發與測試。

## Render 部署

`render.yaml` 使用 Python web service，啟動指令為 `python bot.py`。

自動部署預設關閉。GitHub push 或 merge 不會直接部署正式服務；完成資料遷移與 staging 驗證後，請由 Render Dashboard 人工觸發部署。

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
$env:PYTHONUTF8 = "1"
python -m pip install -r requirements-dev.txt
python -m pip check
python -m pytest -q
```

測試涵蓋啟動退避、指令同步閘門、健康端點、SIGTERM、SQLite 非同步 contract、併發 XP 更新，以及遷移 snapshot 的完整性與隱私輸出。

## 自動驗證（GitHub Actions）

[CI workflow](.github/workflows/ci.yml) 在推送 `main`、向 `main` 提出 PR 或手動執行時驗證：

- Windows 與 Linux 使用 `.python-version` 指定的 Python（目前 3.13.14），安裝 `requirements-dev.txt`（包含正式環境的 `requirements.lock`），執行 `pip check` 與既有測試。
- Linux 使用正式 Dockerfile 建置映像，檢查映像內依賴及 yolab-quote 0.1.1，並以 Flask test client 驗證 `/live` 和尚未連線的 `/health`。
- CI 設定 `PYTHONUTF8=1`。映像 smoke test 使用 `--network none`，不啟動 Discord 連線、不需要 Bot token 或正式資料庫。

此流程只驗證，不推送映像，也不呼叫 Render 或任何部署 webhook。`render.yaml` 的自動部署維持關閉；CI 通過不代表已驗證正式 Render 部署或真實 PostgreSQL／Discord 連線。

## 安全與資料

- 不要把憑證、真實連線字串、資料庫快照或私人紀錄提交到 Git。
- 正式環境不允許使用 Render 臨時檔案系統保存 Bot 狀態。
- 遷移工具不輸出資料列、使用者名稱、Discord ID 或連線資訊。
- Discord 使用者看到固定友善錯誤；內部日誌只記錄錯誤類型。
- 股票資料可能延遲，本專案不構成投資建議。

## 授權

MIT License
