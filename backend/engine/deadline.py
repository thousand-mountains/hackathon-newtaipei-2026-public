"""訴願期間計算引擎（純 Python，零 LLM 依賴）。

法源：
- 訴願法 14 I：自行政處分達到或公告期滿之次日起 30 日內
- 訴願法 14 III：以受理機關收受訴願書之日期為準
- 訴願法 16：不在受理機關所在地者扣除在途期間（日數由呼叫端查表提供）
- 訴願法 17 → 民法 120/121/122：始日不計入；期滿日為星期六/日者以次一工作日代之
- 行政程序法 74：寄存送達自寄存之日起發生送達效力
  （113年/03 決定書原文：「無論應受送達人實際上於何時受領文書，均以寄存之日視為收受送達之日期」）

刻意不自動判定（輸出 caveat 交人工）：
- 訴願法 14 II 利害關係人自「知悉時」起算
- 訴願法 15 回復原狀
- 訴願法 80 逾期案原處分顯屬違法或不當之職權撤銷
- 公示送達之公告期滿日認定
- 國定假日（v0 僅處理週六日；國定假日需行政日曆，輸出 caveat）
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

PERIOD_DAYS = 30

SERVICE_METHODS = {
    "personal": "本人（或同居人/受僱人）簽收",
    "deposit": "寄存送達",
    "public": "公示送達",
}


@dataclass
class Step:
    rule: str
    basis: str
    value: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "basis": self.basis, "value": self.value}


@dataclass
class Result:
    effective_date: dt.date | None
    deadline: dt.date | None
    overdue: bool | None
    steps: list[Step] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "overdue": self.overdue,
            "steps": [s.as_dict() for s in self.steps],
            "caveats": self.caveats,
        }


def _roc(d: dt.date) -> str:
    return f"{d.year - 1911}/{d.month}/{d.day}"


def compute(
    service_method: str,
    service_date: dt.date,
    filing_date: dt.date | None = None,
    transit_days: int = 0,
    interested_party: bool = False,
) -> Result:
    """計算訴願期間。filing_date 為 None 時只算期滿日不判逾期。"""
    if service_method not in SERVICE_METHODS:
        raise ValueError(f"未知送達方式: {service_method}")

    r = Result(effective_date=None, deadline=None, overdue=None)

    if service_method == "public":
        r.caveats.append(
            "公示送達：生效日為公告期滿日之認定涉及公告方式與刊登日，"
            "本引擎不自動判定，請人工確認後改以「本人簽收」模式輸入生效日。"
        )
        return r

    # 步驟 1：送達生效日
    if service_method == "deposit":
        eff = service_date
        r.steps.append(Step(
            rule="寄存送達自寄存之日起發生效力（不論實際何時領取）",
            basis="行政程序法 74；最高行 108 判 531 意旨",
            value=f"生效日 = {_roc(eff)}",
        ))
    else:
        eff = service_date
        r.steps.append(Step(
            rule="本人簽收，送達即生效",
            basis="行政程序法 72",
            value=f"生效日 = {_roc(eff)}",
        ))
    r.effective_date = eff

    # 步驟 2：始日不計入，次日起算
    start = eff + dt.timedelta(days=1)
    r.steps.append(Step(
        rule="自送達生效之次日起算（始日不計入）",
        basis="訴願法 14 I；民法 120 II",
        value=f"起算日 = {_roc(start)}",
    ))

    # 步驟 3：30 日期間（末日）
    deadline = start + dt.timedelta(days=PERIOD_DAYS - 1)
    r.steps.append(Step(
        rule=f"法定期間 {PERIOD_DAYS} 日",
        basis="訴願法 14 I",
        value=f"第 {PERIOD_DAYS} 日 = {_roc(deadline)}",
    ))

    # 步驟 4：在途期間
    if transit_days > 0:
        deadline += dt.timedelta(days=transit_days)
        r.steps.append(Step(
            rule=f"扣除在途期間 {transit_days} 日（訴願人不在受理機關所在地）",
            basis="訴願法 16；訴願扣除在途期間辦法（日數由查表提供）",
            value=f"加計後 = {_roc(deadline)}",
        ))

    # 步驟 5：期滿日逢週六/日順延
    extended = False
    while deadline.weekday() >= 5:  # 5=Sat, 6=Sun
        deadline += dt.timedelta(days=1)
        extended = True
    if extended:
        r.steps.append(Step(
            rule="期滿日為星期六/日，以次一工作日代之",
            basis="訴願法 17 → 民法 122",
            value=f"順延後期滿日 = {_roc(deadline)}",
        ))
    r.deadline = deadline
    r.steps.append(Step(
        rule="期滿日確定",
        basis="以上各步",
        value=f"訴願期間至 {_roc(deadline)} 屆滿",
    ))

    # 步驟 6：逾期判定
    if filing_date is not None:
        r.overdue = filing_date > deadline
        r.steps.append(Step(
            rule="以受理機關收受訴願書之日為提起日",
            basis="訴願法 14 III",
            value=f"提起日 {_roc(filing_date)} {'>' if r.overdue else '≤'} 期滿日 → "
                  f"{'逾期' if r.overdue else '未逾期'}",
        ))

    # caveats（一律輸出，人工判斷區）
    if interested_party:
        r.caveats.append("訴願人主張為利害關係人：期間自「知悉時」起算（訴願法 14 II），知悉時點屬事實認定，本結果僅供對照，請人工確認。")
    r.caveats.append("國定假日順延未納入（v0 僅處理週六日），期滿日落在國定假日者請對照行政機關辦公日曆。")
    if r.overdue:
        r.caveats.append("縱屬逾期，原處分顯屬違法或不當者，機關仍得依職權撤銷或變更（訴願法 80），逾期≠案件終結。")
        r.caveats.append("訴願人如有不可歸責事由，得申請回復原狀（訴願法 15），是否受理屬人工判斷。")
    return r
