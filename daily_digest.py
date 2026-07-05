"""
Astro Daily Digest
------------------
1) arXiv 新着論文(X線天文・小型衛星・多波長観測・検出器)
2) GCN Circulars(新天体・トランジェント速報)
を毎日取得し、Claude API で日本語ダイジェストを作成して
GitHub Issue(または Slack/Discord Webhook)に投稿するスクリプト。

必要な環境変数:
  ANTHROPIC_API_KEY : Claude API キー(必須)
  GITHUB_TOKEN      : GitHub Actions が自動で提供(Issue 投稿に使用)
  GITHUB_REPOSITORY : GitHub Actions が自動で提供(例: "user/repo")
  WEBHOOK_URL       : (任意)Slack/Discord の Webhook URL。設定すると Issue の代わりに送信。

使い方:
  python daily_digest.py                # 通常実行(直近 HOURS_BACK 時間分を取得)
  python daily_digest.py --backfill 7   # 昨日から遡って7日分を過去データとして一括生成
                                         # (docs/data/ に既にある日付はスキップ)
"""

import json
import io
import csv
import html
import os
import re
import sys
import tarfile
import time
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# ======== 設定(ここを編集)========
# --- arXiv ---
CATEGORIES = [
    "astro-ph.HE",      # 高エネルギー天体物理(X線・ガンマ線天文)
    "astro-ph.IM",      # 天文の装置・観測手法(小型衛星・多波長観測)
    "physics.ins-det",  # 測定器・検出器
]
# 優先キーワード(タイトル・アブストラクトに含まれると優先的に要約対象になる)
KEYWORDS = [
    "x-ray", "multi-wavelength", "multiwavelength", "multi-messenger",
    "cubesat", "smallsat", "small satellite", "nanosatellite",
    "detector", "ccd", "cmos sensor", "sipm", "tes", "microcalorimeter",
    "xrism", "nicer", "swift", "chandra", "nustar", "erosita", "athena",
]
MAX_PAPERS = 15            # 1日に要約する論文の最大件数

# --- GCN 速報 ---
INCLUDE_GCN = True         # False にすると GCN セクションを無効化
MAX_CIRCULARS = 60         # 1日に処理する Circular の最大件数
GCN_BODY_TRUNCATE = 800    # 各 Circular 本文をこの文字数に切り詰めて要約に渡す

# --- ATel 速報 ---
INCLUDE_ATEL = True        # False にすると ATel セクションを無効化
MAX_ATELS = 12             # 1日に処理する ATel の最大件数
ATEL_BODY_TRUNCATE = 900   # 個別本文が取れた場合、この文字数に切り詰めて要約に渡す

# --- 国内プレスリリース ---
INCLUDE_DOMESTIC_PRESS = True
MAX_PRESS_RELEASES = 8
PRESS_LOOKBACK_DAYS = 14
PRESS_SOURCES = [
    {"name": "ISAS/JAXA", "url": "https://www.isas.jaxa.jp/topics/"},
    {"name": "JAXA", "url": "https://www.jaxa.jp/press/index_j.html"},
    {"name": "理化学研究所", "url": "https://www.riken.jp/press/"},
    {"name": "東京大学", "url": "https://www.u-tokyo.ac.jp/focus/ja/press/"},
    {"name": "国立天文台", "url": "https://www.nao.ac.jp/news/"},
]
PRESS_KEYWORDS = [
    "X線", "Ｘ線", "x-ray", "XRISM", "Resolve", "Xtend", "MAXI", "すざく", "ひとみ",
    "ASTRO-H", "ブラックホール", "中性子星", "超新星", "銀河団", "高エネルギー天体",
    "ガンマ線", "宇宙線", "マイクロカロリメータ", "CCD", "検出器",
]

# --- TNS / ミッション・観測所ニュース ---
INCLUDE_TNS = True
MAX_TNS = 8
TNS_LOOKBACK_DAYS = 7
INCLUDE_MISSION_NEWS = True
MAX_MISSION_NEWS = 10
MISSION_NEWS_LOOKBACK_DAYS = 14
MISSION_NEWS_SOURCES = [
    {"name": "NASA Science", "url": "https://science.nasa.gov/feed/", "type": "rss"},
    {"name": "XRISM", "url": "https://www.xrism.jaxa.jp/en/topics/", "type": "html"},
    {"name": "NuSTAR", "url": "https://www.nustar.caltech.edu/news", "type": "html"},
    {"name": "Chandra", "url": "https://chandra.harvard.edu/press/", "type": "html"},
]
MISSION_KEYWORDS = [
    "x-ray", "x ray", "gamma-ray", "gamma ray", "transient", "supernova", "black hole",
    "neutron star", "pulsar", "magnetar", "grb", "swift", "nicer", "chandra", "nustar",
    "xrism", "ixpe", "fermi", "maxi",
]

# --- 共通 ---
HOURS_BACK = 26            # 何時間前までを対象にするか(毎日実行なら26hで取りこぼし防止)
# 使用する AI: GEMINI_API_KEY があれば Gemini(無料枠)、
# なければ ANTHROPIC_API_KEY で Claude を使う(自動判別)
GEMINI_MODEL = "gemini-2.5-flash"            # 無料枠対応の安定モデル
CLAUDE_MODEL = "claude-haiku-4-5-20251001"   # 精度重視なら "claude-sonnet-4-6"
# ===================================

ARXIV_API = "http://export.arxiv.org/api/query"
GCN_BASE = "https://gcn.nasa.gov"
GCN_ARCHIVE = f"{GCN_BASE}/circulars/archive.json.tar.gz"
GCN_RETRY_WAITS = [45, 120, 240]
ATEL_BASE = "https://www.astronomerstelegram.org"
TNS_CSV = "https://www.wis-tns.org/search?format=csv&classified_sne=1"
ADS_BASE = "https://ui.adsabs.harvard.edu"
ATOM = "{http://www.w3.org/2005/Atom}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; AstroDigest/1.0; +https://github.com/)"}


def http_get(url, timeout=60, retry_statuses=(429, 500, 502, 503, 504), waits=(20, 60)):
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(len(waits) + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code not in retry_statuses or attempt >= len(waits):
                raise
            retry_after = e.headers.get("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else waits[attempt]
            print(f"HTTP {e.code} from {url}. {wait}秒待って再試行します...")
            time.sleep(wait)


# ---------------------------------------------------------------- arXiv

def fetch_papers(start, end, max_papers=MAX_PAPERS):
    """[start, end) の期間に投稿された論文を arXiv から取得する。"""
    cat_query = " OR ".join(f"cat:{c}" for c in CATEGORIES)
    date_query = f"submittedDate:[{start.strftime('%Y%m%d%H%M')} TO {end.strftime('%Y%m%d%H%M')}]"
    params = urllib.parse.urlencode({
        "search_query": f"({cat_query}) AND {date_query}",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": 200,
    })
    root = ET.fromstring(http_get(f"{ARXIV_API}?{params}"))

    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        papers.append({
            "title": " ".join(entry.findtext(f"{ATOM}title").split()),
            "abstract": " ".join(entry.findtext(f"{ATOM}summary").split()),
            "url": entry.findtext(f"{ATOM}id"),
            "ads_url": ads_search_url(entry.findtext(f"{ATOM}id")),
            "authors": [a.findtext(f"{ATOM}name") for a in entry.findall(f"{ATOM}author")],
            "categories": [c.get("term") for c in entry.findall(f"{ATOM}category")],
        })

    if KEYWORDS:
        def score(p):
            text = f"{p['title']} {p['abstract']}".lower()
            return sum(1 for kw in KEYWORDS if kw.lower() in text)
        papers.sort(key=score, reverse=True)

    return papers[:max_papers]


def arxiv_id(url):
    match = re.search(r"arxiv\.org/abs/([^?#]+)", url or "", flags=re.I)
    if not match:
        return ""
    return re.sub(r"v\d+$", "", match.group(1))


def ads_search_url(arxiv_url):
    aid = arxiv_id(arxiv_url)
    if not aid:
        return ADS_BASE
    query = urllib.parse.quote(f"arXiv:{aid}")
    return f"{ADS_BASE}/search/q={query}&sort=date%20desc"


# ---------------------------------------------------------------- GCN

_archive_cache = None


def fetch_circulars(start, end, use_index_first=True):
    """[start, end) の期間に作成された Circular を返す。

    通常は公式一括アーカイブを優先する。個別JSON巡回はリクエスト数が多く、
    GCN側の 429 を誘発しやすいため、アーカイブ取得に失敗した場合だけ使う。
    """
    try:
        return fetch_circulars_from_archive_window(start, end)
    except Exception as e:
        print(f"GCN archive 取得に失敗、最新一覧へフォールバックします: {e}")
    if use_index_first:
        try:
            return fetch_circulars_from_index(start, end)
        except Exception as e:
            print(f"GCN index 取得にも失敗しました: {e}")
    raise RuntimeError("GCN archive and index fetch both failed")


def normalize_circular(data):
    return {
        "id": data["circularId"],
        "subject": data["subject"],
        "event": data.get("eventId") or "(その他)",
        "body": data["body"][:GCN_BODY_TRUNCATE],
        "url": f"{GCN_BASE}/circulars/{data['circularId']}",
    }


def fetch_circulars_from_index(start, end):
    """最新一覧ページから個別JSONをたどる通常ルート(現在時刻に近い期間向け)。"""
    html = http_get(f"{GCN_BASE}/circulars?view=index&limit={MAX_CIRCULARS + 40}").decode()
    if "Unexpected error" in html:
        raise RuntimeError("GCN circulars index returned an unexpected error page")

    ids = re.findall(r'href="(?:https://gcn\.nasa\.gov)?/circulars/([\d.]+)"', html)
    # 順序を保ったまま重複除去(新しい順)
    ids = list(dict.fromkeys(ids))[:MAX_CIRCULARS + 40]
    if not ids:
        raise RuntimeError("GCN circulars index contained no circular links")

    circulars = []
    for cid in ids:
        try:
            data = json.loads(http_get(f"{GCN_BASE}/circulars/{cid}.json"))
        except Exception as e:
            print(f"  skip circular {cid}: {e}")
            continue
        created = datetime.fromtimestamp(data["createdOn"] / 1000, tz=timezone.utc)
        if created >= end:
            continue  # ウィンドウより新しい分はスキップ
        if created < start:
            break  # 新しい順なので、時間窓を出たら打ち切り
        circulars.append(normalize_circular(data))
        time.sleep(0.3)  # サーバーへの負荷軽減
        if len(circulars) >= MAX_CIRCULARS:
            break
    return circulars


def fetch_all_circulars_archive():
    """公式一括JSONアーカイブを取得し、新しい順の生データ一覧を返す(プロセス内でキャッシュ)。"""
    global _archive_cache
    if _archive_cache is not None:
        return _archive_cache

    raw = http_get(GCN_ARCHIVE, timeout=240, waits=GCN_RETRY_WAITS)
    records = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        names = [
            name for name in archive.getnames()
            if re.fullmatch(r"archive\.json/\d+\.json", name)
        ]
        for name in names:
            member = archive.extractfile(name)
            if member is None:
                continue
            records.append(json.load(member))
    records.sort(key=lambda data: data["createdOn"], reverse=True)
    _archive_cache = records
    return records


def fetch_circulars_from_archive_window(start, end):
    """公式一括JSONアーカイブから [start, end) の期間分を抽出する(過去日付にも対応)。"""
    circulars = []
    for data in fetch_all_circulars_archive():
        created = datetime.fromtimestamp(data["createdOn"] / 1000, tz=timezone.utc)
        if created >= end:
            continue
        if created < start:
            break  # 新しい順なので、時間窓を出たら打ち切り
        circulars.append(normalize_circular(data))
        if len(circulars) >= MAX_CIRCULARS:
            break
    return circulars


# ---------------------------------------------------------------- ATel

def clean_html_text(value):
    """HTML断片をプレーンテキストにする。"""
    text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(text).split())


def parse_atel_date(value):
    return datetime.strptime(value.strip(), "%d %b %Y; %H:%M UT").replace(tzinfo=timezone.utc)


def fetch_atel_body(url):
    """ATel 個別ページ本文を取れた範囲で返す。取得制限時は空文字を返す。"""
    try:
        page = http_get(url, timeout=60).decode("utf-8", "replace")
    except Exception as e:
        print(f"  skip ATel body {url}: {e}")
        return ""
    if "We're Sorry" in page or "We are in the middle of updating" in page:
        return ""

    body_match = re.search(r"<BODY[^>]*>(.*)</BODY>", page, flags=re.S | re.I)
    body = clean_html_text(body_match.group(1) if body_match else page)
    body = re.sub(r"^\s*The Astronomer'?s Telegram\s*", "", body, flags=re.I)
    return body[:ATEL_BODY_TRUNCATE].strip()


def fetch_atels(start, end):
    """ATel トップページの新着表から [start, end) の Telegram を取得する。"""
    page = http_get(f"{ATEL_BASE}/", timeout=90).decode("utf-8", "replace")

    row_re = re.compile(
        r'<TR valign=top><TD\s+class="num"\s*>\s*(\d+)\s*</TD>\s*'
        r'<TD class="title"><A HREF="([^"]+)">(.*?)</A></TD>\s*'
        r'<TD\s+class="author"[^>]*>(.*?)<BR><EM>\s*([^<]+?)\s*</EM>',
        flags=re.S | re.I,
    )
    rows = list(row_re.finditer(page))
    if not rows:
        if "We're Sorry" in page:
            raise RuntimeError("ATel index returned an access-limited page")
        raise RuntimeError("ATel index contained no telegram rows")

    atels = []
    for match in rows:
        atel_id, href, title_html, authors_html, posted_text = match.groups()
        try:
            posted = parse_atel_date(clean_html_text(posted_text))
        except ValueError as e:
            print(f"  skip ATel {atel_id}: date parse failed: {e}")
            continue
        if posted >= end:
            continue
        if posted < start:
            break

        url = href if href.startswith("http") else f"{ATEL_BASE}/{href.lstrip('/')}"
        atels.append({
            "id": atel_id,
            "title": clean_html_text(title_html),
            "authors": clean_html_text(authors_html),
            "posted": posted,
            "url": url,
            "body": fetch_atel_body(url),
        })
        time.sleep(0.25)
        if len(atels) >= MAX_ATELS:
            break
    return atels


# ---------------------------------------------------------------- 国内プレスリリース

def absolute_url(url, href):
    return urllib.parse.urljoin(url, html.unescape(href))


def parse_press_date(text):
    patterns = [
        (r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", "%Y-%m-%d"),
        (r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", "%Y-%m-%d"),
    ]
    for pattern, _ in patterns:
        match = re.search(pattern, text)
        if match:
            y, m, d = (int(x) for x in match.groups())
            try:
                return datetime(y, m, d, tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def press_score(item):
    text = f"{item['title']} {item['excerpt']}".lower()
    return sum(1 for kw in PRESS_KEYWORDS if kw.lower() in text)


def press_title_score(item):
    text = item["title"].lower()
    return sum(1 for kw in PRESS_KEYWORDS if kw.lower() in text)


def fetch_press_source(source, start, end):
    page = http_get(source["url"], timeout=60).decode("utf-8", "replace")
    page = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", page)
    anchors = re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, flags=re.S | re.I)
    items = []
    for match in anchors:
        href, title_html = match.groups()
        title = clean_html_text(title_html)
        if len(title) < 8 or href.startswith("#"):
            continue
        if any(skip in title for skip in ("本文へ移動", "English", "サイトマップ", "お問い合わせ")):
            continue

        context = page[max(0, match.start() - 600):match.end() + 600]
        text_context = clean_html_text(context)
        posted = parse_press_date(text_context)
        if posted is None:
            continue
        if posted < start or posted >= end:
            continue

        item = {
            "source": source["name"],
            "title": title,
            "url": absolute_url(source["url"], href),
            "posted": posted,
            "excerpt": text_context[:260],
        }
        if press_title_score(item) <= 0:
            continue
        item["excerpt"] = fetch_press_excerpt(item["url"], item["title"]) or item["excerpt"]
        items.append(item)

    unique = {}
    for item in items:
        unique[item["url"]] = item
    return list(unique.values())


def fetch_press_excerpt(url, title):
    try:
        page = http_get(url, timeout=60).decode("utf-8", "replace")
    except Exception as e:
        print(f"  skip press excerpt {url}: {e}")
        return ""
    page = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<nav.*?</nav>|<header.*?</header>|<footer.*?</footer>", " ", page)
    text = clean_html_text(page)
    start = -1
    for marker in ("発表のポイント", "ポイント", "概要", title):
        start = text.find(marker)
        if start >= 0:
            break
    if start < 0:
        return ""
    excerpt = text[start:]
    excerpt = re.sub(r"^発表のポイント\s*", "", excerpt)
    excerpt = re.sub(r"^ポイント\s*", "", excerpt)
    return excerpt[:360].strip()


def fetch_domestic_press(start, end):
    window_start = min(start, end - timedelta(days=PRESS_LOOKBACK_DAYS))
    items = []
    for source in PRESS_SOURCES:
        try:
            items.extend(fetch_press_source(source, window_start, end))
        except Exception as e:
            print(f"  skip press source {source['name']}: {e}")
    items.sort(key=lambda item: (press_score(item), item["posted"]), reverse=True)
    return items[:MAX_PRESS_RELEASES]


def format_domestic_press(items):
    blocks = []
    for item in items:
        date = item["posted"].strftime("%Y-%m-%d")
        blocks.append(
            f"### {item['title']}\n"
            f"原文: [{item['source']}]({item['url']})\n"
            f"- **日付**: {date}\n"
            f"- **抜粋**: {item['excerpt']}"
        )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- TNS / mission news

def parse_rfc2822_date(value):
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(value.strip())
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_url_date(url):
    patterns = [
        r"(?<!\d)(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?!\d)",
        r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if not match:
            continue
        parts = [int(x) for x in match.groups()]
        if len(parts[0:1]) and parts[0] < 100:
            y, m, d = 2000 + parts[0], parts[1], parts[2]
        else:
            y, m, d = parts
        try:
            return datetime(y, m, d, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def keyword_score(text, keywords):
    low = text.lower()
    return sum(1 for kw in keywords if kw.lower() in low)


def tns_object_url(name):
    slug = re.sub(r"^(SN|AT)\s+", "", name or "", flags=re.I).replace(" ", "")
    return f"https://www.wis-tns.org/object/{urllib.parse.quote(slug)}"


def fetch_tns(start, end):
    window_start = min(start, end - timedelta(days=TNS_LOOKBACK_DAYS))
    data = http_get(TNS_CSV, timeout=90).decode("utf-8", "replace")
    rows = csv.DictReader(io.StringIO(data))
    items = []
    for row in rows:
        discovered = parse_press_date(row.get("Discovery Date (UT)", ""))
        if discovered is None or discovered < window_start or discovered >= end:
            continue
        name = row.get("Name", "").strip()
        if not name:
            continue
        obj_type = row.get("Obj. Type", "").strip() or "unclassified"
        items.append({
            "name": name,
            "type": obj_type,
            "ra": row.get("RA", "").strip(),
            "dec": row.get("DEC", "").strip(),
            "mag": row.get("Discovery Mag/Flux", "").strip(),
            "filt": row.get("Discovery Filter", "").strip(),
            "posted": discovered,
            "source": row.get("Discovery Data Source/s", "").strip() or row.get("Reporting Group/s", "").strip(),
            "url": tns_object_url(name),
        })
        if len(items) >= MAX_TNS:
            break
    items.sort(key=lambda item: item["posted"], reverse=True)
    return items[:MAX_TNS]


def format_tns(items):
    blocks = []
    for item in items:
        meta = [item["type"]]
        if item["mag"]:
            meta.append(f"{item['mag']}{(' ' + item['filt']) if item['filt'] else ''}")
        if item["source"]:
            meta.append(item["source"])
        coords = f"{item['ra']} {item['dec']}".strip()
        blocks.append(
            f"### {item['name']}\n"
            f"原文: [TNS]({item['url']})\n"
            f"- **日付**: {item['posted'].strftime('%Y-%m-%d')}\n"
            f"- **種別**: {' / '.join(meta)}\n"
            f"- **座標**: {coords or 'TNSを確認'}"
        )
    return "\n\n".join(blocks)


def parse_rss_items(source, start, end):
    window_start = min(start, end - timedelta(days=MISSION_NEWS_LOOKBACK_DAYS))
    root = ET.fromstring(http_get(source["url"], timeout=90))
    items = []
    for item in root.findall(".//item"):
        title = clean_html_text(item.findtext("title") or "")
        link = item.findtext("link") or source["url"]
        description = clean_html_text(item.findtext("description") or "")
        posted = parse_rfc2822_date(item.findtext("pubDate") or "")
        if posted is None or posted < window_start or posted >= end:
            continue
        if keyword_score(f"{title} {description}", MISSION_KEYWORDS) <= 0:
            continue
        items.append({
            "source": source["name"],
            "title": title,
            "url": link,
            "posted": posted,
            "excerpt": description[:320],
        })
    return items


def parse_html_news_items(source, start, end):
    window_start = min(start, end - timedelta(days=MISSION_NEWS_LOOKBACK_DAYS))
    page = http_get(source["url"], timeout=90).decode("utf-8", "replace")
    page = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", page)
    items = []
    for match in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page, flags=re.S | re.I):
        href, title_html = match.groups()
        title = clean_html_text(title_html)
        if len(title) < 10 or title.lower() in ("privacy policy", "image use policy"):
            continue
        url = absolute_url(source["url"], href)
        context = clean_html_text(page[max(0, match.start() - 500):match.end() + 500])
        posted = parse_press_date(context) or parse_url_date(url)
        if posted is None or posted < window_start or posted >= end:
            continue
        if keyword_score(f"{title} {context} {url}", MISSION_KEYWORDS) <= 0:
            continue
        items.append({
            "source": source["name"],
            "title": title,
            "url": url,
            "posted": posted,
            "excerpt": fetch_news_excerpt(url) or context[:320],
        })
    unique = {}
    for item in items:
        unique[item["url"]] = item
    return list(unique.values())


def fetch_news_excerpt(url):
    try:
        page = http_get(url, timeout=60).decode("utf-8", "replace")
    except Exception as e:
        print(f"  skip news excerpt {url}: {e}")
        return ""
    page = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<nav.*?</nav>|<header.*?</header>|<footer.*?</footer>", " ", page)
    meta = re.search(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)["\']', page, flags=re.I)
    if meta:
        return clean_html_text(meta.group(1))[:320]
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", page, flags=re.S | re.I)
    for paragraph in paragraphs:
        text = clean_html_text(paragraph)
        if len(text) >= 80:
            return text[:320]
    return ""


def fetch_mission_news(start, end):
    items = []
    for source in MISSION_NEWS_SOURCES:
        try:
            if source.get("type") == "rss":
                items.extend(parse_rss_items(source, start, end))
            else:
                items.extend(parse_html_news_items(source, start, end))
        except Exception as e:
            print(f"  skip mission news source {source['name']}: {e}")
    items.sort(key=lambda item: (keyword_score(f"{item['title']} {item['excerpt']}", MISSION_KEYWORDS), item["posted"]), reverse=True)
    return items[:MAX_MISSION_NEWS]


def format_mission_news(items):
    blocks = []
    for item in items:
        blocks.append(
            f"### {item['title']}\n"
            f"原文: [{item['source']}]({item['url']})\n"
            f"- **日付**: {item['posted'].strftime('%Y-%m-%d')}\n"
            f"- **抜粋**: {item['excerpt'] or '原文ページを確認してください。'}"
        )
    return "\n\n".join(blocks)


def group_by_event(circulars):
    groups = defaultdict(list)
    for c in circulars:
        groups[c["event"]].append(c)
    return groups


def attach_gcn_source_links(summary, groups):
    result = summary
    unmatched = []
    for event, circs in groups.items():
        links = " / ".join(
            f"[GCN {c['id']}: {c['subject']}]({c['url']})"
            for c in circs
        )
        source_line = f"原文: {links}"
        pattern = re.compile(rf"^(###\s*{re.escape(event)}[^\n]*)$", flags=re.M)
        result, count = pattern.subn(rf"\1\n{source_line}", result, count=1)
        if count == 0:
            unmatched.append((event, source_line))

    if unmatched:
        result += "\n\n### GCN 原文リンク\n\n" + "\n".join(
            f"- **{event}**: {source_line.removeprefix('原文: ')}"
            for event, source_line in unmatched
        )
    return result


# ---------------------------------------------------------------- LLM 呼び出し

def call_llm(prompt, max_tokens=4000):
    """GEMINI_API_KEY があれば Gemini、なければ Claude を呼び出す。"""
    if os.environ.get("GEMINI_API_KEY"):
        return _call_gemini(prompt, max_tokens)
    return _call_claude(prompt, max_tokens)


def _call_gemini(prompt, max_tokens):
    api_key = os.environ["GEMINI_API_KEY"]
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as res:
                data = json.loads(res.read())
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            wait = 25 * (attempt + 1)
            print(f"Gemini API が混雑しています({e.code})。{wait}秒待って再試行します...")
            time.sleep(wait)
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def _call_claude(prompt, max_tokens):
    api_key = os.environ["ANTHROPIC_API_KEY"]
    body = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as res:
        data = json.loads(res.read())
    return "".join(b["text"] for b in data["content"] if b["type"] == "text")


def summarize_papers(papers):
    paper_text = "\n\n".join(
        f"[{i+1}] タイトル: {p['title']}\nURL: {p['url']}\nADS: {p['ads_url']}\nアブストラクト: {p['abstract']}"
        for i, p in enumerate(papers)
    )
    prompt = (
        "以下は対象期間中に arXiv に投稿された論文の一覧です。"
        "各論文のアブストラクトを、要約ではなく自然な日本語に和訳してください。\n\n"
        "出力形式を厳守してください。前置きや ``` は不要です。\n"
        "各論文はタイトル、元論文リンク、アブストラクト和訳だけにしてください。"
        "ハイライト、目的、方法、結果、意義、結論、章別説明、details は出さないでください。\n\n"
        "### 1. 論文タイトル\n"
        "元論文: [arXiv](URL) / [ADS](ADS_URL)\n"
        "- **アブストラクト和訳**: アブストラクト全文の日本語訳\n"
        "\n"
        "この形式で全論文を番号順に出してください。\n"
        "専門用語は無理に訳さず残してください(例: QPO、ハードステート)。"
        "アブストラクトに書かれていないことは推測で補わないでください。\n\n"
        f"{paper_text}"
    )
    raw = call_llm(prompt, max_tokens=10000)
    return re.sub(r"```(?:markdown)?|```", "", raw).strip()


def parse_llm_json(raw):
    text = re.sub(r"```(?:json)?|```", "", raw).strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Gemini can emit LaTeX-style backslashes inside JSON strings.
        text = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)
        return json.loads(text)


def format_paper_fallback(papers):
    blocks = []
    for i, p in enumerate(papers):
        blocks.append(
            f"### {i+1}. {p['title']}\n"
            f"元論文: [arXiv]({p['url']}) / [ADS]({p['ads_url']})\n"
            f"- **アブストラクト和訳**: 自動和訳に失敗したため、arXivリンク先を確認してください。"
        )
    return "\n\n".join(blocks)


def summarize_gcn(groups):
    sections = []
    for event, circs in groups.items():
        entries = "\n".join(
            f"- GCN {c['id']}: {c['subject']}\n  本文抜粋: {c['body']}"
            for c in circs
        )
        sections.append(f"■ イベント: {event}({len(circs)}報)\n{entries}")
    prompt = (
        "以下は対象期間に GCN (General Coordinates Network) に流れた"
        "天体速報(Circulars)を、天体イベントごとにまとめたものです。\n"
        "各イベントについて、日本語で以下をまとめてください:\n"
        "1. どんな天体・現象か(GRB / X線トランジェント / ニュートリノなど)\n"
        "2. 重要な測定値(座標、赤方偏移、フラックス、対応天体の有無など、分かるもののみ)\n"
        "3. 追観測の状況(どの装置・波長で何が見えた/見えなかったか)\n\n"
        "出力形式(Markdown):\n"
        "### イベント名(速報N報)\n"
        "まとめ本文(3〜5文)\n\n"
        "重要度が高い順(新発見・多波長で追観測が活発なものが上)に並べてください。"
        "定常的な誤検出報告(flaring star 等)は最後に1行でまとめて構いません。\n\n"
        + "\n\n".join(sections)
    )
    return call_llm(prompt, max_tokens=6000)


def format_gcn_fallback(groups):
    blocks = []
    for event, circs in groups.items():
        links = " / ".join(
            f"[GCN {c['id']}: {c['subject']}]({c['url']})"
            for c in circs
        )
        bullets = []
        for c in circs[:5]:
            body = clean_html_text(c["body"])[:280]
            bullets.append(f"- **GCN {c['id']}**: {c['subject']}。{body}")
        if len(circs) > 5:
            bullets.append(f"- ほか {len(circs) - 5} 報。原文リンクを確認してください。")
        blocks.append(
            f"### {event}({len(circs)}報)\n"
            f"原文: {links}\n"
            + "\n".join(bullets)
        )
    return "\n\n".join(blocks)


def summarize_atels(atels):
    sections = []
    for atel in atels:
        body = atel["body"] or "本文未取得。タイトル、著者、投稿時刻、原文リンクのみ。"
        sections.append(
            f"ATel #{atel['id']}\n"
            f"タイトル: {atel['title']}\n"
            f"投稿時刻: {atel['posted'].strftime('%Y-%m-%d %H:%M UT')}\n"
            f"著者: {atel['authors']}\n"
            f"URL: {atel['url']}\n"
            f"本文抜粋: {body}"
        )
    prompt = (
        "以下は対象期間に ATel (The Astronomer's Telegram) に投稿された"
        "天体速報です。日本語Markdownで要点をまとめてください。\n\n"
        "出力形式を厳守してください。前置きや ``` は不要です。\n"
        "### ATel #番号: タイトル\n"
        "原文: [ATel #番号](URL)\n"
        "本文(2〜4文。「まとめ本文:」のようなラベルは付けない)\n\n"
        "本文未取得の項目は、タイトル・著者・投稿時刻から分かる範囲だけを書き、"
        "観測結果や数値を推測で補わないでください。"
        "重要度が高い順(新発見・追観測・多波長連携が分かるものが上)に並べてください。\n\n"
        + "\n\n".join(sections)
    )
    raw = call_llm(prompt, max_tokens=6000)
    return re.sub(r"```(?:markdown)?|```", "", raw).strip()


# ---------------------------------------------------------------- 投稿

def post_github_issue(title, body):
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps({"title": title, "body": body, "labels": ["astro-digest"]}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        print("Issue created:", json.loads(res.read())["html_url"])


def post_webhook(text):
    url = os.environ["WEBHOOK_URL"]
    key = "content" if "discord.com" in url else "text"
    limit = 1900 if key == "content" else 30000
    chunks = [text[i:i + limit] for i in range(0, len(text), limit)]
    for chunk in chunks:
        req = urllib.request.Request(
            url,
            data=json.dumps({key: chunk}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=60)
    print(f"Webhook sent ({len(chunks)} message(s))")


def save_to_site(date_str, body):
    """GitHub Pages 用にダイジェストを docs/data/ に保存し、日付一覧を更新する。"""
    os.makedirs("docs/data", exist_ok=True)
    with open(f"docs/data/{date_str}.md", "w", encoding="utf-8") as f:
        f.write(body)
    index_path = "docs/data/index.json"
    try:
        with open(index_path, encoding="utf-8") as f:
            dates = json.load(f)
    except FileNotFoundError:
        dates = []
    if date_str not in dates:
        dates.append(date_str)
    dates.sort(reverse=True)  # 新しい順(バックフィルで過去日付が後から入っても順序を保つ)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(dates, f, ensure_ascii=False, indent=0)
    print(f"サイト用データを保存: docs/data/{date_str}.md")


# ---------------------------------------------------------------- main

def generate_digest(start, end, use_index_first=True):
    """[start, end) の期間を対象にダイジェスト本文(Markdown)を1件分生成する。"""
    parts = []

    # --- GCN 速報(失敗しても論文セクションは続行)---
    if INCLUDE_GCN:
        try:
            circulars = fetch_circulars(start, end, use_index_first=use_index_first)
            if circulars:
                groups = group_by_event(circulars)
                print(f"GCN: {len(circulars)} 報 / {len(groups)} イベントを要約中...")
                try:
                    gcn_summary = attach_gcn_source_links(summarize_gcn(groups), groups)
                except Exception as e:
                    print(f"GCN 要約に失敗、原文ベース表示にします: {e}")
                    gcn_summary = format_gcn_fallback(groups)
                parts.append(
                    f"## 🚨 新天体・トランジェント速報(GCN {len(circulars)}報 / {len(groups)}イベント)\n\n"
                    f"{gcn_summary}"
                )
            else:
                parts.append("## 🚨 新天体・トランジェント速報\n\n対象期間の GCN Circular はありませんでした。")
        except Exception as e:
            print(f"GCN セクションの生成に失敗: {e}")
            if "429" in str(e) or "Too Many Requests" in str(e):
                parts.append("## 🚨 新天体・トランジェント速報\n\nGCN はNASA側のレート制限中のため、この回は未掲載です。TNS / ATel を優先して確認してください。")
            else:
                parts.append(f"## 🚨 新天体・トランジェント速報\n\n取得制限のため、この回は未掲載です。")

    # --- TNS 新規天体(失敗しても論文セクションは続行)---
    if INCLUDE_TNS:
        try:
            tns_items = fetch_tns(start, end)
            if tns_items:
                print(f"TNS: {len(tns_items)} 件を掲載")
                parts.append(
                    f"## 🔭 TNS 新規・分類天体({len(tns_items)}件)\n\n"
                    f"{format_tns(tns_items)}"
                )
            else:
                parts.append("## 🔭 TNS 新規・分類天体\n\n直近の関連天体は見つかりませんでした。")
        except Exception as e:
            print(f"TNS セクションの生成に失敗: {e}")
            parts.append("## 🔭 TNS 新規・分類天体\n\n取得制限のため、この回は未掲載です。")

    # --- ATel 速報(失敗しても論文セクションは続行)---
    if INCLUDE_ATEL:
        try:
            atels = fetch_atels(start, end)
            if atels:
                print(f"ATel: {len(atels)} 件を要約中...")
                parts.append(
                    f"## 🛰️ ATel 新着速報({len(atels)}件)\n\n"
                    f"{summarize_atels(atels)}"
                )
            else:
                parts.append("## 🛰️ ATel 新着速報\n\n対象期間の ATel 投稿はありませんでした。")
        except Exception as e:
            print(f"ATel セクションの生成に失敗: {e}")
            parts.append(f"## 🛰️ ATel 新着速報\n\n取得エラーのためスキップしました({e})")

    # --- ミッション/観測所ニュース(失敗しても論文セクションは続行)---
    if INCLUDE_MISSION_NEWS:
        try:
            mission_items = fetch_mission_news(start, end)
            if mission_items:
                print(f"ミッション/観測所ニュース: {len(mission_items)} 件を掲載")
                parts.append(
                    f"## 🛰️ ミッション・観測所ニュース({len(mission_items)}件)\n\n"
                    f"{format_mission_news(mission_items)}"
                )
            else:
                parts.append("## 🛰️ ミッション・観測所ニュース\n\n直近の関連ニュースは見つかりませんでした。")
        except Exception as e:
            print(f"ミッション/観測所ニュースセクションの生成に失敗: {e}")
            parts.append("## 🛰️ ミッション・観測所ニュース\n\n取得制限のため、この回は未掲載です。")

    # --- 国内プレスリリース(失敗しても論文セクションは続行)---
    if INCLUDE_DOMESTIC_PRESS:
        try:
            press_items = fetch_domestic_press(start, end)
            if press_items:
                print(f"国内プレス: {len(press_items)} 件を掲載")
                parts.append(
                    f"## 🇯🇵 国内X線天文・関連プレス({len(press_items)}件)\n\n"
                    f"{format_domestic_press(press_items)}"
                )
            else:
                parts.append("## 🇯🇵 国内X線天文・関連プレス\n\n直近の関連プレスリリースは見つかりませんでした。")
        except Exception as e:
            print(f"国内プレスセクションの生成に失敗: {e}")
            parts.append(f"## 🇯🇵 国内X線天文・関連プレス\n\n取得エラーのためスキップしました({e})")

    # --- arXiv 論文(失敗してもサイト保存は続行)---
    try:
        papers = fetch_papers(start, end)
        paper_window = "対象期間"
        if not papers:
            papers = fetch_papers(end - timedelta(days=7), end, max_papers=min(MAX_PAPERS, 8))
            paper_window = "直近1週間"
        if papers:
            print(f"arXiv: {len(papers)} 件の論文を要約中...")
            try:
                paper_summary = summarize_papers(papers)
            except Exception as e:
                print(f"arXiv 要約に失敗、フォールバック表示にします: {e}")
                paper_summary = format_paper_fallback(papers)
            parts.append(
                f"## 📄 arXiv 新着論文({paper_window} / {len(papers)}件)\n\n"
                f"対象カテゴリ: {', '.join(CATEGORIES)}\n\n"
                + paper_summary
            )
        else:
            parts.append("## 📄 arXiv 新着論文\n\n直近1週間の新着はありませんでした。")
    except Exception as e:
        print(f"arXiv セクションの生成に失敗: {e}")
        parts.append(f"## 📄 arXiv 新着論文\n\n取得エラーのためスキップしました({e})")

    return "\n\n---\n\n".join(parts)


def main():
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    start = now - timedelta(hours=HOURS_BACK)

    body = generate_digest(start, now, use_index_first=True)
    title = f"🔭 Astro Daily Digest — {today}"

    save_to_site(today, body)

    if os.environ.get("WEBHOOK_URL"):
        post_webhook(f"**{title}**\n\n{body}")
    elif os.environ.get("POST_GITHUB_ISSUE", "").lower() == "true":
        post_github_issue(title, body)
    else:
        print("Issue 投稿はスキップしました。")


def backfill(days_back=7):
    """昨日から遡って days_back 日分を、docs/data/ に不足していれば生成する。"""
    today = datetime.now(timezone.utc).date()
    targets = sorted(today - timedelta(days=i) for i in range(1, days_back + 1))

    for d in targets:
        date_str = d.strftime("%Y-%m-%d")
        if os.path.exists(f"docs/data/{date_str}.md"):
            print(f"{date_str} は既に存在するためスキップ")
            continue

        run_time = datetime(d.year, d.month, d.day, 23, 0, tzinfo=timezone.utc)
        start = run_time - timedelta(hours=HOURS_BACK)
        print(f"=== {date_str} をバックフィル中({start} 〜 {run_time})===")
        try:
            body = generate_digest(start, run_time, use_index_first=False)
        except Exception as e:
            print(f"{date_str} の生成に失敗、スキップ: {e}")
            continue
        save_to_site(date_str, body)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--backfill":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        sys.exit(backfill(days))
    sys.exit(main())
