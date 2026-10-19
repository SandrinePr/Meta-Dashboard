from pathlib import Path

from db.database import initialize_database
from search.engine import get_recent_posts
from analytics.performance import get_latest_post_month, get_monthly_top_posts
from ui.media import local_media_path

initialize_database()
recent = get_recent_posts(limit=20)
print("recent", len(recent))
for item in recent:
    path = local_media_path(item.entity_id)
    print(
        f"  id={item.entity_id} {item.platform} {(item.text or '')[:40]!r} "
        f"thumb={item.thumbnail_url[:48] if item.thumbnail_url else None} "
        f"file={path}"
    )

latest = get_latest_post_month()
print("latest month", latest)
if latest:
    for metric, posts in get_monthly_top_posts(*latest).items():
        print("metric", metric, "n", len(posts))
        for post in posts:
            path = local_media_path(post.post_id)
            print(f"  id={post.post_id} file={path} cdn={bool(post.thumbnail_url)}")

media = list(Path("data/media").glob("post_*.jpg"))
ids = {int(p.stem.split("_")[1]) for p in media}
print("media files", len(media), "min", min(ids) if ids else None, "max", max(ids) if ids else None)
thumbs = list(Path("data/thumbnails").glob("*.jpg"))
tids = {int(p.stem) for p in thumbs if p.stem.isdigit()}
print("thumbs", len(thumbs), "min", min(tids) if tids else None, "max", max(tids) if tids else None)
