# 四個 KB 的相似案通道並排量測：rerank 之後幾乎沒有差別（2026-09-12）

## 問題

今天為了找「哪個 KB 設定最好」，建了三個實驗 KB 並各跑一次全量 ingestion：

| 代號 | 型態 | embedding | chunk | 距離 |
|---|---|---|---|---|
| A | VECTOR / S3 Vectors | titan-embed-text-v2 · 1024 | 512/15 | cosine |
| M | MANAGED | AWS 內部決定，看不到 | 看不到 | 看不到 |
| B | VECTOR / S3 Vectors | cohere-embed-multilingual-v3 · 1024 | 512/15 | euclidean |
| C | VECTOR / S3 Vectors | titan-embed-text-v2 · 1024 | 1024/15 | cosine |

四個讀**同一個 bucket、同一批側檔**（16,970 份），所以變因只有上表那三欄。

## 結果

`scripts/measure_similar_case.py`，12 筆生產形態短查詢 ＋ 4 筆負控制，
rerank 門檻 0.5、`KB_MIN_SCORE` 0.15：

| KB | 回傳筆數 | 撈空 | rerank min | rerank median | 同案型率 | 重複來源 | 負控制殘存 |
|---|---|---|---|---|---|---|---|
| A titan/512 | 5/5 | 0/12 | 0.850 | 0.952 | 100% | 0 | 0 |
| M managed | 5/5 | 0/12 | 0.907 | 0.996 | 100% | 0 | 0 |
| B cohere/512 | 5/5 | 0/12 | 0.846 | 0.939 | 100% | 0 | 0 |
| C titan/1024 | 5/5 | 0/12 | 0.868 | 0.933 | 100% | 0 | 0 |

**四個在每一個指標上都相同或差在雜訊範圍內。** rerank median 的全距是
0.933–0.996，而 n=12；同案型率、重複來源、負控制三項完全一致。

## 結論：embedding 層的選擇，在 rerank 之後不重要

這是今天最省力的一個結論，但它是**負面結果**——三個實驗 KB、約三小時的
ingestion，換來的是「這條軸不用再調了」。

對照今天稍早在 **rerank 之前**量到的同一批 KB：

| | raw embedding 的真實命中 median |
|---|---|
| MANAGED | 0.206 – 0.213 |
| S3 Vectors | 0.860 |

embedding 層的差距原本是**四倍以上**，rerank 之後剩不到 0.07。
原因在分工：embedding 只需要把對的文件撈進候選池（recall），
排序交給 cross-encoder（precision）——而這四種設定的 recall 都夠好。

## 這對後續的意義

1. **不要再為了檢索品質去換 embedding 模型或 chunk 大小**，先確認 rerank 在線上。
2. 選 KB 的理由回到**非檢索因素**：語料完整度、建置與重建成本、可觀測性
   （MANAGED 的 chunk 與模型看不到，S3 Vectors 看得到也調得動）。
3. 三個實驗 KB 沒有留著的必要，但也不急著刪——它們是現成的備援，
   萬一 demo 當天 A 出問題可以直接切 `.env`。

## 量測方法上的兩個修正

**查詢不含檔頭的「標題」**。第一版把它放進查詢，四個 KB 的同案型率都是 100%、
rerank median 0.97+——因為標題寫的是「因違反空氣污染防制法事件提起訴願」，
**等於把 golden 答案抄進題目**。拿掉之後才看得出差異。

**同案型率從 98% 升到 100% 不是變準了**，是 golden 變乾淨了：案型正規化把
`空氣污染管制法`（錯字）、`受處分人○○違反空氣污染防制法`（夾姓名）等變體
收斂到同一個值，先前那 2% 的「不同案型」有一部分是同一類被寫成不同字串。
