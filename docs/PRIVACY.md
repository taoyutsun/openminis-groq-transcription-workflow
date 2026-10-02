# 公開範圍與資料流

## 允許發布的內容

三支 Python 程式、README、CHANGELOG、LICENSE、`.gitignore`、測試程式、這份文件與測試範圍說明。測試資料在暫存目錄自行產生，無須附上真實錄音或成果。

完整開發對話只供作者撰文參考，不屬於公開專案；不複製、不匯入 Git 歷史、不包入 ZIP。公開文件使用示意檔名與通用目錄。

## 不應發布的內容

API Key、Token、環境設定、手機備份、錄音、逐字稿、SRT、摘要、`chunk_*.json`、`摘要進度.json`、`metadata.json`、真實診斷日誌、會話及供應商設定 ID、Windows／WSL 私人路徑。

`.gitignore` 是輔助，正式打包使用明確檔案清單。ignore 規則不能移除已進入 Git 歷史的檔案。

## 實際資料流

1. 使用者分享或掛載來源資料夾，腳本只讀來源。
2. 手機 Linux 環境解碼並切分，臨時 FLAC 在暫存目錄。
3. 音訊片段透過 HTTPS 傳至 Groq 的 `audio/transcriptions`；multipart 檔名固定為 `chunk.flac`。
4. 辨識結果、時間戳及續跑資料寫入你指定的輸出位置。
5. 逐字稿及分段摘要傳至 Groq 的 `chat/completions`，取得繁中摘要。
6. 檔案由使用者透過 Open Minis／手機檔案管理方式取用；本程式不自動上傳 GitHub。

外部錄音檔名不作為摘要 prompt，但可出現在你自己的輸出檔名、終端機畫面與 `metadata.json`。輸出 JSON 有實際辨識文字，不能只因副檔名是 JSON 就當成無個資的技術檔。

## 憑證及錯誤處理

使用 `GROQ_API_KEY` 環境變數。金鑰只交給 Python 的 HTTP Authorization header；沒有帶金鑰的 curl 命令、沒有將 Key 寫入結果。拒絕 HTTP 重新導向，避免 Authorization 隨導向送往其他端點。API 錯誤不輸出回應正文，例外另有遮蔽。

這些措施處理的是腳本日誌與子程序參數，並不代表環境變數、Python 程序記憶體、手機備份或 Agent 執行環境無法讀到憑證。請自行管理裝置及備份存取權限。

Groq 資料保留規則及帳戶資料控制可能更新，請查看 [Groq 官方資料說明](https://console.groq.com/docs/your-data)。
