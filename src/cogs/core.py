from discord.ext import commands
import discord
from discord import app_commands
import re
import aiohttp
import asyncio
import logging
import uuid


logger = logging.getLogger("discord.archiver")


class ArchiveChannel(commands.Cog):
    WEBHOOK_NAME = "チャンネル転送"
    # Discord の各種制限
    MESSAGE_CONTENT_LIMIT = 2000  # webhook/bot が送信できる content の最大長
    THREAD_NAME_LIMIT = 100  # スレッド名の最大長
    EMOJI_NAME_LIMIT = 32  # 絵文字名の最大長
    WEBHOOK_FILE_LIMIT = 8 * 1024 * 1024  # 8MB
    
    def __init__(self, bot):
        self.bot = bot
        self.temp_emojis = []  # Track temporary emojis for cleanup
        self.temp_emoji_cache = {}  # 元の絵文字ID -> 作成済みの一時絵文字
    
    def has_mentions(self, content):
        """Check if the message content contains any mentions (excluding channel mentions)."""
        # Check for @everyone and @here
        if '@everyone' in content or '@here' in content:
            return True
        
        # Check for user and role mentions only
        mention_pattern = re.compile(r'<@!?\d+>|<@&\d+>')
        return mention_pattern.search(content) is not None
    
    def escape_mentions(self, content):
        """Escape mentions in the message content to prevent re-firing (excluding channel mentions)."""
        # Escape @everyone and @here
        content = content.replace('@everyone', r'@\everyone').replace('@here', r'@\here')
        
        # Escape user and role mentions only
        # Replace <@123456> with <@\123456> to escape the mention
        mention_pattern = re.compile(r'(<@!?|<@&)(\d+>)')
        content = mention_pattern.sub(r'\1\\\2', content)
        
        return content
    
    @classmethod
    def truncate_thread_name(cls, name):
        """Truncate a thread name so that it fits within Discord's limit."""
        if len(name) <= cls.THREAD_NAME_LIMIT:
            return name
        return name[:cls.THREAD_NAME_LIMIT - 1] + "…"
    
    @classmethod
    def split_content(cls, content):
        """Split content into chunks that fit within Discord's message length limit."""
        if not content:
            return []
        if len(content) <= cls.MESSAGE_CONTENT_LIMIT:
            return [content]
        
        chunks = []
        remaining = content
        while len(remaining) > cls.MESSAGE_CONTENT_LIMIT:
            # 可能なら改行で区切る。絵文字やメンション (<...>) の途中で切らないように調整する
            cut = remaining.rfind("\n", 0, cls.MESSAGE_CONTENT_LIMIT)
            if cut <= 0:
                cut = cls.MESSAGE_CONTENT_LIMIT
                last_open = remaining.rfind("<", 0, cut)
                if last_open != -1 and remaining.find(">", last_open, cut) == -1:
                    cut = last_open if last_open > 0 else cut
            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")
        if remaining:
            chunks.append(remaining)
        return chunks
    
    def extract_external_emojis(self, content, destination_guild):
        """Extract external emojis that are not available in the destination guild."""
        emoji_pattern = re.compile(r'<(a?):(\w+):(\d+)>')
        external_emojis = []
        
        for match in emoji_pattern.finditer(content):
            animated = bool(match.group(1))
            name = match.group(2)
            emoji_id = int(match.group(3))
            
            # Check if emoji exists in destination guild
            existing_emoji = discord.utils.get(destination_guild.emojis, id=emoji_id)
            if not existing_emoji:
                external_emojis.append({
                    'animated': animated,
                    'name': name,
                    'id': emoji_id,
                    'full_match': match.group(0)
                })
        
        return external_emojis
    
    async def create_temp_emoji(self, guild, emoji_info):
        """Create a temporary emoji in the destination guild."""
        try:
            # Generate unique name to avoid conflicts (絵文字名は32文字まで)
            prefix = f"temp_{uuid.uuid4().hex[:8]}_"
            temp_name = prefix + emoji_info['name'][:self.EMOJI_NAME_LIMIT - len(prefix)]
            
            # Download emoji image
            emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_info['id']}.{'gif' if emoji_info['animated'] else 'png'}"
            async with aiohttp.ClientSession() as session:
                async with session.get(emoji_url) as response:
                    if response.status != 200:
                        logger.warning("Failed to download emoji %s: HTTP %s", emoji_info['name'], response.status)
                        return None
                    image_data = await response.read()
            
            # Create emoji in guild
            temp_emoji = await guild.create_custom_emoji(
                name=temp_name,
                image=image_data
            )
            
            # Track for cleanup
            self.temp_emojis.append(temp_emoji)
            return temp_emoji
            
        except Exception as e:
            logger.warning("Failed to create temp emoji %s: %s", emoji_info['name'], e)
            return None
    
    async def cleanup_temp_emojis(self):
        """Clean up all temporary emojis."""
        for emoji in self.temp_emojis:
            try:
                await emoji.delete()
            except Exception as e:
                logger.warning("Failed to delete temp emoji %s: %s", emoji.name, e)
        
        self.temp_emojis.clear()
        self.temp_emoji_cache.clear()
    
    async def process_external_emojis(self, content, destination_guild):
        """Process external emojis by creating temporary ones and replacing content."""
        external_emojis = self.extract_external_emojis(content, destination_guild)
        
        if not external_emojis:
            return content
        
        processed_content = content
        
        for emoji_info in external_emojis:
            # 同じ絵文字は転送中に一度だけ作成する (サーバーの絵文字上限対策)
            if emoji_info['id'] not in self.temp_emoji_cache:
                temp_emoji = await self.create_temp_emoji(destination_guild, emoji_info)
                if temp_emoji:
                    self.temp_emoji_cache[emoji_info['id']] = temp_emoji
                    
                    # Add rate limit delay
                    await asyncio.sleep(1)
        
        # Replace external emoji references with temp emoji references
        for emoji_info in external_emojis:
            if emoji_info['id'] in self.temp_emoji_cache:
                temp_emoji = self.temp_emoji_cache[emoji_info['id']]
                temp_emoji_str = f"<{'a' if emoji_info['animated'] else ''}:{temp_emoji.name}:{temp_emoji.id}>"
                processed_content = processed_content.replace(emoji_info['full_match'], temp_emoji_str)
        
        return processed_content
    
    def check_file_sizes(self, attachments):
        """Check if any attachments exceed webhook file size limit (8MB)."""
        large_files = []
        small_files = []
        
        for attachment in attachments:
            if attachment.size > self.WEBHOOK_FILE_LIMIT:
                large_files.append(attachment)
            else:
                small_files.append(attachment)
        
        return large_files, small_files
    
    async def send_bot_message_with_embed(self, content, large_files, message, thread, extra_embed=None):
        """Send a message using bot account with author info in embed for large files."""
        author = message.author
        # Create embed with author information
        embed = discord.Embed(
            description=content[:4096] if content else None,
            color=0x5865F2,
            timestamp=message.created_at,
        )
        embed.set_author(
            name=author.display_name,
            icon_url=author.display_avatar.url if author.display_avatar else None
        )
        embed.set_footer(text=f"元の投稿者: {author.name}")
        
        if large_files:
            file_info = "\n".join([f"📎 {file.filename} ({file.size / (1024*1024):.1f}MB)" for file in large_files])
            embed.add_field(name="大きなファイル", value=file_info, inline=False)
        
        embeds = [embed]
        if extra_embed is not None:
            embeds.append(extra_embed)
        
        # Send message with large files
        files_to_send = [await attachment.to_file() for attachment in large_files]
        
        return await thread.send(embeds=embeds, files=files_to_send)
    
    async def _send_webhook_chunk(self, webhook, content, files, author, thread, embed=None):
        """Send a single (<= 2000 chars) chunk via webhook, escaping mentions if needed."""
        kwargs = dict(
            embed=embed,
            files=files,
            username=author.display_name,
            avatar_url=author.display_avatar.url if author.display_avatar else None,
            thread=thread,
            wait=True,
        )
        if self.has_mentions(content):
            webhook_message = await webhook.send(content=self.escape_mentions(content), **kwargs)
            await webhook_message.edit(content=content)
            return webhook_message
        return await webhook.send(content=content, **kwargs)
    
    async def send_webhook_message(self, webhook, message, thread, embed=None):
        """Send a message via webhook and restore original content after escaping mentions and processing external emojis."""
        author = message.author
        
        # Process external emojis first
        processed_content = await self.process_external_emojis(message.content, webhook.guild)
        
        # Check file sizes
        large_files, small_files = self.check_file_sizes(message.attachments)
        
        # If there are large files, send them via bot account
        if large_files:
            # Send large files via bot account with embed
            bot_message = await self.send_bot_message_with_embed(
                processed_content, large_files, message, thread,
                extra_embed=None if small_files else embed,
            )
            
            # If there are no small files, we are done
            if not small_files:
                return bot_message
        
        # Webhook sending (for small files and/or content).
        # content が 2000 文字を超える場合 (Nitro ユーザーの投稿や絵文字置換で伸びた場合) は分割して送信する
        chunks = self.split_content(processed_content) or [""]
        webhook_message = None
        for index, chunk in enumerate(chunks):
            is_last = index == len(chunks) - 1
            webhook_message = await self._send_webhook_chunk(
                webhook, chunk,
                [await attachment.to_file() for attachment in small_files] if is_last else [],
                author, thread, embed if is_last else None,
            )
        return webhook_message
    
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
    
    async def _archive_message(self, webhook, message, thread, embed=None):
        """Archive a single message, logging (instead of aborting) on failure."""
        try:
            await self.send_webhook_message(webhook, message, thread, embed)
            return True
        except discord.HTTPException as e:
            logger.error("Failed to archive message %s (%s): %s", message.id, message.jump_url, e)
            return False
    
    async def archive_thread(self, ctx, destination_forum, source_thread, category_label, webhook, parent_channel, failed):
        prefix = self._create_thread_name_prefix(ctx, category_label)
        archive_thread_name = self.truncate_thread_name(f"{prefix}{ctx.channel.name}/{source_thread.name}")
        archived_thread, _ = await destination_forum.create_thread(name=archive_thread_name, content="from: " + source_thread.mention + "\nparent: " + parent_channel.mention)
        async for message in source_thread.history(limit=None, oldest_first=True):
            if self._is_empty_message(message):
                continue
            if not await self._archive_message(webhook, message, archived_thread):
                failed.append(message)
        return archived_thread
    
    async def _process_channel_messages(self, ctx, destination_forum, webhook, archive_channel, category_label, archived_threads, failed):
        """Process all messages in the channel and archive them."""
        async for message in ctx.channel.history(limit=None, oldest_first=True):
            # プレフィックスコマンドで実行した場合、コマンド自身のメッセージは転送しない
            if ctx.message is not None and message.id == ctx.message.id:
                continue
            if self._is_empty_message(message):
                continue
            
            if message.thread:
                archived_thread = await self.archive_thread(ctx, destination_forum, message.thread, category_label, webhook, archive_channel, failed)
                archived_threads.append(archived_thread)
                embed = discord.Embed(description=f"{message.author.display_name}がスレッド: {archived_thread.mention}を開始しました。")
                if not await self._archive_message(webhook, message, archive_channel, embed):
                    failed.append(message)
                continue
            
            if not await self._archive_message(webhook, message, archive_channel):
                failed.append(message)
        return archived_threads
    
    async def _update_thread_references(self, archive_header_message, original_channel, archived_threads):
        """Update the header message with thread references if any threads were created."""
        if len(archived_threads) > 0:
            thread_mentions = "\n".join([thread.mention for thread in archived_threads])
            await archive_header_message.edit(content=f"from: {original_channel.mention}\nスレッド:\n{thread_mentions}")
    
    async def _reply(self, ctx, content):
        """Reply to the command invoker.

        スラッシュコマンドのインタラクションは 15 分で失効するため、
        転送に時間がかかった場合はチャンネルへの通常送信にフォールバックする。
        """
        try:
            if ctx.interaction:
                await ctx.interaction.followup.send(content)
            else:
                await ctx.send(content)
        except discord.HTTPException:
            await ctx.channel.send(f"{ctx.author.mention} {content}")

    @commands.hybrid_command(name="チャンネル転送", description="実行したチャンネルのメッセージを指定したフォーラムに転送します。")
    @app_commands.describe(
        destination_forum="転送先のフォーラムを指定",
        category_label="ラベルを指定",
    )
    @app_commands.rename(
        destination_forum="フォーラムチャンネル",
        category_label="ラベル",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def archive_channel(self, ctx: commands.Context, destination_forum: discord.ForumChannel, category_label: str=None):
        if ctx.author.bot:
            return
        if ctx.interaction:
            await ctx.interaction.response.defer(thinking=True)

        webhook = await destination_forum.create_webhook(name=self.WEBHOOK_NAME)

        archived_threads = []
        failed = []
        try:
            prefix = self._create_thread_name_prefix(ctx, category_label)
            archive_channel_name = self.truncate_thread_name(f"{prefix}{ctx.channel.name}")
            archive_channel, archive_header_message = await destination_forum.create_thread(name=archive_channel_name, content="from: " + ctx.channel.mention)
            await self._reply(ctx, archive_channel.mention + "に転送を開始しました。")
            archived_threads = await self._process_channel_messages(ctx, destination_forum, webhook, archive_channel, category_label, archived_threads, failed)
            await self._update_thread_references(archive_header_message, ctx.channel, archived_threads)
        finally:
            # 途中でエラーが起きても webhook と一時絵文字を残さない
            try:
                await webhook.delete()
            except discord.HTTPException as e:
                logger.warning("Failed to delete webhook: %s", e)
            await self.cleanup_temp_emojis()

        result = archive_channel.mention + "に転送完了しました。"
        if failed:
            failed_links = "\n".join(message.jump_url for message in failed[:10])
            if len(failed) > 10:
                failed_links += f"\n...他{len(failed) - 10}件"
            result += f"\n⚠️ {len(failed)}件のメッセージを転送できませんでした:\n{failed_links}"
        await self._reply(ctx, result)



async def setup(bot):
    await bot.add_cog(ArchiveChannel(bot))
