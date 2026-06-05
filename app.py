"""
KlippiK Image Processor
=======================
Crawl a product URL → extract gallery images → resize to 1200×1200
→ convert to high-quality AVIF → generate KlippiK-formatted alt text
→ ZIP download → track all processed products to prevent duplication.

Supports two database backends:
  • Supabase  — set SUPABASE_URL + SUPABASE_KEY in .streamlit/secrets.toml
                (used automatically when running on Streamlit Cloud)
  • SQLite    — automatic fallback for local / Windows use
"""

import io
import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime
from urllib.parse import urljoin, urlparse
import random

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image

# ── Constants ──────────────────────────────────────────────────────────────────
TARGET_SIZE  = (1200, 1200)
AVIF_QUALITY = 85          # default; user can adjust via slider

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(SCRIPT_DIR, "klippik_image_records.db")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "avif_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}

COLORS = [
    # Specific / compound shades first — keeps "midnight" before "blue" etc.
    "midnight","forest","charcoal","mustard","maroon","coral","slate",
    "copper","silver","natural","smoke","dusk","stone","clove",
    # Then specific single-word shades
    "navy","olive","ivory","beige","tan","mint","cream","teal","rose",
    "sand","purple","orange","pink","cyan","gold",
    # Generic primaries last (most likely to false-match)
    "black","white","red","blue","green","grey","gray","brown",
]

VIEW_LABELS = [
    "Main View","Side View","Back View","Detail View","Interior View",
    "Lifestyle View","Close-up View","Flat Lay View","Front View",
    "Top View","Angle View","Open View",
]

# ── Regional alt text config ───────────────────────────────────────────────────
# GB = global sub-site: rotate through key English-speaking + major markets
GB_COUNTRY_CODES = ["US", "UK", "AU", "CA", "SG", "NZ", "IE", "ZA", "DE", "FR"]

REGION_SUFFIX = {
    "kw": "",           # Root domain — no suffix needed
    "ae": " UAE",       # Middle East
    "in": " India",     # India
    # GB handled separately with rotating country codes
}


# ── Database (Supabase or SQLite) ──────────────────────────────────────────────

def _use_supabase() -> bool:
    try:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY", "")
        return bool(url and key)
    except Exception:
        return False


@st.cache_resource
def _supabase_client():
    from supabase import create_client          # type: ignore
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"],
    )


def init_db():
    """Create SQLite table if it doesn't exist (local mode only)."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            url           TEXT    UNIQUE,
            product_name  TEXT,
            image_count   INTEGER,
            processed_at  TEXT,
            alt_texts     TEXT,
            filenames     TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_record(url: str):
    """Return existing record dict or None."""
    url = url.rstrip("/")
    if _use_supabase():
        sb  = _supabase_client()
        res = sb.table("products").select("*").eq("url", url).execute()
        return res.data[0] if res.data else None
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT url,product_name,image_count,processed_at,alt_texts,filenames "
        "FROM products WHERE url=?", (url,)
    ).fetchone()
    conn.close()
    if row:
        return dict(zip(
            ["url","product_name","image_count","processed_at","alt_texts","filenames"],
            row,
        ))
    return None


def save_record(url, product_name, image_count, alt_texts, filenames):
    url  = url.rstrip("/")
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = {
        "url":          url,
        "product_name": product_name,
        "image_count":  image_count,
        "processed_at": now,
        "alt_texts":    json.dumps(alt_texts),
        "filenames":    json.dumps(filenames),
    }
    if _use_supabase():
        _supabase_client().table("products").upsert(
            data, on_conflict="url"
        ).execute()
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO products
            (url,product_name,image_count,processed_at,alt_texts,filenames)
        VALUES (:url,:product_name,:image_count,:processed_at,:alt_texts,:filenames)
    """, data)
    conn.commit()
    conn.close()


def get_all_records():
    if _use_supabase():
        res = (
            _supabase_client()
            .table("products")
            .select("url,product_name,image_count,processed_at")
            .order("processed_at", desc=True)
            .execute()
        )
        return [
            (r["url"], r["product_name"], r["image_count"], r["processed_at"])
            for r in res.data
        ]
    conn  = sqlite3.connect(DB_PATH)
    rows  = conn.execute(
        "SELECT url,product_name,image_count,processed_at "
        "FROM products ORDER BY processed_at DESC"
    ).fetchall()
    conn.close()
    return rows


# ── Image Crawling ─────────────────────────────────────────────────────────────

def _clean_url(u: str) -> str:
    """Strip Cloudinary/CDN transforms to get full-res base URL."""
    # Cloudinary: remove upload transform segment
    u = re.sub(r"(cloudinary\.com/.+?/image/upload/)([^/]+/)(v\d+/)", r"\1\3", u)
    # Remove query params that shrink images
    base = u.split("?")[0]
    return base


def crawl_page(url: str):
    """Returns (product_name: str, image_urls: list[str])."""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # ── Product name ──
    product_name = ""
    for fn in [
        lambda s: s.find("h1"),
        lambda s: s.find("meta", property="og:title"),
        lambda s: s.find("title"),
    ]:
        el = fn(soup)
        if el:
            raw = el.get("content", "") or el.get_text()
            candidate = raw.split("|")[0].split(" – ")[0].split(" - ")[0].strip()
            if candidate:
                product_name = candidate
                break

    image_urls: list[str] = []
    seen: set[str] = set()

    def add(raw_url: str):
        if not raw_url:
            return
        clean = _clean_url(raw_url)
        if not clean.startswith("http"):
            clean = urljoin(url, clean)
        low = clean.lower()
        if any(x in low for x in [
            ".svg", ".gif", "logo", "icon", "badge",
            "placeholder", "spinner", "loading",
            "payment", "cart", "rating", "star",
            "avatar", "flag", "favicon",
        ]):
            return
        if clean not in seen:
            seen.add(clean)
            image_urls.append(clean)

    # Strategy 1 — __NEXT_DATA__ (Next.js / Shopify Hydrogen)
    nd = soup.find("script", id="__NEXT_DATA__")
    if nd and nd.string:
        for u in re.findall(
            r'https://[^\s"\'\\]+\.(?:jpg|jpeg|png|webp)',
            nd.string, re.IGNORECASE,
        ):
            low = u.lower()
            if any(x in low for x in ["cdn","media","image","product","gallery","upload"]):
                if not any(x in low for x in ["_50","_100","_150","_200","thumb","logo","icon"]):
                    add(u)

    # Strategy 2 — JSON-LD Product schema
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            blob = json.dumps(data)
            for u in re.findall(
                r'https://[^\s"\'\\]+\.(?:jpg|jpeg|png|webp)',
                blob, re.IGNORECASE,
            ):
                add(u)
        except Exception:
            pass

    # Strategy 3 — og:image meta tags
    for m in soup.find_all("meta", property=re.compile(r"og:image", re.I)):
        add(m.get("content", ""))

    # Strategy 4 — img tags (fallback)
    if len(image_urls) < 2:
        for img in soup.find_all("img"):
            src = (
                img.get("data-zoom-image")
                or img.get("data-src")
                or img.get("data-original")
                or img.get("data-lazy")
                or img.get("src")
                or ""
            )
            if not src:
                continue
            try:
                w = img.get("width", ""); h = img.get("height", "")
                if w and int(str(w).replace("px", "")) < 200:
                    continue
                if h and int(str(h).replace("px", "")) < 200:
                    continue
            except Exception:
                pass
            add(urljoin(url, src))

    return product_name, image_urls[:12]


# ── Image Processing ───────────────────────────────────────────────────────────

def fetch_image(url: str) -> bytes:
    r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=15)
    r.raise_for_status()
    return r.content


def to_avif(raw: bytes, quality: int = AVIF_QUALITY) -> bytes:
    """Decode → normalise → resize to 1200×1200 (letterboxed) → AVIF encode."""
    img = Image.open(io.BytesIO(raw))

    # Normalise colour mode
    if img.mode in ("RGBA", "LA", "PA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "PA":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode == "P":
        img = img.convert("RGBA").convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Resize maintaining aspect ratio
    img.thumbnail(TARGET_SIZE, Image.LANCZOS)

    # Letterbox on white 1200×1200 canvas
    canvas = Image.new("RGB", TARGET_SIZE, (255, 255, 255))
    canvas.paste(
        img,
        ((TARGET_SIZE[0] - img.width)  // 2,
         (TARGET_SIZE[1] - img.height) // 2),
    )

    buf = io.BytesIO()
    canvas.save(buf, format="AVIF", quality=quality)
    return buf.getvalue()


# ── Alt Text ───────────────────────────────────────────────────────────────────

def extract_color(name: str, url: str) -> str:
    text = f"{name} {url}".lower()
    for c in COLORS:
        if re.search(r"\b" + c + r"\b", text):
            return c.title()
    return ""


def make_alt(product_name: str, color: str, idx: int, region: str = "kw") -> str:
    """
    Generate SEO alt text for a given region.
    region: 'kw' | 'ae' | 'in' | 'gb'
    For 'gb', a country code is cycled deterministically across image indices.
    """
    view      = VIEW_LABELS[idx] if idx < len(VIEW_LABELS) else f"View {idx + 1}"
    color_str = f" in {color}" if color else ""
    base      = f"{product_name}{color_str} – {view} | KlippiK"

    if region == "gb":
        code = GB_COUNTRY_CODES[idx % len(GB_COUNTRY_CODES)]
        return f"{base} {code}"
    else:
        suffix = REGION_SUFFIX.get(region, "")
        return f"{base}{suffix}"


def make_all_alts(product_name: str, color: str, idx: int) -> dict:
    """Return alt text for all 4 regions for a single image index."""
    return {
        "alt_kw": make_alt(product_name, color, idx, "kw"),
        "alt_ae": make_alt(product_name, color, idx, "ae"),
        "alt_in": make_alt(product_name, color, idx, "in"),
        "alt_gb": make_alt(product_name, color, idx, "gb"),
    }


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "_", text)
    return text.strip("_")[:50]


# ── Streamlit UI ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="KlippiK Image Processor",
    page_icon="🖼️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.main .block-container { max-width: 1100px; }
.dup-box  { background:#fff8e1; border:1.5px solid #f0c040;
            border-radius:8px; padding:12px 16px; margin:6px 0 10px; }
.ok-box   { background:#e8f5e9; border:1.5px solid #43a047;
            border-radius:8px; padding:12px 16px; margin:6px 0 10px; }
</style>
""", unsafe_allow_html=True)

# Initialise local DB if not using Supabase
if not _use_supabase():
    init_db()

# ── Header ──
st.title("🖼️ KlippiK Image Processor")
st.caption(
    "Paste a product URL · Crawl gallery · Resize 1200×1200 · "
    "Convert to AVIF · Generate alt text · Download ZIP · Track duplicates"
)
st.divider()

# ── URL Input ──
c1, c2 = st.columns([5, 1])
with c1:
    url_input = st.text_input(
        "URL", placeholder="https://www.dailyobjects.com/products/...",
        label_visibility="collapsed",
    )
with c2:
    go = st.button("🔍 Crawl", type="primary", use_container_width=True)

# ── Manual image URL override ──
with st.expander("📎 Or paste direct image URLs (one per line)", expanded=False):
    manual_urls_raw = st.text_area(
        "Image URLs", height=100, label_visibility="collapsed",
        placeholder="https://cdn.dailyobjects.com/.../image1.jpg\nhttps://cdn.../image2.jpg",
    )
    manual_name = st.text_input(
        "Product name (for alt text + filenames)",
        placeholder="Pedal Daypack 15.6L – Midnight Blue",
    )
    use_manual = st.button("Use these URLs →", type="secondary")

# ── Resolve input source ──
active_url       = ""
active_img_urls  = []
active_name      = ""
triggered        = False

if go and url_input:
    active_url  = url_input.strip()
    triggered   = True
elif use_manual and manual_urls_raw:
    active_img_urls = [u.strip() for u in manual_urls_raw.strip().splitlines() if u.strip()]
    active_name     = manual_name.strip() or "Product"
    active_url      = active_img_urls[0]  # use first URL as record key
    triggered       = True

if triggered:
    # ── Duplicate check ──
    existing = get_record(active_url)
    if existing:
        st.markdown(
            f"""<div class="dup-box">
            ⚠️ <strong>Already processed on {existing["processed_at"]}</strong><br>
            <b>Product:</b> {existing["product_name"]} &nbsp;|&nbsp;
            <b>Images:</b> {existing["image_count"]} AVIF files saved
            </div>""",
            unsafe_allow_html=True,
        )
        reprocess = st.checkbox("Re-process anyway (overwrites previous record)")
        if not reprocess:
            st.stop()

    # ── Crawl (unless manual mode) ──
    if not active_img_urls:
        with st.spinner("Crawling product page…"):
            try:
                active_name, active_img_urls = crawl_page(active_url)
            except Exception as e:
                st.error(f"❌ Crawl failed: {e}")
                st.stop()

    if not active_img_urls:
        st.error(
            "No product images found. The site may be client-rendered. "
            "Try pasting direct image URLs in the manual section above."
        )
        st.stop()

    color = extract_color(active_name, active_url)
    slug  = slugify(active_name or urlparse(active_url).path.split("/")[-1] or "product")

    st.success(
        f"Found **{len(active_img_urls)} images** · "
        f"Product: **{active_name or '—'}** · "
        f"Colour detected: **{color or 'none'}**"
    )

    # ── Image preview + selection ──
    st.subheader("Select images to include")
    COLS = 4
    rows = [active_img_urls[i:i+COLS] for i in range(0, len(active_img_urls), COLS)]
    selected_indices: list[int] = []

    for row_i, row in enumerate(rows):
        cols = st.columns(COLS)
        for col_i, img_url in enumerate(row):
            abs_i = row_i * COLS + col_i
            with cols[col_i]:
                try:
                    preview = fetch_image(img_url)
                    st.image(preview, use_container_width=True)
                except Exception:
                    st.caption("⚠️ Preview unavailable")
                if st.checkbox(f"#{abs_i + 1}", value=True, key=f"chk_{abs_i}"):
                    selected_indices.append(abs_i)

    st.divider()

    # ── Quality slider ──
    quality = st.slider(
        "AVIF Quality", 70, 95, 85, 5,
        help="85 = excellent quality. 90–95 = near-lossless (larger files).",
    )

    # ── Process button ──
    if st.button("⚡ Process Selected Images", type="primary"):
        chosen = [(i, active_img_urls[i]) for i in selected_indices]
        if not chosen:
            st.warning("No images selected.")
            st.stop()

        avif_results: list[tuple[str, bytes]] = []
        all_alts:     list[dict]  = []   # [{alt_kw, alt_ae, alt_in, alt_gb}, ...]
        filenames:    list[str]   = []
        errors:       list[str]   = []

        prog = st.progress(0.0, text="Starting…")
        status_box = st.empty()

        for step, (orig_i, img_url) in enumerate(chosen):
            prog.progress(step / len(chosen), text=f"Image {step + 1}/{len(chosen)}…")
            status_box.caption(f"Processing: {img_url[:80]}…")
            try:
                raw       = fetch_image(img_url)
                avif_data = to_avif(raw, quality)
                fname     = f"{slug}_{orig_i + 1:02d}.avif"
                alts      = make_all_alts(active_name or "Product", color, orig_i)
                avif_results.append((fname, avif_data))
                all_alts.append(alts)
                filenames.append(fname)
            except Exception as e:
                errors.append(f"Image #{orig_i + 1}: {e}")

        prog.progress(1.0, text="Done!")
        status_box.empty()

        for err in errors:
            st.warning(f"⚠️ {err}")

        if avif_results:
            # Save to local disk
            out_dir = os.path.join(OUTPUT_DIR, slug)
            os.makedirs(out_dir, exist_ok=True)
            for fname, data in avif_results:
                with open(os.path.join(out_dir, fname), "wb") as f:
                    f.write(data)

            # Record in DB (store KW alt as the primary reference)
            kw_alts = [a["alt_kw"] for a in all_alts]
            save_record(active_url, active_name, len(avif_results), kw_alts, filenames)

            # Build multi-region alt text CSV
            csv_rows = ["filename,alt_kw,alt_ae,alt_in,alt_gb"]
            for fname, alts in zip(filenames, all_alts):
                csv_rows.append(
                    f'"{fname}",'
                    f'"{alts["alt_kw"]}",'
                    f'"{alts["alt_ae"]}",'
                    f'"{alts["alt_in"]}",'
                    f'"{alts["alt_gb"]}"'
                )
            csv_content = "\n".join(csv_rows)

            # Build ZIP
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for fname, data in avif_results:
                    zf.writestr(fname, data)
                zf.writestr("alt_texts.csv", csv_content)
            zip_buf.seek(0)

            # Results
            st.markdown(
                f"""<div class="ok-box">
                ✅ <strong>{len(avif_results)} AVIF images ready</strong> —
                1200×1200 · Quality {quality} · ZIP includes alt_texts.csv (4 regions)
                </div>""",
                unsafe_allow_html=True,
            )

            # Alt text preview table
            st.subheader("Generated Alt Texts — All Regions")
            preview_data = []
            for fname, alts in zip(filenames, all_alts):
                preview_data.append({
                    "File":   fname,
                    "🇰🇼 KW": alts["alt_kw"],
                    "🇦🇪 AE": alts["alt_ae"],
                    "🇮🇳 IN": alts["alt_in"],
                    "🌐 GB":  alts["alt_gb"],
                })
            st.dataframe(
                pd.DataFrame(preview_data),
                use_container_width=True,
                hide_index=True,
            )

            st.download_button(
                label=f"⬇️ Download {slug}_avif.zip",
                data=zip_buf.getvalue(),
                file_name=f"{slug}_avif.zip",
                mime="application/zip",
                type="primary",
                use_container_width=True,
            )

# ── Processing History ─────────────────────────────────────────────────────────
st.divider()
st.subheader("📋 Processing History")
records = get_all_records()
if records:
    df = pd.DataFrame(
        records, columns=["URL", "Product", "Images", "Processed At"]
    )
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={"URL": st.column_config.LinkColumn("URL")},
    )
    csv_export = df.to_csv(index=False).encode()
    st.download_button(
        "⬇️ Export history CSV",
        csv_export,
        file_name="klippik_image_history.csv",
        mime="text/csv",
    )
else:
    st.caption("No products processed yet — paste a URL above to get started.")
