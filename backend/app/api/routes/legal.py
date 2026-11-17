"""Minimal Terms of Service and Privacy Policy pages.

Developer platforms (TikTok, Meta) ask for these links when registering an app. The app is a
personal tool, so the pages say exactly that. They are served under /api/legal/ so they are
reachable through the same tunnel as the OAuth callbacks.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/legal", tags=["legal"])

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - Clip Generator</title>
<style>body{{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;
line-height:1.6;color:#1e293b}}h1{{font-size:1.5rem}}</style></head>
<body><h1>{title}</h1>{body}</body></html>"""

_TERMS = """
<p>Clip Generator is a personal tool run by its owner on their own computer. It turns the
owner's videos into short clips and, when the owner asks, posts them to the owner's own
social media accounts.</p>
<p>It is not offered to the public. Only the owner signs in and only the owner's accounts are
connected. The owner is responsible for having the rights to everything they post.</p>
<p>The software is provided as is, without warranty.</p>
"""

_PRIVACY = """
<p>Clip Generator runs only on its owner's computer and is used only by its owner.</p>
<ul>
<li><b>What is stored:</b> the access tokens that the connected platforms (YouTube, Facebook,
Instagram, TikTok) issue when the owner connects an account, plus the account's name and ID.
Tokens are encrypted and stored on the owner's computer.</li>
<li><b>How it is used:</b> only to upload the clips the owner chooses to publish, to the
owner's own accounts.</li>
<li><b>Sharing:</b> no data is sold or shared with anyone. Clips are sent only to the
platform the owner picks.</li>
<li><b>Deleting:</b> disconnecting an account in the app's Settings deletes its tokens and
revokes access where the platform supports it.</li>
</ul>
"""


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
def terms() -> str:
    return _PAGE.format(title="Terms of Service", body=_TERMS)


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy() -> str:
    return _PAGE.format(title="Privacy Policy", body=_PRIVACY)
