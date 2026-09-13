"""敘述層：決定書骨架模板 + 幕僚卡片文案（architecture §3.2）。

兩件事，共同的紅線是**沒有任何一句是模型寫的，也沒有任何一個數字是寫死的**：

1. `build_doc_skeleton()`：決定書的四型區塊（title／meta／h／p）是靜態骨架，
   句子內容分三種來源——`record`（卷證直錄）、`engine`（期間引擎算式）、
   `llm`（fixture 重播的模板句）。句子 id 由這裡統一編號，不由模型給。
2. `merge_agent_narrative()` / `render_degraded_log()`：把節點回傳的統計數字套模板，
   降級一律外顯（紅色 log）。降級可以，隱瞞不行。
"""
from __future__ import annotations

from typing import Any

from backend.config import settings
from backend.config.settings import AGENTS_NARRATIVE
# 條號抽取只有這一份實作（國字數字、回指「同法第X條」、庫外法規都在裡面）。
# 在這裡重寫一個「找法規名 + 數字」的簡版，會在「第五十七條」這種國字寫法上靜默漏掉。
from backend.retrieval.lawtable import extract_law_refs

PLACEHOLDER_CONCLUSION_TEXT = "（結論段由承辦人判斷後填寫）"

# fixture 草稿裡手寫的 `cite_ids`（"L1"、"L3"…）**預設不帶進 doc[]**。
#
# 為什麼：那些 id 是寫 fixture 的人在 N4 還沒跑之前手填的，指向的是一份當時並不存在的
# 檢索結果。2026-09-05 N4 改成獨立檢索之後，它們只會有兩種下場——
# (a) 序號剛好對上 N4 的 L1/L2/L3，於是「解析成功」但指到完全不相干的法條；
# (b) 序號對不上，N6 判 unresolved → 紅燈 → 假的 blocker。
# 兩種都是假訊號。**真正的引用查核本來就不靠 id**：N6 直接從句子本文與 basis 抽引用、
# 對快照查四態，那條路徑不受影響（假法條照樣被攔）。
#
# **2026-09-07：bedrock 分支已打開這個開關**（N5 呼叫端傳 `carry_draft_cite_ids=True`）。
# 那時 N5 是看著 N4 的候選清單寫的，cite_ids 才有意義（architecture §6.2：
# 「N5 的 cite_ids 經 N6 解析比對」），而 client 端清掉的引用也要以 `unsupported`
# 一路帶到 N6 判紅（spec §5.3）。**預設值仍是 False**：fixture 的手寫 id 語意沒有變。
CARRY_DRAFT_CITE_IDS_DEFAULT = False


def _sentence(
    sid: str,
    text: str,
    origin: str,
    slot: str,
    cite_ids: list[str] | None = None,
    basis: str | None = None,
    engine: str | None = None,
    placeholder: bool = False,
    adversarial: bool = False,
    adversarial_note: str | None = None,
    unsupported: bool = False,
    dropped_cite_ids: list[str] | None = None,
    structural: bool = False,
    why_fixed: str | None = None,
    quoted_statute: bool = False,
) -> dict[str, Any]:
    """一句話的骨架。`l`／`why`／`refs` 留給 N6 填——模型不產燈號。

    `adversarial` 必須一路帶到 `doc[]`：對抗測資裡刻意注入的假引用如果在輸出 JSON 裡
    沒有標記，任何人只截 `doc[]`（或把它貼進簡報）就會看到一句沒有註記的假法條。

    `unsupported`／`dropped_cite_ids` 同理，而且更嚴重：那是 client 端白名單清掉的引用
    （模型引了檢索結果之外的來源）。只留在 `draft.slots` 的話 `doc[]` 與 N6 都看不到，
    spec §5.3 承諾的「結構層第二道」就是空的（2026-09-07 覆核 I-3）。
    **兩個鍵只在為真時才出現**：fixture 模板不會有這種句子，多加兩個永遠是 False 的鍵
    等於改了 fixture 的 payload 形狀（AC1 要求零變化）。
    """
    extra: dict[str, Any] = {}
    if unsupported:
        extra["unsupported"] = True
        extra["dropped_cite_ids"] = list(dropped_cite_ids or [])
    if structural:
        # 公文格式句（引導句、落款、教示條款），不是這一案的內容。
        # N6 的 `content_sentences` 要靠它排除，否則一份整個空掉的草稿會因為
        # 「至少有一句引導句」而躲掉 `empty_draft` 那條 P0 blocker。
        extra["structural"] = True
    if why_fixed:
        # 這一句的 why 由編排層寫定，N6 不要用引用狀態去猜一個。
        # 教示條款不附引用是**它本來就不附**，不是「未附可查證的引用」。
        extra["why_fixed"] = why_fixed
    if quoted_statute:
        # 逐字引述的條文原文。N6 的主文型語句偵測要跳過它——引文裡的
        # 「應為不受理之決定」是法條本文，不是這一案的結論（見 n6_gate 的說明）。
        extra["quoted_statute"] = True
    return {
        "id": sid,
        "t": text,
        "origin": origin,
        "slot": slot,
        "adversarial": adversarial,
        "adversarial_note": adversarial_note,
        "cite_ids": list(cite_ids or []),
        "basis": basis,
        "engine": engine,
        "placeholder": placeholder,
        "l": None,
        "why": None,
        "refs": [],
        # 分層誠實：這兩個欄位標明燈號與理由由誰產出，check_payload 會驗
        "l_origin": "rule",
        "why_origin": "rule",
        **extra,
    }


def build_doc_skeleton(
    intake: dict[str, Any],
    facts_excerpt: list[dict[str, Any]],
    deadline_result: dict[str, Any],
    draft_slots: dict[str, list[dict[str, Any]]],
    requires_human_conclusion: bool,
    carry_draft_cite_ids: bool = CARRY_DRAFT_CITE_IDS_DEFAULT,
    screen: dict[str, Any] | None = None,
    laws: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """組出 doc[]（四型區塊）。句子 id 由編排層統一編號 s1, s2, …

    ## 段序就是訴願決定書的段序（2026-09-13 重寫）

        抬頭（title／meta：案號、訴願人、原處分機關）
        引導句　上列訴願人因……提起訴願一案，本府依法決定如下：
        主　文
        事　實
        理　由　（按○○法第 N 條規定：「……」→ 理由句 → 綜上論結）
        附錄　期間計算（規則引擎逐步驗算）
        落款・教示條款

    **在這次改寫之前是「事實 → 理由 → 期間計算 → 決定主文」**，主文排在最後一段。
    三個輸出（聊天室草稿、`.docx`、`.pdf`）都吃這份骨架，所以三處一起錯：
    承辦人要翻到第二頁才看得到主文，而且主文常常是一個空標題（見下面「主文一定有內容」）。

    ## 主文一定有內容

    `ty=="h"` 開 section、`ss` 為空時，三個 renderer 都只畫得出一個孤零零的標題——
    畫面上看起來就是「這份決定書沒有主文」。所以主文段**在任何情況下至少有一句**：
    封鎖時是佔位句，沒封鎖但模型沒給結論時是另一句講明「模型沒寫」的佔位句。
    兩者都標 `placeholder=True`，不會被 N6 算成實質內容（`conclusion_missing` 那條
    blocker 仍然報得出來）。

    ## `structural` 是什麼

    引導句、落款、教示條款是**公文格式**，不是這一案的內容。它們標 `structural=True`，
    好讓 N6 的 `content_sentences` 把它們排除——否則一份整個空掉的草稿會因為
    「至少有一句引導句」而通過 `empty_draft` 那條 P0 blocker，被標成可送出。

    ## `carry_draft_cite_ids`

    `False`（預設）時，草稿槽位帶的 `cite_ids` 不會進 `doc[]`——理由見模組頂端
    `CARRY_DRAFT_CITE_IDS_DEFAULT`。引用查核不受影響（N6 從本文抽）。這個開關同時管
    `unsupported`／`dropped_cite_ids`：它們講的是「模型標的 cite_ids 有一部分不在
    白名單內」，跟 cite_ids 本身是同一組語意，開關要一起翻才前後一致。
    **條文引述句不受這個開關管**：那些 id 是本函式自己從 `laws` 對出來的，
    不是模型標的，語意上一直有效。
    """
    doc: list[dict[str, Any]] = []
    counter = {"n": 0}
    screen = screen or {}
    art77 = screen.get("art77") or {}

    def cids(s: dict[str, Any]) -> list[str] | None:
        return s.get("cite_ids") if carry_draft_cite_ids else []

    def nid() -> str:
        counter["n"] += 1
        return f"s{counter['n']}"

    def heading(text: str, role: str | None = None) -> None:
        """開一個 section。`text` 為空字串＝無名 section（renderer 不畫標題）。

        落款與教示條款要各自成段，但公文裡它們沒有標題。`build_sections()` 只有在
        遇到 `ty=="h"` 時才開新 section，所以這裡用空標題當分段記號——
        不這樣做的話，落款會被接到上一段（附錄）的尾巴裡。
        """
        block: dict[str, Any] = {"ty": "h", "text": text, "ind": 0, "ss": []}
        if role:
            block["role"] = role
        doc.append(block)

    def para(sentences: list[dict[str, Any]], role: str | None = None) -> None:
        if not sentences:
            return
        block: dict[str, Any] = {"ty": "p", "text": "", "ind": 1, "ss": sentences}
        if role:
            block["role"] = role
        doc.append(block)

    # ── 抬頭 ──────────────────────────────────────────────────────
    # 標題寫的是**決定機關**（受理訴願機關），不是原處分機關。`intake.org` 是後者，
    # 拿它當標題在合成測資裡剛好看不出錯（兩個都是新北市政府）。
    doc.append(
        {
            "ty": "title",
            "text": f"{settings.deciding_authority()}訴願決定書（合成測資草稿）",
            "ind": 0,
            "ss": [],
        }
    )
    # 抬頭**一欄一行**。原本三個欄位用「／」擠成一行，那不是公文抬頭的排法，
    # 而且案號一長就會折行折在奇怪的地方。
    # **案號不放進 meta 行**：公文把它靠右排在標題那一行（見 `build_sections` 的
    # `case_no`）。留在這裡的話它會跟訴願人、原處分機關一樣縮排排在左邊，那不是公文的樣子。
    for label, value in (
        (settings.META_APPELLANT, intake.get("person")),
        (settings.META_RESPONDENT, intake.get("org")),
    ):
        doc.append({"ty": "meta", "text": f"{label}　{value or '—'}", "ind": 0, "ss": []})

    # ── 引導句 ────────────────────────────────────────────────────
    heading("", role="preamble")
    para([_sentence(nid(), _preamble_text(intake), origin="record", slot="preamble",
                    structural=True, why_fixed=PREAMBLE_WHY)], role="preamble")

    # ── 主文（決定書的第一段，不是最後一段）──────────────────────
    heading(settings.SECTION_MAIN_TEXT, role="main_text")
    conclusion: list[dict[str, Any]] = []
    if requires_human_conclusion:
        text, why = _blocked_conclusion(art77, screen)
        conclusion.append(
            _sentence(
                nid(),
                text,
                origin="human_required",
                slot="conclusion",
                placeholder=True,
                why_fixed=why,
            )
        )
    else:
        for s in draft_slots.get("conclusion", []):
            conclusion.append(
                _sentence(
                    nid(),
                    s.get("t", ""),
                    origin="llm",
                    slot="conclusion",
                    cite_ids=cids(s),
                    basis=s.get("basis"),
                    adversarial=bool(s.get("adversarial")),
                    adversarial_note=s.get("adversarial_note"),
                    unsupported=carry_draft_cite_ids and bool(s.get("unsupported")),
                    dropped_cite_ids=s.get("dropped_cite_ids"),
                )
            )
    if not conclusion:
        # 沒封鎖、模型也沒給結論。**不能讓主文段是空的**：空的主文在三個 renderer 裡
        # 都只剩一個標題，看起來像「這份決定書沒有主文」而不是「主文沒生出來」。
        # 標 placeholder=True，所以 N6 的 `conclusion_missing` 仍然報得出來。
        conclusion.append(
            _sentence(
                nid(),
                MISSING_CONCLUSION_TEXT,
                origin="human_required",
                slot="conclusion",
                placeholder=True,
                why_fixed=MISSING_CONCLUSION_WHY,
            )
        )
    para(conclusion, role="main_text")

    # ── 事實段：卷證原文直錄，不生成 ──────────────────────────────
    heading(settings.SECTION_FACTS, role="facts")
    facts: list[dict[str, Any]] = []
    if facts_excerpt:
        for ex in facts_excerpt:
            facts.append(
                _sentence(
                    nid(),
                    ex.get("text", ""),
                    origin="record",
                    slot="facts",
                    basis=ex.get("quote_ref"),
                )
            )
    else:
        facts.append(
            _sentence(
                nid(),
                "（卷證中未擷取到可辨識的事實段，請承辦人自卷證補錄；系統不代為生成事實。）",
                origin="human_required",
                slot="facts",
                placeholder=True,
            )
        )
    para(facts, role="facts")

    # ── 理由段：條文引述 → 理由句 → 綜上論結 ──────────────────────
    heading(settings.SECTION_REASONING, role="reasoning")
    reasoning: list[dict[str, Any]] = []

    # 「一、按訴願法第 77 條第 7 款規定：『……』」——決定書理由段的標準開頭。
    # 條文原文由 N4 查表帶進 `laws[].q`，**不是模型寫的**（見 retrieval/law_articles.py）。
    for quote in article_quotes(draft_slots.get("reasoning", []), laws, art77):
        reasoning.append(
            _sentence(
                nid(),
                quote["text"],
                origin="retrieval",
                slot="reasoning",
                cite_ids=[quote["id"]] if quote.get("id") else [],
                basis=quote["basis"],
                why_fixed=settings.ARTICLE_QUOTE_WHY,
                quoted_statute=True,
            )
        )

    for s in draft_slots.get("reasoning", []):
        reasoning.append(
            _sentence(
                nid(),
                s.get("t", ""),
                origin="llm",
                slot="reasoning",
                cite_ids=cids(s),
                basis=s.get("basis"),
                adversarial=bool(s.get("adversarial")),
                adversarial_note=s.get("adversarial_note"),
                unsupported=carry_draft_cite_ids and bool(s.get("unsupported")),
                dropped_cite_ids=s.get("dropped_cite_ids"),
            )
        )

    # 「綜上論結，本件訴願為程序不合，爰依訴願法第 77 條第 N 款規定，決定如主文。」
    # **只有在程序結論由規則算得出來時才寫**（`art77.clause` 是引擎算的 77-N）。
    # 算不出來時寫佔位句，不猜「訴願為無理由」——那是實體法律判斷，系統不代為認定。
    reasoning.append(_summary_sentence(nid(), art77, requires_human_conclusion))
    para(reasoning, role="reasoning")

    # ── 附錄：期間計算（規則引擎逐步驗算）────────────────────────
    # **這不是決定書的一段**，正式決定書沒有它。但 CONSTITUTION §1 的可驗算層要它
    # 看得見，所以留在文件裡、標成附錄，由 renderer 另排（不混進理由段）。
    if deadline_result.get("steps"):
        heading(settings.SECTION_CALCULATION, role="appendix")
        para(
            [
                _sentence(
                    nid(),
                    f"{step['rule']}：{step['value']}",
                    origin="engine",
                    slot="calculation",
                    basis=step["basis"],
                    engine="deadline",
                )
                for step in deadline_result["steps"]
            ],
            role="appendix",
        )

    # ── 落款 ──────────────────────────────────────────────────────
    # 委員名單與用印日期系統一概不知道，**一律留空格給人填**。猜一個主任委員的名字
    # 會讓這份草稿看起來像已經審過了。
    heading("", role="signature")
    para(
        [
            _sentence(nid(), line, origin="human_required", slot="signature",
                      placeholder=True, structural=True, why_fixed=settings.SIGNATURE_WHY)
            for line in settings.SIGNATURE_LINES
        ],
        role="signature",
    )

    # ── 教示條款（訴願法 §90 的法定記載）─────────────────────────
    heading("", role="teaching")
    para(
        [
            _sentence(
                nid(),
                settings.TEACHING_CLAUSE_TEMPLATE.format(court=settings.administrative_court()),
                origin="static",
                slot="teaching",
                structural=True,
                why_fixed=settings.TEACHING_CLAUSE_WHY,
            )
        ],
        role="teaching",
    )
    return doc


#: 沒封鎖、模型也沒給結論時，主文段的佔位句。**要說出是哪一種缺**——
#: 跟 C 型封鎖（`PLACEHOLDER_CONCLUSION_TEXT`）是兩回事，混在一起會讓承辦人
#: 以為這一案也被系統封鎖了，而其實是模型漏寫。
MISSING_CONCLUSION_TEXT = "（本次未產出主文：本案未觸發結論封鎖，但主筆節點沒有寫出結論句，請承辦人補寫或重新生成。）"
MISSING_CONCLUSION_WHY = (
    "本案未觸發結論封鎖，但主筆節點沒有產出結論句。這一格是佔位，不是決定——"
    "送出端點會以 `conclusion_missing` 擋下本案。"
)
PREAMBLE_WHY = "本句為決定書抬頭引導句，由卷證抽取欄位（案由、處分日期）套模板組成，非模型生成。"


#: 程序審查**算出不受理**、但所憑欄位還沒人確認時的主文。
#: 結論寫出來（那是規則算的，不是猜的），但明說它還踩在未確認的輸入上。
BLOCKED_PROCEDURAL_TEXT = (
    "訴願不受理。（程序審查結果：{basis}；惟本結論所憑之送達日與提起日"
    "尚未經承辦人確認，請至收文頁確認後再行定稿。）"
)
BLOCKED_PROCEDURAL_WHY = (
    "逾期由期間引擎算出（純規則、零模型參與），結論本身可驗算；"
    "但它吃的送達日與提起日來自模型抽取、尚未經承辦人確認，"
    "所以這一格仍標為待確認，不是已定稿的主文。"
)

#: 程序審查**沒有算出不受理事由**時的主文。
#: **不寫「應予受理」**：系統只自動判定訴願法 §77 第 2 款（逾期）這一種不受理事由，
#: 其餘各款屬法律判斷（見 `n3_procedure.screen_art77` 的 `not_auto_screened_reason`）。
#: 寫「應予受理」等於宣稱「其餘各款都不成立」，而那是系統查不到的事。
SUBSTANTIVE_PENDING_TEXT = (
    "（本件程序審查未發現逾期情事〔訴願法第 77 條第 2 款不成立〕；"
    "是否受理，以及訴願有無理由〔駁回或撤銷〕，由承辦人自行判斷後填寫。）"
)
SUBSTANTIVE_PENDING_WHY = (
    "期間引擎只自動判定訴願法 §77 第 2 款（逾期）一種不受理事由；"
    "其餘各款與訴願有無理由屬實體法律判斷，本系統不代為認定——"
    "此處留白不是失敗，是刻意不猜。"
)


def _blocked_conclusion(art77: dict[str, Any], screen: dict[str, Any]) -> tuple[str, str]:
    """結論封鎖時，主文那一格要寫什麼。

    **依程序審查的結果分兩種**（2026-09-13 Claire 指定）：

    - 引擎算出不受理事由（`art77.clause` 有值）→ 結論寫出來（「訴願不受理。」），
      但附上它還踩在未確認輸入上這件事。這是判斷卡 7 擋下來的那條路：
      改一個模型抽的日期就能翻轉結論，所以不因為算得出來就當它定稿。
    - 沒有算出不受理事由 → 受理與否、有無理由一律交人判斷，不寫「應予受理」。

    兩種都是 `placeholder=True`，匯出檔會用紅字標（見 `export_render`）。
    """
    clause = (art77.get("clause") or "").strip()
    if clause:
        basis = (art77.get("basis") or "").split("。")[0] or f"訴願法 §{clause}"
        return BLOCKED_PROCEDURAL_TEXT.format(basis=basis), BLOCKED_PROCEDURAL_WHY
    return SUBSTANTIVE_PENDING_TEXT, SUBSTANTIVE_PENDING_WHY


def _preamble_text(intake: dict[str, Any]) -> str:
    """「上列訴願人因○○事件，不服原處分機關民國 ○ 所為之處分，提起訴願一案，本府依法決定如下：」

    處分日期（`d1`）缺的時候**整段日期省略**，不寫「民國 — 」：公文上一個破折號
    比沒有那幾個字更醒目，而它指的只是我們沒抽到日期。
    """
    d1 = (intake.get("d1") or "").strip()
    return settings.PREAMBLE_TEMPLATE.format(
        case_type=intake.get("type") or "（案由未擷取）",
        disposition=settings.PREAMBLE_DISPOSITION.format(date=roc_date(d1)) if d1 else "",
        self_ref=settings.deciding_authority_self_ref(),
    )


def roc_date(iso: str) -> str:
    """`2024-06-11` → `113 年 6 月 11 日`。解析不了就原樣回傳，不猜。"""
    parts = iso.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return iso
    y, m, d = (int(p) for p in parts)
    return f"{y - 1911} 年 {m} 月 {d} 日"


def _summary_sentence(sid: str, art77: dict[str, Any], blocked: bool) -> dict[str, Any]:
    """理由段最後一句（綜上論結）。

    `art77.clause` 形如 `77-2`，是**期間引擎算出來的**，所以這一句標 `origin="engine"`
    ——它跟算式句同一個保證等級。算不出來（clause 為 None）時改佔位句並標
    `human_required`：訴願有無理由屬實體法律判斷，系統不代為認定。

    **`blocked` 為真時一律走佔位句**，就算引擎算得出款次也一樣。這一句會寫出
    「本件訴願為程序不合……決定如主文」——那是實質結論。結論段既然封鎖了，
    同一個結論不能換一段寫出來（N6 的主文型語句偵測會抓到它，而被抓到就等於
    我們自己先製造了一條 P0 blocker，實測 2026-09-13）。
    """
    clause = (art77.get("clause") or "").strip()
    _, _, num = clause.partition("-")
    numeral = None if blocked else _clause_numeral(num)
    if not numeral:
        return _sentence(sid, settings.CONCLUSION_SUMMARY_PLACEHOLDER, origin="human_required",
                         slot="reasoning", placeholder=True)
    return _sentence(
        sid,
        settings.CONCLUSION_SUMMARY_TEMPLATE.format(clause=_ARABIC_OF_NUMERAL[numeral]),
        origin="engine",
        slot="reasoning",
        basis=art77.get("basis"),
        engine="deadline",
    )


_CLAUSE_NUMERALS = "一二三四五六七八九十"
#: 國字款次 → 阿拉伯數字（顯示用）。
_ARABIC_OF_NUMERAL = {c: str(i + 1) for i, c in enumerate(_CLAUSE_NUMERALS)}


def _clause_numeral(num: str) -> str | None:
    if not num.isdigit():
        return None
    n = int(num)
    return _CLAUSE_NUMERALS[n - 1] if 1 <= n <= len(_CLAUSE_NUMERALS) else None


#: 理由段最多引幾條條文。**不是排版考量，是可讀性**：引到第六條的時候，
#: 理由段已經變成一份法規彙編，承辦人反而看不出本案的爭點是哪一條。
MAX_ARTICLE_QUOTES = 4


def article_quotes(
    reasoning_slots: list[dict[str, Any]],
    laws: list[dict[str, Any]] | None,
    art77: dict[str, Any],
) -> list[dict[str, Any]]:
    """理由段要引述的條文，依「本案命中的款次 → 理由句提到的條號」排序。

    **來源全部是 `laws[].q`**（N4 查表帶進來的條文原文），一個字都不是模型寫的。
    `q` 是空的（條文索引沒建、或這一條不在索引涵蓋的法規內）就**不產這一句**——
    寧可沒有引述，也不要一句「按○○法第 N 條規定：『』」。

    命中的款次排第一，是因為那是這一案的操作判準：範本決定書的理由段第一句
    引的就是它（「一、按訴願法第 77 條第 7 款規定：……」）。
    """
    index = {(law.get("law"), law.get("article")): law for law in (laws or []) if law.get("law")}
    wanted: list[tuple[str, str, str | None]] = []  # (法規名, 條號, 款次國字)

    clause = (art77.get("clause") or "").strip()
    if clause.startswith("77-"):
        numeral = _clause_numeral(clause.partition("-")[2])
        if numeral:
            wanted.append(("訴願法", "77", numeral))

    # 理由句自己提到的條號。從**本文與 basis**抽，不從模型標的 cite_ids 抽——
    # cite_ids 的語意受 `carry_draft_cite_ids` 管，而條文引述要的是「這句話在講哪一條」。
    text = "\n".join(f"{s.get('t', '')}\n{s.get('basis') or ''}" for s in reasoning_slots)
    law_names = sorted({law.get("law") for law in (laws or []) if law.get("law")})
    if law_names and text.strip():
        for law_name, article, _display in extract_law_refs(text, list(law_names)):
            if law_name and article:
                wanted.append((law_name, article, None))

    # 模型自己寫的理由句（拿來比對「這一條它是不是已經引過原文了」）。
    drafted = _squeeze("\n".join(s.get("t", "") for s in reasoning_slots))

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for law_name, article, numeral in wanted:
        if (law_name, article) in seen:
            continue
        seen.add((law_name, article))
        law = index.get((law_name, article))
        body = _clause_of(law.get("q"), numeral) if law and law.get("q") else None
        if not body:
            continue
        if _already_quoted(body, drafted):
            continue
        # 顯示用**阿拉伯數字**（範本：「第 77 條第 7 款」），切條文用**國字**
        # （條文本文裡的款次記號是「七、」）。兩者不是同一個東西，別共用一個變數。
        ref = f"第 {article} 條" + (f"第 {_ARABIC_OF_NUMERAL[numeral]} 款" if numeral else "")
        out.append(
            {
                "id": law.get("id"),
                "basis": f"{law_name}{ref}",
                "text": settings.ARTICLE_QUOTE_TEMPLATE.format(law=law_name, ref=ref, text=body),
            }
        )
        if len(out) >= MAX_ARTICLE_QUOTES:
            break
    return out


#: 判斷「模型是不是已經把這一條的原文抄出來了」要比對幾個字。
#: 太短（例如 8 個字）會誤判——「行政機關就該管行政」是很多條文的共同開頭；
#: 太長則會被一個異體字或全半形差異打掉。24 個字在本專案的 11 部法規上實測可分。
_QUOTE_ECHO_CHARS = 24


def _squeeze(text: str) -> str:
    """去掉所有空白與常見的引號、全半形差異，只留可比對的字。

    模型抄條文時常常換一種引號（「」vs『』）、把全形數字寫成半形、或在標點後多一個
    空格。逐字比對會因為這些差異而判成「沒抄過」，然後我們就在它旁邊再貼一次原文。
    """
    table = str.maketrans("", "", " \u3000\t\n「」『』（）()，,、。．.；;：:")
    return text.translate(table)


def _already_quoted(article_body: str, drafted: str) -> bool:
    """模型的理由句裡是不是已經有這一條的原文了。

    **這是 2026-09-13 的迴歸修正。** 在那之前，條文引述句無條件插在理由段最前面，
    而模型本來就會自己寫「按『……』○○法第 N 條定有明文」——於是同一條在同一段裡
    出現兩次，實測真實卷證時三條法規全部重複（廢清法 §27、§50、行程法 §9）。

    比對的是**條文原文的前 24 個字**在不在模型寫的句子裡（兩邊都先去空白與標點）。
    比引用編號可靠：編號寫法有「第27條」「第 27 條第 1 款」好幾種，而原文只有一種。
    """
    head = _squeeze(article_body)[:_QUOTE_ECHO_CHARS]
    return bool(head) and head in drafted


def _clause_of(article_text: str, numeral: str | None) -> str:
    """整條原文 → 只留柱書 ＋ 指定款次。

    `laws[].q` 是整條原文（`retrieval/law_articles.ArticleTextStore.text()` 組的），
    而範本引的是「柱書 ＋ 命中的那一款」。切不出來就回整條——那仍然是真的條文，
    比回空字串（＝整句引述消失）好。
    """
    if not numeral or f"{numeral}、" not in article_text:
        return article_text
    head, _, rest = article_text.partition(f"{numeral}、")
    # 柱書是第一款之前的部分；款與款之間沒有分隔符，所以下一款的「○、」就是結尾。
    lead = head.split("一、")[0] if "一、" in head else head
    tail = rest
    for nxt in _CLAUSE_NUMERALS:
        marker = f"{nxt}、"
        if _CLAUSE_NUMERALS.index(nxt) > _CLAUSE_NUMERALS.index(numeral) and marker in tail:
            tail = tail.split(marker)[0]
            break
    return f"{lead}{numeral}、{tail}"


def conclusion_block_criterion(
    screen: dict[str, Any],
    classification: dict[str, Any],
    substantive_types: tuple[str, ...],
) -> dict[str, Any]:
    """如實描述「這一案的結論段為什麼被封鎖（或沒有）」。

    **這個函式不做任何判斷，只是把 `gate/lamps.requires_human_conclusion()` 已經做完的
    判斷重新講一遍人話。** 之所以要重講，是因為原本 UI 只拿得到 lamps 產出的訊號字串陣列，
    而那個陣列把「操作判準」跟「附帶偵測到的事實爭點」並排列出，讀起來像兩個都是原因。
    覆核實測證實不是：對抗案例把事實爭點全拿掉仍然封鎖。

    判斷順序刻意對齊 `lamps.requires_human_conclusion()` 的 early-return 順序
    （fail-safe → substantive → 高風險爭點），這樣「哪一條才是操作判準」才會講對。
    漂移由契約測試釘住：`blocked` 必須等於 `screen.requires_human_conclusion`。
    """
    art77 = screen.get("art77") or {}
    fact_issues = screen.get("fact_issues") or []
    case_type = ((classification or {}).get("class") or {}).get("case_type") or ""

    # 與 lamps.requires_human_conclusion 同一套比對，異體字處理必須一致——
    # 兩邊漂掉的話，封鎖判準與它的說明就會各說各話（契約測試盯著這件事）。
    _ct = settings.normalize_case_type(case_type)
    _types = tuple(settings.normalize_case_type(t) for t in substantive_types)
    substantive = bool(art77.get("requires_substantive_review")) and _ct in _types
    high = [i for i in fact_issues if i.get("severity") == "high"]
    unknown_type = _ct not in _types
    inputs_confirmed = bool(screen.get("procedural_inputs_confirmed"))
    unconfirmed_fields = list(screen.get("unconfirmed_procedural_fields") or [])
    procedurally_resolved = bool(art77.get("clause")) and inputs_confirmed
    # 判斷卡 7：靠未確認的抽取欄位算出來的程序結論，不准拿來解除封鎖
    unconfirmed_unlock = bool(art77.get("clause")) and not inputs_confirmed
    fail_safe = unknown_type and not procedurally_resolved

    if fail_safe:
        reason_id = "unknown_case_type_fail_safe"
        text = settings.BLOCK_CRITERION_FAIL_SAFE.format(case_type=case_type or "（空白）")
    elif unconfirmed_unlock and not substantive:
        reason_id = "unconfirmed_procedural_inputs"
        text = settings.BLOCK_CRITERION_UNCONFIRMED.format(
            fields="、".join(unconfirmed_fields) or "（未指明）",
            clause=art77.get("clause") or "—",
        )
    elif substantive:
        reason_id = "procedurally_valid_needs_substantive_review"
        text = settings.BLOCK_CRITERION_SUBSTANTIVE.format(case_type=case_type)
    elif high:
        reason_id = "high_severity_fact_issue"
        text = settings.BLOCK_CRITERION_FACT_ISSUE.format(issue_ids="、".join(i["id"] for i in high))
    else:
        reason_id = "not_blocked"
        text = settings.BLOCK_CRITERION_NONE

    blocked = bool(fail_safe or substantive or high or unconfirmed_unlock)
    # 高風險爭點只有在它「就是」操作判準時才算原因；其餘情況一律標成提醒
    fact_issue_is_operative = reason_id == "high_severity_fact_issue"
    return {
        "blocked": blocked,
        "reason_id": reason_id,
        "text": text,
        "fact_issue_role": "operative" if fact_issue_is_operative else "observation",
        "fact_issue_label": (
            settings.FACT_ISSUE_OPERATIVE_LABEL
            if fact_issue_is_operative
            else settings.FACT_ISSUE_OBSERVATION_LABEL
        ),
        "origin": "rule",
    }


def merge_agent_narrative(
    acc: dict[str, Any], node: str, result_narrative: dict[str, Any], degraded: bool, reason: str | None
) -> dict[str, Any]:
    """把節點 narrative 併進 agents[] 結構；降級時強制追加一條紅色 log。"""
    for key, payload in result_narrative.items():
        static = AGENTS_NARRATIVE.get(key, {})
        entry = {
            "k": key,
            "ico": static.get("ico", key),
            "name": static.get("name", key),
            "role": static.get("role", ""),
            "node": node,
            "out": payload.get("out", ""),
            "logs": [list(l) for l in payload.get("logs", [])],
        }
        if degraded:
            entry["logs"].append([f"⚠ 本節點已降級：{reason or '未說明原因'}", "r"])
        acc[key] = entry
    return acc


def summary_line(run_meta: dict[str, Any]) -> str:
    degraded = run_meta.get("degraded", [])
    return (
        f"本次執行 {len(run_meta.get('node_timings', {}))} 個節點，"
        f"共 {run_meta.get('elapsed_ms', 0)} ms，"
        f"其中 {len(degraded)} 個節點降級。"
    )
