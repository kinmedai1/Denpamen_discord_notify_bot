# 📅 denpamen bot セットアップガイド

このガイドでは、Discord スケジュール通知Bot を動作させるまでのすべての手順を説明します。

---

## 目次

1. [Discord Bot の作成](#1-discord-bot-の作成)
2. [Discord サーバーのチャンネル設定](#2-discord-サーバーのチャンネル設定)
3. [Google Sheets の設定](#3-google-sheets-の設定)
4. [環境構築](#4-環境構築)
5. [Bot の起動](#5-bot-の起動)
6. [Windows 自動起動設定](#6-windows-自動起動設定)
7. [GitHub Actions の設定（定期通知用）](#7-github-actions-の設定定期通知用)
8. [トラブルシューティング](#8-トラブルシューティング)
9. [公式サイト新着通知の設定（オプション）](#9-公式サイト新着通知の設定オプション)
10. [定期（永遠）イベントの自動生成（オプション）](#10-定期永遠イベントの自動生成オプション)

---

## 1. Discord Bot の作成

### 1.1 アプリケーションの作成

1. [Discord Developer Portal](https://discord.com/developers/applications) にアクセス
2. 右上の **「New Application」** をクリック
3. アプリケーション名を入力（例: `denpamen bot`）→ **「Create」**

### 1.2 Bot トークンの取得

1. 左メニューの **「Bot」** を選択
2. **「Reset Token」** をクリックしてトークンを生成
3. 表示されたトークンを**安全な場所にコピー**（後で `.env` に設定します）

> ⚠️ **重要:** トークンは一度しか表示されません。紛失した場合はリセットが必要です。

### 1.3 Bot の権限設定（Intents）

「Bot」ページの **Privileged Gateway Intents** セクションで以下を有効化：

- [x] **SERVER MEMBERS INTENT** — （任意）メンバー情報の取得
- [x] **MESSAGE CONTENT INTENT** — メッセージ内容の読み取り

### 1.4 Bot をサーバーに招待

1. 左メニューの **「OAuth2」** → **「URL Generator」** を選択
2. **SCOPES** で `bot` にチェック
3. **BOT PERMISSIONS** で以下にチェック：
   - `Send Messages` — メッセージ送信
   - `Embed Links` — Embed の送信
   - `Attach Files` — ファイル添付（ガントチャート画像）
   - `Read Message History` — メッセージ履歴の読み取り
   - `Use External Emojis` — 外部絵文字の使用
4. 生成されたURLをコピーしてブラウザで開く
5. Bot を追加したいサーバーを選択して **「認証」**

---

## 2. Discord サーバーのチャンネル設定

### 2.1 チャンネルの作成

Bot の操作と通知を分離するため、2つのチャンネルを作成します：

| チャンネル名 | 用途 | 説明 |
|-------------|------|------|
| `#bot操作` | ボタン操作 | 固定メッセージ（コントロールパネル）が表示されるチャンネル |
| `#通知` | 通知受信 | 定期通知・リマインダーが投稿されるチャンネル |
| `#電波人間公式サイト通知` | サイト更新 | 公式サイトの新着記事が通知されるチャンネル（オプション） |

**作成手順:**
1. サーバーで右クリック → **「チャンネルを作成」**
2. チャンネル名を入力（例: `bot操作`）
3. テキストチャンネルを選択 → **「チャンネルを作成」**
4. `#通知` チャンネルも同様に作成

### 2.2 チャンネルIDの取得

1. Discord の **設定** → **詳細設定** → **「開発者モード」を ON** にする
2. `#bot操作` チャンネルを右クリック → **「チャンネルIDをコピー」**
3. メモしておく（後で `.env` の `OPERATION_CHANNEL_ID` に設定）
4. `#通知` チャンネルも同様にIDをコピー（`NOTIFICATION_CHANNEL_ID` に設定）

### 2.3 通知チャンネルの Webhook URL 作成（GitHub Actions 用）

1. `#通知` チャンネルの **設定**（⚙️アイコン）を開く
2. **「連携サービス」** → **「ウェブフックを作成」**
3. 名前を設定（例: `denpamen bot notifications`）
4. **「ウェブフックURLをコピー」** → メモ（`.env` の `DISCORD_WEBHOOK_URL` に設定）

### 2.4 チャンネル権限の設定（推奨）

`#bot操作` チャンネルでは、Bot のメッセージのみ表示し、ユーザーの書き込みを制限することを推奨します：

1. `#bot操作` チャンネル設定 → **「権限」**
2. **@everyone** の権限:
   - `メッセージを送信` → ❌ 拒否
   - `メッセージ履歴を読む` → ✅ 許可
3. **Bot のロール** の権限:
   - `メッセージを送信` → ✅ 許可

---

## 3. Google Sheets の設定

### 3.1 スプレッドシートの作成

1. [Google Sheets](https://sheets.google.com) で**新しいスプレッドシート**を作成
2. スプレッドシート名を設定（例: `denpamen_schedule`）
3. **1行目にヘッダーを入力**（Bot が自動作成しますが、手動で設定しても構いません）：

| A | B | C | D | E | F | G |
|---|---|---|---|---|---|---|
| ID | タイトル | 開始日 | 終了日 | 説明 | 担当者 | 作成日 |

4. URLからスプレッドシートIDをコピー:
   ```
   https://docs.google.com/spreadsheets/d/【ここがスプレッドシートID】/edit
   ```

### 3.2 Google Cloud サービスアカウントの作成

1. [Google Cloud Console](https://console.cloud.google.com) にアクセス
2. 新しいプロジェクトを作成（または既存のプロジェクトを選択）
3. **「APIとサービス」** → **「ライブラリ」** で以下を有効化:
   - **Google Sheets API**
   - **Google Drive API**
4. **「APIとサービス」** → **「認証情報」** → **「認証情報を作成」** → **「サービスアカウント」**
5. サービスアカウント名を入力（例: `denpamen-bot`）→ **「作成して続行」**
6. ロール: **「編集者」** を選択 → **「完了」**
7. 作成したサービスアカウントをクリック → **「鍵」タブ** → **「鍵を追加」** → **「新しい鍵を作成」**
8. **JSON** を選択 → **「作成」**
9. ダウンロードされたJSONファイルを `service_account.json` にリネームして**プロジェクトルートに配置**

### 3.3 スプレッドシートをサービスアカウントと共有

1. ダウンロードした JSON ファイルを開き、`client_email` の値をコピー
   ```json
   "client_email": "denpamen-bot@your-project.iam.gserviceaccount.com"
   ```
2. Google Sheets のスプレッドシートを開く
3. 右上の **「共有」** → コピーしたメールアドレスを入力
4. 権限を **「編集者」** に設定 → **「送信」**

---

## 4. 環境構築

### 4.1 Python のインストール

> Python 3.11 以上が必要です。

[Python 公式サイト](https://www.python.org/downloads/) からダウンロード・インストール。

インストール時に **「Add Python to PATH」** にチェックを入れてください。

### 4.2 依存パッケージのインストール

```bash
cd denpamen_bot
pip install -r requirements.txt
```

### 4.3 .env ファイルの作成

`.env.example` をコピーして `.env` を作成し、各値を設定します：

```bash
copy .env.example .env
```

```env
# Discord Bot トークン（手順1.2で取得）
DISCORD_BOT_TOKEN=your_bot_token_here

# Discord Webhook URL（手順2.3で取得、GitHub Actions用）
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# チャンネルID（手順2.2で取得）
OPERATION_CHANNEL_ID=123456789012345678
NOTIFICATION_CHANNEL_ID=987654321098765432

# Google Sheets（手順3.1で取得）
GOOGLE_SHEETS_ID=your_spreadsheet_id_here
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json

# 固定メッセージID（初回起動時に自動設定されます）
CONTROL_MESSAGE_ID=
```

---

## 5. Bot の起動

### 5.1 手動起動

```bash
cd denpamen_bot
python src/bot.py
```

正常に起動すると以下のようなログが表示されます：

```
2026-03-10 09:00:00 [INFO] __main__: ログイン成功: denpamen bot#1234 (ID: 123456789)
2026-03-10 09:00:00 [INFO] __main__: 接続サーバー数: 1
2026-03-10 09:00:01 [INFO] __main__: 新しい固定メッセージを作成しました (ID: 111222333)
2026-03-10 09:00:01 [INFO] __main__: Bot の起動が完了しました 🟢
```

### 5.2 動作確認

1. `#bot操作` チャンネルに固定メッセージ（コントロールパネル）が表示されていることを確認
2. **➕ 追加** ボタンを押して、Modal が表示されることを確認
3. テストスケジュールを登録し、Google Sheets に反映されることを確認
4. **📋 一覧** ボタンで登録したスケジュールが表示されることを確認

---

## 6. Windows 自動起動設定

PC 起動時に Bot を自動的に開始するには、Windows タスクスケジューラを使用します。

### 6.1 バッチファイルの作成

プロジェクトルートに `start_bot.bat` を作成：

```bat
@echo off
cd /d "%~dp0"
python src/bot.py
```

### 6.2 タスクスケジューラの設定

1. **Win + R** → `taskschd.msc` と入力 → Enter
2. 右側の **「タスクの作成」** をクリック
3. **全般タブ:**
   - 名前: `denpamen bot`
   - 「最上位の特権で実行する」にチェック
4. **トリガータブ:**
   - **「新規」** → 「ログオン時」を選択
5. **操作タブ:**
   - **「新規」** → プログラム: `start_bot.bat` のフルパスを指定
   - 開始（オプション）: プロジェクトルートのパス
6. **条件タブ:**
   - 「コンピューターを AC 電源で使用している場合のみ～」のチェックを外す
7. **設定タブ:**
   - 「タスクが失敗した場合の再起動」にチェック → 1分おきに3回再起動
8. **「OK」** をクリックして保存

---

## 7. GitHub Actions の設定（定期通知用）

PCがオフでも定期通知を送信するための設定です。

### 7.1 リポジトリの作成

1. [GitHub](https://github.com) にログイン
2. 右上の **「+」** → **「New repository」** をクリック
3. 以下を設定：
   - **Repository name:** `denpamen-bot`（任意の名前）
   - **Description:** スケジュール通知Bot（任意）
   - **Public / Private:** **🔒 Private を推奨**

> ⚠️ **Private を推奨する理由:**
> - `.env` や `service_account.json` を誤って公開するリスクを避けるため
> - GitHub Actions は Private リポジトリでも無料枠（月2,000分）で動作します
> - Public にすると誰でもコードを閲覧できます

4. その他のオプション（README、.gitignore等）は**チェックしない**（既にプロジェクトにファイルがあるため）
5. **「Create repository」** をクリック

### 7.2 Git の初期設定（初回のみ）

Git を初めて使う場合は、ユーザー名とメールアドレスを設定します：

```bash
git config --global user.name "あなたのユーザー名"
git config --global user.email "あなたのメールアドレス"
```

> 💡 ここで設定する名前・メールはコミット履歴に記録されます。GitHub のアカウント名・メールと合わせるのがおすすめです。

### 7.3 プロジェクトのコミットとプッシュ

#### ステップ1: .gitignore の確認

プッシュ前に、機密ファイルが除外されていることを確認します。プロジェクトの `.gitignore` に以下が含まれていることを確認してください：

```
.env
service_account.json
bot.log
__pycache__/
```

> ⚠️ **重要:** `.env` と `service_account.json` は絶対に GitHub にアップロードしないでください。トークンやAPIキーが含まれています。

#### ステップ2: Git リポジトリの初期化

コマンドプロンプト（またはターミナル）を開き、プロジェクトフォルダに移動して以下を順番に実行します：

```bash
cd denpamen_bot
```

```bash
git init
```

> 💡 `git init` はこのフォルダを Git リポジトリとして初期化します。`.git` フォルダが作成されます。

#### ステップ3: ファイルをステージングに追加

```bash
git add .
```

> 💡 `git add .` は `.gitignore` で除外されていない全ファイルをコミット対象に追加します。

追加されるファイルを確認したい場合は：

```bash
git status
```

赤色のファイルは未追加、緑色は追加済みです。`.env` や `service_account.json` が表示されていないことを確認してください。

#### ステップ4: コミット（変更の記録）

```bash
git commit -m "初回コミット"
```

> 💡 `-m` の後の文字列はコミットメッセージです。どんな変更をしたかの説明を書きます。

#### ステップ5: リモートリポジトリの接続

GitHub で作成したリポジトリのURLを設定します（URLは作成したリポジトリのページに表示されています）：

```bash
git remote add origin https://github.com/あなたのユーザー名/denpamen-bot.git
```

#### ステップ6: プッシュ（GitHubにアップロード）

```bash
git push -u origin main
```

> 💡 初回のプッシュ時に GitHub のログインを求められる場合があります。ブラウザが開いて認証を求められたら、GitHubアカウントでログインしてください。

> ⚠️ エラー `error: src refspec main does not match any` が出た場合は、ブランチ名が `master` の可能性があります。以下を試してください：
> ```bash
> git branch -M main
> git push -u origin main
> ```

#### 今後の変更をプッシュする場合

コードを修正した後、変更を GitHub に反映する手順：

```bash
git add .
git commit -m "変更内容の説明"
git push
```

### 7.4 Repository Secrets の設定

GitHub Actions からDiscordやGoogle Sheetsにアクセスするために、機密情報を「Secrets」として登録します。

#### Secrets 設定画面の開き方

1. GitHubで自分のリポジトリ（`denpamen-bot`）を開く
2. 上部の **「Settings」** タブをクリック（⚙️ アイコン）
3. 左メニューの **「Secrets and variables」** → **「Actions」** をクリック
4. **「New repository secret」** ボタンをクリック

#### Secret ①: DISCORD_WEBHOOK_URL

1. **Name** に `DISCORD_WEBHOOK_URL` と入力
2. **Secret** に 手順2.3で作成した Webhook URL を貼り付け
   - 例: `https://discord.com/api/webhooks/123456789/abcdefg...`
3. **「Add secret」** をクリック

#### Secret ②: GOOGLE_SHEETS_ID

1. **「New repository secret」** をクリック
2. **Name** に `GOOGLE_SHEETS_ID` と入力
3. **Secret** に Google Sheets のスプレッドシートID を貼り付け
   - スプレッドシートのURLの `https://docs.google.com/spreadsheets/d/【この部分】/edit` がIDです
4. **「Add secret」** をクリック

#### Secret ③: GOOGLE_SERVICE_ACCOUNT_JSON

1. **「New repository secret」** をクリック
2. **Name** に `GOOGLE_SERVICE_ACCOUNT_JSON` と入力
3. **Secret** に `service_account.json` の**中身全体**を貼り付け:
   - プロジェクトフォルダの `service_account.json` をメモ帳などで開く
   - **Ctrl + A**（全選択）→ **Ctrl + C**（コピー）
   - GitHub の Secret 入力欄に **Ctrl + V**（貼り付け）
   - `{` で始まり `}` で終わるJSON全体が入っていればOK
4. **「Add secret」** をクリック

#### Secret ④: DISCORD_BOT_TOKEN

1. **「New repository secret」** をクリック
2. **Name** に `DISCORD_BOT_TOKEN` と入力
3. **Secret** に 手順1.2で取得したBotのトークンを貼り付け
4. **「Add secret」** をクリック

#### Secret ⑤: NOTIFICATION_CHANNEL_ID

1. **「New repository secret」** をクリック
2. **Name** に `NOTIFICATION_CHANNEL_ID` と入力
3. **Secret** に `#通知` チャンネルのID（手順2.2で取得）を貼り付け
4. **「Add secret」** をクリック

#### Secret ⑥: DISCORD_WEBSITE_WEBHOOK_URL（オプション）

1. **「New repository secret」** をクリック
2. **Name** に `DISCORD_WEBSITE_WEBHOOK_URL` と入力
3. **Secret** に 公式サイト通知用の Webhook URL（手順9.1で取得）を貼り付け
4. **「Add secret」** をクリック

> ✅ 設定完了後、Secrets 一覧に最大6つのシークレットが表示されていれば成功です。
> なお、登録した値は**二度と表示されません**（更新は可能です）。

### 7.5 通知スケジュールの変更

`.github/workflows/notify.yml` の `cron` を編集してスケジュールを変更できます：

```yaml
on:
  schedule:
    # cron形式: 分 時(UTC) 日 月 曜日
    - cron: '0 0 * * 1'     # 毎週月曜 09:00 JST
    - cron: '0 0 * * 5'     # 毎週金曜 09:00 JST（追加例）
    - cron: '0 23 * * 0'    # 毎週日曜 08:00 JST（追加例）
```

> 💡 **JSTからUTCへの変換:** JST時間 − 9時間 = UTC時間

### 7.6 手動テスト

1. GitHub リポジトリの **Actions** タブを開く
2. 左側の **「📅 定期スケジュール通知」** を選択
3. **「Run workflow」** → **「Run workflow」** をクリック
4. `#通知` チャンネルに通知が届くことを確認

---

## 8. トラブルシューティング

### Bot がオンラインにならない

- `.env` の `DISCORD_BOT_TOKEN` が正しいか確認
- トークンをリセットして再設定してみる
- ファイアウォールが Discord への接続をブロックしていないか確認

### ボタンが反応しない

- Bot が起動していることを確認
- Bot を再起動（固定メッセージは自動的に再接続されます）
- `CONTROL_MESSAGE_ID` が `.env` に正しく設定されているか確認

### Google Sheets に接続できない

- `service_account.json` がプロジェクトルートにあるか確認
- サービスアカウントのメールアドレスがスプレッドシートに共有されているか確認
- Google Sheets API と Google Drive API が有効になっているか確認

### GitHub Actions の通知が届かない

- Repository Secrets が正しく設定されているか確認
- Actions タブでワークフローの実行ログを確認
- `DISCORD_WEBHOOK_URL` が有効か確認（ブラウザで直接アクセスしてみる）

### 日本語が文字化けする

- ガントチャートの場合: 日本語フォントがインストールされているか確認
- Windows: `Yu Gothic` または `Meiryo` フォントが必要

---

## 9. 公式サイト新着通知の設定（オプション）

『New 電波人間のRPG FREE！』公式サイトの新着記事を1時間ごとにチェックし、新着があれば通知＆スケジュールへ自動登録する機能の設定です。

### 9.1 専用チャンネルの作成と Webhook 設定

1. Discord サーバーで新しいチャンネルを作成（例: `#電波人間公式サイト通知`）
2. 作成したチャンネルの **設定** → **連携サービス** → **ウェブフックを作成**
3. **「ウェブフックURLをコピー」** しておく

### 9.2 動作の仕組み

- **定期実行**: 1時間ごとに公式サイトをチェックします（GitHub Actions `website_notify.yml`）
- **新着通知**: 記事が更新されると、指定したチャンネルに通知が届きます
- **自動登録**: 「イベント情報」などの期間が記載された記事の場合、**Google Sheets に自動的にスケジュールが追加**されます
  - 担当者は `公式サイト` 、説明に記事URLが設定されます
- **初回実行時**: 初回実行時は、全ての既存記事を「既知」として登録するため、通知は送信されません（大量通知を防ぐため）

---

## 10. 定期（永遠）イベントの自動生成（オプション）

毎週決まった曜日や、偶数日などに永遠に繰り返されるイベントは、Discord やスプレッドシートから手動で登録せず、`config.json` にルールを書いておくことで**自動的に今日から60日先まで仮のスケジュールとして生成**させることができます。

### 9.1 設定方法

プロジェクトフォルダにある `config.json` を開き、末尾の `recurring_events` 配列に以下のようにルールを追加します：

```json
  "recurring_events": [
    {
      "title": "毎週日曜の定期イベント",
      "type": "weekly",
      "days_of_week": [6],
      "description": "毎週日曜日に自動生成",
      "assignee": "System"
    },
    {
      "title": "毎月偶数日のイベント",
      "type": "even_days",
      "description": "偶数日に自動生成",
      "assignee": "System"
    }
  ]
```

### 9.2 サポートしている `type`
- `"weekly"`: `days_of_week` で曜日を指定（0=月曜, 1=火曜 ... 6=日曜）。配列で複数指定可能（例：`[2, 4]` で水・金）。
- `"even_days"`: 毎月偶数日（2, 4, 6...日）に生成。
- `"odd_days"`: 毎月奇数日（1, 3, 5...日）に生成。
- `"daily"`: 毎日生成。

これらはDiscordの「📋 一覧」コマンドや、定期通知のガントチャートなどの一覧に自動で組み込まれて表示されます。
※スプレッドシートには直接書き込まれないため、データの肥大化を防げます。
