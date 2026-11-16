"""One caption for every platform, adapted to each platform's rules.

The clip's AI-written title, description and hashtags are the default; the user can override
the title and caption once and the same text goes to every platform.
"""

import re
from dataclasses import dataclass

from app.models import Platform


@dataclass(frozen=True)
class PostContent:
    title: str        # YouTube title (other platforms put everything in the caption)
    caption: str      # description / caption text, hashtags included
    tags: list[str]   # keywords without '#' (YouTube tags)


# Character limits per platform.
LIMITS = {
    Platform.YOUTUBE: {"title": 100, "caption": 5000, "tags": 500},
    Platform.INSTAGRAM: {"title": 0, "caption": 2200, "tags": 0},
    Platform.FACEBOOK: {"title": 255, "caption": 5000, "tags": 0},
    Platform.TIKTOK: {"title": 0, "caption": 2200, "tags": 0},
}


def default_caption(description: str, hashtags: list[str]) -> str:
    return "\n\n".join(part for part in (description.strip(), " ".join(hashtags)) if part)


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return f"{cut}…"


def tags_for(platform: Platform, hashtags: list[str]) -> list[str]:
    """Keyword tags without '#', within the platform's total length (YouTube only)."""
    limit = LIMITS[platform]["tags"]
    kept, total = [], 0
    for tag in (h.lstrip("#") for h in hashtags):
        if not tag:
            continue
        if total + len(tag) + 1 > limit:
            break
        kept.append(tag)
        total += len(tag) + 1
    return kept


def build_post_content(platform: Platform, *, title: str, caption: str,
                       hashtags: list[str]) -> PostContent:
    limits = LIMITS[platform]

    if platform is Platform.YOUTUBE:
        # YouTube rejects angle brackets in titles and descriptions.
        title = re.sub(r"[<>]", "", title)
        caption = re.sub(r"[<>]", "", caption)
        if "#shorts" not in caption.lower() and "#shorts" not in title.lower():
            caption = f"{caption} #Shorts".strip()
        return PostContent(_truncate(title, limits["title"]),
                           _truncate(caption, limits["caption"]), tags_for(platform, hashtags))

    # Instagram / TikTok have no title: lead with it in the caption.
    text = caption
    if not limits["title"] and title and not caption.startswith(title):
        text = f"{title}\n\n{caption}"
    return PostContent(_truncate(title, limits["title"]) if limits["title"] else "",
                       _truncate(text, limits["caption"]), [])
