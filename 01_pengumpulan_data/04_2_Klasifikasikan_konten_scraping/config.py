"""
CONFIGURATION - ROOT DIRECTORY & PATHS
======================================
"""

from pathlib import Path

# ============================================================================
# ROOT DIRECTORY - UPDATE INI
# ============================================================================

ROOT_DIR = Path(r"C:\Users\nesha\Tugas_akhir\seleksi-influencer")

# ============================================================================
# FOLDER STRUCTURE
# ============================================================================

PENGUMPULAN_DATA = ROOT_DIR / "01_pengumpulan_data"
FOLDER_04_1 = PENGUMPULAN_DATA / "04_1_mendapatkan_metadata_akun"
FOLDER_04_2 = PENGUMPULAN_DATA / "04_2_Klasifikasikan_konten_scraping"

# ============================================================================
# INPUT
# ============================================================================

INPUT_METADATA = FOLDER_04_1 / "metadata_video.csv"

# ============================================================================
# OUTPUT
# ============================================================================

OUTPUT_VIDEO_CLASSIFIED = FOLDER_04_2 / "video_classified.csv"
OUTPUT_HEALTH_ONLY = FOLDER_04_2 / "health_contents_only.csv"
OUTPUT_VIDEOS_TO_LABEL = FOLDER_04_2 / "videos_to_label.csv"
OUTPUT_LABELED_VIDEOS = FOLDER_04_2 / "labeled_videos.csv"
OUTPUT_ACCURACY_REPORT = FOLDER_04_2 / "accuracy_report.json"
OUTPUT_CONFUSION_MATRIX = FOLDER_04_2 / "confusion_matrix.csv"
OUTPUT_CLASSIFICATION_LOG = FOLDER_04_2 / "classification.log"
OUTPUT_CHECKPOINT = FOLDER_04_2 / "checkpoint.csv"

# ============================================================================
# VALIDATION
# ============================================================================

def validate_paths():
    """Validate paths exist"""
    print("\n" + "="*70)
    print("PATH VALIDATION")
    print("="*70)
    
    print(f"\n📁 Root: {ROOT_DIR}")
    print(f"   {'✅' if ROOT_DIR.exists() else '❌'}")
    print(f"\n📁 Input folder: {FOLDER_04_1}")
    print(f"   {'✅' if FOLDER_04_1.exists() else '❌'}")
    print(f"\n📁 Output folder: {FOLDER_04_2}")
    print(f"   {'✅' if FOLDER_04_2.exists() else '❌'}")
    print(f"\n📄 Input file: {INPUT_METADATA}")
    print(f"   {'✅' if INPUT_METADATA.exists() else '❌'}")
    
    if not INPUT_METADATA.exists():
        print(f"\n❌ ERROR: {INPUT_METADATA}")
        return False
    
    print(f"\n✅ All paths valid!")
    return True

def print_config():
    """Print config"""
    print("\n" + "="*70)
    print("CONFIGURATION")
    print("="*70)
    print(f"\nRoot: {ROOT_DIR}")
    print(f"Input: {INPUT_METADATA}")
    print(f"Output: {OUTPUT_VIDEO_CLASSIFIED}")
    print("="*70 + "\n")

if __name__ == "__main__":
    print_config()
    validate_paths()