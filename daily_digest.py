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
"""

import json
import io
import os
import re
import sys
import tarfile
import time
import urllib.parse
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
ATOM = "{http://www.w3.org/2005/Atom}"
UA = {"User-Agent": "astro-daily-digest/1.0"}


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


# ---------------------------------------------------------------- arXiv

def fetch_recent_papers(hours_back=HOURS_BACK, max_papers=MAX_PAPERS):
    query = " OR ".join(f"cat:{c}" for c in CATEGORIES)
    params = urllib.parse.urlencode({
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": 200,
    })
    root = ET.fromstring(http_get(f"{ARXIV_API}?{params}"))

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        published = datetime.fromisoformat(
            entry.findtext(f"{ATOM}published").replace("Z", "+00:00")
        )
        if published < cutoff:
            continue
        papers.append({
            "title": " ".join(entry.findtext(f"{ATOM}title").split()),
            "abstract": " ".join(entry.findtext(f"{ATOM}summary").split()),
            "url": entry.findtext(f"{ATOM}id"),
            "authors": [a.findtext(f"{ATOM}name") for a in entry.findall(f"{ATOM}author")],
            "categories": [c.get("term") for c in entry.findall(f"{ATOM}category")],
        })

    if KEYWORDS:
        def score(p):
            text = f"{p['title']} {p['abstract']}".lower()
            return sum(1 for kw in KEYWORDS if kw.lower() in text)
        papers.sort(key=score, reverse=True)

    return papers[:max_papers]


# ---------------------------------------------------------------- GCN

def fetch_recent_circulars():
    """GCN の最新 Circular 一覧を取得し、直近 HOURS_BACK 時間のものを返す。"""
    try:
        return fetch_recent_circulars_from_index()
    except Exception as e:
        print(f"GCN index 取得に失敗、アーカイブへフォールバックします: {e}")
        return fetch_recent_circulars_from_archive()


def normalize_circular(data):
    return {
        "id": data["circularId"],
        "subject": data["subject"],
        "event": data.get("eventId") or "(その他)",
        "body": data["body"][:GCN_BODY_TRUNCATE],
        "url": f"{GCN_BASE}/circulars/{data['circularId']}",
    }


def fetch_recent_circulars_from_index():
    """最新一覧ページから個別JSONをたどる通常ルート。"""
    html = http_get(f"{GCN_BASE}/circulars?view=index&limit={MAX_CIRCULARS + 40}").decode()
    if "Unexpected error" in html:
        raise RuntimeError("GCN circulars index returned an unexpected error page")

    ids = re.findall(r'href="(?:https://gcn\.nasa\.gov)?/circulars/([\d.]+)"', html)
    # 順序を保ったまま重複除去(新しい順)
    ids = list(dict.fromkeys(ids))[:MAX_CIRCULARS + 40]
    if not ids:
        raise RuntimeError("GCN circulars index contained no circular links")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    circulars = []
    for cid in ids:
        try:
            data = json.loads(http_get(f"{GCN_BASE}/circulars/{cid}.json"))
        except Exception as e:
            print(f"  skip circular {cid}: {e}")
            continue
        created = datetime.fromtimestamp(data["createdOn"] / 1000, tz=timezone.utc)
        if created < cutoff:
            break  # 新しい順なので、時間窓を出たら打ち切り
        circulars.append(normalize_circular(data))
        time.sleep(0.3)  # サーバーへの負荷軽減
        if len(circulars) >= MAX_CIRCULARS:
            break
    return circulars


def fetch_recent_circulars_from_archive():
    """公式一括JSONアーカイブから直近分を拾うフォールバック。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    circulars = []
    raw = http_get(GCN_ARCHIVE, timeout=180)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        names = [
            name for name in archive.getnames()
            if re.fullmatch(r"archive\.json/\d+\.json", name)
        ]
        names.sort(key=lambda name: int(name.rsplit("/", 1)[1].split(".", 1)[0]), reverse=True)

        for name in names:
            member = archive.extractfile(name)
            if member is None:
                continue
            data = json.load(member)
            created = datetime.fromtimestamp(data["createdOn"] / 1000, tz=timezone.utc)
            if created < cutoff:
                break
            circulars.append(normalize_circular(data))
            if len(circulars) >= MAX_CIRCULARS:
                break
    return circulars


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
    with urllib.request.urlopen(req, timeout=300) as res:
        data = json.loads(res.read())
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
        f"[{i+1}] タイトル: {p['title']}\nアブストラクト: {p['abstract']}"
        for i, p in enumerate(papers)
    )
    prompt = (
        "以下は本日 arXiv に投稿された論文の一覧です。"
        "各論文のアブストラクトを読み、日本語で構造化してください。\n\n"
        "次の JSON のみを出力してください(前置きや ``` は不要):\n"
        '{"papers": [{"n": 1, "purpose": "目的(1文)", '
        '"abstract_jp": "アブストラクト全体の日本語要約(3〜4文)", '
        '"content": "どんなデータ・手法で何を議論しているか(1〜2文)", '
        '"conclusion": "何が分かったか・主な主張(1〜2文)"}, ...], '
        '"highlight": "今日のハイライト: 特に注目すべき論文1〜2本とその理由(2〜3文)"}\n\n'
        "専門用語は無理に訳さず残してください(例: QPO、ハードステート)。"
        "アブストラクトに書かれていないことは推測で補わないでください。\n\n"
        f"{paper_text}"
    )
    raw = call_llm(prompt, max_tokens=10000)

    try:
        data = parse_llm_json(raw)
        by_n = {item["n"]: item for item in data["papers"]}
    except Exception as e:
        print(f"JSON パース失敗、簡易一覧で出力します: {e}")
        return format_paper_fallback(papers)

    blocks = []
    for i, p in enumerate(papers):
        s = by_n.get(i + 1, {})
        blocks.append(
            f"### {i+1}. [{p['title']}]({p['url']})\n"
            f"- **目的**: {s.get('purpose', '(生成失敗)')}\n"
            f"- **アブストラクト**: {s.get('abstract_jp', s.get('content', ''))}\n"
            f"- **内容**: {s.get('content', '')}\n"
            f"- **結論**: {s.get('conclusion', '')}"
        )
    if data.get("highlight"):
        blocks.append(f"**🌟 {data['highlight']}**")
    return "\n\n".join(blocks)


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
            f"### {i+1}. [{p['title']}]({p['url']})\n"
            f"- **著者**: {', '.join(p['authors'][:6])}{' ほか' if len(p['authors']) > 6 else ''}\n"
            f"- **カテゴリ**: {', '.join(p['categories'])}\n"
            f"- **アブストラクト**: 自動要約に失敗したため、arXivリンク先を確認してください。"
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
        "以下は直近24時間に GCN (General Coordinates Network) に流れた"
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
        dates.insert(0, date_str)  # 新しい順
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(dates, f, ensure_ascii=False, indent=0)
    print(f"サイト用データを保存: docs/data/{date_str}.md")


# ---------------------------------------------------------------- main

def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = []

    # --- GCN 速報(失敗しても論文セクションは続行)---
    if INCLUDE_GCN:
        try:
            circulars = fetch_recent_circulars()
            if circulars:
                groups = group_by_event(circulars)
                print(f"GCN: {len(circulars)} 報 / {len(groups)} イベントを要約中...")
                gcn_summary = attach_gcn_source_links(summarize_gcn(groups), groups)
                parts.append(
                    f"## 🚨 新天体・トランジェント速報(GCN {len(circulars)}報 / {len(groups)}イベント)\n\n"
                    f"{gcn_summary}"
                )
            else:
                parts.append("## 🚨 新天体・トランジェント速報\n\n直近24時間の GCN Circular はありませんでした。")
        except Exception as e:
            print(f"GCN セクションの生成に失敗: {e}")
            parts.append(f"## 🚨 新天体・トランジェント速報\n\n取得エラーのため本日はスキップしました({e})")

    # --- arXiv 論文 ---
    papers = fetch_recent_papers()
    paper_window = "直近24時間"
    if not papers:
        papers = fetch_recent_papers(hours_back=24 * 7, max_papers=min(MAX_PAPERS, 8))
        paper_window = "直近1週間"
    if papers:
        print(f"arXiv: {len(papers)} 件の論文を要約中...")
        parts.append(
            f"## 📄 arXiv 新着論文({paper_window} / {len(papers)}件)\n\n"
            f"対象カテゴリ: {', '.join(CATEGORIES)}\n\n"
            + summarize_papers(papers)
        )
    else:
        parts.append("## 📄 arXiv 新着論文\n\n直近1週間の新着はありませんでした。")

    title = f"🔭 Astro Daily Digest — {today}"
    body = "\n\n---\n\n".join(parts)

    save_to_site(today, body)

    if os.environ.get("WEBHOOK_URL"):
        post_webhook(f"**{title}**\n\n{body}")
    elif os.environ.get("POST_GITHUB_ISSUE", "").lower() == "true":
        post_github_issue(title, body)
    else:
        print("Issue 投稿はスキップしました。")


if __name__ == "__main__":
    sys.exit(main())
