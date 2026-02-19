import os
import requests
import json
from datetime import datetime
from supabase import create_client, Client
import time
import re

# Initialize Supabase
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ⚠️ CONFIGURATION - CHANGE THIS FOR YOUR CLIENT
INSTAGRAM_USERNAME = "realestateduo.pnw"  # ← CHANGE THIS!
TABLE_NAME = "instagram_posts"
STORAGE_BUCKET = "instagram-images"

def get_instagram_posts_json(username):
    """Fetch Instagram posts using public JSON endpoint"""
    
    # Method 1: Try direct JSON API
    url = f"https://www.instagram.com/{username}/?__a=1&__d=dis"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    }
    
    try:
        print(f"Fetching Instagram data for @{username}...")
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            # Try to parse as JSON first
            try:
                data = response.json()
                print("✓ Got JSON response directly")
                return data
            except:
                # If not JSON, try to extract from HTML
                html = response.text
                
                # Look for embedded JSON in script tags
                patterns = [
                    r'window\._sharedData\s*=\s*({.+?});</script>',
                    r'window\.__additionalDataLoaded\([^,]+,\s*({.+?})\);</script>',
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, html)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            print("✓ Extracted JSON from HTML")
                            return data
                        except:
                            continue
                
                print("⚠️ Could not parse JSON from response")
                return None
        else:
            print(f"❌ HTTP {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ Error fetching data: {e}")
        return None

def parse_instagram_data(data):
    """Parse Instagram JSON to extract posts"""
    try:
        user_data = None
        
        # Try multiple JSON structures (Instagram changes these often)
        possible_paths = [
            # Structure 1: Direct graphql
            lambda d: d.get('graphql', {}).get('user'),
            # Structure 2: data.user
            lambda d: d.get('data', {}).get('user'),
            # Structure 3: entry_data
            lambda d: d.get('entry_data', {}).get('ProfilePage', [{}])[0].get('graphql', {}).get('user'),
            # Structure 4: items (newer API)
            lambda d: d.get('items', [{}])[0] if 'items' in d else None
        ]
        
        for path_func in possible_paths:
            try:
                user_data = path_func(data)
                if user_data:
                    break
            except:
                continue
        
        if not user_data:
            print("❌ Could not find user data in response")
            print("Response keys:", list(data.keys())[:5] if isinstance(data, dict) else "Not a dict")
            return []
        
        # Extract posts/edges
        edges = []
        if 'edge_owner_to_timeline_media' in user_data:
            edges = user_data['edge_owner_to_timeline_media'].get('edges', [])
        elif 'edge_felix_video_timeline' in user_data:
            edges = user_data['edge_felix_video_timeline'].get('edges', [])
        elif 'media' in user_data:
            # Convert media items to edge format
            edges = [{'node': item} for item in user_data['media'].get('nodes', [])]
        
        if not edges:
            print("❌ No posts found")
            return []
        
        posts = []
        print(f"Found {len(edges)} posts, processing latest 12...")
        
        for edge in edges[:12]:
            node = edge.get('node', {})
            
            shortcode = node.get('shortcode')
            if not shortcode:
                continue
            
            # Get media URL
            display_url = node.get('display_url') or node.get('thumbnail_src')
            is_video = node.get('is_video', False)
            
            if is_video:
                media_url = node.get('video_url', display_url)
            else:
                media_url = display_url
            
            if not media_url:
                print(f"⚠️ No media URL for {shortcode}, skipping")
                continue
            
            # Get caption
            caption = ""
            caption_edges = node.get('edge_media_to_caption', {}).get('edges', [])
            if caption_edges:
                caption = caption_edges[0].get('node', {}).get('text', '')
            elif 'caption' in node:
                caption = node.get('caption', '')
            
            # Limit caption length
            if caption:
                caption = caption[:500]
            
            # Get likes
            likes = 0
            if 'edge_liked_by' in node:
                likes = node['edge_liked_by'].get('count', 0)
            elif 'edge_media_preview_like' in node:
                likes = node['edge_media_preview_like'].get('count', 0)
            elif 'like_count' in node:
                likes = node.get('like_count', 0)
            
            # Get timestamp
            timestamp = node.get('taken_at_timestamp') or node.get('taken_at')
            if timestamp:
                posted_at = datetime.fromtimestamp(timestamp).isoformat()
            else:
                posted_at = datetime.utcnow().isoformat()
            
            posts.append({
                'shortcode': shortcode,
                'post_url': f"https://www.instagram.com/p/{shortcode}/",
                'media_url': media_url,
                'caption': caption,
                'likes': likes,
                'is_video': is_video,
                'posted_at': posted_at
            })
        
        print(f"✓ Successfully parsed {len(posts)} posts")
        return posts
    
    except Exception as e:
        print(f"❌ Error parsing data: {e}")
        import traceback
        traceback.print_exc()
        return []

def download_media(url, filename):
    """Download media file from URL"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(response.content)
            return True
        else:
            print(f"❌ Download failed: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Download error: {e}")
        return False

def upload_to_supabase_storage(local_path, remote_path):
    """Upload media file to Supabase Storage"""
    try:
        with open(local_path, 'rb') as f:
            file_data = f.read()
        
        # Determine content type
        content_type = "video/mp4" if local_path.endswith('.mp4') else "image/jpeg"
        
        supabase.storage.from_(STORAGE_BUCKET).upload(
            remote_path,
            file_data,
            file_options={"content-type": content_type, "upsert": "true"}
        )
        
        # Get public URL
        public_url = supabase.storage.from_(STORAGE_BUCKET).get_public_url(remote_path)
        return public_url
    
    except Exception as e:
        print(f"❌ Upload error: {e}")
        return None

def save_to_database(post_data, media_url):
    """Save post to database"""
    try:
        data = {
            'shortcode': post_data['shortcode'],
            'post_url': post_data['post_url'],
            'image_url': media_url,
            'caption': post_data['caption'],
            'likes': post_data['likes'],
            'is_video': post_data['is_video'],
            'posted_at': post_data['posted_at'],
            'scraped_at': datetime.utcnow().isoformat()
        }
        
        # Try insert
        supabase.table(TABLE_NAME).insert(data).execute()
        return True
        
    except Exception as e:
        # If duplicate, try update
        try:
            supabase.table(TABLE_NAME).update({
                'likes': post_data['likes'],
                'caption': post_data['caption'],
                'image_url': media_url,
                'scraped_at': datetime.utcnow().isoformat()
            }).eq('shortcode', post_data['shortcode']).execute()
            return True
        except Exception as e2:
            print(f"❌ Save error: {e2}")
            return False

def check_if_exists(shortcode):
    """Check if post exists in database"""
    try:
        response = supabase.table(TABLE_NAME)\
            .select('shortcode')\
            .eq('shortcode', shortcode)\
            .execute()
        return len(response.data) > 0
    except:
        return False

def delete_old_posts(keep_count=12):
    """Delete posts beyond the latest keep_count"""
    try:
        response = supabase.table(TABLE_NAME)\
            .select('id, shortcode, image_url')\
            .order('posted_at', desc=True)\
            .execute()
        
        all_posts = response.data
        
        if len(all_posts) <= keep_count:
            print(f"✓ Database has {len(all_posts)} posts (within limit)")
            return
        
        posts_to_delete = all_posts[keep_count:]
        print(f"\nCleaning up {len(posts_to_delete)} old posts...")
        
        for post in posts_to_delete:
            # Delete from database
            supabase.table(TABLE_NAME).delete().eq('id', post['id']).execute()
            
            # Delete from storage
            try:
                image_url = post['image_url']
                if STORAGE_BUCKET in image_url:
                    path_start = image_url.find(STORAGE_BUCKET) + len(STORAGE_BUCKET) + 1
                    storage_path = image_url[path_start:].split('?')[0]
                    supabase.storage.from_(STORAGE_BUCKET).remove([storage_path])
            except:
                pass
        
        print(f"✓ Kept {keep_count} latest posts")
        
    except Exception as e:
        print(f"❌ Cleanup error: {e}")

def main():
    print("=" * 60)
    print("Instagram Scraper - JSON Method (No Rate Limits)")
    print(f"Target: @{INSTAGRAM_USERNAME}")
    print(f"Method: Public JSON API")
    print("=" * 60)
    
    # Fetch Instagram data
    data = get_instagram_posts_json(INSTAGRAM_USERNAME)
    
    if not data:
        print("\n❌ Failed to fetch Instagram data")
        print("This could mean:")
        print("  1. Account is private")
        print("  2. Username is incorrect")
        print("  3. Instagram changed their API")
        return
    
    # Parse posts
    posts = parse_instagram_data(data)
    
    if not posts:
        print("\n❌ No posts found or parsing failed")
        return
    
    print(f"\n{'=' * 60}")
    print(f"Processing {len(posts)} posts...")
    print('=' * 60)
    
    success_count = 0
    updated_count = 0
    
    for idx, post in enumerate(posts, 1):
        shortcode = post['shortcode']
        exists = check_if_exists(shortcode)
        
        print(f"\n[{idx}/{len(posts)}] Post: {shortcode}")
        print(f"  Type: {'Video' if post['is_video'] else 'Image'}")
        print(f"  Likes: {post['likes']}")
        
        # Determine file extension
        file_ext = '.mp4' if post['is_video'] else '.jpg'
        local_file = f"temp_{shortcode}{file_ext}"
        remote_path = f"{INSTAGRAM_USERNAME}/{shortcode}{file_ext}"
        
        # Download media
        print(f"  {'Updating' if exists else 'Downloading'}...")
        if download_media(post['media_url'], local_file):
            # Upload to Supabase
            media_url = upload_to_supabase_storage(local_file, remote_path)
            
            if media_url:
                # Save to database
                if save_to_database(post, media_url):
                    if exists:
                        print(f"  ✅ Updated")
                        updated_count += 1
                    else:
                        print(f"  ✅ Added")
                        success_count += 1
                else:
                    print(f"  ❌ Failed to save to database")
            else:
                print(f"  ❌ Failed to upload to Supabase")
            
            # Clean up local file
            try:
                os.remove(local_file)
            except:
                pass
        else:
            print(f"  ❌ Failed to download")
        
        # Small delay between posts
        time.sleep(2)
    
    # Clean up old posts
    delete_old_posts(keep_count=12)
    
    print("\n" + "=" * 60)
    print("✓ Scraping Complete!")
    print(f"New posts added: {success_count}")
    print(f"Existing posts updated: {updated_count}")
    print(f"Total processed: {len(posts)}")
    print("=" * 60)

if __name__ == "__main__":
    main()
