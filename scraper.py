import os
import requests
import json
import re
from datetime import datetime
from supabase import create_client, Client
import time

# =============================
# Supabase
# =============================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# =============================
# CONFIG
# =============================
INSTAGRAM_USERNAME = "realestateduo.pnw"  # no @
TABLE_NAME = "instagram_posts"
STORAGE_BUCKET = "instagram-images"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "en-US,en;q=0.9",
}

# =============================
# Fetch HTML
# =============================
def fetch_profile_html(username):
    url = f"https://www.instagram.com/{username}/"
    r = requests.get(url, headers=HEADERS, timeout=15)

    if r.status_code != 200:
        print(f"❌ HTML fetch failed: {r.status_code}")
        return None

    return r.text

# =============================
# Extract embedded JSON
# =============================
def extract_json_from_html(html):
    match = re.search(
        r"window\._sharedData\s*=\s*(\{.*?\});",
        html
    )

    if not match:
        print("❌ Could not find embedded JSON")
        return None

    return json.loads(match.group(1))

# =============================
# Parse posts
# =============================
def parse_posts(data):
    try:
        user = data["entry_data"]["ProfilePage"][0]["graphql"]["user"]
        edges = user["edge_owner_to_timeline_media"]["edges"]

        posts = []
        for edge in edges[:12]:
            node = edge["node"]

            posts.append({
                "shortcode": node["shortcode"],
                "post_url": f"https://www.instagram.com/p/{node['shortcode']}/",
                "media_url": node["display_url"],
                "caption": node["edge_media_to_caption"]["edges"][0]["node"]["text"]
                if node["edge_media_to_caption"]["edges"] else "",
                "likes": node["edge_liked_by"]["count"],
                "is_video": node["is_video"],
                "posted_at": datetime.fromtimestamp(
                    node["taken_at_timestamp"]
                ).isoformat(),
            })

        return posts

    except Exception as e:
        print(f"❌ Parse error: {e}")
        return []

# =============================
# Media helpers
# =============================
def download_media(url, path):
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code == 200:
        with open(path, "wb") as f:
            f.write(r.content)
        return True
    return False

def upload_media(local, remote):
    with open(local, "rb") as f:
        supabase.storage.from_(STORAGE_BUCKET).upload(
            remote,
            f.read(),
            file_options={"upsert": "true"}
        )

    return supabase.storage.from_(STORAGE_BUCKET).get_public_url(remote)

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
# Main
# =============================
def main():
    print("=" * 50)
    print("Instagram Scraper - HTML Method (Stable)")
    print(f"Target: @{INSTAGRAM_USERNAME}")
    print("=" * 50)

    html = fetch_profile_html(INSTAGRAM_USERNAME)
    if not html:
        return

    data = extract_json_from_html(html)
    if not data:
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

        time.sleep(2)

    print("✓ Done")

if __name__ == "__main__":
    main()
