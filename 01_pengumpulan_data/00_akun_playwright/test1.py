import asyncio
import csv
import random
from pytok.tiktok import PyTok
from pytok.accounts import AccountsPool

async def scrape_video_comments(
    username,
    video_id, 
    output_file="comments.csv", 
    max_comments=50,
    min_delay=30,      # ⬆️ LEBIH LAMA
    max_delay=60       # ⬆️ LEBIH LAMA
):
    """
    Scrape komentar dengan delay EXTRA LAMA (comments lazy-load)
    """
    
    pool = AccountsPool()
    # ⬇️ Custom PyTok dengan request_delay lebih besar
    async with await PyTok.from_pool(pool, request_delay=10) as api:
        video = api.video(id=video_id, username=username)
        video_info = await video.info()
        
        print(f"📺 Video dari @{username}")
        print(f"📝 Deskripsi: {video_info.get('desc', 'No description')[:100]}")
        print(f"❤️ Likes: {video_info.get('stats', {}).get('diggCount', 0)}")
        print(f"💬 Total Comments: {video_info.get('stats', {}).get('commentCount', 0)}")
        print(f"⏱️  Delay: {min_delay}-{max_delay} detik (EXTRA SAFE)")
        print(f"⚙️  Request delay: 10 detik")
        print(f"\n🔄 Mulai scraping... (ini akan lama!)\n")
        
        comments_data = []
        comment_count = 0
        
        try:
            async for comment in video.comments(count=max_comments):
                # Delay random EXTRA LAMA
                delay = random.uniform(min_delay, max_delay)
                print(f"  ⏳ Tunggu {delay:.1f}s...", end="\r")
                await asyncio.sleep(delay)
                
                comment_data = await comment.info()
                author = comment_data.get('author', {})
                stats = comment_data.get('stats', {})
                
                row = {
                    'comment_id': comment_data.get('id'),
                    'author_name': author.get('uniqueId', 'unknown'),
                    'author_id': author.get('id', ''),
                    'text': comment_data.get('text', '')[:500],
                    'likes': stats.get('diggCount', 0),
                    'replies': stats.get('replyCount', 0),
                    'created_at': comment_data.get('createTime', ''),
                }
                
                comments_data.append(row)
                comment_count += 1
                
                # Progress
                print(f"  ✅ [{comment_count}] @{author.get('uniqueId', '?')}: {comment_data.get('text', '')[:50]}...")
        
        except Exception as e:
            print(f"⚠️ Error saat scraping: {e}")
        
        # Save CSV
        if comments_data:
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=comments_data[0].keys())
                writer.writeheader()
                writer.writerows(comments_data)
            
            total_time = comment_count * (min_delay + max_delay) / 2
            print(f"\n✅ SELESAI!")
            print(f"   📊 Total: {comment_count} comments")
            print(f"   📁 File: {output_file}")
        else:
            print("❌ Tidak ada comments yang berhasil di-scrape")

async def main():
    username = "tirtacipeng"
    video_id = "7678524985136254229"
    
    await scrape_video_comments(
        username=username,
        video_id=video_id, 
        output_file="comments_tirtacipeng.csv", 
        max_comments=20,   # ⬇️ START KECIL DULU (20 comments)
        min_delay=30,
        max_delay=60
    )

if __name__ == "__main__":
    asyncio.run(main())