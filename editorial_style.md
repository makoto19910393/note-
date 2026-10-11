# 制作基準の参照先

恒久ルール・文章と画像の共通プロンプトは [editorial_prompt.md](editorial_prompt.md) に一本化。最新のユーザー指示を優先する。

過去112本を分析済み：無料73本・有料39本。無料本文の中央値5,572字、67本で本文画像4枚、69本でFAQ。現在の長さはユーザー指定の「内容に応じ3,000〜7,000字、水増しなし」を優先。

原本は .private-editorial/user-master-prompt.md、過去本文の取得資料は非公開。画像見本は config.json の style_reference_urls にある過去サムネと各図解①〜④。見本の旧事実・文字・ブランドは採用しない。Image API は gpt-image-2.5-sunburst / high。チャット画像生成ツール内部のモデル名は断定しない。Canvaは使用しない。

株価はnote標準の「$＋企業名→Enter」の埋め込み。価格・終値・値動きの取得や書き写しは行わない。
