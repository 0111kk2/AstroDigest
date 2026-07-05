# Astro Daily Digest

X線天文・小型衛星・多波長観測・検出器分野の情報を毎日自動収集し、Claude API で日本語ダイジェストを作って通知するツールです。

## 毎日届く内容

1. **🚨 新天体・トランジェント速報** — GCN (General Coordinates Network) の直近24時間の Circulars を天体イベントごとにグループ化し、「何が起きたか・重要な測定値・追観測の状況」を要約。GRB、Einstein Probe の新X線トランジェント、IceCube ニュートリノなどが対象
2. **🛰️ ATel 新着速報** — ATel (The Astronomer's Telegram) の新着投稿を取得し、原文リンク付きで速報内容を日本語要約
3. **📄 arXiv 新着論文** — astro-ph.HE / astro-ph.IM / physics.ins-det の新着から、キーワード(X線・小型衛星・検出器など)関連度の高い論文を優先して要約

GitHub Actions が毎朝8時(日本時間)に実行され、結果は次のように保存・通知されます:

- **フィードサイト(既定・常時)**: `docs/data/` に保存されます。GitHub Pages を有効化すると、タイムライン型のページに毎日積み重なり、スマホでスクロールして読めます(無料)
- **GitHub Issue 通知(任意)**: 既定では無効(`daily-digest.yml` の `POST_GITHUB_ISSUE` が `"false"`)。`"true"` に変更すると Issue として投稿され、Watch 設定でメールも届くようになります
- **Slack / Discord 通知(任意)**: `WEBHOOK_URL` を設定すると、その日は Issue の代わりに Webhook 経由で送信されます(手順は後述)

## セットアップ(5分)

1. **GitHub にリポジトリを作成**(Private でOK)し、このフォルダの中身をそのままアップロード。`.github/workflows/daily-digest.yml` のパスが崩れないように注意
2. **Gemini API キーを取得(無料)**: https://aistudio.google.com で Google アカウントでログインし、API キーを発行。課金設定をしなければ無料枠のまま使えます
3. **キーを登録**: リポジトリの `Settings → Secrets and variables → Actions → New repository secret` で、名前 `GEMINI_API_KEY`、値に API キーを貼り付けて保存
   - Claude を使いたい場合は代わりに `ANTHROPIC_API_KEY` を登録(https://console.anthropic.com で発行、従量課金)。両方登録した場合は Gemini が優先されます
4. **フィードサイトを有効化**: リポジトリの `Settings → Pages → Source: Deploy from a branch → Branch: main, フォルダ: /docs` を選んで Save。数分後に `https://あなたのID.github.io/リポジトリ名/` でサイトが見られるようになります
5. **動作テスト**: `Actions` タブ → `Astro Daily Digest` → `Run workflow` で手動実行。数分後にフィードサイトに反映されていれば成功
6. **(任意) Issue 通知を有効化**: メールでも受け取りたい場合は `daily-digest.yml` 内の `POST_GITHUB_ISSUE: "false"` を `"true"` に変更してコミットし、リポジトリ右上の `Watch → All Activity` を設定

## カスタマイズ(daily_digest.py 冒頭の設定)

- `CATEGORIES`: arXiv の対象分野。一覧は https://arxiv.org/category_taxonomy
- `KEYWORDS`: 優先キーワード。一致数の多い論文から要約されます。ミッション名や技術名を自由に追加可(空リスト `[]` で無効化)
- `MAX_PAPERS` / `MAX_CIRCULARS` / `MAX_ATELS`: 論文・速報の1日あたり処理上限
- `INCLUDE_GCN`: `False` で速報セクションを無効化
- `INCLUDE_ATEL`: `False` で ATel セクションを無効化
- `GEMINI_MODEL` / `CLAUDE_MODEL`: 使用モデル。Gemini は無料枠対応の Flash 系を指定
- 実行時刻: `daily-digest.yml` の cron(UTC 表記。日本時間−9時間)

## Slack / Discord に切り替える

1. Slack の Incoming Webhook または Discord の Webhook URL を作成
2. リポジトリ Secrets に `WEBHOOK_URL` として登録
3. `daily-digest.yml` の `Generate digest` ステップの `env:` に次の行を追加:

   ```yaml
   WEBHOOK_URL: ${{ secrets.WEBHOOK_URL }}
   ```

   (設定すると、その日は GitHub Issue の代わりに Webhook で送信されます)

## 過去分をまとめて取得する(バックフィル)

運用を始めたばかりでフィードサイトの「1週間」表示に十分な過去データがない場合、以下のコマンドで昨日から遡って7日分をまとめて生成できます(`docs/data/` に既にある日付はスキップされます)。

```bash
GEMINI_API_KEY=... python daily_digest.py --backfill 7
```

日数を変えたい場合は引数を変更してください(例: `--backfill 14`)。ローカル実行後は `docs/data/` の変更をコミット・プッシュするとサイトに反映されます。

## コストの目安

- **Gemini(既定)**: 無料枠内で運用可能(1日数回の呼び出しなのでレート制限にも余裕)。ただし無料枠の入力データは Google の製品改善に使われる場合があります(送るのは公開情報のみなので実害はほぼなし)
- **Claude**: Haiku 使用で1日数円程度。料金: https://claude.com/pricing

GitHub Actions・arXiv・GCN・ATel の取得はすべて無料です。

## 補足

- GCN の取得はアーカイブページ(https://gcn.nasa.gov/circulars)の公開 JSON を利用しています。サイト構造が変わった場合、速報セクションだけスキップされ論文セクションは届き続けます
- ATel の取得はトップページの公開新着表を利用しています。個別本文が取得制限にかかった場合も、タイトル・著者・投稿時刻・原文リンクを使って分かる範囲だけを掲載します
