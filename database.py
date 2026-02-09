"""
Discord Bot 資料庫模組
使用 SQLite 儲存用戶等級、獎勵、伺服器設定、歡迎記錄
"""

import sqlite3
import os
import math
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

# 資料庫路徑
DB_PATH = os.getenv('DB_PATH', 'data/discord_bot.db')


def get_connection() -> sqlite3.Connection:
    """取得資料庫連線"""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化資料庫，建立所有資料表"""
    conn = get_connection()
    cursor = conn.cursor()

    # 用戶等級
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            username TEXT,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            total_messages INTEGER DEFAULT 0,
            last_xp_time TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id, user_id)
        )
    ''')

    # 等級獎勵（角色）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS level_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            role_id TEXT NOT NULL,
            role_name TEXT,
            UNIQUE(guild_id, level)
        )
    ''')

    # 伺服器設定
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id TEXT PRIMARY KEY,
            welcome_channel_id TEXT,
            welcome_message TEXT,
            rules_channel_id TEXT,
            log_channel_id TEXT,
            level_up_channel_id TEXT,
            xp_per_message INTEGER DEFAULT 15,
            xp_cooldown INTEGER DEFAULT 60
        )
    ''')

    # 歡迎記錄
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS welcome_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            username TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    print("✅ 資料庫初始化完成")


# ==================== 等級公式 ====================

def calculate_level(xp: int) -> int:
    """
    根據經驗值計算等級
    公式: level = 1 + floor(sqrt(xp / 100))
    
    Lv.2  需要 100 XP
    Lv.3  需要 400 XP
    Lv.5  需要 1600 XP
    Lv.10 需要 8100 XP
    """
    if xp < 0:
        return 1
    return 1 + int(math.floor(math.sqrt(xp / 100)))


def xp_for_level(level: int) -> int:
    """計算達到指定等級所需的最低經驗值"""
    if level <= 1:
        return 0
    return (level - 1) ** 2 * 100


# ==================== 等級系統 ====================

def get_user_level(guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """取得用戶等級資料"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?',
        (guild_id, user_id)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def add_xp(guild_id: str, user_id: str, username: str, xp_amount: int) -> Tuple[int, int, bool]:
    """
    為用戶增加經驗值
    回傳: (目前等級, 目前經驗值, 是否升級)
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 取得或建立用戶資料
    cursor.execute(
        'SELECT xp, level FROM user_levels WHERE guild_id = ? AND user_id = ?',
        (guild_id, user_id)
    )
    row = cursor.fetchone()

    now = datetime.now().isoformat()

    if row:
        old_level = row['level']
        new_xp = row['xp'] + xp_amount
        new_level = calculate_level(new_xp)
        leveled_up = new_level > old_level

        cursor.execute('''
            UPDATE user_levels 
            SET xp = ?, level = ?, username = ?, total_messages = total_messages + 1, last_xp_time = ?
            WHERE guild_id = ? AND user_id = ?
        ''', (new_xp, new_level, username, now, guild_id, user_id))
    else:
        new_xp = xp_amount
        new_level = calculate_level(new_xp)
        leveled_up = new_level > 1

        cursor.execute('''
            INSERT INTO user_levels (guild_id, user_id, username, xp, level, total_messages, last_xp_time)
            VALUES (?, ?, ?, ?, ?, 1, ?)
        ''', (guild_id, user_id, username, new_xp, new_level, now))

    conn.commit()
    conn.close()
    return (new_level, new_xp, leveled_up)


def get_leaderboard(guild_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """取得排行榜"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM user_levels WHERE guild_id = ? ORDER BY xp DESC LIMIT ?',
        (guild_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user_rank(guild_id: str, user_id: str) -> Optional[int]:
    """取得用戶在伺服器中的排名"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COUNT(*) as rank FROM user_levels 
        WHERE guild_id = ? AND xp > (
            SELECT COALESCE(xp, 0) FROM user_levels WHERE guild_id = ? AND user_id = ?
        )
    ''', (guild_id, guild_id, user_id))
    row = cursor.fetchone()
    conn.close()
    if row is not None:
        return row['rank'] + 1
    return None


# ==================== 等級獎勵 ====================

def add_level_reward(guild_id: str, level: int, role_id: str, role_name: str):
    """新增或更新等級獎勵"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO level_rewards (guild_id, level, role_id, role_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, level) DO UPDATE SET role_id = ?, role_name = ?
    ''', (guild_id, level, role_id, role_name, role_id, role_name))
    conn.commit()
    conn.close()


def get_level_reward(guild_id: str, level: int) -> Optional[Dict[str, Any]]:
    """取得指定等級的獎勵"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM level_rewards WHERE guild_id = ? AND level = ?',
        (guild_id, level)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def get_all_level_rewards(guild_id: str) -> List[Dict[str, Any]]:
    """取得伺服器所有等級獎勵"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM level_rewards WHERE guild_id = ? ORDER BY level ASC',
        (guild_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_level_reward(guild_id: str, level: int) -> bool:
    """移除等級獎勵，回傳是否成功"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'DELETE FROM level_rewards WHERE guild_id = ? AND level = ?',
        (guild_id, level)
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ==================== 伺服器設定 ====================

def get_guild_settings(guild_id: str) -> Dict[str, Any]:
    """取得伺服器設定，若不存在則建立預設"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM guild_settings WHERE guild_id = ?', (guild_id,))
    row = cursor.fetchone()

    if not row:
        cursor.execute(
            'INSERT INTO guild_settings (guild_id) VALUES (?)',
            (guild_id,)
        )
        conn.commit()
        cursor.execute('SELECT * FROM guild_settings WHERE guild_id = ?', (guild_id,))
        row = cursor.fetchone()

    conn.close()
    return dict(row)


def update_guild_settings(guild_id: str, **kwargs):
    """更新伺服器設定"""
    if not kwargs:
        return

    # 確保設定存在
    get_guild_settings(guild_id)

    conn = get_connection()
    cursor = conn.cursor()

    allowed_fields = {
        'welcome_channel_id', 'welcome_message', 'rules_channel_id',
        'log_channel_id', 'level_up_channel_id', 'xp_per_message', 'xp_cooldown'
    }

    set_clauses = []
    values = []
    for key, value in kwargs.items():
        if key in allowed_fields:
            set_clauses.append(f'{key} = ?')
            values.append(value)

    if set_clauses:
        values.append(guild_id)
        sql = f"UPDATE guild_settings SET {', '.join(set_clauses)} WHERE guild_id = ?"
        cursor.execute(sql, values)
        conn.commit()

    conn.close()


# ==================== 歡迎記錄 ====================

def log_welcome(guild_id: str, user_id: str, username: str):
    """記錄新成員加入"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO welcome_logs (guild_id, user_id, username) VALUES (?, ?, ?)',
        (guild_id, user_id, username)
    )
    conn.commit()
    conn.close()


# 啟動時自動初始化
init_db()
