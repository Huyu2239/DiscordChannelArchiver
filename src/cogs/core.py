from discord.ext import commands
import discord
from discord import app_commands
from datetime import datetime


class ArchiveChannel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def archive_thread(self, ctx, forum, old_thread, label, webhook, parent):
        new_thread_name = f"{old_thread.name}/{ctx.channel.name}{"/" + ctx.channel.category.name if ctx.channel.category else ""}{"/" + label if label else ""}"
        new_thread, _ = await forum.create_thread(name=new_thread_name, content="from: " + old_thread.mention + "\nparent: " + parent.mention)
        async for message in old_thread.history(limit=None, oldest_first=True):
            if len(message.content) == 0 and len(message.attachments) == 0:
                continue
            await webhook.send(
                content=message.content,
                files=[await attachment.to_file() for attachment in message.attachments],
                username=message.author.display_name,
                avatar_url=message.author.display_avatar.url if message.author.display_avatar else None,
                thread=new_thread
            )
        return new_thread

    @commands.hybrid_command(name="チャンネル転送", description="実行したチャンネルのメッセージを指定したフォーラムに転送します。")
    @app_commands.describe(
        forum="転送先のフォーラムを指定",
        label="ラベルを指定",
    )
    @app_commands.rename(
        forum="フォーラムチャンネル",
        label="ラベル",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def archive_channel(self, ctx: commands.Context, forum: discord.ForumChannel, label: str=None):
        if ctx.interaction:
            await ctx.interaction.response.defer(thinking=True)
        if ctx.author.bot:
            return

        webhook = await forum.create_webhook(name="チャンネル転送")

        new_threads = []
        new_channel_name = f"{label + "/" if label else ""}{ctx.channel.category.name + "/" if ctx.channel.category else ""}{ctx.channel.name}"
        new_channel, first_message = await forum.create_thread(name=new_channel_name, content="from: " + ctx.channel.mention)
        async for message in ctx.channel.history(limit=None, oldest_first=True):
            if len(message.content) == 0 and len(message.attachments) == 0:
                continue
            if message.thread:
                new_thread = await self.archive_thread(ctx, forum, message.thread, label, webhook, new_channel)
                new_threads.append(new_thread)
                embed = discord.Embed(description=f"{message.author.display_name}がスレッド: {new_thread.mention}を開始しました。")
                await webhook.send(
                    embed=embed,
                    files=[await attachment.to_file() for attachment in message.attachments],
                    username=message.author.display_name,
                    avatar_url=message.author.display_avatar.url if message.author.display_avatar else None,
                    thread=new_channel
                )
                continue
            await webhook.send(
                content=message.content,
                files=[await attachment.to_file() for attachment in message.attachments],
                username=message.author.display_name,
                avatar_url=message.author.display_avatar.url if message.author.display_avatar else None,
                thread=new_channel
            )
        if len(new_threads) > 0:
            await first_message.edit(content=f"from: {ctx.channel.mention}\nスレッド:\n" + "\n".join([thread.mention for thread in new_threads]))
        await webhook.delete()

        if ctx.interaction:
            await ctx.interaction.followup.send(new_channel.mention + "に転送完了しました。")
        else:
            await ctx.send(new_channel.mention + "に転送完了しました。")



async def setup(bot):
    await bot.add_cog(ArchiveChannel(bot))
