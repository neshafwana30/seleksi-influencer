"""
04_tambahan_scrap.py
====================
Bikin daftar video TAMBAHAN untuk di-scrape.

Per influencer (definisi sama dengan notebook ringkasan_per_influencer):
    n_valid = jumlah video di to_scrape.csv yang video_id-nya punya komentar di comments.csv
    kurang  = TARGET (20) - n_valid
Video target yang tidak punya komentar dianggap hilang (apa pun status kolom 'scraped').
Kalau kurang > 0 dan influencer masih punya video kesehatan yang belum pernah masuk
to_scrape.csv, diambil sebanyak 'kurang' (tidak lebih dari yang tersedia).

Stratifikasi dijaga seperti generate_scrape_list.py:
  - bulan = bulan dari metadata_video.scraped_at
  - tier  = per (username, bulan) urut comment/like turun; top len//2 = HIGH, sisanya MEDIUM
Tiap slot tambahan dipilih dengan cara:
  1. tier yg masih paling sedikit di set influencer itu (jaga rasio 50:50)
  2. bulan yg masih paling sedikit di set influencer itu (sebar antar bulan)
  3. sisanya acak dengan random.Random(42) -> reproducible
Kalau kombinasi (tier, bulan) itu tidak ada, turun ke tier lain / bulan lain.

to_scrape.csv TIDAK diubah. Output:
  05_scrap_komentar/to_scrape_pengganti.csv         <- daftar siap di-scrape
  05_scrap_komentar/detail_tambahan/log_penggantian.csv
  05_scrap_komentar/detail_tambahan/ringkasan_influencer.csv
  05_scrap_komentar/detail_tambahan/video_hilang.csv
"""

import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

SEED = 42
TARGET = 20

FOLDER_DATA = "01_pengumpulan_data"
PATH_COMMENTS = Path("05_scrap_komentar/hasil_07_comments/comments.csv")
PATH_HEALTH = Path("04_2_Klasifikasikan_konten_scraping/health_videos.csv")
PATH_META = Path("04_1_mendapatkan_metadata_akun/metadata_video.csv")
PATH_TO_SCRAPE = Path("05_scrap_komentar/to_scrape.csv")
OUT_DIR = Path("05_scrap_komentar")

OUTPUT_FIELDS = ["username", "video_id", "month", "engagement_tier", "scraped", "scraped_at"]


def find_base():
    for start in [Path(__file__).resolve().parent, Path.cwd()]:
        for p in [start, *start.parents]:
            if p.name == FOLDER_DATA:
                return p
            if (p / FOLDER_DATA).is_dir():
                return p / FOLDER_DATA
    raise FileNotFoundError(f"Folder '{FOLDER_DATA}' tidak ditemukan")


def parse_month(s):
    if s is None or pd.isna(s):
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).month
        except ValueError:
            continue
    return None


def parse_count(s):
    if s is None or pd.isna(s) or str(s).strip() == "":
        return 0
    s = str(s).strip().upper().replace(",", "")
    try:
        if "K" in s:
            return int(float(s.replace("K", "")) * 1_000)
        if "M" in s:
            return int(float(s.replace("M", "")) * 1_000_000)
        if "B" in s:
            return int(float(s.replace("B", "")) * 1_000_000_000)
        return int(float(s))
    except ValueError:
        return 0


def is_true(x):
    return str(x).strip().lower() in ("true", "1", "yes")


def read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")


def check_cols(name, df, cols):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"{name}.csv kurang kolom {missing}. Kolom yang ada: {list(df.columns)}")


def build_pool(health, meta):
    """Semua video kesehatan + bulan + tier (cara hitung sama dgn generate_scrape_list.py)."""
    meta_idx = meta.drop_duplicates("video_id").set_index("video_id")
    rows = []
    for _, hv in health.drop_duplicates("video_id").iterrows():
        vid = hv["video_id"]
        if vid not in meta_idx.index:
            continue
        m = meta_idx.loc[vid]
        month = parse_month(m.get("scraped_at", ""))
        if month is None:
            continue
        like = parse_count(m.get("like_count", 0))
        com = parse_count(m.get("comment_count", 0))
        rows.append({"username": hv["username"], "video_id": vid, "month": month,
                     "rate": com if like == 0 else com / like})
    pool = pd.DataFrame(rows)
    pool = pool.sort_values(["username", "month", "rate", "video_id"],
                            ascending=[True, True, False, True]).reset_index(drop=True)
    tiers = []
    for _, g in pool.groupby(["username", "month"], sort=False):
        split = len(g) // 2
        tiers.extend(["HIGH" if i < split else "MEDIUM" for i in range(len(g))])
    pool["tier"] = tiers
    return pool


def pilih_slot(rng, avail, tier_count, month_count):
    """Pilih 1 video: tier tersedikit -> bulan tersedikit -> acak."""
    tiers_order = sorted(["HIGH", "MEDIUM"], key=lambda t: (tier_count[t], t))
    for t in tiers_order:
        c = avail[avail.tier == t]
        if c.empty:
            continue
        status = "TIER_SEIMBANG" if t == tiers_order[0] else "TIER_LAIN (tier target habis)"
        low = min(month_count[m] for m in c.month.unique())
        c = c[c.month.map(lambda m: month_count[m] == low)]
        return rng.choice(sorted(c.video_id.tolist())), status
    return None, "HABIS"


def main():
    print("=" * 70)
    print("VIDEO TAMBAHAN SCRAPING (stratified bulan + tier, per influencer)")
    print("=" * 70)

    base = find_base()
    print(f"Base: {base}")

    comments = read(base / PATH_COMMENTS)
    health = read(base / PATH_HEALTH)
    meta = read(base / PATH_META)
    to_scrape = read(base / PATH_TO_SCRAPE)

    check_cols("comments", comments, ["video_id"])
    check_cols("health_videos", health, ["username", "video_id"])
    check_cols("metadata_video", meta, ["video_id", "like_count", "comment_count", "scraped_at"])
    check_cols("to_scrape", to_scrape, OUTPUT_FIELDS)

    # --- komentar valid: baris yg benar2 punya teks komentar ---
    text_col = "comment_text" if "comment_text" in comments.columns else None
    if text_col:
        valid_rows = comments[comments[text_col].str.strip() != ""]
    else:
        print("PERINGATAN: kolom comment_text tidak ada, semua baris dianggap komentar.")
        valid_rows = comments
    vids_valid = set(valid_rows["video_id"])
    print(f"comments {len(comments)} baris | berisi teks {len(valid_rows)} | "
          f"video dgn komentar valid {len(vids_valid)}")
    print(f"health {len(health)} | metadata {len(meta)} | to_scrape {len(to_scrape)}")

    to_scrape = to_scrape.copy()
    to_scrape["_scraped"] = to_scrape["scraped"].map(is_true)
    to_scrape["_valid"] = to_scrape["video_id"].isin(vids_valid)

    detail = base / OUT_DIR / "detail_tambahan"
    detail.mkdir(parents=True, exist_ok=True)
    hilang = to_scrape[~to_scrape._valid]
    hilang[OUTPUT_FIELDS].to_csv(detail / "video_hilang.csv", index=False, encoding="utf-8")
    print(f"\nTarget {len(to_scrape)} | punya komentar {int(to_scrape._valid.sum())} | "
          f"hilang (tidak ada komentar) {len(hilang)} "
          f"[scraped=True: {int((hilang._scraped).sum())}, scraped=False: {int((~hilang._scraped).sum())}]")

    pool = build_pool(health, meta)
    used = set(to_scrape["video_id"])
    cadangan = pool[~pool.video_id.isin(used)]
    print(f"Pool health {len(pool)} | cadangan (belum pernah dipilih) {len(cadangan)}")

    rng = random.Random(SEED)
    pool_idx = pool.set_index("video_id")
    out_rows, log_rows, ringkas = [], [], []

    # semua influencer yg ada di to_scrape
    for user in sorted(to_scrape.username.unique()):
        t = to_scrape[to_scrape.username == user]
        aktif = t[t._valid]
        n_valid = len(aktif)
        n_hilang = len(t) - n_valid
        n_pending = 0
        kurang = max(0, TARGET - n_valid)

        tier_count = Counter({"HIGH": 0, "MEDIUM": 0})
        month_count = Counter()
        for _, r in aktif.iterrows():
            tier_count[r.engagement_tier] += 1
            month_count[int(r.month)] += 1
        # bulan yg ada di pool user tapi belum terwakili -> hitungan 0
        avail = cadangan[cadangan.username == user]
        for m in pool[pool.username == user].month.unique():
            month_count.setdefault(int(m), 0)

        ditambah = 0
        for _ in range(kurang):
            vid, status = pilih_slot(rng, avail, tier_count, month_count)
            if vid is None:
                break
            r = pool_idx.loc[vid]
            avail = avail[avail.video_id != vid]
            tier_count[r.tier] += 1
            month_count[int(r.month)] += 1
            ditambah += 1
            out_rows.append({"username": user, "video_id": vid, "month": int(r.month),
                             "engagement_tier": r.tier, "scraped": "False", "scraped_at": ""})
            log_rows.append({"username": user, "video_id": vid, "month": int(r.month),
                             "tier": r.tier, "status": status,
                             "video_ada_komentar": n_valid, "hilang": n_hilang})

        ringkas.append({"username": user, "target_to_scrape": len(t), "video_ada_komentar": n_valid,
                        "video_hilang": n_hilang, "kurang": kurang,
                        "ditambah": ditambah, "masih_kurang": kurang - ditambah,
                        "sisa_cadangan": len(avail)})

    out = pd.DataFrame(out_rows, columns=OUTPUT_FIELDS)
    log = pd.DataFrame(log_rows)
    ring = pd.DataFrame(ringkas)
    out.to_csv(base / OUT_DIR / "to_scrape_pengganti.csv", index=False, encoding="utf-8")
    log.to_csv(detail / "log_penggantian.csv", index=False, encoding="utf-8")
    ring.to_csv(detail / "ringkasan_influencer.csv", index=False, encoding="utf-8")

    print(f"\nInfluencer total {len(ring)} | perlu tambahan {int((ring.kurang > 0).sum())}")
    print(f"Video tambahan dipilih : {len(out)}")
    if len(out):
        print("Tier  :", out.engagement_tier.value_counts().to_dict())
        print("Bulan :", dict(sorted(out.month.value_counts().to_dict().items())))
    sisa = ring[ring.masih_kurang > 0].sort_values("masih_kurang", ascending=False)
    if len(sisa):
        print(f"\n{len(sisa)} influencer masih kurang dari {TARGET} karena cadangan habis:")
        print(sisa[["username", "target_to_scrape", "video_ada_komentar", "ditambah", "masih_kurang"]]
              .to_string(index=False))
    print(f"\nOutput: {base / OUT_DIR}")
    print("  to_scrape_pengganti.csv  <- ini yang di-scrape")
    print("  detail_tambahan/         <- log, ringkasan, video hilang")
    print("to_scrape.csv TIDAK diubah.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)