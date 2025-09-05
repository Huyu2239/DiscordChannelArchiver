from discord.ext import commands
import discord
from discord import app_commands
from datetime import datetime

from libs.mentions import has_mentions, escape_mentions
from libs.private_emoji import PrivateEmojiProcessor
from libs.message_utils import check_file_sizes, send_bot_message_with_embed


class ArchiveChannel(commands.Cog):
    WEBHOOK_NAME = "チャンネル転送"
    
    def __init__(self, bot):
        self.bot = bot
        self.private_emoji_processor = PrivateEmojiProcessor()

    async def send_webhook_message(self, webhook, content, attachments, author, thread, embed=None):
        """Send a message via webhook and restore original content after escaping mentions and processing external emojis."""
        try:
            processed_content = await self.private_emoji_processor.process_external_emojis(content, webhook.guild)
            large_files, small_files = check_file_sizes(attachments)

            if large_files:
                bot_message = await send_bot_message_with_embed(
                    webhook.channel, processed_content, large_files, author, thread
                )
                if small_files:
                    if has_mentions(processed_content):
                        escaped_content = escape_mentions(processed_content)
                        webhook_message = await webhook.send(
                            content=escaped_content,
                            embed=embed,
                            files=[await attachment.to_file() for attachment in small_files],
                            username=author.display_name,
                            avatar_url=author.display_avatar.url if author.display_avatar else None,
                            thread=thread,
                            wait=True
                        )
                        await webhook_message.edit(content=processed_content)
                        return webhook_message
                    return await webhook.send(
                        content=processed_content,
                        embed=embed,
                        files=[await attachment.to_file() for attachment in small_files],
                        username=author.display_name,
                        avatar_url=author.display_avatar.url if author.display_avatar else None,
                        thread=thread,
                        wait=True
                    )
                return bot_message

            if has_mentions(processed_content):
                escaped_content = escape_mentions(processed_content)
                webhook_message = await webhook.send(
                    content=escaped_content,
                    embed=embed,
                    files=[await attachment.to_file() for attachment in small_files],
                    username=author.display_name,
                    avatar_url=author.display_avatar.url if author.display_avatar else None,
                    thread=thread,
                    wait=True
                )
                await webhook_message.edit(content=processed_content)
            else:
                webhook_message = await webhook.send(
                    content=processed_content,
                    embed=embed,
                    files=[await attachment.to_file() for attachment in small_files],
                    username=author.display_name,
                    avatar_url=author.display_avatar.url if author.display_avatar else None,
                    thread=thread,
                    wait=True
                )
            return webhook_message
        except Exception as e:
            raise Exception(f"Failed to send webhook message: {e}")
    def _is_empty_message(self, message):
        """Check if message has no content or attachments."""
        return len(message.content) == 0 and len(message.attachments) == 0

    def _create_thread_name_prefix(self, ctx, category_label):
        """Create prefix for thread names based on category_label and category."""
        prefix = ""
        if category_label:
            prefix += category_label + "/"
        if ctx.channel.category:
            prefix += ctx.channel.category.name + "/"
        return prefix

    async def archive_thread(self, ctx, destination_forum, source_thread, category_label, webhook, parent_channel):
        prefix = self._create_thread_name_prefix(ctx, category_label)
        archive_thread_name = f"{prefix}{ctx.channel.name}/{source_thread.name}"
        archived_thread, _ = await destination_forum.create_thread(name=archive_thread_name, content="from: " + source_thread.mention + "\nparent: " + parent_channel.mention)
        async for message in source_thread.history(limit=None, oldest_first=True):
            if self._is_empty_message(message):
                continue
            await self.send_webhook_message(
                webhook, message.content, message.attachments, message.author, archived_thread
            )
        return archived_thread
    
    async def _process_channel_messages(self, ctx, destination_forum, webhook, archive_channel, category_label, archived_threads):
        """Process all messages in the channel and archive them."""
        async for message in ctx.channel.history(limit=None, oldest_first=True):
            if self._is_empty_message(message):
                continue
            
            if message.thread:
                archived_thread = await self.archive_thread(ctx, destination_forum, message.thread, category_label, webhook, archive_channel)
                archived_threads.append(archived_thread)
                embed = discord.Embed(description=f"{message.author.display_name}がスレッド: {archived_thread.mention}を開始しました。")
                await self.send_webhook_message(
                    webhook, message.content, message.attachments, message.author, archive_channel, embed
                )
                continue
            
            await self.send_webhook_message(
                webhook, message.content, message.attachments, message.author, archive_channel
            )
        return archived_threads
    
    async def _update_thread_references(self, archive_header_message, original_channel, archived_threads):
        """Update the header message with thread references if any threads were created."""
        if len(archived_threads) > 0:
            thread_mentions = "\n".join([thread.mention for thread in archived_threads])
            await archive_header_message.edit(content=f"from: {original_channel.mention}\nスレッド:\n{thread_mentions}")

    @commands.hybrid_command(name="チャンネル転送", description="実行したチャンネルのメッセージを指定したフォーラムに転送します。")
    @app_commands.describe(
        destination_forum="転送先のフォーラムを指定",
        category_label="ラベルを指定",
    )
    @app_commands.rename(
        destination_forum="フォーラムチャンネル",
        category_label="ラベル",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def archive_channel(self, ctx: commands.Context, destination_forum: discord.ForumChannel, category_label: str=None):
        if ctx.interaction:
            await ctx.interaction.response.defer(thinking=True)
        if ctx.author.bot:
            return

        webhook = await destination_forum.create_webhook(name=self.WEBHOOK_NAME)

        archived_threads = []
        prefix = self._create_thread_name_prefix(ctx, category_label)
        archive_channel_name = f"{prefix}{ctx.channel.name}"
        archive_channel, archive_header_message = await destination_forum.create_thread(name=archive_channel_name, content="from: " + ctx.channel.mention)
        archived_threads = await self._process_channel_messages(ctx, destination_forum, webhook, archive_channel, category_label, archived_threads)
        await self._update_thread_references(archive_header_message, ctx.channel, archived_threads)
        await webhook.delete()
        
        # Clean up temporary emojis
        await self.private_emoji_processor.cleanup_temp_emojis()

        if ctx.interaction:
            await ctx.interaction.followup.send(archive_channel.mention + "に転送完了しました。")
        else:
            await ctx.send(archive_channel.mention + "に転送完了しました。")



async def setup(bot):
    await bot.add_cog(ArchiveChannel(bot))
