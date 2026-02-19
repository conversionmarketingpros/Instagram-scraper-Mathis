import instaloader
import os
import requests
from datetime import datetime
from supabase import create_client, Client
import time

# Initialize Supabase
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize Instaloader with conservative settings
L = instaloader.Instaloader(
    download_pictures=True,
    download_videos=True,
    download_video_thumbnails=False,
    compress_json=False,
    save_metadata=False,
    post_metadata_txt_pattern="",
    max_connection_attempts=3,
    request_timeout=300
)

# ⚠️ CONFIGURATION - CHANGE THIS FOR YOUR NEW CLIENT
INSTAGRAM_USERNAME = "realestateduo.pnw"  # ← CHANGE THIS!
TABLE_NAME = "instagram_posts"
STORAGE_BUCKET = "instagram-images"

def get_latest_post_from_db():
    """Get the most recent post date from database"""
    try:
        response = supabase.table(TABLE_NAME)\
            .select('posted_at')\
            .order('posted_at', desc=True)\
            .limit(1)\
            .execute()
        
        if response.data and len(response.data) > 0:
            return datetime.fromisoformat(response.data[0]['posted_at'].replace('Z', '+00:00'))
        return None
    except Exception as e:
        print(f"Error getting latest post: {e}")
        return None

def download_latest_posts(username, limit=12):
    """Download only the latest posts from Instagram with LONG delays"""
    print(f"Fetching latest posts from @{username}...")
    print("⚠️ Using SLOW mode with long delays to avoid rate limits")
    
    latest_db_date = get_latest_post_from_db()
    if latest_db_date:
        print(f"Latest post in database: {latest_db_date}")
    else:
        print("No posts in database yet, fetching recent posts...")
    
    try:
        # LONG initial delay before starting
        print("⏳ Waiting 10 seconds before fetching profile...")
        time.sleep(10)
        
        profile = instaloader.Profile.from_username(L.context, username)
        posts = []
        
        print(f"✓ Profile loaded. Starting to fetch posts slowly...")
        
        post_count = 0
        for post in profile.get_posts():
            # If we already have this post or older, stop
            if latest_db_date and post.date_utc <= latest_db_date:
                print(f"Reached existing posts (post from {post.date_utc}), stopping...")
                break
            
            if len(posts) >= limit:
                break
            
            # Get the post URL
            post_url = f"https://www.instagram.com/p/{post.shortcode}/"
            
            post_data = {
                'shortcode': post.shortcode,
                'post_url': post_url,
                'caption': post.caption if post.caption else "",
                'likes': post.likes,
                'date': post.date_utc.isoformat(),
                'is_video': post.is_video
            }
            
            posts.append(post_data)
            post_count += 1
            
            # Download the media (image or video)
            print(f"\n[{post_count}/{limit}] Downloading {'video' if post.is_video else 'image'}: {post.shortcode}")
            
            # LONG delay before downloading (15 seconds)
            print("⏳ Waiting 15 seconds before download...")
            time.sleep(15)
            
            try:
                L.download_post(post, target=f"temp_{post.shortcode}")
                print(f"✓ Downloaded successfully")
            except Exception as e:
                print(f"⚠️ Error downloading {post.shortcode}: {e}")
                # If download fails, wait even longer before continuing
                print("⏳ Error occurred, waiting extra 20 seconds...")
                time.sleep(20)
                continue
            
            # EXTRA LONG delay every 2 posts (60 seconds!)
            if post_count % 2 == 0:
                print(f"⏳ Downloaded {post_count} posts. Taking a 60-second break to avoid rate limits...")
                time.sleep(60)
            else:
                # Regular delay between posts (20 seconds)
                print("⏳ Waiting 20 seconds before next post...")
                time.sleep(20)
        
        print(f"\n✓ Found {len(posts)} new posts")
        return posts
    
    except instaloader.exceptions.ConnectionException as e:
        print(f"❌ Connection error (possible rate limit): {e}")
        print("💡 Try again in 1-2 hours. Instagram has temporarily blocked requests.")
        return []
    except instaloader.exceptions.QueryReturnedNotFoundException:
        print(f"❌ User @{username} not found or account is private")
        return []
    except Exception as e:
        print(f"❌ Error fetching posts: {e}")
        return []

def upload_to_supabase_storage(local_path, remote_path):
    """Upload media file to Supabase Storage"""
    try:
        with open(local_path, 'rb') as f:
            file_data = f.read()
        
        # Determine content type
        content_type = "video/mp4" if local_path.endswith('.mp4') else "image/jpeg"
        
        response = supabase.storage.from_(STORAGE_BUCKET).upload(
            remote_path,
            file_data,
            file_options={"content-type": content_type, "upsert": "true"}
        )
        
        # Get public URL
        public_url = supabase.storage.from_(STORAGE_BUCKET).get_public_url(remote_path)
        return public_url
    
    except Exception as e:
        print(f"Error uploading to Supabase: {e}")
        return None

def save_to_database(post_data, media_url):
    """Save post metadata to Supabase database"""
    try:
        data = {
            'shortcode': post_data['shortcode'],
            'post_url': post_data['post_url'],
            'image_url': media_url,
            'caption': post_data['caption'],
            'likes': post_data['likes'],
            'is_video': post_data['is_video'],
            'posted_at': post_data['date'],
            'scraped_at': datetime.utcnow().isoformat()
        }
        
        # Insert (will fail if duplicate due to unique constraint)
        response = supabase.table(TABLE_NAME).insert(data).execute()
        return response
    
    except Exception as e:
        print(f"Error saving to database: {e}")
        return None

def check_if_exists(shortcode):
    """Check if post already exists in database"""
    try:
        response = supabase.table(TABLE_NAME)\
            .select('shortcode')\
            .eq('shortcode', shortcode)\
            .execute()
        return len(response.data) > 0
    except:
        return False

def cleanup_temp_files(shortcode):
    """Clean up temporary downloaded files"""
    import glob
    import shutil
    
    temp_folder = f"temp_{shortcode}"
    if os.path.exists(temp_folder):
        shutil.rmtree(temp_folder)

def find_media_file(temp_folder):
    """Find the main media file in temp folder"""
    if not os.path.exists(temp_folder):
        return None
    
    # Look for mp4 (video) or jpg (image)
    files = os.listdir(temp_folder)
    
    # Priority: mp4 > jpg
    for ext in ['.mp4', '.jpg', '.jpeg']:
        for f in files:
            if f.endswith(ext) and not f.endswith('_1.jpg'):  # Skip carousel extras
                return os.path.join(temp_folder, f)
    
    return None

def delete_old_posts(keep_count=12):
    """Delete posts beyond the latest keep_count"""
    try:
        # Get all posts ordered by date
        response = supabase.table(TABLE_NAME)\
            .select('id, shortcode, image_url')\
            .order('posted_at', desc=True)\
            .execute()
        
        all_posts = response.data
        
        if len(all_posts) <= keep_count:
            print(f"✓ Database has {len(all_posts)} posts (within limit of {keep_count})")
            return
        
        # Posts to delete (everything after position keep_count)
        posts_to_delete = all_posts[keep_count:]
        
        print(f"\nCleaning up old posts...")
        print(f"Deleting {len(posts_to_delete)} old posts...")
        
        for post in posts_to_delete:
            # Delete from database
            supabase.table(TABLE_NAME).delete().eq('id', post['id']).execute()
            
            # Delete from storage
            try:
                # Extract file path from URL
                image_url = post['image_url']
                if STORAGE_BUCKET in image_url:
                    # Parse the storage path
                    path_start = image_url.find(STORAGE_BUCKET) + len(STORAGE_BUCKET) + 1
                    storage_path = image_url[path_start:].split('?')[0]
                    supabase.storage.from_(STORAGE_BUCKET).remove([storage_path])
                    print(f"  ✓ Deleted old post: {post['shortcode']}")
            except Exception as e:
                print(f"  ⚠️ Could not delete storage file for {post['shortcode']}: {e}")
        
        print(f"✓ Cleanup complete! Kept {keep_count} latest posts")
        
    except Exception as e:
        print(f"Error during cleanup: {e}")

def main():
    print("=" * 60)
    print("Instagram Scraper Starting (SLOW MODE - No Rate Limits)")
    print(f"Target account: @{INSTAGRAM_USERNAME}")
    print(f"Database table: {TABLE_NAME}")
    print("=" * 60)
    print("\n⚠️ WARNING: This will take 10-20 minutes to complete!")
    print("⚠️ Using very long delays to avoid Instagram rate limits")
    print("=" * 60)
    
    # Download only new posts
    posts = download_latest_posts(INSTAGRAM_USERNAME, limit=12)
    
    if not posts:
        print("\n✓ No new posts found or rate limited.")
        print("If rate limited, wait 1-2 hours and try again.")
        # Still run cleanup to ensure we only have 12 posts
        delete_old_posts(keep_count=12)
        return
    
    print(f"\n{'=' * 60}")
    print(f"Processing {len(posts)} posts and uploading to Supabase...")
    print('=' * 60)
    
    new_posts_count = 0
    updated_posts_count = 0
    
    for idx, post in enumerate(posts, 1):
        shortcode = post['shortcode']
        
        print(f"\n[{idx}/{len(posts)}] Processing post: {shortcode}")
        
        # Check if exists
        exists = check_if_exists(shortcode)
        
        # Find the media file
        temp_folder = f"temp_{shortcode}"
        media_file = find_media_file(temp_folder)
        
        if not media_file:
            print(f"❌ No media file found for {shortcode}")
            cleanup_temp_files(shortcode)
            continue
        
        # Determine file extension
        file_ext = '.mp4' if post['is_video'] else '.jpg'
        remote_path = f"{INSTAGRAM_USERNAME}/{shortcode}{file_ext}"
        
        if exists:
            print(f"🔄 Post already exists, updating...")
            # Upload media (in case it changed)
            media_url = upload_to_supabase_storage(media_file, remote_path)
            if media_url:
                # Update existing post
                try:
                    supabase.table(TABLE_NAME).update({
                        'likes': post['likes'],
                        'caption': post['caption'],
                        'scraped_at': datetime.utcnow().isoformat()
                    }).eq('shortcode', shortcode).execute()
                    print(f"✅ Updated {shortcode}")
                    updated_posts_count += 1
                except Exception as e:
                    print(f"❌ Failed to update {shortcode}: {e}")
        else:
            # Upload new post
            print(f"📤 Uploading new post: {shortcode} ({'video' if post['is_video'] else 'image'})...")
            media_url = upload_to_supabase_storage(media_file, remote_path)
            
            if media_url:
                # Save to database
                result = save_to_database(post, media_url)
                if result:
                    print(f"✅ Added {shortcode}")
                    new_posts_count += 1
                else:
                    print(f"❌ Failed to save {shortcode} to database")
            else:
                print(f"❌ Failed to upload {shortcode}")
        
        # Cleanup
        cleanup_temp_files(shortcode)
    
    # Clean up old posts (keep only latest 12)
    delete_old_posts(keep_count=12)
    
    print("\n" + "=" * 60)
    print(f"✓ Scraping Complete!")
    print(f"New posts added: {new_posts_count}")
    print(f"Existing posts updated: {updated_posts_count}")
    print(f"Total in database: 12 (latest 12)")
    print("=" * 60)

if __name__ == "__main__":
    main()
