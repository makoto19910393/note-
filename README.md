# mako：朝・夜のAIニュース執筆とnote公開

日本時間の毎朝7時・毎夜19時に、最新AIニュースの調査、執筆、原稿保存、note公開を行います。
GitHub Actionsで実行するため、Macやこのチャットを開いたままにする必要はありません。
GitHubの定時実行には遅延や未実行があり、分単位の厳密な時刻は保証されません。

## 現在の設定

- note：https://note.com/quiet_spirea2390
- 初回の記事No.1：https://note.com/quiet_spirea2390/n/nb987b2314fe7
- 2026-10-11、note下書き保存、公開操作、未ログインでのタイトル・本文表示を確認。
- config.jsonのpublisher.modeはpublish、ui_verifiedはtrue。定時実行で公開します。
- 記事番号はNo.1から新しく開始。過去のDriveやスプレッドシートは変更していません。
- 既存のnote記事112件の公開タイトル・URLを重複回避に使用。全記事の本文調査ではありません。
- 現在の定時投稿は無料AIニュースです。1,900円の有料副業シリーズは方針として保存し、まだ自動公開しません。

## 記事の方針

直近48時間の情報を調べ、最低2件の参考資料と、直近の資料を最低1件確認します。
大手AIブランドを優先し、既出のタイトルや参考資料との重複を避けます。
海外は【海外AI解説】、国内は【国内AI学び】でタイトルを始め、学生向けという文言は付けません。
構成は30秒で結論、本文、私の正直な感想、まとめ、参考リンク。
事実と推測を区別し、本人が経験していない体験談・収益実績を作りません。

## 実行と保存

.github/workflows/note-drafts.ymlがUTC22時と10時に起動します。日本時間では7時と19時です。
OpenAI Responses APIのweb_searchで調査し、content/articlesにMarkdown原稿を保存します。
本文の入力を確認し、noteの無料記事として公開します。新しい未ログインのブラウザで
公開記事のタイトルと本文の見出しを確認してから、公開済み状態を記録します。
原稿とcontent/state.jsonはGitHubへ保存し、Actionsの成果物も30日間保管します。

認証情報の値をコード、原稿、ログに表示する処理はありません。
現在はGitHub SecretsのNOTE（またはOPENAI_API_KEY）をAI APIキー、
NOTE_STORAGE_STATEをnoteのログイン状態として使用します。
MAIL（またはNOTE_EMAIL）とNOTE_PASSWORDによるログインも実装していますが、
クラウド接続ではnoteがreCAPTCHAを要求したため、手動で取得したログイン状態を優先します。
API料金は購入済みクレジットから消費されます。ログイン期限切れや残高不足では処理が止まります。

## 手動実行

GitHubのActions → 朝と夜のnote原稿 → Run workflowで実行します。
modeのautoはconfig.jsonとVariablesの設定に従います。draftは下書きまで、publishは公開まで。
run_idを指定すると既存原稿を使用し、AI APIによる記事の再生成は行いません。
同じ時間枠の原稿や公開済み記事は重複作成・再投稿しません。

## 停止と復旧

全停止はGitHub ActionsでワークフローをDisable workflowにします。
投稿だけを止める場合はVariablesのNOTE_MODEをdisabledにします。原稿作成は続きます。
Variablesの指定があればconfig.jsonより優先します。

公開直前にpublishing状態をGitHubへ保存します。保存に失敗したら公開ボタンを押しません。
公開結果が不明ならpublication_uncertainにし、その原稿の自動再投稿を止めます。
実際に公開済みの場合は、同じrun_idとmode=publish、verify_only=trueで
再投稿せず公開状態を確認・復旧できます。未公開の場合は自動復旧できないので確認が必要です。

noteログインが切れた場合はMac用のnote-connect-mac-v2.zipを使用します。
ZIPを展開しconnect-note-mac.commandを開き、案内ボタンに従って本人がログインします。
コピー済みの接続情報をGitHub SecretsのNOTE_STORAGE_STATEに保存します。
途中であとで続けるを選んだ場合は、同じファイルを再実行して登録を再開できます。
接続情報はMacのLibrary/Application Support/note-connectに本人だけが読める権限で保存し、
登録できたを選ぶと削除します。Google/AppleのCookieは書き出しません。

接続情報やパスワードをチャット、Gitのファイル、共有ドライブに貼り付けないでください。
既存のNOTE_STORAGE_STATEがある場合は、GitHubの登録一覧で鉛筆ボタンから更新します。

## 開発時の確認

python -m unittest discover -s tests -vで保存、重複防止、参照資料検証、失敗時の処理を確認します。
投稿用の依存関係はrequirements-publish.txt、公開ブラウザの調査はbrowser_probe.pyです。
TLS証明書の検証は有効なまま使用します。
