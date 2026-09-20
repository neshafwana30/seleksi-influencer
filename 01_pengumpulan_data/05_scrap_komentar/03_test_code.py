# ============================================================
# UPDATE: extract_comments_from_dom() - Line ~330
# ============================================================
# GANTI BAGIAN INI di 07_SCRAPE_COMMENTS.py

async def extract_comments_from_dom(page, root=None):
    """
    Extract comment TOP-LEVEL langsung dari DOM, bukan dari network response.

    PENTING: pas video dibuka via KLIK dari grid/swipe (SPA client-side routing,
    bukan full page navigate), TikTok kadang GAK fire network request baru buat
    comments — data-nya udah ke-preload/ke-embed dari state yang udah ada
    sebelumnya. Efeknya: network listener kita nangkep 0 response SAMA SEKALI
    (raw responses: 0), padahal comments KELIATAN jelas di DOM & bisa di-scroll.

    Makanya kita HARUS extract dari DOM juga sebagai sumber utama (bukan cuma
    fallback buat replies doang kayak sebelumnya).

    `root`: scope pencarian (biasanya comment_container) biar gak ke-mix sama
    elemen lain di halaman. Kalau None, cari di seluruh page.

    comment_id di-generate sintetis (hash dari username+text) karena elemen DOM
    gak selalu punya id asli yang gampang diambil — tapi ini AMAN buat dedup,
    soalnya hash-nya deterministic: comment yang sama (masih ke-render) bakal
    selalu generate id yang sama tiap kali function ini dipanggil ulang.
    """
    comments = []
    search_root = root if root is not None else page
    
    # ⭐ UPDATED SELECTORS dari diagnostic (Grid → Click → SPA routing struktur)
    # Dari: https://www.tiktok.com/@tirtacipeng/video/7686034517571669268
    # Working: [class*="CommentItemContainer"](7), [class*="CommentItem"](7), [class*="Comment"](33), li(9)
    item_selectors = [
        '[class*="CommentItemContainer"]',  # ✅ 7 found - specific container
        '[class*="CommentItem"]',           # ✅ 7 found - specific item
        '[class*="Comment"]',               # ✅ 33 found - general (catches replies too)
        'li',                               # ✅ 9 found - structural fallback
    ]
    
    items = []
    for sel in item_selectors:
        try:
            found = await search_root.query_selector_all(sel)
            if found:
                items = found
                break
        except Exception:
            continue
    
    for item in items:
        try:
            text_elem = await item.query_selector('[data-e2e="comment-content"]')
            if not text_elem:
                # Fallback selector kalau data-e2e beda
                text_elem = await item.query_selector('p[class*="content"], span[class*="content"]')
            if not text_elem:
                # Last resort: cari elemen pertama yang punya text panjang
                try:
                    potential_texts = await item.query_selector_all('p, span, div')
                    for elem in potential_texts:
                        text = await elem.text_content()
                        if text and len(text) > 10:  # Heuristic: comment text pasti >10 char
                            text_elem = elem
                            break
                except:
                    pass
            
            if not text_elem:
                continue
            
            text = (await text_elem.text_content() or "").strip()
            if not text or len(text) < 1:
                continue
            
            username = ""
            try:
                username_elem = await item.query_selector('[data-e2e="comment-username"]')
                if username_elem:
                    username = (await username_elem.text_content() or "").strip()
            except Exception:
                pass
            
            likes = "0"
            try:
                likes_elem = await item.query_selector('[data-e2e="comment-like-count"]')
                if likes_elem:
                    likes = (await likes_elem.text_content() or "0").strip()
            except Exception:
                pass
            
            # comment_id sintetis dari hash username+text (deterministic -> dedup aman)
            import hashlib
            raw_key = f"{username}|{text}"
            synthetic_id = "dom_" + hashlib.md5(raw_key.encode("utf-8")).hexdigest()[:16]
            
            comments.append({
                "comment_id": synthetic_id,
                "username": username,
                "text": text,
                "likes": likes,
                "replies": "0",
                "create_time": "",
            })
        except Exception:
            continue
    
    return comments