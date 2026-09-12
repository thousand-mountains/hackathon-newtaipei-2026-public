# 匯出用 CJK 字型

| 檔案 | `NotoSansTC-Regular.otf` |
|---|---|
| 來源 | <https://github.com/googlefonts/noto-cjk> → `Sans/SubsetOTF/TC/NotoSansTC-Regular.otf` |
| 版本 | 取得日 2026-09-12 |
| 大小 | 5,683,368 bytes（5.42 MiB） |
| sha256 | `5bab0cb3c1cf89dde07c4a95a4054b195afbcfe784d69d75c340780712237537` |
| 授權 | SIL Open Font License 1.1（見 `LICENSE.txt`）——可自由散布、可內嵌 PDF |

## 為什麼需要它

`GET /api/cases/{id}/artifacts/{artifactId}/export?format=pdf` 產 PDF 時，
**沒有 CJK 字型的話整份是豆腐字（□□□），而且不會丟例外**——HTTP 照樣 200，
跑起來完全像成功。這是整個匯出功能唯一一個「假綠」的失敗模式，
所以字型是必需資產，不是優化。

`backend/api/export_render.py` 的 `resolve_cjk_font()` 找不到任何 CJK 字型時
**回 503 硬失敗，不降級**。

## 為什麼是這一份，不是別的

| 候選 | 大小 | 沒選的理由 |
|---|---|---|
| `Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf` | 16.4 MB | 同樣一個 weight，多 10.8 MB 只為了日韓字符 |
| `google/fonts` 的 `NotoSansTC[wght].ttf` | 11.9 MB | 可變字型，實體化行為多一個變數 |
| apt 的 `fonts-noto-cjk` | 0（不進 context） | 裝出來是 `.ttc` 集合檔，fpdf2／reportlab 都要指定 `fontNumber`，**多一個只在雲上才炸的變數** |
| 自行 subset | < 2 MB | 罕用字會靜默變豆腐字——正是本功能要防的那件事 |

**只塞單一 weight**（Regular）。不要為了粗體再塞一份 Bold：`.docx` 的粗體由 Word 自己
合成，PDF 的標題用字級與間距區隔即可。

## 不要把它 ignore 掉

`Dockerfile` 的 `COPY backend/ /app/backend/` 已經含這個目錄，**不需要額外 COPY**。
但如果哪天有人往 `.dockerignore` 或 `infra/cdk/lib/appeal-backend-stack.ts` 的
`exclude` 加 `backend/assets` 之類的規則，雲上的 PDF 匯出會**變成豆腐字而不是報錯**
（本機開發機有系統字型撐著，本機測不出來）。

build context 的實測數字見 `.prospec/changes/draft-and-export/plan.md` 與該批 commit。
