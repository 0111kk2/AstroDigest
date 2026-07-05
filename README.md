# Astro Daily Digest

X線天文・小型衛星・多波長観測・検出器分野の情報を毎日自動収集し、Claude API で日本語ダイジェストを作って通知するツールです。

## 毎日届く内容

1. **🚨 新天体・トランジェント速報** — GCN (General Coordinates Network) の直近24時間の Circulars を天体イベントごとにグループ化し、「何が起きたか・重要な測定値・追観測の状況」を要約。GRB、Einstein Probe の新X線トランジェント、IceCube ニュートリノなどが対象
2. **📄 arXiv 新着論文** — astro-ph.HE / astro-ph.IM / physics.ins-det の新着から、キーワード(X線・小型衛星・検出器など)関連度の高い論文を優先して要約

GitHub Actions が毎朝8時(日本時間)に実行し、次の2箇所に届きます:

- **メール通知**: リポジトリの Issue として投稿されます(Watch 設定でメールが届く)
- **フィードサイト**: タイムライン型のページに毎日積み重なり、スマホでスクロールして読めます(GitHub Pages、無料)

## セットアップ(5分)

1. **GitHub にリポジトリを作成**(Private でOK)し、このフォルダの中身をそのままアップロード。`.github/workflows/daily-digest.yml` のパスが崩れないように注意
2. **Gemini API キーを取得(無料)**: https://aistudio.google.com で Google アカウントでログインし、API キーを発行。課金設定をしなければ無料枠のまま使えます
3. **キーを登録**: リポジトリの `Settings → Secrets and variables → Actions → New repository secret` で、名前 `GEMINI_API_KEY`、値に API キーを貼り付けて保存
   - Claude を使いたい場合は代わりに `ANTHROPIC_API_KEY` を登録(https://console.anthropic.com で発行、従量課金)。両方登録した場合は Gemini が優先されます
4. **通知設定**: リポジトリ右上の `Watch → All Activity` にすると Issue 作成のたびにメールが届きます
5. **フィードサイトを有効化**: リポジトリの `Settings → Pages → Source: Deploy from a branch → Branch: main, フォルダ: /docs` を選んで Save。数分後に `https://あなたのID.github.io/リポジトリ名/` でサイトが見られるようになります
6. **動作テスト**: `Actions` タブ → `Astro Daily Digest` → `Run workflow` で手動実行。数分後に Issue とフィードサイトの両方に反映されていれば成功

## カスタマイズ(daily_digest.py 冒頭の設定)

- `CATEGORIES`: arXiv の対象分野。一覧は https://arxiv.org/category_taxonomy
- `KEYWORDS`: 優先キーワード。一致数の多い論文から要約されます。ミッション名や技術名を自由に追加可(空リスト `[]` で無効化)
- `MAX_PAPERS` / `MAX_CIRCULARS`: 論文・速報の1日あたり処理上限
- `INCLUDE_GCN`: `False` で速報セクションを無効化
- `GEMINI_MODEL` / `CLAUDE_MODEL`: 使用モデル。Gemini は無料枠対応の Flash 系を指定
- 実行時刻: `daily-digest.yml` の cron(UTC 表記。日本時間−9時間)

## Slack / Discord に切り替える

1. Slack の Incoming Webhook または Discord の Webhook URL を作成
2. リポジトリ Secrets に `WEBHOOK_URL` として登録
3. `daily-digest.yml` 内の `WEBHOOK_URL` の行のコメントアウトを外す

## コストの目安

- **Gemini(既定)**: 無料枠内で運用可能(1日数回の呼び出しなのでレート制限にも余裕)。ただし無料枠の入力データは Google の製品改善に使われる場合があります(送るのは公開情報のみなので実害はほぼなし)
- **Claude**: Haiku 使用で1日数円程度。料金: https://claude.com/pricing

GitHub Actions・arXiv・GCN の取得はすべて無料です。

## 補足

- GCN の取得はアーカイブページ(https://gcn.nasa.gov/circulars)の公開 JSON を利用しています。サイト構造が変わった場合、速報セクションだけスキップされ論文セクションは届き続けます
- ATel (The Astronomer's Telegram) など他の速報ソースも同じ枠組みで追加できます
