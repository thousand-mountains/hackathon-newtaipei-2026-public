# hybrid search：三個 KB 都開不了（2026-09-12）

**結論先講：這條槓桿在我們現有的任何一個 KB 上都不存在，而且兩種 KB 是因為不同的理由不支援。**
不是設定沒調對，是 API 形狀與 vector store 能力的問題。

三個 KB 的設定側檔在 `.env.kb-*`（不進 git）。複現方法見文末——**探測用的程式碼已經全部移除**，理由也在文末。

## 為什麼想開 hybrid

純語意檢索對**精確字串**很鈍：`訴願法第77條` 與 `訴願法第79條` 在向量空間幾乎是同一個點，
差的那個數字對 embedding 幾乎沒有重量。而 N4 的查詢句裡塞滿了條號
（`n4_retrieval.py` 的 `build_query_sources()`，「期間引擎逐步援引的法源」那個來源）。
hybrid 的 BM25 那半正好吃這個。

## 結果

| KB | 型態 | 搜尋設定鍵 | `overrideSearchType` | 為什麼 |
|---|---|---|---|---|
| `.env.kb-managed` | MANAGED | `managedSearchConfiguration` | ❌ | **API 形狀裡沒有這個欄位** |
| `.env.kb-petition` | MANAGED | 同上 | ❌ | 同上（未另外打，理由見下） |
| `.env.kb-s3vectors` | VECTOR／S3 Vectors | `vectorSearchConfiguration` | ❌ | 欄位收，**服務端拒絕：S3 Vectors 的 index 不支援 HYBRID** |

兩種「不支援」的性質不同，分開記：

**MANAGED**：botocore 的 service model 裡，`managedSearchConfiguration` 只有四個成員——
`filter`、`numberOfResults`、`rerankingConfiguration`、`rerankingModelType`。
帶 `overrideSearchType` 連請求都送不出去（`ParamValidationError`，客戶端就擋掉）。
`.env.kb-petition` 也是 MANAGED，**API 形狀是型態決定的、與是哪一個 KB 無關**，
所以沒有為它再打一次 Bedrock。

**S3 Vectors**：`vectorSearchConfiguration` 有 `overrideSearchType`，`SEMANTIC` 也收，
但 `HYBRID` 被服務端以 `ValidationException` 拒絕，訊息是
「HYBRID search type is not supported for search operation on index ***」。
理由合理：S3 Vectors 是純向量存儲，沒有全文索引，BM25 那半根本無處可跑。

## 這代表什麼

要開 hybrid 就得換 vector store（例：OpenSearch Serverless 有全文索引）。
那是重建 KB ＋ 全量重新 ingest 的架構改動，**不是調參**。決賽日不做。

## 沒有做的事

- **正式路徑從頭到尾沒有改過**。探測期間 `retrieve_raw()` 有一個預設 None 的
  `search_type` 參數，payload 與沒有它時一字不差；結論出來後連那個參數也拿掉了。
- **沒有量條號辨別力**。探測腳本原本有第二階段（比 SEMANTIC 與 HYBRID 在帶條號的
  查詢上的差異），階段一判定不支援就停了，**一次都沒跑到**，所以本文沒有任何
  關於「hybrid 能改善多少」的數字——它不存在，不是我們沒寫。

## 這條線的替代品：rerank——**同日已由另一個 session 實作並實測**

探測的副產品是 `rerankingConfiguration` 兩種 KB 都有。寫本文時這還是「下一條該探的」，
但同一天稍晚另一個 session 已經把它做完了（`backend/config/settings.py` 的 rerank 段、
`kb.py` 的 `_rerank()`、五條測試）。**結論比 hybrid 能給的好得多**，所以這條線不必再追。

它量到的（在語料最大的那個 MANAGED KB 上，也就是 embedding 分數完全沒有鑑別力的那個）：

| | embedding top1 | rerank top1 |
|---|---|---|
| 真實案件（生產形態查詢） | 0.42–0.79 | **0.809–1.000** |
| 負控制（商標／海關／專利／閒聊） | 0.44–0.48 | **0.000–0.057** |

embedding 的訊號與雜訊**重疊**；rerank 之後空帶寬 0.75。

道理跟 hybrid 想解的是同一個問題，但解得更徹底：embedding 是 bi-encoder，
查詢與文件各自變向量再比距離，模型從來沒有同時看過兩者；rerank 是 cross-encoder，
兩者一起餵進去直接判斷相關性。hybrid 只是在 bi-encoder 旁邊補一路關鍵詞比對，
**cross-encoder 是更強的工具**——所以 hybrid 開不成，實質損失比想像的小。

## 比 hybrid 大得多的槓桿：KB 型態本身

`kb-min-score.md` 與 `kb-min-score-s3vectors.md` 兩份放在一起看：

| | MANAGED | S3 Vectors |
|---|---|---|
| 真實同案型命中 median | 0.206 | **0.860** |
| 閒聊句「今天天氣很好…」 | **0.731** | 0.591 |
| 訊號與雜訊 | **雜訊高過訊號** | 中間有空帶 |

在 MANAGED 上調排序，是在一個分數本身就沒有鑑別力的系統上調排序。
**先把 `.env` 指到哪個 KB 這件事定下來，比任何檢索參數都重要。**

## 附記：輸出遮罩

AWS 的錯誤訊息會把 index id 寫進去。腳本的 `redact()` 把 8 碼以上的純大寫英數換成 `***`，
免得這類識別碼隨著輸出被貼進 evidence（CLAUDE.md 規矩段、CONSTITUTION §7）。
bucket 名（小寫帶連字號）不在守備範圍，貼之前仍要自己掃一遍。

## 探測程式碼已移除，以及怎麼複現

`scripts/probe_hybrid_search.py` 與 `kb.py` 的 `search_type` 參數、三條相關測試
**都已刪除**。理由：唯一的使用者是這次探測，而結論是否定的——留著就是一條永遠
走不到的分支，而同一時間另一個 session 正在 `kb.py` 裡加 rerank，少一份互不相干
的程式碼就少一分衝突面。決賽日的 30 小時紀律（CONSTITUTION §8）也指向同一個選擇。

刪掉程式不該讓結論變成不可複現的宣稱，所以把複現方法留在這裡。兩段都不需要那支腳本。

**一、MANAGED 的 API 形狀**（不打 Bedrock，只讀 botocore 的 service model）：

```python
import boto3
c = boto3.client("bedrock-agent-runtime", region_name="us-west-2")
rc = c.meta.service_model.operation_model("Retrieve").input_shape.members["retrievalConfiguration"]
for name, shp in rc.members.items():
    print(name, "->", sorted(shp.members))
```

會印出 `managedSearchConfiguration` 只有 `filter`／`numberOfResults`／
`rerankingConfiguration`／`rerankingModelType`，而 `vectorSearchConfiguration`
才有 `overrideSearchType`。

**二、S3 Vectors 實打**（`set -a; . ./.env; . ./.env.kb-s3vectors; set +a` 之後）：

```python
import os, boto3
c = boto3.client("bedrock-agent-runtime", region_name=os.environ["AWS_REGION"])
c.retrieve(knowledgeBaseId=os.environ["BEDROCK_KB_ID"],
           retrievalQuery={"text": "訴願"},
           retrievalConfiguration={"vectorSearchConfiguration": {
               "numberOfResults": 1, "overrideSearchType": "HYBRID"}})
```

會拋 `ValidationException`。**訊息裡帶 index id，貼進任何文件前要先遮掉**
（CLAUDE.md 規矩段、CONSTITUTION §7）——本文第 33 行的 `***` 就是遮過的。
