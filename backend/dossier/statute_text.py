"""從母庫的法規全文切出「第 N 條」的條文。**零依賴、零 LLM、純文字切分**。

## 為什麼需要它

chat 的「搜尋相關法規」走的是 `laws-snapshot.json` 查表通道，那份快照**只有條號清單**
（`{法規名: {articles: [...], max: N}}`），沒有一個字的條文。所以歸檔進卷宗的
`lawtable:` 那批 `body_cached` 一律是空的，點開只看到一句
「條號存在性驗證，非法條全文」——那句話是對的，但承辦人要的是**那一條的條文**。

母庫（`kb/public/相關法規_全量/`）有全文。本模組負責「全文 → 某一條」這一步，
`backend/orchestrator/chat_bridge.py` 負責去抓全文並把結果寫進卷宗。

## 切不出來就不要塞（硬條件）

每一個 `slice_article` 回 `None` 的分支都是刻意的：**寧可空著並說明原因，
也不要端一段「看起來像條文、其實是隔壁那條」的文字**。承辦人會照著它寫決定書。

## 怎麼認「第 N 條」的開頭：只認行首

法規全文裡「第72條」會出現在兩種地方——**條次標題**（自成一行）與
**條文裡的引用**（「依第七十二條規定」）。兩者不分的話，切出來的會是
「某一條的中間開始、到下一次提到條號為止」的一段殘文，而**它看起來完全正常**。

所以只認**行首**的條次標題（容許前導空白）。代價是：母庫若把整部法壓成一行，
這裡會一條都切不出來——那時 `body_cached` 留空、note 說明切不出來，
**不會端出錯的東西**。這是刻意選的失敗方向。

## 未驗證事項（2026-09-13，本機沒有憑證）

`CORPUS_STATUTE_PREFIX` 與檔名規則（`{法規名}.txt`）**沒有對著實際的 bucket 驗過**
（本機無 AWS 憑證、無 `S3_KB_BUCKET`）。規則不對時的下場是
「抓不到 → `body_cached` 留空 → note 明講查過哪一個 key」——
**失敗是可見且可診斷的，不是靜默地沒有作用**。呼叫端另有一條後備：
拿法規名去母庫搜尋、用**標題完全相等**認那份檔（見 `chat_bridge.archive_adapter`），
所以檔名規則猜錯也還有救。

母庫實際怎麼寫「第 15 條之 1」也沒驗過，所以 `slice_article` **兩種寫法都認**
（`第15-1條` 與 `第15條之1`），並在兩種都找不到時照實留空。
"""
from __future__ import annotations

import re

#: 母庫法規全文的前綴（`plans/2026-09-12-third-kb-pdf-and-sidecars.md:23` 記的
#: `aws s3 ls --recursive` 勘查結果：`kb/public/相關法規_全量`，668 份 txt）。
#: **那份勘查看到的是目錄，不是個別檔名**——檔名規則見檔頭「未驗證事項」。
CORPUS_STATUTE_PREFIX = "kb/public/相關法規_全量/"

#: 行首的條次標題。`第 72 條` / `第72條之1` / `第 15-1 條` 都認。
#: `(?m)` 讓 `^` 認每一行的行首。
_ARTICLE_HEAD_RE = re.compile(
    r"(?m)^[ \t　]*第\s*(\d+)\s*(?:-\s*(\d+)\s*)?條(?:\s*之\s*(\d+))?"
)

#: 章／節／編／款的標題行。它們夾在條次之間，不切掉的話會被算進前一條的條文。
_DIVISION_HEAD_RE = re.compile(r"(?m)^[ \t　]*第\s*[0-9〇一二三四五六七八九十百]+\s*[章節編款]")

#: `laws-snapshot.json` 的條號 key 寫法（`extract_all_law_refs` 的 `_key_display`
#: 產的也是這個）：主條號，或「主條號之子條號」。
_ARTICLE_KEY_RE = re.compile(r"^(\d+)(?:之(\d+))?$")


def corpus_key(law_name: str) -> str:
    """法規名 → 母庫全文的候選 key。**候選，不是保證存在**（見檔頭「未驗證事項」）。"""
    return f"{CORPUS_STATUTE_PREFIX}{(law_name or '').strip()}.txt"


def parse_article_key(article: str) -> tuple[str, str | None] | None:
    """`"72"` → `("72", None)`；`"15之1"` → `("15", "1")`；讀不懂回 `None`。"""
    m = _ARTICLE_KEY_RE.match(str(article or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def article_headings(full_text: str) -> list[tuple[int, int, str, str | None]]:
    """全文裡每一個**行首**條次標題的 `(起點, 標題結束位置, 主條號, 子條號)`。

    回傳的順序就是在文中出現的順序。切條文時用「這一條的標題起點」到
    「下一個標題起點」當範圍，所以這裡不做任何排序或去重。
    """
    out: list[tuple[int, int, str, str | None]] = []
    for m in _ARTICLE_HEAD_RE.finditer(full_text or ""):
        # `第15-1條` 的子條號在 group(2)，`第15條之1` 的在 group(3)。
        sub = m.group(2) or m.group(3)
        out.append((m.start(), m.end(), m.group(1).lstrip("0") or "0", sub))
    return out


def slice_article(full_text: str, article: str) -> str | None:
    """切出 `article` 那一條的條文（含條次標題）。切不出來回 `None`。

    回 `None` 的五種情形，**全部都是刻意不猜**：
      1. 條號 key 讀不懂（不是 `N` 或 `N之M`）
      2. **整份文件的行首條次標題少於兩個** —— 見下面「為什麼要求至少兩個」
      3. 找不到這一條
      4. **同一條的標題在行首出現不只一次** —— 分不出哪一個才是本文，
         切錯的那一段看起來完全正常，所以寧可不給
      5. 切出來只有標題沒有內文

    ## 為什麼要求至少兩個標題

    2026-09-13 寫測試時抓到的：整部法被壓成一行時
    （`第71條 前條之送達。第72條 送達於…。第73條 …`），只有最前面那個
    `第71條` 在行首。於是第 72、73 條切不出來（那是對的），但**第 71 條會切出
    整行——把三條的文字當成第 71 條端出去，而且讀起來完全正常**。

    根因是「這份文件根本不是以行分條的」，而那件事看一次全文就知道，
    不必逐條猜。母庫的法規檔是整部法一檔（行政程序法實測 176 個條號），
    正常情況下標題遠多於兩個；只有一個標題就代表這份檔的結構不是我們以為的那樣，
    **這時候一條都不要切**。
    """
    want = parse_article_key(article)
    if want is None:
        return None
    heads = article_headings(full_text)
    if len(heads) < 2:
        return None
    hits = [i for i, (_s, _e, num, sub) in enumerate(heads) if (num, sub) == want]
    if len(hits) != 1:
        return None
    idx = hits[0]
    start = heads[idx][0]
    end = heads[idx + 1][0] if idx + 1 < len(heads) else len(full_text)
    # 章／節標題若夾在這一條與下一條之間，條文到它為止。
    div = _DIVISION_HEAD_RE.search(full_text, heads[idx][1], end)
    if div:
        end = div.start()
    body = (full_text[start:end] or "").strip()
    # 只有標題沒有內文 ＝ 沒切到東西，不要端一行「第72條」出去充數。
    return body if len(body) > (heads[idx][1] - start) else None


__all__ = [
    "CORPUS_STATUTE_PREFIX",
    "article_headings",
    "corpus_key",
    "parse_article_key",
    "slice_article",
]
