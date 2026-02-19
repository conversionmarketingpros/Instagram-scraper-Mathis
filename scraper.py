import os
import requests
import json
import re
from datetime import datetime
from supabase import create_client, Client
import time

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

INSTAGRAM_USERNAME = "realestateduo.pnw"  # no @
TABLE_NAME = "instagram_posts"
STORAGE_BUCKET = "instagram-images"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

def fetch_profile_html(username: str) -> str | None:
    url = f"https://www.instagram.com/{username}/"
    r = requests.get(url, headers=HEADERS, timeout=20)
    print(f"HTML status: {r.status_code}")
    if r.status_code != 200:
        print(f"❌ HTML fetch failed: {r.status_code}")
        return None
    return r.text

def extract_json_candidates(html: str):
    """
    Instagram rotates how it embeds data.
    We'll try multiple patterns, returning a list of candidate JSON blobs.
    """
    candidates = []

    # 1) Old: window._sharedData = {...};
    m = re.search(r"window\._sharedData\s*=\s*(\{.*?\})\s*;\s*</script>", html, re.S)
    if m:
        candidates.append(m.group(1))

    # 2) __additionalDataLoaded('key', {...});
    for m in re.finditer(r"__additionalDataLoaded\([^,]+,\s*(\{.*?\})\s*\);", html, re.S):
        candidates.append(m.group(1))

    # 3) Any script tag with ProfilePage / graphql user hints
    # (we grab a larger JSON-ish object that contains "graphql" and "user")
    for m in re.finditer(r"(\{[^<]*\"graphql\"[^<]*\})", html, re.S):
        candidates.append(m.group(1))

    # 4) application/ld+json (sometimes contains useful metadata)
    for m in re.finditer(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', html, re.S):
        candidates.append(m.group(1))

    return candidates

def try_parse_json(candidate: str):
    try:
        return json.loads(candidate)
    except:
        # Sometimes has escaped sequences or trailing junk; try a cleanup
        candidate = candidate.strip()
        # Remove trailing semicolons if any
        candidate = candidate.rstrip(";")
        try:
            return json.loads(candidate)
        except:
            return None

def find_profile_data(html: str):
    candidates = extract_json_candidates(html)
    if not candidates:
        return None

    for idx, c in enumerate(candidates, 1):
        data = try_parse_json(c)
        if not data:
            continue

        # We only accept objects that seem to include post data
        # Try several known structures:
        if isinstance(data, dict):
            if "entry_data" in data:
                return data
            if "graphql" in data and "user" in data.get("graphql", {}):
                return data
            if "data" in data and isinstance(data["data"], dict):
                return data

    return None

def parse_posts(data: dict):
    """
    Supports multiple structures:
    - entry_data.ProfilePage[0].graphql.user...
    - graphql.user...
    - data.user...
    """
    try:
        user = None

        if "entry_data" in data:
            user = data["entry_data"]["ProfilePage"][0]["graphql"]["user"]
        elif "graphql" in data and "user" in data["graphql"]:
            user = data["graphql"]["user"]
        elif "data" in data and "user" in data["data"]:
            user = data["data"]["user"]

        if not user:
            print("❌ Could not locate user object in parsed JSON.")
            return []

        media = user.get("edge_owner_to_timeline_media", {})
        edges = media.get("edges", [])
        if not edges:
            print("❌ No post edges found.")
            return []

        posts = []
        for edge in edges[:12]:
            node = edge.get("node", {})
            shortcode = node.get("shortcode")
            if not shortcode:
                continue

            is_video = bool(node.get("is_video", False))
            display_url = node.get("display_url")

            caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
            caption = caption_edges[0]["node"].get("text", "") if caption_edges else ""

            likes = (
                node.get("edge_liked_by", {}).get("count")
                or node.get("edge_media_preview_like", {}).get("count", 0)
            )

            ts = node.get("taken_at_timestamp")
            posted_at = datetime.fromtimestamp(ts).isoformat() if ts else datetime.utcnow().isoformat()

            posts.append({
                "shortcode": shortcode,
                "post_url": f"https://www.instagram.com/p/{shortcode}/",
                "media_url": display_url,
                "caption": caption[:500],
                "likes": likes,
                "is_video": is_video,
                "posted_at": posted_at,
            })

        return posts
    except Exception as e:
        print(f"❌ Parse error: {e}")
        return []

def download_media(url, path):
    if not url:
        return False
    r = requests.get(url, headers=HEADERS, timeout=30)
    if r.status_code == 200:
        with open(path, "wb") as f:
            f.write(r.content)
        return True
    print(f"❌ Media download failed: {r.status_code}")
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

def main():
    print("=" * 50)
    print("Instagram Scraper - HTML Multi-Extractor (No Login)")
    print(f"Target: @{INSTAGRAM_USERNAME}")
    print("=" * 50)

    html = fetch_profile_html(INSTAGRAM_USERNAME)
    if not html:
        return

    data = find_profile_data(html)
    if not data:
        print("❌ Could not find embedded JSON (Instagram likely served a login/blocked page).")
        # Print a small hint for debugging
        if "login" in html.lower() or "sign up" in html.lower():
            print("⚠️ Looks like Instagram returned a login wall page.")
        return

    posts = parse_posts(data)
    print(f"Found {len(posts)} posts")

    if not posts:
        print("❌ No posts parsed from the page.")
        return

    for post in posts:
        ext = ".mp4" if post["is_video"] else ".jpg"
        local = f"temp_{post['shortcode']}{ext}"
        remote = f"{INSTAGRAM_USERNAME}/{post['shortcode']}{ext}"

        if download_media(post["media_url"], local):
            public_url = upload_media(local, remote)
            save_post(post, public_url)
            os.remove(local)
            print(f"✓ Saved {post['shortcode']}")

        time.sleep(2)

    print("✓ Done")

if __name__ == "__main__":
    main()
