Bingo Bingo ver6.3.0（線型趨勢分析版）
=====================================

本版修正 ver6.2 無資料及前端 SyntaxError: Unexpected token '<' 問題。

資料來源
--------
台灣彩券官方賓果賓果頁面：
https://www.taiwanlottery.com/lotto/result/bingo_bingo/

官方 API：
LatestBingoResult
https://api.taiwanlottery.com/TLCAPIWeB/Lottery/LatestBingoResult

BingoResult
https://api.taiwanlottery.com/TLCAPIWeB/Lottery/BingoResult

官方 API 資料格式
----------------
LatestBingoResult:
content.lotteryBingoLatestPost
- drawTerm
- dDate
- bigShowOrder（排序後20個號碼）
- openShowOrder（實際開出順序20個號碼）
- prizeNum.bullEye（超級獎號）

BingoResult 查詢參數：
openDate=YYYY-MM-DD&pageNum=1&pageSize=250

歷史資料：
content.bingoQueryResult[]
- drawTerm
- bigShowOrder
- openShowOrder
- bullEyeTop

本程式先抓最新一期，再用 BingoResult 依日期查詢，必要時往前查前幾天，
直到取得最近10期。每期都驗證20個號碼、號碼範圍、不重複及開出順序一致。
不足10期時顯示明確錯誤，不用其他來源或假資料補齊。

預測
----
只有在成功取得並驗證完整10期時，才會產生20個統計候選號碼。
依據近10期出現次數與新近期數加權，並加入 10×8 格局的空間線型特徵：
- 橫列：8條，每條10個號碼。
- 直行：10條，每條8個號碼。
- 斜線：左斜／右斜，至少連續4格。
- 比較每條線近10期加權命中數與隨機基準（每個號碼單期理論機率為25%）。
- 只採用各號碼在橫列、直行、斜線中最強的一條訊號，避免重疊斜線過度加分。

注意：每期開獎獨立隨機，線型趨勢不會提高單一號碼的客觀中獎機率；此模型只是把歷史分布轉成候選排序，不能保證提高中獎率。

本版主要修正
------------
1. 修正舊版未定義 SOURCE_URL，導致 Flask API 500 的問題。
2. 修正舊版未匯入 Counter，預測時計算會發生 NameError 的問題。
3. 修正最新 API 的解析路徑：lotteryBingoLatestPost。
4. 修正歷史 API 的資料路徑：content.bingoQueryResult。
5. 修正歷史資料欄位：bigShowOrder、openShowOrder、bullEyeTop。
6. 修正前端收到 HTML 時直接 response.json()，導致 Unexpected token '<'。
7. 後端錯誤一律回傳 JSON，並提供 /api/health 健康檢查。
8. 只有完整10期資料通過驗證，才顯示20個預測候選號碼。
9. 新增近10期橫列、直行、左右斜線的活躍度分析。
10. 候選分數結合近期頻率與線型訊號，並限制線型加權避免過度偏重。

本機啟動
--------
Windows：
1. 安裝 Python 3.10 或更新版本。
2. 在專案資料夾開啟 CMD。
3. 執行：
   py -m venv .venv
   .venv\\Scripts\\activate
   pip install -r requirements.txt
   python app.py
4. 開啟：
   http://127.0.0.1:10000

Render 部署
-----------
Build Command:
pip install -r requirements.txt

Start Command:
gunicorn app:app

部署後可先測：
/api/health
應回傳 JSON 且 version 為 6.3.0。

再測：
/api/data
成功時應看到：
ok=true、sample_size=10、draws 有10筆、prediction 有20筆。

測試
----
pip install -r requirements.txt
pytest -q

資料說明
--------
台彩官方 API 是台彩官網使用的資料介面，並非有版本承諾的公開開發者 API。
官方可能調整回應格式或存取方式；本程式若發現回應異常會顯示錯誤，不會自行補號。
開獎結果與遊戲規則仍以台灣彩券官方公告為準。
