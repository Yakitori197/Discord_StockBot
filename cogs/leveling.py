"""
等級系統 Cog
- 發言自動獲得經驗值（15-25 XP）
- 60 秒冷卻防刷
- 升級自動通知
- 達到指定等級自動給角色
"""

import discord
from discord.ext import commands
from discord import app_commands
import random
from datetime import datetime, timedelta
from database import (
    get_user_level, add_xp, get_leaderboard, get_user_rank,
    get_guild_settings, update_guild_settings,
    add_level_reward, get_level_reward, get_all_level_rewards, remove_level_reward,
    xp_for_level, calculate_level
)


class Leveling(commands.Cog):
    """等級系統"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 冷卻追蹤：{(guild_id, user_id): last_xp_datetime}
        self.xp_cooldowns: dict[tuple, datetime] = {}

    # ==================== 自動經驗值 ====================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """監聽訊息，自動發放經驗值"""
        # 忽略機器人、私訊、指令
        if message.author.bot:
            return
        if not message.guild:
            return

        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        cooldown_key = (guild_id, user_id)

        # 取得伺服器設定
        settings = await get_guild_settings(guild_id)
        xp_cooldown = settings.get('xp_cooldown', 60)
        base_xp = settings.get('xp_per_message', 15)

        # 冷卻檢查
        now = datetime.now()
        if cooldown_key in self.xp_cooldowns:
            elapsed = (now - self.xp_cooldowns[cooldown_key]).total_seconds()
            if elapsed < xp_cooldown:
                return

        # 記錄冷卻時間
        self.xp_cooldowns[cooldown_key] = now

        # 隨機經驗值 (base ~ base+10)
        xp_amount = random.randint(base_xp, base_xp + 10)
        username = str(message.author)
        new_level, new_xp, leveled_up = await add_xp(guild_id, user_id, username, xp_amount)

        if leveled_up:
            await self._handle_level_up(message, new_level, guild_id, user_id)

    async def _handle_level_up(self, message: discord.Message, new_level: int, guild_id: str, user_id: str):
        """處理升級：通知 + 角色獎勵"""
        settings = await get_guild_settings(guild_id)

        # 升級通知
        embed = discord.Embed(
            title="🎉 升級啦！",
            description=f"恭喜 {message.author.mention} 升到了 **等級 {new_level}**！",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        next_level_xp = xp_for_level(new_level + 1)
        embed.set_footer(text=f"下一級需要 {next_level_xp} XP")

        # 決定通知頻道
        channel_id = settings.get('level_up_channel_id')
        if channel_id:
            channel = self.bot.get_channel(int(channel_id))
        else:
            channel = message.channel

        if channel:
            await channel.send(embed=embed)

        # 檢查等級獎勵
        reward = await get_level_reward(guild_id, new_level)
        if reward:
            role = message.guild.get_role(int(reward['role_id']))
            if role and role not in message.author.roles:
                try:
                    await message.author.add_roles(role)
                    reward_embed = discord.Embed(
                        title="🏆 獲得新角色！",
                        description=f"{message.author.mention} 達到等級 {new_level}，獲得了 {role.mention} 角色！",
                        color=discord.Color.purple()
                    )
                    if channel:
                        await channel.send(embed=reward_embed)
                except discord.Forbidden:
                    print(f"❌ 無法給予角色 {role.name}（權限不足）")

    # ==================== 等級查詢 ====================

    @commands.hybrid_command(name='level', aliases=['lv', '等級'])
    async def level_command(self, ctx: commands.Context, member: discord.Member = None):
        """
        查看等級
        用法: !level [@用戶]
        """
        member = member or ctx.author
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)

        data = await get_user_level(guild_id, user_id)

        if not data:
            await ctx.send(f"📊 {member.display_name} 還沒有任何等級資料，多多發言吧！")
            return

        level = data['level']
        xp = data['xp']
        total_messages = data['total_messages']
        rank = await get_user_rank(guild_id, user_id)

        # 計算升級進度
        current_level_xp = xp_for_level(level)
        next_level_xp = xp_for_level(level + 1)
        xp_needed = next_level_xp - current_level_xp
        xp_progress = xp - current_level_xp
        progress_pct = (xp_progress / xp_needed * 100) if xp_needed > 0 else 100

        # 進度條
        bar_length = 20
        filled = int(bar_length * progress_pct / 100)
        bar = '█' * filled + '░' * (bar_length - filled)

        embed = discord.Embed(
            title=f"📊 {member.display_name} 的等級資訊",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="⭐ 等級", value=f"**{level}**", inline=True)
        embed.add_field(name="✨ 經驗值", value=f"**{xp:,}** XP", inline=True)
        embed.add_field(name="🏅 排名", value=f"**#{rank}**" if rank else "N/A", inline=True)
        embed.add_field(
            name=f"📈 升級進度 ({xp_progress:,}/{xp_needed:,})",
            value=f"`{bar}` {progress_pct:.1f}%",
            inline=False
        )
        embed.add_field(name="💬 總訊息數", value=f"{total_messages:,}", inline=True)
        embed.add_field(name="🎯 下一級需要", value=f"{next_level_xp:,} XP", inline=True)

        await ctx.send(embed=embed)

    # ==================== 排行榜 ====================

    @commands.hybrid_command(name='rank', aliases=['leaderboard', 'top', '排行', '排行榜'])
    async def rank_command(self, ctx: commands.Context):
        """
        查看排行榜 TOP 10
        用法: !rank
        """
        guild_id = str(ctx.guild.id)
        leaders = await get_leaderboard(guild_id, 10)

        if not leaders:
            await ctx.send("📊 還沒有任何排行資料，大家多多發言吧！")
            return

        embed = discord.Embed(
            title=f"🏆 {ctx.guild.name} 等級排行榜",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )

        medals = ['🥇', '🥈', '🥉']
        description_lines = []

        for i, user_data in enumerate(leaders):
            medal = medals[i] if i < 3 else f'`{i+1}.`'
            username = user_data.get('username', '未知用戶')
            level = user_data['level']
            xp = user_data['xp']
            description_lines.append(
                f"{medal} **{username}** — 等級 {level} | {xp:,} XP"
            )

        embed.description = '\n'.join(description_lines)

        # 顯示自己的排名
        user_rank = await get_user_rank(guild_id, str(ctx.author.id))
        if user_rank:
            embed.set_footer(text=f"你的排名：#{user_rank}")

        await ctx.send(embed=embed)

    # ==================== 管理員指令 ====================

    @commands.hybrid_command(name='setlevelreward', aliases=['slr'])
    @commands.has_permissions(administrator=True)
    async def set_level_reward(self, ctx: commands.Context, level: int, role: discord.Role):
        """
        設定等級獎勵
        用法: !setlevelreward <等級> @角色
        """
        if level < 1:
            await ctx.send("❌ 等級必須大於 0")
            return

        guild_id = str(ctx.guild.id)
        await add_level_reward(guild_id, level, str(role.id), role.name)

        embed = discord.Embed(
            title="✅ 等級獎勵設定完成",
            description=f"達到 **等級 {level}** 的成員將自動獲得 {role.mention} 角色",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='removelevelreward', aliases=['rlr'])
    @commands.has_permissions(administrator=True)
    async def remove_level_reward_cmd(self, ctx: commands.Context, level: int):
        """
        移除等級獎勵
        用法: !removelevelreward <等級>
        """
        guild_id = str(ctx.guild.id)
        removed = await remove_level_reward(guild_id, level)

        if removed:
            await ctx.send(f"✅ 已移除等級 {level} 的獎勵")
        else:
            await ctx.send(f"❌ 等級 {level} 沒有設定獎勵")

    @commands.hybrid_command(name='levelrewards', aliases=['lr', '等級獎勵'])
    async def level_rewards_cmd(self, ctx: commands.Context):
        """
        查看所有等級獎勵
        用法: !levelrewards
        """
        guild_id = str(ctx.guild.id)
        rewards = await get_all_level_rewards(guild_id)

        if not rewards:
            await ctx.send("📋 目前沒有設定任何等級獎勵\n使用 `!setlevelreward <等級> @角色` 來設定")
            return

        embed = discord.Embed(
            title="🏆 等級獎勵列表",
            color=discord.Color.purple(),
            timestamp=datetime.now()
        )

        description_lines = []
        for r in rewards:
            role = ctx.guild.get_role(int(r['role_id']))
            role_text = role.mention if role else f"~~{r['role_name']}~~（已刪除）"
            description_lines.append(f"⭐ **等級 {r['level']}** → {role_text}")

        embed.description = '\n'.join(description_lines)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='setlevelchannel', aliases=['slc'])
    @commands.has_permissions(administrator=True)
    async def set_level_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        設定升級通知頻道
        用法: !setlevelchannel #頻道
        """
        guild_id = str(ctx.guild.id)
        await update_guild_settings(guild_id, level_up_channel_id=str(channel.id))

        embed = discord.Embed(
            title="✅ 升級通知頻道設定完成",
            description=f"升級通知將發送到 {channel.mention}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='setxp')
    @commands.has_permissions(administrator=True)
    async def set_xp(self, ctx: commands.Context, xp_amount: int, cooldown: int = None):
        """
        設定經驗值參數
        用法: !setxp <每次經驗值> [冷卻秒數]
        """
        if xp_amount < 1 or xp_amount > 100:
            await ctx.send("❌ 經驗值範圍：1 - 100")
            return

        guild_id = str(ctx.guild.id)
        kwargs = {'xp_per_message': xp_amount}

        if cooldown is not None:
            if cooldown < 0 or cooldown > 600:
                await ctx.send("❌ 冷卻時間範圍：0 - 600 秒")
                return
            kwargs['xp_cooldown'] = cooldown

        await update_guild_settings(guild_id, **kwargs)

        msg = f"✅ 每次經驗值設為 **{xp_amount}** XP"
        if cooldown is not None:
            msg += f"，冷卻時間設為 **{cooldown}** 秒"
        await ctx.send(msg)

    # ==================== 錯誤處理 ====================

    @set_level_reward.error
    @remove_level_reward_cmd.error
    @set_level_channel.error
    @set_xp.error
    async def admin_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ 你需要 **管理員** 權限才能使用此指令")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ 缺少必要參數！請查看 `!help_stock` 了解用法")
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))
    print("✅ 已載入 Leveling Cog")
