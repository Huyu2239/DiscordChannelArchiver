from datetime import datetime
import discord

WEBHOOK_FILE_LIMIT = 8 * 1024 * 1024  # 8MB in bytes


def check_file_sizes(attachments):
    """Separate attachments into large and small lists based on webhook limit."""
    large_files = []
    small_files = []
    for attachment in attachments:
        if attachment.size > WEBHOOK_FILE_LIMIT:
            large_files.append(attachment)
        else:
            small_files.append(attachment)
    return large_files, small_files


async def send_bot_message_with_embed(channel, content, large_files, author, thread=None):
    """Send a message using bot account with author info in embed for large files."""
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
        file_info = "\n".join(
            [f"📎 {file.filename} ({file.size / (1024*1024):.1f}MB)" for file in large_files]
        )
        embed.add_field(name="大きなファイル", value=file_info, inline=False)

    files_to_send = [await attachment.to_file() for attachment in large_files]
    if thread:
        return await thread.send(embed=embed, files=files_to_send)
    return await channel.send(embed=embed, files=files_to_send)
