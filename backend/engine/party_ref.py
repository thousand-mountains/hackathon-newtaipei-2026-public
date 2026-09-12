#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
程序審查模型 v1 — 規則 R2：當事人適格（訴願法 §18 / §77 第 3 款）

訴願法 §18：受行政處分之相對人及利害關係人得提起訴願。
訴願法 §77 III：訴願人不符合 §18 者，應為不受理之決定。
實務：所稱「利害關係」限法律上利害關係，不含經濟上、情感上、其他事實上
      （反射）利害關係（最高行政法院 101 判 1002 等參照）。

輸入：已結構化的當事人資訊（前端從訴願書＋原處分書擷取），本引擎不做 NLU。
輸出：適格(相對人) / 適格(利害關係人) / 不適格 / 需人工認定 + 理由 + 法條。

出處：`origin/hack-petition-procedure-review-ref` 的
`reference/petition-procedure-review/party/rule_engine.py`（Jacky，2026-09-12），
逐字複製進 `backend/engine/`，仿 `overdue_ref.py` 的位置與做法。純標準庫、零 LLM。

⚠️ 呼叫端請走 `backend/nodes/n3_procedure.check_party_standing()`，不要直接呼叫
`check_standing()`。兩個理由：
1. `respondent_name` 為空、而 `appellant_capacity` 有值時，第 4/5/6 分支會在
   **從未比對過姓名**的情況下直接下不適格／需人工——包一層才擋得住。
2. 本引擎的驗證成績有一個必須一起講的限制：`party/validate.py` 的
   「200 件負樣本 100% 判對」是把**同一個字串當訴願人也當相對人**餵進來，
   只證明了 `_same_party(x, x) is True`，**沒有量到「姓名確實不同但仍適格」的
   偽不適格率**。所以引擎的意見不得直接作成不受理決定。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

# 訴願人自述身分 → 標準化代碼
CAPACITY = {
    "本人": "self",
    "代表人": "representative", "負責人": "representative", "法定代理人": "representative",
    "代理人": "agent", "委任代理人": "agent",
    "員工": "reflective", "受僱人": "reflective", "受雇人": "reflective", "勞工": "reflective",
    "股東": "reflective", "配偶": "reflective", "子女": "reflective", "父": "reflective",
    "母": "reflective", "親屬": "reflective", "家屬": "reflective", "繼承人前": "reflective",
    "承租人": "legal_interest", "使用人": "legal_interest", "管理人": "legal_interest",
    "所有人": "legal_interest", "共有人": "legal_interest", "土地所有人": "legal_interest",
    "受讓人": "legal_interest", "繼受人": "legal_interest", "抵押權人": "legal_interest",
    "檢舉人": "informant", "陳情人": "informant",
}

_CORP_SUFFIX = re.compile(r"(股份有限公司|有限公司|企業社|工程行|商行|事務所|"
                          r"實業|工廠|廠$|社$|行號|合作社|基金會|財團法人|社團法人)")


def _norm_name(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", "", s)
    s = s.replace("○", "").replace("Ｏ", "").replace("O", "").replace("　", "")
    return s.strip("「」（）()、，。 ")


def _is_corp(name: str) -> bool:
    return bool(_CORP_SUFFIX.search(name or ""))


def _same_party(a: str, b: str) -> Optional[bool]:
    """名稱是否指同一人。遮罩字消掉後：完全相同→True；同姓不同名→False；資訊不足→None。"""
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return None
    if na == nb:
        return True
    # 公司：去頭尾後比對核心字
    if _is_corp(a) and _is_corp(b):
        ca = _CORP_SUFFIX.sub("", na)
        cb = _CORP_SUFFIX.sub("", nb)
        if ca and cb and (ca == cb):
            return True
        return False
    # 自然人：遮罩後長度太短無法判斷
    if len(na) <= 1 or len(nb) <= 1:
        return None
    return na == nb


@dataclass
class StandingInput:
    appellant_name: str                     # 訴願人（訴願書）
    respondent_name: str                    # 原處分相對人（原處分書）
    appellant_capacity: str = ""            # 訴願人自述身分（見 CAPACITY）
    filed_in_own_name: Optional[bool] = None  # 是否以訴願人「個人名義」提起
    claimed_legal_interest: Optional[str] = None  # 主張之法律上利害關係內容（自由文字）
    respondent_is_corp: Optional[bool] = None


@dataclass
class StandingResult:
    verdict: str                     # 適格 / 不適格 / 需人工認定
    standing_as: str = ""            # 相對人 / 利害關係人 / ""
    is_qualified: Optional[bool] = None
    confidence: str = "high"         # high / medium / low
    legal_refs: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    missing: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _cap_code(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    for k, v in CAPACITY.items():
        if k in raw:
            return v
    return "other"


def check_standing(inp: StandingInput) -> StandingResult:
    steps, refs, missing = [], ["訴願法 §18", "訴願法 §77 第 3 款"], []
    corp = inp.respondent_is_corp
    if corp is None:
        corp = _is_corp(inp.respondent_name)

    same = _same_party(inp.appellant_name, inp.respondent_name)
    cap = _cap_code(inp.appellant_capacity)

    # 1. 訴願人即原處分相對人本人 → 適格
    if same is True:
        steps.append(f"訴願人「{inp.appellant_name}」即原處分相對人 → 適格（處分相對人）。")
        return StandingResult("適格", "相對人", True, "high", refs, steps, missing)

    # 2. 代理人
    if cap == "agent":
        if inp.filed_in_own_name is False:
            steps.append("訴願人為相對人之代理人，且以相對人（本人）名義提起 → 由本人具訴願能力，適格。")
            return StandingResult("適格", "相對人", True, "medium", refs, steps, missing)
        steps.append("訴願人以「代理人自己名義」提起訴願，非以相對人名義 → 當事人不適格。"
                     "（應由相對人本人為訴願人，代理人代為提起）")
        return StandingResult("不適格", "", False, "medium", refs, steps, missing)

    # 3. 公司代表人 / 負責人
    if cap == "representative" and corp:
        if inp.filed_in_own_name is True:
            steps.append("原處分相對人為公司，訴願人以「代表人個人名義」提起。代表人與公司各具"
                         "獨立人格，個人非處分相對人，其權利或法律上利益未受處分影響 → 不適格。")
            return StandingResult("不適格", "", False, "high",
                                  refs + ["最高行政法院 101 判 1002 參照"], steps, missing)
        if inp.filed_in_own_name is False:
            steps.append("原處分相對人為公司，訴願人為其代表人，係「代表公司」提起（僅由代表人簽名）"
                         " → 訴願人實為公司本人，適格。")
            return StandingResult("適格", "相對人", True, "medium", refs, steps, missing)
        missing.append("filed_in_own_name（係以個人名義或代表公司名義提起）")
        steps.append("相對人為公司、訴願人為其代表人：需確認係以個人名義或代表公司名義提起，"
                     "始能判斷適格。個人名義→不適格；公司名義→適格。")
        return StandingResult("需人工認定", "", None, "low", refs, steps, missing)

    # 4. 反射利益（員工／親屬／股東等）
    if cap == "reflective":
        steps.append(f"訴願人以「{inp.appellant_capacity}」身分提起。此屬經濟上、情感上或其他"
                     "事實上（反射）利害關係，非訴願法 §18 所稱法律上利害關係 → 不適格。")
        return StandingResult("不適格", "", False, "high",
                              refs + ["最高行政法院 101 判 1002 參照"], steps, missing)

    # 5. 可能之法律上利害關係（共有人／承租人／受讓人等）
    if cap == "legal_interest":
        steps.append(f"訴願人以「{inp.appellant_capacity}」身分主張法律上利害關係"
                     + (f"：{inp.claimed_legal_interest}" if inp.claimed_legal_interest else "")
                     + "。其權利或法律上利益是否因原處分直接受損，須個案認定 → 建議實體審查其利害關係。")
        return StandingResult("需人工認定", "利害關係人（待認定）", None, "medium", refs, steps, missing)

    # 6. 檢舉人 / 陳情人
    if cap == "informant":
        steps.append("訴願人係檢舉人／陳情人。對於主管機關是否處分、處分輕重，原則上無公法上"
                     "請求權，非利害關係人 → 不適格（除法律另有明文賦予請求權）。")
        return StandingResult("不適格", "", False, "medium", refs, steps, missing)

    # 7. 訴願人與相對人不同，且無可辨識之適格身分
    if same is False:
        steps.append(f"訴願人「{inp.appellant_name}」與原處分相對人「{inp.respondent_name}」"
                     "並非同一人，訴願人亦未釋明其為法律上利害關係人 → 不適格。")
        if not inp.appellant_capacity:
            missing.append("appellant_capacity（訴願人與相對人之關係）")
        return StandingResult("不適格", "", False,
                              "medium" if not inp.appellant_capacity else "high",
                              refs + ["最高行政法院 101 判 1002 參照"], steps, missing)

    # 8. 連姓名都無法比對
    missing.append("respondent_name / appellant_name（無法比對是否同一人）")
    steps.append("訴願人與原處分相對人之姓名資訊不足，無法判斷是否同一人。")
    return StandingResult("需人工認定", "", None, "low", refs, steps, missing)


if __name__ == "__main__":
    import json
    demos = [
        StandingInput("陳○珠", "盧江溪"),                                 # 不同自然人
        StandingInput("李○祥", "晟○密有限公司", "代表人", filed_in_own_name=True),
        StandingInput("陳○詳", "林○美（即良○企業社）", "員工"),
        StandingInput("王大明", "王大明"),                                 # 本人
        StandingInput("某甲", "某土地", "土地共有人", claimed_legal_interest="系爭土地共有人"),
    ]
    for d in demos:
        r = check_standing(d)
        print(json.dumps({"in": asdict(d), "out": r.to_dict()}, ensure_ascii=False, indent=1))
        print("-" * 60)
