import re


def has_mentions(content: str) -> bool:
    """Check if the message content contains any mentions (excluding channel mentions)."""
    if '@everyone' in content or '@here' in content:
        return True
    mention_pattern = re.compile(r'<@!?\d+>|<@&\d+>')
    return mention_pattern.search(content) is not None


def escape_mentions(content: str) -> str:
    """Escape mentions in the message content to prevent re-firing (excluding channel mentions)."""
    content = content.replace('@everyone', r'@\\everyone').replace('@here', r'@\\here')
    mention_pattern = re.compile(r'(<@!?|<@&)(\d+>)')
    return mention_pattern.sub(r'\1\\\\\2', content)
