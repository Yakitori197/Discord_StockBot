"""
歡迎系統 Cog
- 新成員加入自動歡迎
- 私訊發送伺服器規則
- 成員離開通知
- 自訂歡迎訊息
"""

import discord
from discord.ext import commands
from datetime import datetime
from database import get_guild_settings, update_guild_settings, log_welcome


# 預設歡迎訊息
DEFAULT_WELCOME_MSG = "🎉 歡迎 {user} 加入 **{server}**！你是我們的第 {member_count} 位成員！"


class Welcome(commands.Cog):
    """歡迎系統"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ==================== 事件監聽 ====================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """新成員加入"""
        if member.bot:
            return

        guild = member.guild
        guild_id = str(guild.id)
        settings = get_guild_settings(guild_id)

        # 記錄加入
        log_welcome(guild_id, str(member.id), str(member))

        # 發送歡迎訊息
        welcome_channel_id = settings.get('welcome_channel_id')
        if welcome_channel_id:
            channel = self.bot.get_channel(int(welcome_channel_id))
            if channel:
                welcome_msg = settings.get('welcome_message') or DEFAULT_WELCOME_MSG
                formatted_msg = self._format_welcome(welcome_msg, member, guild)

                embed = discord.Embed(
                    title="👋 新成員加入！",
                    description=formatted_msg,
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"成員 #{guild.member_count}")

                await channel.send(embed=embed)

        # 私訊規則
        rules_channel_id = settings.get('rules_channel_id')
        if rules_channel_id:
            rules_channel = self.bot.get_channel(int(rules_channel_id))
            if rules_channel:
                try:
                    dm_embed = discord.Embed(
                        title=f"📜 歡迎加入 {guild.name}！",
                        description=(
                            f"嗨 {member.name}，歡迎你！\n\n"
                            f"請先閱讀我們的伺服器規則：{rules_channel.mention}\n\n"
                            f"如果有任何問題，歡迎在伺服器中提問 😊"
                        ),
                        color=discord.Color.blue()
                    )
                    if guild.icon:
                        dm_embed.set_thumbnail(url=guild.icon.url)
                    await member.send(embed=dm_embed)
                except discord.Forbidden:
                    # 用戶關閉私訊
                    pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """成員離開"""
        if member.bot:
            return

        guild = member.guild
        guild_id = str(guild.id)
        settings = get_guild_settings(guild_id)

        # 在歡迎頻道或 log 頻道通知
        channel_id = settings.get('log_channel_id') or settings.get('welcome_channel_id')
        if channel_id:
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                embed = discord.Embed(
                    title="👋 成員離開",
                    description=f"**{member.display_name}** ({member}) 離開了伺服器",
                    color=discord.Color.red(),
                    timestamp=datetime.now()
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"目前成員數：{guild.member_count}")
                await channel.send(embed=embed)

    # ==================== 格式化 ====================

    def _format_welcome(self, template: str, member: discord.Member, guild: discord.Guild) -> str:
        """替換歡迎訊息中的變數"""
        return template.format(
            user=member.mention,
            username=member.display_name,
            server=guild.name,
            member_count=guild.member_count
        )

    # ==================== 管理指令 ====================

    @commands.hybrid_command(name='setwelcome', aliases=['sw'])
    @commands.has_permissions(administrator=True)
    async def set_welcome(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        設定歡迎頻道
        用法: !setwelcome #頻道
        """
        guild_id = str(ctx.guild.id)
        update_guild_settings(guild_id, welcome_channel_id=str(channel.id))

        embed = discord.Embed(
            title="✅ 歡迎頻道設定完成",
            description=f"歡迎訊息將發送到 {channel.mention}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='setrules', aliases=['sr'])
    @commands.has_permissions(administrator=True)
    async def set_rules(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        設定規則頻道
        用法: !setrules #頻道
        """
        guild_id = str(ctx.guild.id)
        update_guild_settings(guild_id, rules_channel_id=str(channel.id))

        embed = discord.Embed(
            title="✅ 規則頻道設定完成",
            description=f"新成員將收到 {channel.mention} 的規則提示",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='setwelcomemsg', aliases=['swm'])
    @commands.has_permissions(administrator=True)
    async def set_welcome_msg(self, ctx: commands.Context, *, message: str):
        """
        自訂歡迎訊息
        用法: !setwelcomemsg 歡迎 {user} 加入 {server}！
        變數: {user} @提及, {username} 名稱, {server} 伺服器, {member_count} 人數
        """
        guild_id = str(ctx.guild.id)
        update_guild_settings(guild_id, welcome_message=message)

        # 預覽
        preview = self._format_welcome(message, ctx.author, ctx.guild)

        embed = discord.Embed(
            title="✅ 歡迎訊息設定完成",
            color=discord.Color.green()
        )
        embed.add_field(name="📝 模板", value=f"`{message}`", inline=False)
        embed.add_field(name="👀 預覽", value=preview, inline=False)
        embed.add_field(
            name="💡 可用變數",
            value="`{user}` @提及 | `{username}` 名稱 | `{server}` 伺服器 | `{member_count}` 人數",
            inline=False
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='testwelcome', aliases=['tw'])
    @commands.has_permissions(administrator=True)
    async def test_welcome(self, ctx: commands.Context):
        """
        測試歡迎訊息
        用法: !testwelcome
        """
        guild_id = str(ctx.guild.id)
        settings = get_guild_settings(guild_id)

        welcome_msg = settings.get('welcome_message') or DEFAULT_WELCOME_MSG
        formatted_msg = self._format_welcome(welcome_msg, ctx.author, ctx.guild)

        embed = discord.Embed(
            title="👋 新成員加入！（測試）",
            description=formatted_msg,
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.set_footer(text=f"成員 #{ctx.guild.member_count}")

        await ctx.send(embed=embed)

        # 顯示設定摘要
        info_parts = []
        wc = settings.get('welcome_channel_id')
        rc = settings.get('rules_channel_id')
        info_parts.append(f"歡迎頻道：{'<#' + wc + '>' if wc else '❌ 未設定'}")
        info_parts.append(f"規則頻道：{'<#' + rc + '>' if rc else '❌ 未設定'}")

        await ctx.send(f"📋 **目前設定**\n" + "\n".join(info_parts))

    @commands.hybrid_command(name='welcomeinfo', aliases=['wi', '歡迎設定'])
    async def welcome_info(self, ctx: commands.Context):
        """
        查看歡迎設定
        用法: !welcomeinfo
        """
        guild_id = str(ctx.guild.id)
        settings = get_guild_settings(guild_id)

        embed = discord.Embed(
            title=f"📋 {ctx.guild.name} 歡迎系統設定",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )

        wc = settings.get('welcome_channel_id')
        rc = settings.get('rules_channel_id')
        lc = settings.get('log_channel_id')
        wm = settings.get('welcome_message') or DEFAULT_WELCOME_MSG

        embed.add_field(
            name="📢 歡迎頻道",
            value=f"<#{wc}>" if wc else "❌ 未設定",
            inline=True
        )
        embed.add_field(
            name="📜 規則頻道",
            value=f"<#{rc}>" if rc else "❌ 未設定",
            inline=True
        )
        embed.add_field(
            name="📝 記錄頻道",
            value=f"<#{lc}>" if lc else "❌ 未設定",
            inline=True
        )
        embed.add_field(
            name="💬 歡迎訊息模板",
            value=f"`{wm}`",
            inline=False
        )
        embed.add_field(
            name="💡 可用變數",
            value="`{user}` @提及 | `{username}` 名稱 | `{server}` 伺服器 | `{member_count}` 人數",
            inline=False
        )

        await ctx.send(embed=embed)

    # ==================== 錯誤處理 ====================

    @set_welcome.error
    @set_rules.error
    @set_welcome_msg.error
    @test_welcome.error
    async def admin_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ 你需要 **管理員** 權限才能使用此指令")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ 缺少必要參數！")
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
    print("✅ 已載入 Welcome Cog")
