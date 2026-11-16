# Publishing clips to social platforms

| Platform | Status | What you need |
|----------|--------|---------------|
| YouTube Shorts | ✅ Available | A YouTube channel + a free Google Cloud project (below) |
| Instagram Reels | Later | Instagram Business/Creator account linked to a Facebook Page, Meta developer app |
| Facebook Reels | Later | A Facebook Page (personal profiles can't be posted to by apps), Meta developer app |
| TikTok | Later | TikTok developer app; posts are private ("only me") until TikTok audits the app |

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
