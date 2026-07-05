# Astro Daily Digest

X線天文・小型衛星・多波長観測・検出器分野の情報を1時間ごとに自動収集し、Gemini API / Claude API で日本語ダイジェストを作ってフィード化するツールです。

## 届く内容

1. **🚨 新天体・トランジェント速報** — GCN (General Coordinates Network) の Circulars を天体イベントごとにグループ化し、「何が起きたか・重要な測定値・追観測の状況」を要約。GRB、Einstein Probe の新X線トランジェント、IceCube ニュートリノなどが対象
2. **🔭 TNS 新規・分類天体** — TNS (Transient Name Server) に新規登録・分類された天体を、座標・等級・ホスト天体・赤方偏移などとともに掲載
3. **🛰️ ATel 新着速報** — ATel (The Astronomer's Telegram) の新着投稿を取得し、原文リンク付きで速報内容を日本語要約
4. **🛰️ ミッション・観測所ニュース** — NASA Science、XRISM、NuSTAR、Chandra など各ミッション・観測所の発表から、X線・高エネルギー天文関連のニュースを抽出
5. **🇯🇵 国内X線天文・関連プレス** — ISAS/JAXA、JAXA、理研、東京大学、国立天文台の公開一覧から、XRISM・X線天文・関連検出器などの国内発表を抽出
6. **📄 arXiv 新着論文** — astro-ph.HE / astro-ph.IM / physics.ins-det の直近7日分から、キーワード(X線・小型衛星・検出器など)関連度の高い論文を優先してアブストラクトを和訳

GitHub Actions が**1時間ごと**に実行されます。各回は差分だけを見に行き、`.seen.json` に保存した既出ID/URLを使って、**新しく増えた情報だけ**をその日のフィードへ追記します。新着が1件もなければファイルも通知も更新せず静かにスキップします。

フィードサイトでは、上部のタブで **新天体 / プレス / 論文** を切り替えられます。各タブ内のカードはカテゴリ別の塊ではなく、掲載日時の新しい順にタイムライン表示されます。カレンダーの日付を押すと、その日の `00:00 JST` から翌日 `00:00 JST` までに取得したログだけを表示します。

結果は次のように保存・通知されます:

- **フィードサイト(既定・常時)**: `docs/data/` に保存されます。GitHub Pages を有効化すると、タイムライン型のページで新着のたびに積み重なり、スマホでスクロールして読めます
- **GitHub Issue 通知(任意)**: 既定では無効(`daily-digest.yml` の `POST_GITHUB_ISSUE` が `"false"`)。`"true"` に変更すると新着があった回だけ Issue として投稿され、Watch 設定でメールも届くようになります
- **Slack / Discord 通知(任意)**: `WEBHOOK_URL` を設定すると、新着があった回だけ Issue の代わりに Webhook 経由で送信されます(手順は後述)

## セットアップ(5分)

1. **GitHub にリポジトリを作成**(Private でOK)し、このフォルダの中身をそのままアップロード。`.github/workflows/daily-digest.yml` のパスが崩れないように注意
2. **Gemini API キーを取得(無料)**: https://aistudio.google.com で Google アカウントでログインし、API キーを発行。課金設定をしなければ無料枠のまま使えます
3. **キーを登録**: リポジトリの `Settings → Secrets and variables → Actions → New repository secret` で、名前 `GEMINI_API_KEY`、値に API キーを貼り付けて保存
   - Claude を使いたい場合は代わりに `ANTHROPIC_API_KEY` を登録(https://console.anthropic.com で発行、従量課金)。両方登録した場合は Gemini が優先されます
4. **フィードサイトを有効化**: リポジトリの `Settings → Pages → Source: Deploy from a branch → Branch: main, フォルダ: /docs` を選んで Save。数分後に `https://あなたのID.github.io/リポジトリ名/` でサイトが見られるようになります
5. **動作テスト**: `Actions` タブ → `Astro Daily Digest` → `Run workflow` で手動実行。数分後にフィードサイトに反映されていれば成功(対象期間に新着が無いと何も更新されないので、反映されない場合はもう一度実行するか `--backfill` で過去分を試すと確認しやすいです)
6. **(任意) Issue 通知を有効化**: メールでも受け取りたい場合は `daily-digest.yml` 内の `POST_GITHUB_ISSUE: "false"` を `"true"` に変更してコミットし、リポジトリ右上の `Watch → All Activity` を設定

## カスタマイズ(daily_digest.py 冒頭の設定)

- `CATEGORIES`: arXiv の対象分野。一覧は https://arxiv.org/category_taxonomy
- `KEYWORDS`: 優先キーワード。一致数の多い論文から要約されます。ミッション名や技術名を自由に追加可(空リスト `[]` で無効化)
- `MAX_PAPERS` / `MAX_CIRCULARS` / `MAX_ATELS` / `MAX_TNS` / `MAX_MISSION_NEWS` / `MAX_PRESS_RELEASES`: 各セクションの1回あたり処理上限
- `INCLUDE_GCN` / `INCLUDE_ATEL` / `INCLUDE_TNS` / `INCLUDE_MISSION_NEWS` / `INCLUDE_DOMESTIC_PRESS`: `False` でそれぞれのセクションを無効化
- `TNS_LOOKBACK_DAYS` / `MISSION_NEWS_LOOKBACK_DAYS` / `PRESS_LOOKBACK_DAYS`: 各セクションで遡って探す日数(HOURS_BACK より広めに探索し、期間内で未掲載のものを拾う)
- `PRESS_SOURCES` / `PRESS_KEYWORDS`: 国内プレスリリースの取得元とキーワード
- `MISSION_NEWS_SOURCES` / `MISSION_KEYWORDS`: ミッション・観測所ニュースの取得元(RSS/HTML)とキーワード
- `GEMINI_MODEL` / `CLAUDE_MODEL`: 使用モデル。Gemini は無料枠対応の Flash 系を指定
- `HOURS_BACK`: 通常実行(1時間おき)で何時間前まで遡るか。既定は2時間(cron間隔1h + Actions側の実行遅延バッファ)。実行頻度を変える場合はここも合わせて調整してください
- `BACKFILL_WINDOW_HOURS`: `--backfill` で過去1日を再現する際の窓(既定26時間、通常実行の `HOURS_BACK` とは独立)
- 実行頻度: `daily-digest.yml` の cron(UTC 表記)。既定は毎時7分

## 重複排除

各回の実行後、掲載済みのID/URLを `docs/data/.seen.json` に保存します。次回以降はこの一覧を見て、新しい差分だけを追記します。

- arXiv: arXiv ID
- GCN: Circular ID
- ATel: Telegram ID
- TNS: 天体名
- ミッション・観測所ニュース / 国内プレス: URL

さらにフィードサイト側でも、同じURLまたは同じタイトルのカードが過去ログに複数入っている場合は1件だけ表示します。これは過去の試行中に重複して保存されたデータを見えにくくするための保険です。

## 情報源の位置づけ

日々のスクリーニング用途としては、現在の情報源でかなり実用的です。速報は GCN / ATel / TNS、論文は arXiv、ミッション運用や国内発表は各機関ニュースを押さえています。

ただし、研究で引用・追跡する段階では以下も別途確認してください。

- **ADS**: 査読済み版、被引用、関連論文の確認
- **HEASARC / MAST / IRSA**: 実際の観測データや公開アーカイブの確認
- **各ミッション公式ページ**: ToO、観測計画、校正情報、運用制約
- **学会・研究会・BlueSky等**: コミュニティ内の議論や未整理の速報

このツールは「見落とし防止の新着監視」として使い、重要そうなものは原文リンク・ADS・観測アーカイブへ進む前提です。

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

GitHub Actions・arXiv・GCN・TNS・ATel・各ミッションニュースの取得はすべて無料です。

## 補足

- 各セクションは独立して取得・要約しており、1つが失敗(サイト側のレート制限など)しても他のセクションは通常どおり届きます
- GCN の取得は一括アーカイブ(https://gcn.nasa.gov/circulars)の公開 JSON を優先します。失敗した場合も他セクションは継続し、取得できなかったセクションのプレースホルダーはフィードに出しません
- TNS の取得は https://www.wis-tns.org の公開 CSV 検索を利用しています
- ATel の取得はトップページの公開新着表を利用しています。個別本文が取得制限にかかった場合も、タイトル・著者・投稿時刻・原文リンクを使って分かる範囲だけを掲載します
- ミッション・観測所ニュースは `MISSION_NEWS_SOURCES` の RSS/HTML を巡回して、`MISSION_KEYWORDS` に一致するものだけ抽出します
- GitHub Actions の `schedule` は正確な時刻には実行されません(特に毎時00分は混みやすく遅延しがち)。そのため cron は毎時00分を避けて設定しており、`HOURS_BACK` にも1時間分のバッファを持たせています。数分〜十数分程度の実行ズレは想定内です
- 同じ実行が重複しないよう `concurrency` で直列化しています。前回実行が長引いた場合、次の回は開始が遅れることがあります
