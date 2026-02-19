import os
import requests
import json
from datetime import datetime
from supabase import create_client, Client
import time

# =============================
# Supabase setup
# =============================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# =============================
# CONFIG — CHANGE THIS
# =============================
INSTAGRAM_USERNAME = "realestateduo.pnw"  # no @
TABLE_NAME = "instagram_posts"
STORAGE_BUCKET = "instagram-images"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "X-IG-App-ID": "936619743392459",
}

# =============================
# Fetch Instagram JSON
# =============================
def fetch_instagram_json(username):
    urls = [
        f"https://www.instagram.com/{username}/?__a=1&__d=dis",
        f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
    ]

    for url in urls:
        try:
            print(f"Trying endpoint: {url}")
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                print("✓ JSON fetched")
                return r.json()
            else:
                print(f"✗ Status {r.status_code}")
        except Exception as e:
            print(f"✗ Error: {e}")

    return None

# =============================
# Parse posts
# =============================
def parse_posts(data):
    user = None

    if "graphql" in data:
        user = data["graphql"]["user"]
    elif "data" in data and "user" in data["data"]:
        user = data["data"]["user"]

    if not user:
        return []

    edges = user["edge_owner_to_timeline_media"]["edges"]
    posts = []

    for edge in edges[:12]:
        node = edge["node"]

        shortcode = node["shortcode"]
        is_video = node["is_video"]

        media_url = (
            node.get("video_url")
            if is_video
            else node.get("display_url")
        )

        caption_edges = node["edge_media_to_caption"]["edges"]
        caption = caption_edges[0]["node"]["text"] if caption_edges else ""

        likes = (
            node.get("edge_liked_by", {}).get("count")
            or node.get("edge_media_preview_like", {}).get("count", 0)
        )

        posted_at = datetime.fromtimestamp(
            node["taken_at_timestamp"]
        ).isoformat()

        posts.append({
            "shortcode": shortcode,
            "post_url": f"https://www.instagram.com/p/{shortcode}/",
            "media_url": media_url,
            "caption": caption[:500],
            "likes": likes,
            "is_video": is_video,
            "posted_at": posted_at,
        })

    return posts

# =============================
# Download media
# =============================
def download_media(url, path):
    r = requests.get(url, timeout=20)
    if r.status_code == 200:
        with open(path, "wb") as f:
            f.write(r.content)
        return True
    return False

# =============================
# Upload to Supabase Storage
# =============================
def upload_media(local_path, remote_path):
    with open(local_path, "rb") as f:
        supabase.storage.from_(STORAGE_BUCKET).upload(
            remote_path,
            f.read(),
            file_options={"upsert": "true"}
        )

    return supabase.storage.from_(STORAGE_BUCKET).get_public_url(remote_path)

# =============================
# Save to DB
# =============================
def save_post(post, media_url):
    payload = {
        "shortcode": post["shortcode"],
        "post_url": post["post_url"],
        "image_url": media_url,
        "caption": post["caption"],
        "likes": post["likes"],
        "is_video": post["is_video"],
        "posted_at": post["posted_at"],
        "scraped_at": datetime.utcnow().isoformat(),
    }

    try:
        supabase.table(TABLE_NAME).insert(payload).execute()
    except:
        supabase.table(TABLE_NAME).update({
            "likes": post["likes"],
            "caption": post["caption"],
            "scraped_at": datetime.utcnow().isoformat(),
        }).eq("shortcode", post["shortcode"]).execute()

# =============================
# Cleanup old posts
# =============================
def cleanup_old_posts(keep=12):
    res = supabase.table(TABLE_NAME)\
        .select("id, image_url")\
        .order("posted_at", desc=True)\
        .execute()

    if len(res.data) <= keep:
        return

    for row in res.data[keep:]:
        supabase.table(TABLE_NAME).delete().eq("id", row["id"]).execute()

# =============================
# MAIN
# =============================
def main():
    print("=" * 50)
    print("Instagram Scraper - JSON Method (No Rate Limits)")
    print(f"Target: @{INSTAGRAM_USERNAME}")
    print("=" * 50)

    data = fetch_instagram_json(INSTAGRAM_USERNAME)
    if not data:
        print("❌ Failed to fetch Instagram data")
        return

    posts = parse_posts(data)
    print(f"Found {len(posts)} posts")

    for post in posts:
        ext = ".mp4" if post["is_video"] else ".jpg"
        local = f"temp_{post['shortcode']}{ext}"
        remote = f"{INSTAGRAM_USERNAME}/{post['shortcode']}{ext}"

        if download_media(post["media_url"], local):
            url = upload_media(local, remote)
            save_post(post, url)
            os.remove(local)
            print(f"✓ Saved {post['shortcode']}")

        time.sleep(1.5)

    cleanup_old_posts()
    print("✓ Done")

if __name__ == "__main__":
    main()
