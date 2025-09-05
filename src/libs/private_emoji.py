import re
import uuid
import asyncio
import aiohttp
import discord


class PrivateEmojiProcessor:
    """Handle external emoji processing for message transfers."""

    def __init__(self):
        self.temp_emojis = []  # Track temporary emojis for cleanup

    def extract_external_emojis(self, content, destination_guild):
        """Extract external emojis that are not available in the destination guild."""
        emoji_pattern = re.compile(r'<(a?):(\w+):(\d+)>')
        external_emojis = []

        for match in emoji_pattern.finditer(content):
            animated = bool(match.group(1))
            name = match.group(2)
            emoji_id = int(match.group(3))

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
            temp_name = f"temp_{uuid.uuid4().hex[:8]}_{emoji_info['name']}"
            emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_info['id']}.{'gif' if emoji_info['animated'] else 'png'}"
            async with aiohttp.ClientSession() as session:
                async with session.get(emoji_url) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        temp_emoji = await guild.create_custom_emoji(name=temp_name, image=image_data)
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
            if emoji_info['id'] not in emoji_mapping:
                temp_emoji = await self.create_temp_emoji(destination_guild, emoji_info)
                if temp_emoji:
                    emoji_mapping[emoji_info['id']] = temp_emoji
                    await asyncio.sleep(1)

        for emoji_info in external_emojis:
            if emoji_info['id'] in emoji_mapping:
                temp_emoji = emoji_mapping[emoji_info['id']]
                temp_emoji_str = f"<{'a' if emoji_info['animated'] else ''}:{temp_emoji.name}:{temp_emoji.id}>"
                processed_content = processed_content.replace(emoji_info['full_match'], temp_emoji_str)

        return processed_content
