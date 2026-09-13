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

## 母庫實況（2026-09-13 team-lead 以 `scripts/check_statute_corpus.py` 實掃）

**快照那 11 部法規，母庫全部有全文，一部都沒缺**——但那句話驗的是「檔案在不在」，
**不是「內容全不全」**。2026-09-13 逐條比對後補正：`kb/public/相關法規_全量/` 那批
**每條都被截掉最後一項或一款**（訴願法 §77 只有七款、§14 只有 3 項、§79 只有 2 項；
訴願法 101 條裡 57 條、行政程序法 176 條裡 107 條內容短少）。條號對得上、內容缺一截，
而缺的那一款看起來完全正常——正是本檔「切不出來就不要塞」在防的那種錯，只是換了一層。

所以那 11 部**不走這裡**：`chat_bridge.archive_adapter` 先查
`backend/retrieval/law_articles.py` 的條文原文索引（從 `kb/official/相關法規/` 建的
官方列印版，完整且已解析），查不到才落回本模組。本模組服務的是索引涵蓋不到的
另外 657 部，而那些目前**只有全量那批可用**，所以歸檔時的 note 會標明可能缺末項／款。

檔名規則只對 8 部成立：

- 8 部在 `kb/public/相關法規_全量/{法規名}.txt`（＝`corpus_key()` 直接組出來的）
- **3 部（建築法／民法／洗錢防制法）在 `kb/official/相關法規/` 底下**，
  靠 `chat_bridge.archive_adapter` 的後備（拿法規名去母庫搜、**標題完全相等**才認）
  才找得到。**那條後備是必要路徑，不是保險**——拿掉就有三部法一條都切不出來。

逐筆數字見 `docs/handoff/2026-09-13-statute-corpus-survey.md`。

「第 15 條之 1」母庫實際怎麼寫仍未逐一確認，所以 `slice_article` **兩種寫法都認**
（`第15-1條` 與 `第15條之1`），兩種都找不到時照實留空。

⚠️ **比對基準是快照的 `articles[]`，不是 `max`**（team-lead 2026-09-13 更正）：
`articles[]` 含「97之1」這種之一條文，`max` 不含。建築法 `max=105`／`articles=123`，
差的 18 筆全是之一條文——拿 `1..max` 去比會得出「有一批條文不見了」的錯結論。
"""
from __future__ import annotations

import re

#: 母庫法規全文的前綴（`plans/2026-09-12-third-kb-pdf-and-sidecars.md:23` 記的
#: `aws s3 ls --recursive` 勘查結果：`kb/public/相關法規_全量`，668 份 txt）。
#: **那份勘查看到的是目錄，不是個別檔名**——檔名規則見檔頭「未驗證事項」。
CORPUS_STATUTE_PREFIX = "kb/public/相關法規_全量/"

#: 條次標題與行首之間允許出現的字元。**每一個都是「不帶字義」的**：
#: 半形空白、tab、全形空白（U+3000），以及**換頁符 `\x0c`**。
#:
#: `\x0c` 是 2026-09-13 雲上實掃出來的（team-lead 跑 `scripts/check_statute_corpus.py`）：
#: 母庫的法規檔是 PDF 轉文字來的，分頁處會留下一個換頁符，
#: 而它剛好落在**「建築法第25條」那一行的最前面**——demo 案子的核心法條。
#:
#:     行110   '    第 23 條'
#:     行113   '    第 24 條'
#:     行116   '\x0c    第 25 條'   ← 就這一個字元，整條切不出來
#:
#: 旁證：洗錢防制法有 13 個換頁符，收進去之後行首標題數 27 → 31，
#: **與快照的 31 條完全相同**。
#:
#: **刻意列舉，不寫成 `\s`。** 要先更正一個說法：`\s` **不會**讓內文引用進來——
#: `(?m)^` 照樣只在行首成立，`^\s*第` 對「依第72條規定」實測仍然零命中
#: （2026-09-13 兩個 pattern 對跑過）。`\s*` 唯一的差別是**匹配起點會往前吃掉空行**，
#: 那只影響切片的起點，`strip()` 之後看不出來。
#:
#: 所以理由不是安全，是**可見度**：`\s` 會安靜地吃掉任何像空白的東西
#: （含 `\v`、`\r`、U+00A0），於是母庫裡到底有什麼髒字元我們永遠不會知道；
#: 而 BOM（U+FEFF）**不在 `\s` 裡**，照樣會漏。列舉 ＋ `check_statute_corpus.py`
#: 的前導字元普查，才會逼我們去看實際的檔案。
#:
#: ⚠️ 誠實地說：這個取捨的代價已經發生過一次——`\x0c` 若當初寫 `\s` 就不會漏。
#: 取捨成不成立由 team-lead 決定，改成 `\s*` 的話上面那條普查仍然要留著。
#:
#: ⚠️ **這份清單是列舉的，不是窮舉的**：PDF 轉文字還可能留下 BOM（U+FEFF）、
#: 不換行空白（U+00A0）等等，本機沒有憑證掃不到那 11 份檔案。
#: `scripts/check_statute_corpus.py` 會列出實際出現過的前導字元——
#: 掃出新的就加進這裡，**不要因為「可能會有」就先放寬**。
_LINE_LEAD = "[ \t\x0c\u3000]*"

#: 行首的條次標題。`第 72 條` / `第72條之1` / `第 15-1 條` 都認。
#: `(?m)` 讓 `^` 認每一行的行首。
_ARTICLE_HEAD_RE = re.compile(
    rf"(?m)^{_LINE_LEAD}第\s*(\d+)\s*(?:-\s*(\d+)\s*)?條(?:\s*之\s*(\d+))?"
)

#: 章／節／編／款的標題行。它們夾在條次之間，不切掉的話會被算進前一條的條文。
#: 前導字元與條次標題同一套——分頁剛好落在章標題前面時也要認得出來。
_DIVISION_HEAD_RE = re.compile(
    rf"(?m)^{_LINE_LEAD}第\s*[0-9〇一二三四五六七八九十百]+\s*[章節編款]"
)

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
