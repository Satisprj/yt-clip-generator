# Publishing clips to social platforms

| Platform | What you need | Posting limits for new apps |
|----------|---------------|-----------------------------|
| YouTube Shorts | YouTube channel + Google Cloud OAuth client (below) | **Private** until Google approves the API audit; ~6 uploads/day |
| Facebook Reels | A Facebook **Page** + Meta developer app | Public from day one on your own Page (posting to personal profiles is impossible) |
| Instagram Reels | Instagram **Business/Creator** account linked to that Page + the same Meta app | Public from day one on your own account |
| TikTok | TikTok account + TikTok for Developers app | **Private ("only me")** until TikTok audits the app |

All four use the same **Publish** button: pick the platforms, write one title and caption
(or keep the AI's), and the app adapts it to each platform (YouTube title ≤100 characters
plus `#Shorts`; Instagram and TikTok put the title at the start of the caption).

## YouTube setup (about 15 minutes, once)

You create your own Google Cloud project; the app then uploads to **your** channel with
**your** permission. Nothing is shared with anyone else.

### 1. Create a project and enable the API

1. Open https://console.cloud.google.com and sign in with the Google account that owns
   your YouTube channel.
2. Click the project picker at the top, then **New project**. Name it e.g. `Clip Generator`
   and click **Create**. Make sure it is selected.
3. Go to **APIs & Services → Library**, search for **YouTube Data API v3**, open it and click
   **Enable**.

### 2. Configure the consent screen

1. Go to **APIs & Services → OAuth consent screen** (also called *Google Auth Platform*).
2. Click **Get started**. App name `Clip Generator`, support email: your email.
3. Audience: **External**. Contact email: your email. Agree and **Create**.
4. Under **Audience → Test users**, click **Add users** and add your own Google account.

> **Refresh every 7 days?** While the app's publishing status is **Testing**, Google expires
> the sign-in after 7 days, so you'd click **Connect** again weekly. To avoid that, click
> **Publish app** under *Audience* (status *In production*). Because the app isn't verified
> by Google, the sign-in shows a warning screen ("Google hasn't verified this app"). That's
> expected for your own private app: click **Advanced → Go to Clip Generator (unsafe)**.

### 3. Create the OAuth client

1. Go to **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type: **Web application**. Name: `Clip Generator local`.
3. Under **Authorized redirect URIs**, add exactly:
   `http://localhost:5173/api/integrations/youtube/callback`
4. Click **Create**, then copy the **Client ID** and **Client secret**.

### 4. Add the credentials to the app

Edit `backend\.env` and add:

```
GOOGLE_CLIENT_ID=<your client id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<your client secret>
```

Restart the app (close the three windows and run `scripts\dev.ps1` again).

### 5. Connect your channel

In the app, open **Settings → YouTube Shorts → Connect**, choose your account and channel,
and allow **"Manage your YouTube videos"** (needed to upload). You return to Settings with the
channel shown as connected.

### 6. Publish

- **One clip**: **📤 Publish** on the clip. Edit the title/caption if you like (the same
  text is used on every selected platform), pick the visibility, publish.
- **All clips of a job**: **📤 Publish all** above the clips (each clip keeps its own
  AI-written title, description and hashtags).
- Each clip shows its status per platform: waiting → uploading % → ✓ published (link) or
  ✗ failed (reason + retry). A clip is never posted twice to the same account.

## YouTube limits to know

- **Private until audited.** Videos uploaded through a Google Cloud project that hasn't
  passed YouTube's API audit are locked to **private**. You can still review them on YouTube,
  but can't make them public. To lift this, submit the free *YouTube API Services Audit and
  Quota Extension* form (search for it in the YouTube API documentation); it reviews how the
  app uses the API and can take days to weeks.
- **About 6 uploads per day.** Each upload costs ~1,600 of the default 10,000 daily quota
  units. The quota resets at midnight Pacific Time. The audit form can also raise it.
- **Shorts**: vertical videos up to 3 minutes are shown as Shorts; the app also adds
  `#Shorts` to the description.
- Uploads run in the same worker as video processing, one at a time: while a long video is
  being processed, uploads wait their turn.

## Troubleshooting

| Message | Fix |
|---------|-----|
| "YouTube isn't set up yet" | Add `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` to `backend\.env` and restart |
| `redirect_uri_mismatch` on Google's page | The redirect URI in step 3.3 must match `GOOGLE_REDIRECT_URI` exactly (including `http`, port 5173 and the path) |
| "Access blocked: app has not completed verification" | Add your account as a **test user** (step 2.4), or publish the app (step 2 note) |
| "YouTube access expired or was revoked" | Settings → Connect again (common weekly while the app is in *Testing*) |
| "The daily YouTube upload quota is used up" | Wait until tomorrow (Pacific Time), or request more quota |
| Published but private | Expected until the audit is approved (see limits above) |

## Facebook Page + Instagram setup (one Meta app, about 20 minutes)

Before you start: you need a **Facebook Page** you manage, and for Instagram an Instagram
**Business or Creator** account **linked to that Page** (Instagram app → Settings → Account
type and tools → switch to professional; then link it to the Page in the Page's settings).

1. Open https://developers.facebook.com/apps and click **Create app**.
2. Use case: choose the one for managing Pages / Instagram content (Facebook Login for
   Business). App type **Business**. Name it `Clip Generator`.
3. In the app, add the products **Facebook Login for Business** and **Instagram**
   (Instagram API with Facebook Login).
4. Under **Facebook Login for Business → Settings**, add the redirect URI:
   `http://localhost:5173/api/integrations/meta/callback`
   (Meta accepts `localhost` while the app is in **Development** mode.)
5. Under **App settings → Basic**, copy the **App ID** and **App secret** into `backend\.env`:
   ```
   META_APP_ID=<app id>
   META_APP_SECRET=<app secret>
   ```
6. Restart the app, open **Settings → Connect with Facebook**, sign in, and select your Page
   (and its Instagram account) when Meta asks which ones to share. Allow all requested
   permissions (`pages_manage_posts`, `instagram_content_publish`, …).

You stay in **Development mode**: that's fine because you only post to accounts you own
(you are the app's admin). App Review is only needed to let *other people* connect their
accounts. If you manage several Pages, set `META_PAGE_ID` to the one to use.

**How posting works:** Facebook Reels are uploaded and published on the Page (choosing
*Private* saves the Reel as a draft on the Page instead). Instagram Reels are uploaded,
processed by Instagram (usually under a minute), then published, and also shared to the feed.
The Page token doesn't expire, so you don't need to reconnect.

## TikTok setup (about 20 minutes)

1. Open https://developers.tiktok.com, log in, and create an app (**Manage apps → Connect an app**).
2. Add the products **Login Kit** and **Content Posting API**; enable **Direct Post**.
3. Scopes: `user.info.basic` and `video.publish`.
4. Redirect URI (Login Kit → Web): `http://localhost:5173/api/integrations/tiktok/callback`.
   If TikTok only accepts `https` addresses, use an HTTPS tunnel (e.g. `ngrok http 5173` or
   Cloudflare Tunnel), register `https://<your-tunnel>/api/integrations/tiktok/callback`, and
   set the same value as `TIKTOK_REDIRECT_URI` in `backend\.env`.
5. Add your TikTok account under **Sandbox / Target users** if TikTok asks for test users.
6. Copy the **Client key** and **Client secret** into `backend\.env`:
   ```
   TIKTOK_CLIENT_KEY=<client key>
   TIKTOK_CLIENT_SECRET=<client secret>
   ```
7. Restart the app and use **Settings → TikTok → Connect**.

**Limits:** until TikTok approves your app's audit, posts can only be **private ("only
me")**; the app picks the most open privacy level TikTok allows and falls back to private.
TikTok also limits how many videos an account can post per day through the API. Access
tokens last a day and are refreshed automatically.

## What has and hasn't been verified

The four connectors follow each platform's published API flow and are covered by automated
tests against fake servers that mimic those flows (sign-in, token refresh, chunked or
resumable uploads, processing status, errors). They have **not yet been run against the real
platforms with your accounts**, because that needs the developer apps above. After you add
the keys, connect each account and publish one clip: if a platform answers differently from
its documentation, the clip shows the platform's exact error message, which tells us what to
adjust.
