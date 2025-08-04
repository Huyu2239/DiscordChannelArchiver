from discord.ext import commands
import discord
from discord import app_commands
from datetime import datetime
import re
import aiohttp
import asyncio
import uuid


class ArchiveChannel(commands.Cog):
    WEBHOOK_NAME = "チャンネル転送"
    
    def __init__(self, bot):
        self.bot = bot
        self.temp_emojis = []  # Track temporary emojis for cleanup
    
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
            # Generate unique name to avoid conflicts
            temp_name = f"temp_{uuid.uuid4().hex[:8]}_{emoji_info['name']}"
            
            # Download emoji image
            emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_info['id']}.{'gif' if emoji_info['animated'] else 'png'}"
            async with aiohttp.ClientSession() as session:
                async with session.get(emoji_url) as response:
                    if response.status == 200:
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
            print(f"Failed to create temp emoji {emoji_info['name']}: {e}")
            return None
    
    async def cleanup_temp_emojis(self):
        """Clean up all temporary emojis."""
        for emoji in self.temp_emojis:
            try:
                await emoji.delete()
            except Exception as e:
                print(f"Failed to delete temp emoji {emoji.name}: {e}")
        
        self.temp_emojis.clear()
    
    async def process_external_emojis(self, content, destination_guild):
        """Process external emojis by creating temporary ones and replacing content."""
        external_emojis = self.extract_external_emojis(content, destination_guild)
        
        if not external_emojis:
            return content
        
        processed_content = content
        emoji_mapping = {}
        
        for emoji_info in external_emojis:
            # Check if we already created this emoji
            if emoji_info['id'] not in emoji_mapping:
                temp_emoji = await self.create_temp_emoji(destination_guild, emoji_info)
                if temp_emoji:
                    emoji_mapping[emoji_info['id']] = temp_emoji
                    
                    # Add rate limit delay
                    await asyncio.sleep(1)
        
        # Replace external emoji references with temp emoji references
        for emoji_info in external_emojis:
            if emoji_info['id'] in emoji_mapping:
                temp_emoji = emoji_mapping[emoji_info['id']]
                temp_emoji_str = f"<{'a' if emoji_info['animated'] else ''}:{temp_emoji.name}:{temp_emoji.id}>"
                processed_content = processed_content.replace(emoji_info['full_match'], temp_emoji_str)
        
        return processed_content
    
    def check_file_sizes(self, attachments):
        """Check if any attachments exceed webhook file size limit (8MB)."""
        WEBHOOK_FILE_LIMIT = 8 * 1024 * 1024  # 8MB in bytes
        large_files = []
        small_files = []
        
        for attachment in attachments:
            if attachment.size > WEBHOOK_FILE_LIMIT:
                large_files.append(attachment)
            else:
                small_files.append(attachment)
        
        return large_files, small_files
    
    async def send_bot_message_with_embed(self, channel, content, large_files, author, thread=None):
        """Send a message using bot account with author info in embed for large files."""
        # Create embed with author information
        embed = discord.Embed(
            description=content if content else None,
            color=0x5865F2,
            timestamp=datetime.utcnow()
        )
        embed.set_author(
            name=author.display_name,
            icon_url=author.display_avatar.url if author.display_avatar else None
        )
        embed.set_footer(text=f"元の投稿者: {author.name}")
        
        if large_files:
            file_info = "\n".join([f"📎 {file.filename} ({file.size / (1024*1024):.1f}MB)" for file in large_files])
            embed.add_field(name="大きなファイル", value=file_info, inline=False)
        
        # Send message with large files
        files_to_send = [await attachment.to_file() for attachment in large_files]
        
        if thread:
            return await thread.send(embed=embed, files=files_to_send)
        else:
            return await channel.send(embed=embed, files=files_to_send)
    
    async def send_webhook_message(self, webhook, content, attachments, author, thread, embed=None):
        """Send a message via webhook and restore original content after escaping mentions and processing external emojis."""
        try:
            # Process external emojis first
            processed_content = await self.process_external_emojis(content, webhook.guild)
            
            # Check file sizes
            large_files, small_files = self.check_file_sizes(attachments)
            
            # If there are large files, send them via bot account
            if large_files:
                # Send large files via bot account with embed
                bot_message = await self.send_bot_message_with_embed(
                    webhook.channel, processed_content, large_files, author, thread
                )
                
                # If there are also small files, send them via webhook
                if small_files:
                    if self.has_mentions(processed_content):
                        escaped_content = self.escape_mentions(processed_content)
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
                    else:
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
            
            # Normal webhook sending for small files only
            if self.has_mentions(processed_content):
                escaped_content = self.escape_mentions(processed_content)
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
        await self.cleanup_temp_emojis()

        if ctx.interaction:
            await ctx.interaction.followup.send(archive_channel.mention + "に転送完了しました。")
        else:
            await ctx.send(archive_channel.mention + "に転送完了しました。")



async def setup(bot):
    await bot.add_cog(ArchiveChannel(bot))
