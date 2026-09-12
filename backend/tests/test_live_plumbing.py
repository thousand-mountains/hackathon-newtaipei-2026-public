"""Task 1–7 的單元測試：全部 stdlib，live 分支一律用 monkeypatch 假物件。"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import tempfile
from contextlib import contextmanager

import backend.intake.uploads as up
import backend.orchestrator.graph as graph_mod
from backend.config import settings
from backend.config.settings import NODE_TO_AGENTS, load_snapshot
from backend.engine import deadline as deadline_engine
from backend.intake.documents import CJK_RATIO_THRESHOLD, cjk_ratio, route_documents
from backend.intake.uploads import (
    MAX_BYTES,
    list_upload_cases,
    load_upload_case,
    save_upload,
)
from backend.llm import client
from backend.nodes import n1_extract, n2_classify, n4_retrieval, n5_draft, n6_gate
from backend.orchestrator import runstore
from backend.orchestrator.graph import (
    build_payload,
    digest_from_state,
    list_synthetic_cases,
    load_case,
    run_case,
)
from backend.orchestrator.state import CaseState, NodeCtx
from backend.retrieval.base import Hit
import backend.retrieval.kb as kb_module
from backend.retrieval.kb import KBRetriever, build_retriever
from backend.tests import run_all
from backend.tests.harness import assert_eq, assert_in, assert_true
from backend.tests.test_gate_hardening import _confirmed_of


@contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_settings_defaults_are_offline():
    with env(RUN_MODE=None, MODEL_PROVIDER=None, RETRIEVER=None, KB_MIN_SCORE=None):
        assert_eq(settings.run_mode(), "fixture")
        assert_eq(settings.model_provider(), "bedrock")
        assert_eq(settings.retriever_kind(), "lawtable_only")
        assert_eq(settings.kb_min_score(), 0.25)
        assert_eq(settings.missing_live_settings("fixture", "lawtable_only"), [])


def test_missing_live_settings_names_every_absent_variable():
    with env(BEDROCK_MODEL_ID_EXTRACT=None, BEDROCK_MODEL_ID_DRAFT=None, AWS_REGION=None, BEDROCK_KB_ID=None):
        missing = settings.missing_live_settings("bedrock", "kb")
        for name in ("BEDROCK_MODEL_ID_EXTRACT", "BEDROCK_MODEL_ID_DRAFT", "AWS_REGION", "BEDROCK_KB_ID"):
            assert_in(name, missing)
    with env(BEDROCK_MODEL_ID_EXTRACT="m1", BEDROCK_MODEL_ID_DRAFT="m2", AWS_REGION="r", BEDROCK_KB_ID="k"):
        assert_eq(settings.missing_live_settings("bedrock", "kb"), [])


# ── Task 2：backend/llm/ client ──────────────────────────────────
# 這四個測試全部 monkeypatch `client._invoke_structured`（唯一測試接縫），
# 所以不裝 strands／pydantic 也能跑；跑的是 reshape、值域驗證、cite_ids 白名單與重試語意。


def _fake_extraction() -> dict:
    fields = {
        "no": ("synthetic-1130000001", 0.97), "type": ("違反空氣污染防制法事件", 0.93),
        "person": ("（合成）吳○庭", 0.9), "org": ("新北市政府（合成測資）", 0.95),
        "d1": ("2024-06-11", 0.88), "d2": ("2024-06-13", 0.94), "d3": ("2024-07-20", 0.96),
        "agent": ("無", 0.8), "note": ("訴願人主張未實際收受處分書。", 0.7),
        "service_method": ("deposit", 0.91), "transit_days": (0, 0.85), "interested_party": (False, 0.85),
    }
    return {
        **{k: {"value": v, "conf": c, "quote": f"quote-{k}"} for k, (v, c) in fields.items()},
        "facts_excerpt": [{"text": "訴願人於農地露天燃燒稻稈。", "page": 2, "quote_ref": "synthetic-原處分裁處書.pdf#p2"}],
    }


def test_extract_intake_reshapes_structured_result():
    def fake(system, user, schema_name, tools=None, model_kind="extract", **kw):
        assert_eq(schema_name, "ExtractionResult")
        assert_in("訴願書全文", user)
        return _fake_extraction(), {"input_tokens": 10, "output_tokens": 5}

    orig = client._invoke_structured
    client._invoke_structured = fake
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="model-x", AWS_REGION="r"):
            out = client.extract_intake("訴願書全文：……")
    finally:
        client._invoke_structured = orig
    assert_eq(out["intake"]["d2"], "2024-06-13")
    assert_eq(out["conf"]["service_method"], 0.91)
    assert_eq(out["quotes"]["no"], "quote-no")
    assert_eq(len(out["facts_excerpt"]), 1)
    assert_eq(out["model_id"], "model-x")
    assert_eq(out["usage"]["input_tokens"], 10)


def test_extract_intake_rejects_unknown_service_method():
    bad = _fake_extraction()
    bad["service_method"]["value"] = "by_pigeon"
    orig = client._invoke_structured
    client._invoke_structured = lambda *a, **k: (bad, None)
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            try:
                client.extract_intake("x")
            except client.LLMError as e:
                assert_in("service_method", str(e))
            else:
                raise AssertionError("未知送達方式必須 raise LLMError")
    finally:
        client._invoke_structured = orig


def test_extract_intake_rejects_boolean_in_a_text_field():
    """真模型第一次呼叫就踩到的洞（2026-09-08，OpenAI 的 mini 檔位，開發期調 prompt 用）：`type` 回 True，
    一路流到 N2 的 `(intake.get("type") or "").strip()` 才炸 AttributeError。

    根因在 schema：`FieldValue.value` 的 `str | int | bool | None` 聯集是為
    transit_days（int）與 interested_party（bool）開的，卻套在全部十二欄上；
    N1 又只驗 service_method 與 transit_days，於是文字欄收得下布林值。
    布林值不可以 str() 成 "True" 混過去——那個字面值會進 N2 案型分類的 haystack。
    """
    bad = _fake_extraction()
    bad["type"]["value"] = True
    orig = client._invoke_structured
    client._invoke_structured = lambda *a, **k: (bad, None)
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            try:
                client.extract_intake("x")
            except client.LLMError as e:
                assert_in("type", str(e))
                assert_in("布林", str(e))
            else:
                raise AssertionError("文字欄回布林值必須 raise LLMError")
    finally:
        client._invoke_structured = orig


def test_extract_intake_accepts_int_in_a_text_field_as_string():
    """案號寫成數字是**無損**轉換，不必炸——跟布林值不同，str() 之後語意沒有改變。"""
    ok = _fake_extraction()
    ok["no"]["value"] = 1130000001
    orig = client._invoke_structured
    client._invoke_structured = lambda *a, **k: (ok, None)
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            out = client.extract_intake("x")
    finally:
        client._invoke_structured = orig
    assert_eq(out["intake"]["no"], "1130000001")


def test_classify_by_rule_does_not_crash_on_non_string_type():
    """縱深防禦：N1 已擋布林值，但 `confirmed_intake` 也餵得進 N2，型別不能只靠上游。

    `(intake.get("type") or "").strip()` 的 `or ""` 是 **falsy 守衛不是型別守衛**：
    `True or ""` → `True` → `.strip()` → AttributeError。同一個函式裡 note 與 org
    都包了 `str()`，只有 type 沒有。
    """
    for bad in (True, 123, ["x"]):
        case_type, why, hits = n2_classify.classify_by_rule(
            {"type": bad, "note": "", "org": ""}, "違反空氣污染防制法"
        )
        assert_true(isinstance(case_type, str), f"type={bad!r} 時 case_type 仍須是字串")
        assert_true(isinstance(why, str))


def test_draft_sentences_filters_cite_ids_outside_context():
    def fake(system, user, schema_name, tools=None, model_kind="draft", **kw):
        assert_eq(schema_name, "DraftResult")
        return {
            "reasoning": [
                {"t": "按訴願法第14條……", "cite_ids": ["L1"], "basis": "訴願法第14條", "source_kind": "law"},
                {"t": "另參最高行 999 判……", "cite_ids": ["L9"], "basis": None, "source_kind": "ref"},
            ],
            "conclusion": [{"t": "訴願不受理。", "cite_ids": ["L4"], "basis": "訴願法第77條", "source_kind": "law"}],
        }, None

    ctx = {"intake": {}, "facts_excerpt": [], "screen": {},
           "laws": [{"id": "L1"}, {"id": "L4"}], "cases": [{"id": "C1"}]}
    orig = client._invoke_structured
    client._invoke_structured = fake
    try:
        with env(BEDROCK_MODEL_ID_DRAFT="m", AWS_REGION="r"):
            out = client.draft_sentences(ctx, slots=["reasoning"])
    finally:
        client._invoke_structured = orig
    assert_eq(list(out["slots"].keys()), ["reasoning"], "只回要求的 slot，conclusion 不得出現")
    assert_eq(out["slots"]["reasoning"][0]["cite_ids"], ["L1"])
    assert_eq(out["slots"]["reasoning"][1]["cite_ids"], [], "L9 不在 N4 結果也不在工具命中，必須被清空")
    assert_true(out["slots"]["reasoning"][1].get("unsupported") is True)


def test_client_raises_llm_error_after_retries():
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("simulated throttling")

    orig = client._invoke_structured
    client._invoke_structured = boom
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            try:
                client.extract_intake("x", retries=3, backoff_s=0.0)
            except client.LLMError as e:
                assert_in("simulated throttling", str(e))
            else:
                raise AssertionError("必須 raise LLMError")
    finally:
        client._invoke_structured = orig
    assert_eq(calls["n"], 3)


def test_extract_intake_passes_pdf_documents_as_attachments():
    """Task 3b：掃描件走視覺讀——PDF 原樣往下傳，且提示模型讀附件。"""
    seen: dict = {}

    def fake(system, user, schema_name, tools=None, model_kind="extract", attachments=None, **kw):
        seen["attachments"] = attachments
        seen["user"] = user
        return _fake_extraction(), None

    orig = client._invoke_structured
    client._invoke_structured = fake
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            client.extract_intake("", pdf_documents=[("原處分書.pdf", b"%PDF-1.4")])
    finally:
        client._invoke_structured = orig
    assert_eq(seen["attachments"], [("原處分書.pdf", b"%PDF-1.4")])
    assert_in("請直接閱讀其頁面內容", seen["user"])
    assert_in("（無文字層，請閱讀附件 PDF）", seen["user"])


def test_safe_doc_name_keeps_only_bedrock_allowed_characters():
    """Bedrock document 區塊的 name 只收英數／空白／連字號／括號。"""
    assert_eq(client._safe_doc_name("原處分書.pdf"), "_____pdf")  # 4 個中文字 + 1 個點
    assert_eq(client._safe_doc_name("case (1) [a].pdf"), "case (1) [a]_pdf")
    assert_eq(client._safe_doc_name("原"), "_")
    assert_eq(client._safe_doc_name(""), "doc")
    assert_eq(len(client._safe_doc_name("a" * 200)), 60)


# 測試裡不寫真的 model id：`llm/client.py` 的規矩是「程式不得出現任何 model id 的
# 實際值」，值一律由環境變數注入（`.env`／task definition）。測試只需要一個
# 「不是 Bedrock 那兩個」的可辨識字串。
_FAKE_OPENAI_MODEL = "fake-openai-model-for-tests"


def test_model_ids_report_the_model_actually_called_when_provider_is_not_bedrock():
    """MODEL_PROVIDER=openai 時 `model_ids` 不得報 Bedrock 的 id——那個 id 一次都沒被呼叫過。

    graph.py 的 `_model_ids_if_live` 已經擋掉「fixture 卻報 model id」那種謊報，
    但同一件事從 provider 這個入口還進得來：openai 分支讀的是 `OPENAI_MODEL_ID`，
    而 `model_ids()` 回報的是 `BEDROCK_MODEL_ID_*`，兩者毫無關係（CONSTITUTION §1）。
    """
    with env(MODEL_PROVIDER="openai", OPENAI_MODEL_ID=_FAKE_OPENAI_MODEL,
             BEDROCK_MODEL_ID_EXTRACT="bedrock-id-never-called",
             BEDROCK_MODEL_ID_DRAFT="bedrock-id-never-called-2"):
        ids = client.model_ids()
    assert_eq(ids["provider"], "openai")
    assert_eq(ids["extract"], _FAKE_OPENAI_MODEL, "extract 要報真的被呼叫的 model")
    assert_eq(ids["draft"], _FAKE_OPENAI_MODEL, "draft 要報真的被呼叫的 model")
    assert_true(
        "bedrock-id-never-called" not in json.dumps(ids, ensure_ascii=False),
        "沒被呼叫的 Bedrock model id 不得出現在 model_ids 裡",
    )


def test_model_ids_note_says_the_output_is_not_from_aws_when_provider_is_not_bedrock():
    """非 AWS provider 必須在 payload 上明說，否則畫面與證據看起來就是 Bedrock 跑的。"""
    with env(MODEL_PROVIDER="openai", OPENAI_MODEL_ID=_FAKE_OPENAI_MODEL):
        note = graph_mod._model_ids_note("bedrock", {"n1", "n5"})
    assert_true(note, "provider 非 bedrock 時 model_ids_note 不得為 None")
    assert_in("openai", note.lower(), "要指名 provider")
    assert_in("不得", note, "要明說輸出不得當驗收證據")


def test_load_model_openai_requires_explicit_model_id():
    """openai 分支不預設任何 model id；缺變數要在 import strands 之前就 raise LLMError。

    檢查排在 import 之前，才會在**沒裝 strands 的機器**上吐出「缺哪個變數」而不是 ModuleNotFoundError。
    """
    with env(MODEL_PROVIDER="openai", OPENAI_API_KEY="k", OPENAI_MODEL_ID=None):
        try:
            client._load_model("extract")
        except client.LLMError as e:
            assert_in("OPENAI_MODEL_ID", str(e))
        else:
            raise AssertionError("缺 OPENAI_MODEL_ID 必須 raise LLMError")


def test_load_model_bedrock_reports_missing_settings_before_importing_strands():
    with env(MODEL_PROVIDER=None, BEDROCK_MODEL_ID_EXTRACT=None, AWS_REGION="r"):
        try:
            client._load_model("extract")
        except client.LLMError as e:
            assert_in("BEDROCK_MODEL_ID_EXTRACT", str(e))
        else:
            raise AssertionError("缺 BEDROCK_MODEL_ID_EXTRACT 必須 raise LLMError")


def test_extract_intake_rejects_non_integer_transit_days_without_retrying():
    """值域錯誤是模型輸出不合格，不是暫時性失敗——直接 LLMError，不燒重試額度。"""
    bad = _fake_extraction()
    bad["transit_days"]["value"] = "七日"
    calls = {"n": 0}

    def once(*a, **k):
        calls["n"] += 1
        return bad, None

    orig = client._invoke_structured
    client._invoke_structured = once
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            try:
                client.extract_intake("x", retries=3, backoff_s=0.0)
            except client.LLMError as e:
                assert_in("transit_days", str(e))
            else:
                raise AssertionError("非整數在途期間必須 raise LLMError")
    finally:
        client._invoke_structured = orig
    assert_eq(calls["n"], 1, "值域錯誤不得進重試")


# ── Task 3：合成案例 documents ＋ N1 三向分流 ─────────────────────
# bedrock 分支的測試一律 monkeypatch `client.extract_intake`（唯一接縫），
# 所以不裝 strands、沒有憑證也能跑；跑的是分流、卷證餵入與錯誤傳遞。


def _ordinary_fixture():
    return load_case("synthetic-ordinary-01")


def test_synthetic_cases_carry_documents_text():
    for cid in list_synthetic_cases():
        fx = load_case(cid)
        docs = fx.get("documents") or []
        assert_true(len(docs) >= 1, f"{cid} 缺 documents")
        joined = "".join(d["text"] for d in docs)
        for ex in fx["extraction"].get("facts_excerpt", []):
            assert_in(ex["text"], joined, f"{cid} 的 facts_excerpt 必須逐字出現在 documents 內")


def test_n1_bedrock_branch_uses_client_and_marks_origin_llm():
    seen = {}

    def fake_extract(document_text, **kw):
        seen["text"] = document_text
        e = _fake_extraction()
        return {
            "intake": {k: e[k]["value"] for k in client.INTAKE_FIELDS},
            "conf": {k: e[k]["conf"] for k in client.INTAKE_FIELDS},
            "quotes": {k: e[k]["quote"] for k in client.INTAKE_FIELDS},
            "facts_excerpt": e["facts_excerpt"],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "model_id": "model-x",
        }

    orig = client.extract_intake
    client.extract_intake = fake_extract
    try:
        state = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
        r = n1_extract.run(state, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.extract_intake = orig
    assert_in("寄存於轄區派出所", seen["text"], "卷證全文必須餵給模型")
    assert_eq(state.intake_origin["d2"], "llm")
    assert_eq(state.intake["service_method"], "deposit")
    assert_eq(r.data["generation"]["model_id"], "model-x")
    assert_true(not any("fixture" in log[0] or "重播" in log[0] for log in r.narrative["clerk"]["logs"]),
                "bedrock 分支的敘述不得再說自己是重播")


def test_n1_bedrock_branch_propagates_llm_error():
    orig = client.extract_intake
    client.extract_intake = lambda *a, **k: (_ for _ in ()).throw(client.LLMError("boom"))
    try:
        try:
            n1_extract.run(CaseState(case_id="x", run_mode="bedrock"), NodeCtx(run_mode="bedrock"),
                           case_fixture=_ordinary_fixture())
        except client.LLMError:
            pass
        else:
            raise AssertionError("LLMError 必須往上拋，不得吞掉改吐 fixture")
    finally:
        client.extract_intake = orig


def test_n1_still_raises_for_unknown_mode():
    try:
        n1_extract.run(CaseState(case_id="x", run_mode="local"), NodeCtx(run_mode="local"), case_fixture=_ordinary_fixture())
    except NotImplementedError:
        pass
    else:
        raise AssertionError("local 模式仍未實作，必須 raise")


# ── Task 3b：卷證文字路由、上傳案件、N2/N3 digest 來源 ─────────────
# route_documents 的 PDF 文字抽取一律用注入的 extractor，測試不依賴機器上有沒有 pdftotext。


def test_cjk_ratio_and_routing_with_injected_extractor():

    assert_true(cjk_ratio("訴願人於農地露天燃燒") > 0.9)
    assert_true(cjk_ratio("abc def 123") == 0.0)
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "a.txt").write_text("訴願書全文（合成）", encoding="utf-8")
    (d / "digital.pdf").write_bytes(b"%PDF-1.4 fake")
    (d / "scan.pdf").write_bytes(b"%PDF-1.4 fake")

    def extractor(p):
        return "裁處書（合成）本文" if p.name == "digital.pdf" else "\x0c\x0c   "

    docs = route_documents(d, text_extractor=extractor)
    kinds = {x.n: x.kind for x in docs}
    assert_eq(kinds, {"a.txt": "txt", "digital.pdf": "pdf_text", "scan.pdf": "pdf_visual"})
    assert_true(all(x.cjk_ratio >= CJK_RATIO_THRESHOLD for x in docs if x.kind != "pdf_visual"))


def test_save_and_load_upload_case_shape_and_prefix():

    d = pathlib.Path(tempfile.mkdtemp())
    meta = save_upload([("訴願書.txt", "訴願書全文（合成）".encode("utf-8"))], uploads_dir=d)
    assert_true(meta["case_id"].startswith("upload-"))
    assert_eq(meta["provenance"]["kind"], "uploaded")
    fx = load_upload_case(meta["case_id"], uploads_dir=d)
    assert_eq(fx["id"], meta["case_id"])
    assert_eq([f["n"] for f in fx["files"]], ["訴願書.txt"])
    assert_eq(fx["documents"][0]["kind"], "txt")
    assert_in("訴願書全文", fx["documents"][0]["text"])
    assert_true("extraction" not in fx and "draft_fixture" not in fx, "上傳案沒有 fixture 區塊")
    assert_eq(list_upload_cases(uploads_dir=d), [meta["case_id"]])


def test_save_upload_rejects_bad_suffix_and_oversize():

    d = pathlib.Path(tempfile.mkdtemp())
    for files in ([("x.docx", b"1")], [("x.pdf", b"0" * (MAX_BYTES + 1))]):
        try:
            save_upload(files, uploads_dir=d)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{files[0][0]} 必須被拒絕")


def test_load_case_dispatches_on_prefix():
    assert_eq(load_case("synthetic-ordinary-01")["id"], "synthetic-ordinary-01")
    for bad in ("real-123", "upload-does-not-exist"):
        try:
            load_case(bad)
        except (ValueError, FileNotFoundError):
            pass
        else:
            raise AssertionError(f"{bad} 必須 raise")


def test_upload_case_in_fixture_mode_is_refused_honestly():

    d = pathlib.Path(tempfile.mkdtemp())
    meta = save_upload([("訴願書.txt", "訴願書全文（合成）".encode("utf-8"))], uploads_dir=d)
    orig = up.UPLOADS_DIR
    up.UPLOADS_DIR = d
    try:
        try:
            run_case(meta["case_id"], mode="fixture")
        except ValueError as e:
            assert_in("bedrock", str(e), "要說清楚上傳案只能在 bedrock 模式跑")
        else:
            raise AssertionError("上傳案在 fixture 模式沒有 extraction 可重播，必須 raise")
    finally:
        up.UPLOADS_DIR = orig


def test_n1_bedrock_passes_pdf_visual_docs_as_attachments():
    seen = {}

    def fake_extract(document_text, *, pdf_documents=None, **kw):
        seen["text"] = document_text
        seen["pdfs"] = pdf_documents
        e = _fake_extraction()
        return {
            "intake": {k: e[k]["value"] for k in client.INTAKE_FIELDS},
            "conf": {k: e[k]["conf"] for k in client.INTAKE_FIELDS},
            "quotes": {},
            "facts_excerpt": e["facts_excerpt"],
            "usage": None,
            "model_id": "m",
        }

    fixture = {
        "id": "upload-x", "files": [], "provenance": {"kind": "uploaded"},
        "documents": [
            {"n": "a.txt", "kind": "txt", "text": "訴願書全文", "cjk_ratio": 1.0, "path": None},
            {"n": "scan.pdf", "kind": "pdf_visual", "text": "", "cjk_ratio": 0.0, "bytes": b"%PDF-1.4 fake"},
        ],
    }
    orig = client.extract_intake
    client.extract_intake = fake_extract
    try:
        r = n1_extract.run(
            CaseState(case_id="upload-x", run_mode="bedrock"), NodeCtx(run_mode="bedrock"), case_fixture=fixture
        )
    finally:
        client.extract_intake = orig
    assert_in("訴願書全文", seen["text"])
    # 檔名加序號前綴：多份中文檔名 PDF 會被 _safe_doc_name 清成同一個名字，序號讓模型分得出來
    assert_eq(seen["pdfs"], [("2-scan.pdf", b"%PDF-1.4 fake")])
    assert_eq(r.data["generation"]["input_route"], {"a.txt": "txt", "scan.pdf": "pdf_visual"})
    assert_true(any("視覺" in log[0] for log in r.narrative["clerk"]["logs"]), "視覺讀取要在敘述裡講出來")


def test_n2_n3_digest_falls_back_to_n1_output():
    st = CaseState(case_id="upload-x", run_mode="bedrock")
    st.facts_excerpt = [{"text": "訴願人於農地露天燃燒稻稈。"}, {"text": "經稽查查獲。"}]
    st.intake = {"note": "主張未收受處分書"}
    d = digest_from_state(st)
    assert_in("露天燃燒稻稈", d)
    assert_in("經稽查查獲", d)
    assert_in("主張未收受", d)


def test_save_upload_refuses_colliding_filenames():
    """兩份檔案清成同一個檔名時必須拒絕，不能後者默默蓋掉前者。

    `_safe_name` 只取 basename，所以「卷證/訴願書.txt」與「訴願書.txt」會落在同一個路徑：
    覆蓋之後磁碟上只剩一份，但 `case.json` 的 `files[]` 仍宣稱有兩份——
    那就是對承辦人虛報「這些卷證我都收到了」。寧可擋下來請人改名。
    """
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        save_upload([("卷證/訴願書.txt", "甲（合成測資）".encode("utf-8")),
                     ("訴願書.txt", "乙（合成測資）".encode("utf-8"))], uploads_dir=d)
    except ValueError as e:
        assert_in("訴願書.txt", str(e), "要講出是哪個檔名撞在一起")
    else:
        raise AssertionError("同名檔案會互相覆蓋、files[] 會虛報份數，必須拒絕")


def test_n1_bedrock_refuses_pdf_visual_without_bytes():
    """pdf_visual 卻沒有 bytes：直接 raise，不要送一份空的 PDF 給模型。

    送 b"" 過去的話，模型「讀」的是一份不存在的文件，回來的欄位會被記成
    input_route 裡的 pdf_visual——等於宣稱視覺讀過了。這是分層誠實違規（CONSTITUTION §1）。
    """
    calls = {"n": 0}

    def fake_extract(document_text, *, pdf_documents=None, **kw):
        calls["n"] += 1
        raise AssertionError("空的 pdf_visual 不該走到模型呼叫")

    fixture = {
        "id": "upload-y", "files": [], "provenance": {"kind": "uploaded"},
        "documents": [{"n": "scan.pdf", "kind": "pdf_visual", "text": "", "cjk_ratio": 0.0}],
    }
    orig = client.extract_intake
    client.extract_intake = fake_extract
    try:
        n1_extract.run(
            CaseState(case_id="upload-y", run_mode="bedrock"), NodeCtx(run_mode="bedrock"), case_fixture=fixture
        )
    except ValueError as e:
        assert_in("scan.pdf", str(e), "要指名是哪一份卷證缺內容")
    else:
        raise AssertionError("pdf_visual 缺 bytes 必須 raise，不得送空檔案")
    finally:
        client.extract_intake = orig
    assert_eq(calls["n"], 0, "不該呼叫到模型")


# ── Task 4：N5 bedrock 分支（含 retrieve 工具介面）─────────────────
# 一律 monkeypatch `client.draft_sentences`（節點端唯一接縫），所以不裝 strands、
# 沒有憑證也能跑；跑的是分流、槽位封鎖、工具接線與 refs 落地。


def _screened_state(requires_human: bool) -> CaseState:
    # deadline 直接用期間引擎的真實輸出：`steps` 的形狀（rule／value／basis）是
    # build_doc_skeleton 的硬需求，手寫一份簡化版會讓測試在假的形狀上通過。
    computed = deadline_engine.compute(
        service_method="deposit", service_date=dt.date(2024, 6, 13),
        filing_date=dt.date(2024, 7, 20), transit_days=0, interested_party=False,
    ).as_dict()
    st = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
    st.intake = {"no": "synthetic-1130000001", "type": "違反空氣污染防制法事件", "d2": "2024-06-13", "d3": "2024-07-20"}
    st.facts_excerpt = [{"text": "事實段。", "page": 1}]
    st.screen = {"requires_human_conclusion": requires_human,
                 "deadline": computed,
                 "art77": {"clause": "77-2"}}
    st.retrieval = {"laws": [{"id": "L1", "t": "訴願法第14條"}, {"id": "L4", "t": "訴願法第77條"}],
                    "cases": [{"id": "C1", "t": "113年-…-駁回", "outcome": "駁回"}], "retrieval_meta": {}}
    return st


def test_n5_bedrock_branch_requests_only_allowed_slots_and_keeps_shape():
    seen = {}

    def fake_draft(context, slots, retrieve_fn=None, **kw):
        seen["slots"] = list(slots)
        seen["ctx_ids"] = [law["id"] for law in context["laws"]] + [c["id"] for c in context["cases"]]
        seen["retrieve_fn"] = retrieve_fn
        return {"slots": {s: [{"t": f"{s} 句", "cite_ids": ["L1"], "basis": "訴願法第14條", "source_kind": "law"}] for s in slots},
                "tool_calls": [], "usage": None, "model_id": "model-d"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        r = n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    assert_eq(seen["slots"], ["reasoning"], "封鎖時 conclusion 不得進 slots")
    assert_eq(seen["ctx_ids"], ["L1", "L4", "C1"])
    assert_true(seen["retrieve_fn"] is None, "ctx.retriever 為 None 時不給工具")
    assert_eq(st.draft["generation_mode"], "bedrock_live")
    assert_eq(st.draft["model_id"], "model-d")
    assert_eq(list(st.draft["slots"].keys()), ["reasoning"])
    assert_true("l" not in st.draft["slots"]["reasoning"][0] and "why" not in st.draft["slots"]["reasoning"][0])
    assert_true(r.degraded is False, "真模型生成不是降級")


def test_n5_bedrock_branch_strips_conclusion_even_if_model_returns_it():
    def fake_draft(context, slots, retrieve_fn=None, **kw):
        return {"slots": {"reasoning": [{"t": "理由", "cite_ids": [], "basis": None, "source_kind": "law"}],
                          "conclusion": [{"t": "訴願不受理。", "cite_ids": [], "basis": None, "source_kind": "law"}]},
                "tool_calls": [], "usage": None, "model_id": "m"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    assert_true("conclusion" not in st.draft["slots"], "程式端第二道：模型多回的 conclusion 必須刪掉")
    assert_true(st.draft["conclusion_dropped"] is True)


def test_n5_bedrock_branch_wires_retriever_into_tool():
    class FakeRetriever:
        name = "fake_kb"

        def __init__(self):
            self.queries = []

        def search(self, query, filters=None, top_k=5):
            self.queries.append((query, filters))
            return [Hit(id="R1", title="法務部 93 函釋", score=0.9, source="行政函釋/法務部93.txt",
                        payload={"text": "寄存之日視為收受送達之日"})]

    def fake_draft(context, slots, retrieve_fn=None, **kw):
        hits = retrieve_fn("寄存送達 生效")
        assert_eq(hits[0]["id"], "R1")
        return {"slots": {"reasoning": [{"t": "依函釋……", "cite_ids": ["R1"], "basis": None, "source_kind": "ref"}]},
                "tool_calls": [{"query": "寄存送達 生效", "hit_ids": ["R1"]}], "usage": None, "model_id": "m"}

    fr = FakeRetriever()
    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        n5_draft.run(st, NodeCtx(run_mode="bedrock", retriever=fr), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    assert_eq(fr.queries[0][1]["prefix"], ["行政函釋/", "司法院釋字及行政判解/"], "N5 的工具只准查函釋與判解前綴")
    assert_eq(st.draft["refs"][0]["id"], "R1")
    assert_eq(st.draft["refs"][0]["src"], "行政函釋/法務部93.txt")


def test_draft_sentences_resets_tool_state_between_retries():
    """重試是「重新來過」，不是「接著上一次」。

    `tool_calls` 與 `allowed` 是 draft_sentences 的閉包狀態，被工具就地累加。
    若不在每次嘗試前重設，重試回來的 `tool_calls` 會混進上一次失敗嘗試的紀錄
    （報告上會虛報查了幾次），白名單也會留著上一輪命中的 id——那等於讓模型引用
    一批「這次沒查到」的來源，違反引用必可驗（CONSTITUTION §2）。

    strands 未安裝時 `client.tool` 是 None，這裡用 identity decorator 頂上，
    讓 `tools[0]` 就是未包裝的 retrieve_refs，測試才拿得到工具本體來呼叫。
    """
    attempts = {"n": 0}

    def fake_invoke(system, user, schema_name, tools=None, model_kind="draft", **kw):
        attempts["n"] += 1
        tools[0](f"第{attempts['n']}次查詢")
        if attempts["n"] == 1:
            raise RuntimeError("simulated throttling")
        return {"reasoning": [{"t": "依函釋……", "cite_ids": ["R1", "R9"], "basis": None, "source_kind": "ref"}]}, None

    def retrieve_fn(query):
        rid = "R9" if "第1次" in query else "R1"
        return [{"id": rid, "src": "行政函釋/x.txt", "text": "……"}]

    ctx = {"intake": {}, "facts_excerpt": [], "screen": {}, "laws": [{"id": "L1"}], "cases": []}
    orig_invoke, orig_tool = client._invoke_structured, client.tool
    client._invoke_structured = fake_invoke
    client.tool = lambda f: f
    try:
        with env(BEDROCK_MODEL_ID_DRAFT="m", AWS_REGION="r"):
            out = client.draft_sentences(ctx, ["reasoning"], retrieve_fn, retries=3, backoff_s=0.0)
    finally:
        client._invoke_structured = orig_invoke
        client.tool = orig_tool
    assert_eq(attempts["n"], 2)
    assert_eq(out["tool_calls"], [{"query": "第2次查詢", "hit_ids": ["R1"]}], "第一次嘗試的工具紀錄不得殘留")
    assert_eq(out["slots"]["reasoning"][0]["cite_ids"], ["R1"], "R9 是上一次失敗嘗試命中的，白名單必須已重設")
    assert_true(out["slots"]["reasoning"][0].get("unsupported") is True)


def test_n5_renumbers_ref_ids_so_two_tool_calls_cannot_collide():
    """ref id 一律由 N5 重編：檢索器兩次都回 id="R1" 時，兩筆必須是 R1／R2。

    沿用檢索器的 id 會讓 `refs` 兩筆不同來源共用一個號碼，模型引 R1 時對不出是哪一筆
    ——引用必可驗（CONSTITUTION §2）的直接破口。原始 id 留在 `src_id`，仍能追回檢索器那端。
    """
    class CollidingRetriever:
        name = "fake_kb"

        def __init__(self):
            self.n = 0

        def search(self, query, filters=None, top_k=5):
            self.n += 1
            return [Hit(id="R1", title=f"來源 {self.n}", score=0.9,
                        source=f"行政函釋/第{self.n}份.txt", payload={"text": f"段落 {self.n}"})]

    def fake_draft(context, slots, retrieve_fn=None, **kw):
        first = retrieve_fn("寄存送達 生效")
        second = retrieve_fn("回復原狀 不可歸責")
        assert_eq([h["id"] for h in first], ["R1"])
        assert_eq([h["id"] for h in second], ["R2"], "第二次工具呼叫要接著編號，不得從頭來過")
        assert_eq(second[0]["src_id"], "R1", "回給模型的 dict 也要帶得回原始 id")
        return {"slots": {"reasoning": [{"t": "依函釋……", "cite_ids": ["R2"], "basis": None, "source_kind": "ref"}]},
                "tool_calls": [], "usage": None, "model_id": "m"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        n5_draft.run(st, NodeCtx(run_mode="bedrock", retriever=CollidingRetriever()),
                     case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    refs = st.draft["refs"]
    assert_eq([r["id"] for r in refs], ["R1", "R2"], "兩筆不同來源不得共用一個 ref id")
    assert_eq([r["src_id"] for r in refs], ["R1", "R1"], "原始 id 要留著，才追得回檢索器那端")
    assert_eq([r["src"] for r in refs], ["行政函釋/第1份.txt", "行政函釋/第2份.txt"])


def test_n5_logs_when_upstream_retrieval_is_empty():
    """N4 什麼都沒查到時，模型的引用一定全被清空——畫面上要看得出原因出在上游。"""
    def fake_draft(context, slots, retrieve_fn=None, **kw):
        return {"slots": {"reasoning": [{"t": "理由", "cite_ids": [], "basis": None, "source_kind": "law"}]},
                "tool_calls": [], "usage": None, "model_id": "m"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        st.retrieval = {"laws": [], "cases": [], "retrieval_meta": {}}
        r = n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    logs = [row[0] for row in r.narrative["draft"]["logs"]]
    assert_true(any("上游檢索結果為空" in text for text in logs), f"缺空檢索的說明 log：{logs}")


# ── Task 5：Managed KB 檢索器與 N4 通道 B ────────────────────────────
class _FakeBedrockAgentRuntime:
    """模擬 boto3 bedrock-agent-runtime client 的 retrieve()。"""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def retrieve(self, **kw):
        self.calls.append(kw)
        return {"retrievalResults": self.results}


def _kb_result(uri_tail, score, text, file_type="TXT"):
    return {"score": score,
            "content": {"text": text},
            "location": {"s3Location": {"uri": f"s3://bucket/kb/official/{uri_tail}"}},
            "metadata": {"_file_type": file_type, "_source_uri": f"s3://bucket/kb/official/{uri_tail}"}}


def test_kb_retriever_filters_by_prefix_score_filetype_and_exclusion():
    fake = _FakeBedrockAgentRuntime([
        _kb_result("歷史訴願決定書/113年/16.113年-違反洗錢防制法事件-79I-駁回.txt", 0.9, "主文：訴願駁回。"),
        _kb_result("歷史訴願決定書/114年/1131030896-撤銷.txt", 0.8, "撤銷"),          # exclude_case 命中
        _kb_result("行政函釋/法務部93.txt", 0.95, "函釋"),                              # 前綴不符
        _kb_result("歷史訴願決定書/112年/低分.txt", 0.1, "低分"),                       # 分數不足
        _kb_result("歷史訴願決定書/112年/舊PDF.pdf", 0.9, "亂碼", file_type="PDF"),      # PDF 一律丟
    ])
    r = KBRetriever(kb_id="kb-x", region="r", min_score=0.25, client=fake)
    hits = r.search("露天燃燒", filters={"prefix": ["歷史訴願決定書/"], "exclude_case": "1131030896"}, top_k=5)
    assert_eq([h.source for h in hits], ["歷史訴願決定書/113年/16.113年-違反洗錢防制法事件-79I-駁回.txt"])
    assert_eq(hits[0].payload["outcome"], "駁回")
    assert_eq(hits[0].payload["provenance"], "official")
    assert_true(hits[0].verified is False, "verified 由 N6 對 manifest 決定，檢索不自己宣稱")
    call = fake.calls[0]
    assert_eq(call["knowledgeBaseId"], "kb-x")
    assert_eq(call["retrievalConfiguration"]["vectorSearchConfiguration"]["numberOfResults"],
              kb_module.REF_FETCH_DEPTH, "先多抓再後過濾（深度見 REF_FETCH_DEPTH）")


def test_kb_retriever_parses_public_prefix_and_outcome_from_filename():
    res = _kb_result("x", 0.9, "t")
    res["location"]["s3Location"]["uri"] = "s3://b/kb/public/新北訴願決定書_全量/1121051256_不受理.txt"
    res["metadata"]["_source_uri"] = res["location"]["s3Location"]["uri"]
    r = KBRetriever(kb_id="k", region="r", client=_FakeBedrockAgentRuntime([res]))
    hits = r.search("q", filters={"prefix": ["新北訴願決定書_全量/"]})
    assert_eq(hits[0].payload["provenance"], "public_crawl")
    assert_eq(hits[0].payload["outcome"], "不受理")


def _kb_public_result(uri_tail, score, text, file_type="TXT"):
    uri = f"s3://bucket/kb/public/{uri_tail}"
    return {"score": score,
            "content": {"text": text},
            "location": {"s3Location": {"uri": uri}},
            "metadata": {"_file_type": file_type, "_source_uri": uri}}


def test_default_prefixes_take_both_batches_of_appeal_decisions():
    """相似案通道預設同時收 official 與 public_crawl 兩批訴願決定書。

    2026-09-12 決賽環境實測：KB 裡 public 有 2347 筆、official 只有 101 筆，
    前 15 名全被 public 佔滿；只收 official 的後過濾會把結果清成 0 筆，
    等於這個功能形同虛設。兩批都是新北市政府訴願決定書，來源差異靠
    `payload.provenance`（official／public_crawl）標示，不靠丟掉 2347 筆真實決定書。
    """
    fake = _FakeBedrockAgentRuntime([
        _kb_public_result("新北訴願決定書_全量/1121070551_不受理.txt", 0.9, "主文：訴願不受理。"),
        _kb_result("歷史訴願決定書/113年/16.113年-違反空氣污染防制法事件-駁回.txt", 0.8, "主文：訴願駁回。"),
    ])
    r = KBRetriever(kb_id="k", region="r", min_score=0.25, client=fake)
    hits = r.search("露天燃燒")  # 不傳 filters → 走 DEFAULT_PREFIXES
    assert_eq(len(hits), 2, "public 批的命中不得被前綴過濾掉")
    assert_eq([h.payload["provenance"] for h in hits], ["public_crawl", "official"])
    assert_eq(hits[0].payload["outcome"], "不受理", "結果仍照檔名，不由模型推測")


def test_default_prefixes_still_exclude_interpretations_and_court_rulings():
    """放寬不等於全收：行政函釋／司法院釋字及行政判解是通道 A 與 N5 的材料，不是相似案。"""
    fake = _FakeBedrockAgentRuntime([
        _kb_result("行政函釋/法務部93.txt", 0.95, "函釋"),
        _kb_result("司法院釋字及行政判解/釋字第684號.txt", 0.94, "釋字"),
        _kb_public_result("新北訴願決定書_全量/1121070551_不受理.txt", 0.5, "主文：訴願不受理。"),
    ])
    r = KBRetriever(kb_id="k", region="r", min_score=0.25, client=fake)
    hits = r.search("q")
    assert_eq([h.source for h in hits], ["新北訴願決定書_全量/1121070551_不受理.txt"],
              "函釋與釋字判解不得進相似案通道")


class _QuotaFakeRuntime:
    """依查詢批次回不同結果的假 client：相似案通道會打兩次（official／public 各一次）。"""

    def __init__(self, batches):
        self.batches = batches   # 依呼叫順序回傳
        self.calls = []

    def retrieve(self, **kw):
        self.calls.append(kw)
        i = min(len(self.calls) - 1, len(self.batches) - 1)
        return {"retrievalResults": self.batches[i]}


@contextmanager
def _no_retrieve_interval():
    """測試不需要等那 1.1 秒的 RPS 間隔（真的 sleep 會讓整套測試多跑好幾秒）。"""
    orig = kb_module.RETRIEVE_INTERVAL_S
    kb_module.RETRIEVE_INTERVAL_S = 0
    try:
        yield
    finally:
        kb_module.RETRIEVE_INTERVAL_S = orig


def _quota_retriever(batches, min_score=0.25):
    return KBRetriever(kb_id="k", region="r", min_score=min_score,
                       client=_QuotaFakeRuntime(batches))


def test_similar_case_quota_queries_each_batch_separately():
    """配額是「兩批分開查再合併」，不是「先撈一大包再硬塞席次」。"""
    official = [_kb_result(f"歷史訴願決定書/113年/o{i}-駁回.txt", 0.90 - i / 100, "o") for i in range(4)]
    public = [_kb_public_result(f"新北訴願決定書_全量/p{i}_駁回.txt", 0.95 - i / 100, "p") for i in range(6)]
    r = _quota_retriever([official, public])
    with _no_retrieve_interval():
        hits = r.search("q", top_k=5)
    calls = r._client.calls
    assert_eq(len(calls), 2, "相似案通道要兩批各打一次，不是打一次撈一大包")
    assert_eq(calls[0]["retrievalConfiguration"]["vectorSearchConfiguration"]["numberOfResults"], 50,
              "配額查詢要抓夠深，否則少數批永遠被擠掉（2026-09-12 實測 15 撈到 0 筆 official）")
    provs = [h.payload["provenance"] for h in hits]
    assert_eq(len(hits), 5)
    assert_eq(provs.count("official"), 2, "official 最多 2 席")
    assert_eq(provs.count("public_crawl"), 3, "public 最多 3 席")


def test_similar_case_quota_sorts_merged_results_by_score():
    """合併後按分數排序，C1 永遠是分數最高的那筆。"""
    official = [_kb_result("歷史訴願決定書/113年/高分-駁回.txt", 0.99, "o"),
                _kb_result("歷史訴願決定書/113年/次高-撤銷.txt", 0.60, "o")]
    public = [_kb_public_result("新北訴願決定書_全量/p1_駁回.txt", 0.80, "p"),
              _kb_public_result("新北訴願決定書_全量/p2_駁回.txt", 0.70, "p"),
              _kb_public_result("新北訴願決定書_全量/p3_駁回.txt", 0.50, "p")]
    with _no_retrieve_interval():
        hits = _quota_retriever([official, public]).search("q", top_k=5)
    assert_eq([h.score for h in hits], sorted([h.score for h in hits], reverse=True), "沒有按分數排序")
    assert_eq([h.id for h in hits], ["kb-1", "kb-2", "kb-3", "kb-4", "kb-5"], "編號要照排序後的順序給")
    assert_eq(hits[0].score, 0.99)
    assert_eq(hits[0].payload["provenance"], "official")


def test_similar_case_quota_backfills_when_one_batch_is_short():
    """official 只撈到 1 筆時，剩下的席次由 public 補滿，不留空位。"""
    official = [_kb_result("歷史訴願決定書/113年/唯一-駁回.txt", 0.88, "o")]
    public = [_kb_public_result(f"新北訴願決定書_全量/p{i}_駁回.txt", 0.80 - i / 100, "p") for i in range(6)]
    with _no_retrieve_interval():
        hits = _quota_retriever([official, public]).search("q", top_k=5)
    provs = [h.payload["provenance"] for h in hits]
    assert_eq(len(hits), 5, "某批不足要由另一批補滿")
    assert_eq(provs.count("official"), 1)
    assert_eq(provs.count("public_crawl"), 4, "public 補到 4 席（超過平時的 3 席上限）")


def test_similar_case_quota_never_admits_hits_below_the_score_threshold():
    """配額不得讓低於 KB_MIN_SCORE 的東西進來——寧可少一筆。"""
    official = [_kb_result("歷史訴願決定書/113年/低分-駁回.txt", 0.10, "o")]
    public = [_kb_public_result("新北訴願決定書_全量/p1_駁回.txt", 0.80, "p"),
              _kb_public_result("新北訴願決定書_全量/低分_駁回.txt", 0.05, "p")]
    with _no_retrieve_interval():
        hits = _quota_retriever([official, public], min_score=0.25).search("q", top_k=5)
    assert_eq([h.source for h in hits], ["新北訴願決定書_全量/p1_駁回.txt"],
              "只有過門檻的那一筆能進，配額不是塞滿五席的理由")


def test_explicit_prefix_still_does_a_single_query():
    """N5 的 retrieve_refs 明確指定前綴 → 單次查詢，不得變成配額那條兩批的路。"""
    r = _quota_retriever([[_kb_result("行政函釋/法務部93.txt", 0.9, "函釋")]])
    hits = r.search("q", filters={"prefix": ["行政函釋/", "司法院釋字及行政判解/"]}, top_k=5)
    assert_eq(len(r._client.calls), 1, "指定前綴時不得變成兩次查詢")
    assert_eq(r._client.calls[0]["retrievalConfiguration"]["vectorSearchConfiguration"]["numberOfResults"],
              kb_module.REF_FETCH_DEPTH,
              "比對常數而不是寫死的數字——改深度時不該連帶要改這條測試")
    assert_eq([h.source for h in hits], ["行政函釋/法務部93.txt"])


def test_ref_channel_fetch_depth_is_not_shallower_than_the_similar_case_channel():
    """判解／函釋通道的抓取深度不得淺於相似案通道（2026-09-12 回歸）。

    判解 19 筆＋函釋 10 筆只佔 KB 2477 筆的 1.2%，比 official 決定書那批還稀疏。
    相似案通道早就因為「抓不夠深、席次永遠空著」改成 QUOTA_FETCH_DEPTH=50，
    這條通道當時被漏掉、仍停在 top_k*3＝15。釘住這個不等式，
    免得日後有人調相似案的深度卻又把這條留在原地。
    """
    assert_true(kb_module.REF_FETCH_DEPTH >= kb_module.QUOTA_FETCH_DEPTH,
                f"判解通道 {kb_module.REF_FETCH_DEPTH} 不得淺於相似案通道 "
                f"{kb_module.QUOTA_FETCH_DEPTH}")


def test_ref_channel_dedupes_chunks_of_the_same_document_before_taking_top_k():
    """同一份判決的多個 chunk 只能佔一個席次。

    KB 把一份判決切成數段、各自以不同分數回來。不去重的話 `top_k=5`
    可能全被同一份判決的 5 個 chunk 佔滿，畫面顯示「命中 5 筆」、
    實際上只有 1 份判解——那是對「找到多少依據」說謊。
    去重必須發生在取 top_k 之前，所以這條測試餵的重複 chunk 數量刻意多於 top_k。
    """
    same_doc = "司法院釋字及行政判解/最高行政法院108年度判字第531號行政判決-行政罰法7條1項.txt"
    other_doc = "司法院釋字及行政判解/最高行政法院109年度上字第780號行政判決-行政罰法7條1項.txt"
    r = _quota_retriever([[
        _kb_result(same_doc, 0.79, "chunk A"),
        _kb_result(same_doc, 0.77, "chunk B"),
        _kb_result(same_doc, 0.75, "chunk C"),
        _kb_result(other_doc, 0.70, "另一份判決"),
    ]])
    hits = r.search("q", filters={"prefix": ["司法院釋字及行政判解/"]}, top_k=3)
    assert_eq([h.source for h in hits], [same_doc, other_doc],
              "同一份文件只留一筆，且不得把另一份判決擠出去")
    assert_eq(hits[0].score, 0.79, "保留的要是該文件分數最高的那個 chunk")


def test_similar_case_channel_is_deliberately_left_undeduped():
    """相似案通道**刻意不去重**——這是已驗證行為，不是漏做。

    2026-09-12 決賽期間實跑驗證過 official 2 ＋ public_crawl 3 的配額分布，
    當天不為了一個一般性的改善去動已驗證的路徑。這條測試釘住「只有判解通道去重」，
    如果日後有人要讓相似案也去重，會在這裡看到這是一個需要重新驗證配額的決定，
    而不是順手改掉。
    """
    dup = "新北訴願決定書_全量/1121070551_不受理.txt"
    with _no_retrieve_interval():
        hits = _quota_retriever([
            [],  # official 批沒有命中
            [_kb_public_result(dup, 0.90, "chunk A"), _kb_public_result(dup, 0.80, "chunk B")],
        ]).search("q", top_k=5)
    assert_eq([h.source for h in hits], [dup, dup],
              "相似案通道目前不去重（若要改，請一併重驗配額分布）")


def test_n4_reports_hits_by_provenance():
    """payload 自己說得出「賽方資料集用在哪」，不用人去數 cases[]。"""
    class FakeKB:
        name = "bedrock_kb"

        def search(self, query, filters=None, top_k=5):
            return [
                Hit(id="kb-1", title="o", score=0.9, source="歷史訴願決定書/113年/o-駁回.txt",
                    payload={"outcome": "駁回", "provenance": "official", "text": "t"}),
                Hit(id="kb-2", title="p", score=0.8, source="新北訴願決定書_全量/p_駁回.txt",
                    payload={"outcome": "駁回", "provenance": "public_crawl", "text": "t"}),
            ]

        def meta(self):
            return {"backend": "bedrock_kb", "available": True}

    st = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
    st.intake = {"type": "違反空氣污染防制法事件", "note": "主張未收受"}
    st.facts_excerpt = [{"text": "訴願人於農地露天燃燒稻稈。", "page": 2}]
    st.classification = {"class": {"case_type": "違反空氣污染防制法事件", "law_hits": ["空氣污染防制法"]}}
    st.screen = {"art77": {"clause": "77-2"}, "deadline": {"steps": []}}
    n4_retrieval.run(st, NodeCtx(run_mode="bedrock", snapshot=load_snapshot(), retriever=FakeKB()))
    by = st.retrieval["retrieval_meta"]["similar_case_channel"]["hits_by_provenance"]
    assert_eq(by, {"official": 1, "public_crawl": 1})


def test_build_retriever_requires_env_for_kb():
    with env(BEDROCK_KB_ID=None, AWS_REGION=None):
        try:
            build_retriever("kb")
        except ValueError as e:
            assert_in("BEDROCK_KB_ID", str(e))
        else:
            raise AssertionError("缺變數必須 raise")
    assert_true(build_retriever("lawtable_only") is None)


def test_n4_uses_injected_retriever_for_similar_cases():
    class FakeKB:
        name = "bedrock_kb"

        def __init__(self):
            self.queries = []

        def search(self, query, filters=None, top_k=5):
            self.queries.append((query, filters, top_k))
            return [Hit(id="kb-1", title="113年-違反空氣污染防制法事件-駁回", score=0.88,
                        source="歷史訴願決定書/113年/x-駁回.txt",
                        payload={"outcome": "駁回", "provenance": "official", "text": "主文：訴願駁回。"})]

        def meta(self):
            return {"backend": "bedrock_kb", "available": True}

    st = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
    st.intake = {"type": "違反空氣污染防制法事件", "note": "主張未收受"}
    st.facts_excerpt = [{"text": "訴願人於農地露天燃燒稻稈。", "page": 2}]
    st.classification = {"class": {"case_type": "違反空氣污染防制法事件", "law_hits": ["空氣污染防制法"]}}
    st.screen = {"art77": {"clause": "77-2"}, "deadline": {"steps": []}}
    kb = FakeKB()
    r = n4_retrieval.run(st, NodeCtx(run_mode="bedrock", snapshot=load_snapshot(), retriever=kb))
    assert_eq(len(st.retrieval["cases"]), 1)
    c = st.retrieval["cases"][0]
    assert_eq(c["id"], "C1")
    assert_eq(c["outcome"], "駁回")
    assert_eq(c["src"], "歷史訴願決定書/113年/x-駁回.txt")
    assert_eq(c["origin"], "retrieval")
    assert_true(c["lamp"] is None, "燈號歸 N6")
    assert_in("露天燃燒稻稈", kb.queries[0][0], "查詢句必須含事實段原文")
    assert_true(
        not (kb.queries[0][1] or {}).get("prefix"),
        "N4 不得自己寫一份 prefix——收哪些前綴由 retrieval.kb.DEFAULT_PREFIXES 單點決定",
    )
    assert_eq(st.retrieval["retrieval_meta"]["backend"], "lawtable+bedrock_kb")
    assert_true(r.degraded is False, "兩條通道都有結果就不是降級")


def test_n4_without_retriever_keeps_phase0_behaviour():
    st = CaseState(case_id="x", run_mode="fixture")
    st.classification = {"class": {"case_type": "違反建築法事件", "law_hits": ["建築法"]}}
    st.screen = {"art77": {}, "deadline": {"steps": []}}
    r = n4_retrieval.run(st, NodeCtx(run_mode="fixture", snapshot=load_snapshot()))
    assert_eq(st.retrieval["cases"], [])
    assert_eq(st.retrieval["retrieval_meta"]["backend"], "lawtable_only")
    assert_true(r.degraded is True)


def test_n4_result_distribution_survives_unlabelled_outcome():
    """檔名讀不出主文時 `outcome` 是 None——結果分布那行不得因為 None 混字串而炸掉。

    這條釘的是 `_count(c['outcome'] or '未標示' ...)` 那個 `or`：拿掉它，
    `sorted()` 會在 None 與 '駁回' 之間比大小，`TypeError` 直接打死整個 N4。
    「未標示」是明講讀不出來，不是替它猜一個結果（CONSTITUTION §2）。
    """
    class MixedKB:
        name = "bedrock_kb"

        def search(self, query, filters=None, top_k=5):
            return [
                Hit(id="kb-1", title="113年-違反建築法事件-駁回", score=0.9,
                    source="歷史訴願決定書/113年/a-駁回.txt",
                    payload={"outcome": "駁回", "provenance": "official", "text": "主文：訴願駁回。"}),
                Hit(id="kb-2", title="113年-違反建築法事件", score=0.7,
                    source="歷史訴願決定書/113年/b.txt",
                    payload={"outcome": None, "provenance": "official", "text": "（檔名不含結果字樣）"}),
            ]

        def meta(self):
            return {"backend": "bedrock_kb", "available": True}

    st = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
    st.facts_excerpt = [{"text": "訴願人擅自變更建物使用。", "page": 1}]
    st.classification = {"class": {"case_type": "違反建築法事件", "law_hits": ["建築法"]}}
    st.screen = {"art77": {}, "deadline": {"steps": []}}
    r = n4_retrieval.run(st, NodeCtx(run_mode="bedrock", snapshot=load_snapshot(), retriever=MixedKB()))
    assert_eq(len(st.retrieval["cases"]), 2)
    assert_true(st.retrieval["cases"][1]["outcome"] is None, "讀不出主文就留 None，不猜一個結果")
    dist = [row[0] for row in r.narrative["case"]["logs"] if row[0].startswith("結果分布：")]
    assert_eq(len(dist), 1, f"結果分布那行不見了：{r.narrative['case']['logs']}")
    assert_in("駁回 1 件", dist[0])
    assert_in("未標示 1 件", dist[0])


def test_run_case_passes_fixture_exclude_case_into_build_retriever():
    """demo 案不得檢索到自己的來源決定書——這條釘住 fixture → graph → build_retriever 的傳遞。

    假的 build_retriever 回 None（等同 lawtable_only），所以不需要 boto3、
    N4 通道 B 的行為也不變；被釘住的只有「參數有沒有真的傳過去」。
    """
    calls: list[tuple[str, str | None]] = []

    def fake_build(kind, *, exclude_case=None):
        calls.append((kind, exclude_case))
        return None

    orig = graph_mod.build_retriever
    graph_mod.build_retriever = fake_build
    try:
        with env(RETRIEVER=None):
            graph_mod.run_case("synthetic-ordinary-01", mode="fixture")
    finally:
        graph_mod.build_retriever = orig

    assert_eq(len(calls), 1, "run_case 必須向編排層要一次 retriever")
    assert_eq(calls[0][0], "lawtable_only", "kind 來自 settings.retriever_kind()")
    assert_eq(calls[0][1], "synthetic-src-0000000001", "fixture 的 exclude_case 沒有傳到檢索器")


# ── Task 6：run 持久化與 from_node 續跑 ────────────────────────────
def test_runstore_roundtrip():
    d = pathlib.Path(tempfile.mkdtemp())
    st = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    p = runstore.save_run(st, runs_dir=d)
    assert_true(p.exists() and p.name == f"{st.run_id}.json")
    back = runstore.load_run(st.run_id, runs_dir=d)
    assert_eq(back.retrieval, st.retrieval)
    assert_eq(back.gate.get("doc"), st.gate.get("doc"))
    try:
        runstore.load_run("run-does-not-exist", runs_dir=d)
    except runstore.RunNotFound:
        pass
    else:
        raise AssertionError("找不到必須 raise RunNotFound")


def test_from_node_reuses_upstream_and_reruns_downstream():
    base = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    again = run_case(
        "synthetic-ordinary-01", mode="fixture", base_state=base, from_node="n5", persist=False
    )
    assert_true(again.run_id != base.run_id)
    assert_eq(again.run_meta["base_run_id"], base.run_id)
    assert_eq(again.run_meta["from_node"], "n5")
    assert_eq(again.retrieval, base.retrieval, "N4 結果必須原樣沿用")
    assert_eq(sorted(again.run_meta["node_timings"].keys()), ["n5", "n6"], "只重跑 N5、N6")
    assert_eq(again.state, "VERIFIED")


def test_from_node_requires_base_state_and_valid_node():
    for kw in (
        {"from_node": "n5"},
        {"from_node": "n9", "base_state": run_case("synthetic-ordinary-01", mode="fixture", persist=False)},
    ):
        try:
            run_case("synthetic-ordinary-01", mode="fixture", persist=False, **kw)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{kw} 必須 raise ValueError")


def test_confirm_then_continue_from_n2_does_not_rerun_n1():
    base = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    confirmed = {
        k: base.intake[k]
        for k in ("d2", "d3", "service_method", "transit_days", "interested_party", "note")
    }
    cont = run_case(
        "synthetic-ordinary-01",
        mode="fixture",
        base_state=base,
        from_node="n2",
        confirmed_intake=confirmed,
        persist=False,
    )
    assert_true("n1" not in cont.run_meta["node_timings"])
    assert_eq(cont.intake_origin["d2"], "human")
    assert_true(cont.screen.get("procedural_inputs_confirmed") is True)


def test_n4_query_override_is_recorded_and_whitelisted():
    base = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    r = run_case(
        "synthetic-ordinary-01",
        mode="fixture",
        base_state=base,
        from_node="n4",
        overrides={"n4_query": "建築法第25條"},
        persist=False,
    )
    assert_eq(r.run_meta["overrides"], {"n4_query": "建築法第25條"})
    assert_in("建築法第25條", r.retrieval["retrieval_meta"]["query_text"])
    try:
        run_case(
            "synthetic-ordinary-01",
            mode="fixture",
            base_state=base,
            from_node="n4",
            overrides={"n5_prompt": "x"},
            persist=False,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("白名單外的 override 必須 raise")


def test_on_event_emits_start_done_per_node_and_run_done():
    events = []
    run_case(
        "synthetic-ordinary-01",
        mode="fixture",
        persist=False,
        on_event=lambda k, d: events.append((k, d)),
    )
    kinds = [k for k, _ in events]
    assert_eq(kinds[:2], ["node_start", "node_done"])
    assert_eq(kinds.count("node_start"), 6)
    assert_eq(kinds.count("node_done"), 6)
    assert_eq(kinds[-1], "run_done")
    assert_eq([d["node"] for k, d in events if k == "node_done"], ["n1", "n2", "n3", "n4", "n5", "n6"])


def test_node_done_carries_agents_and_merged_narrative():
    """契約 C1：node_done 帶該節點的卡片清單與 merge 後的 out／logs，前端靠它逐張填卡。"""
    events: list[tuple[str, dict]] = []
    st = run_case(
        "synthetic-ordinary-01",
        mode="fixture",
        persist=False,
        on_event=lambda k, d: events.append((k, d)),
    )
    done = {d["node"]: d for k, d in events if k == "node_done"}
    for node, d in done.items():
        assert_eq(d["agents"], NODE_TO_AGENTS[node], f"{node} 的 agents 必須等於 NODE_TO_AGENTS")
        assert_eq(sorted(d["narrative"]), sorted(NODE_TO_AGENTS[node]), f"{node} 的 narrative 鍵不對")
        json.dumps(d, ensure_ascii=False)  # SSE 要序列化得出去

    n4 = done["n4"]
    assert_eq(n4["agents"], ["law", "case"])
    for k in ("law", "case"):
        card = n4["narrative"][k]
        assert_eq(sorted(card), ["logs", "out"], "narrative 只給 out／logs（契約 C1）")
        assert_true(all(len(l) == 2 for l in card["logs"]), f"{k} 的 logs 元素必須是 [text, cls]")
        assert_eq(card["out"], st.agents_narrative[k]["out"], "事件裡的 out 必須與終態 payload 一致")
        assert_eq(card["logs"], st.agents_narrative[k]["logs"], "事件裡的 logs 必須與終態 payload 一致")
    # fixture 的 N4 會降級（通道 B 不可用）：紅 log 是 merge 時才加的，
    # 事件裡看得到它＝事件取的是 merge 後的版本，不是節點原始 narrative。
    assert_true(n4["degraded"] is True, "前提不成立：fixture 模式的 N4 本來會降級")
    red = [l for k in ("law", "case") for l in n4["narrative"][k]["logs"] if l[1] == "r"]
    assert_true(any("已降級" in l[0] for l in red), f"降級紅 log 沒進事件，實得 {red}")


def test_fixture_payload_agents_carry_prompt_keys_without_prompt_text():
    """契約 C2：fixture 沒呼叫模型，任何卡都不給提示詞原文；n1／n5 與規則節點的說明要分得開。"""
    p = build_payload(run_case("synthetic-ordinary-01", mode="fixture", persist=False))
    assert_eq(len(p["agents"]), 7)
    for a in p["agents"]:
        assert_true("prompt" in a and a["prompt"] is None, f"{a['k']} 在 fixture 檔位不得有 prompt 原文")
        want = graph_mod.PROMPT_NOTE_FIXTURE if a["node"] in ("n1", "n5") else graph_mod.PROMPT_NOTE_RULE
        assert_eq(a["prompt_note"], want, f"{a['k']}（{a['node']}）的 prompt_note 不對")


def test_agent_prompt_gives_file_text_only_for_live_llm_nodes():
    """bedrock 檔位：n1／n5 給提示詞檔全文，其餘節點 None。直接測 helper，不真打模型。"""
    prompts = pathlib.Path(client.__file__).parent / "prompts"
    for node, name in (("n1", "n1_extract"), ("n5", "n5_draft")):
        text, note = graph_mod._agent_prompt(node, "bedrock")
        assert_eq(text, (prompts / f"{name}.md").read_text(encoding="utf-8"), f"{node} 的 prompt 不是檔案原文")
        assert_eq(note, graph_mod.PROMPT_NOTE_LIVE)
    for node in ("n2", "n3", "n4", "n6"):
        assert_eq(graph_mod._agent_prompt(node, "bedrock"), (None, graph_mod.PROMPT_NOTE_RULE))


def test_resume_from_n2_or_n3_without_confirmation_keeps_human_origin():
    """B3：上一次已確認的欄位，續跑沒再送 confirmed_intake 時仍是 human，不得被打回 llm。

    `_apply_confirmed_intake(state, None)` 在 n2 會被呼叫——這條測試釘住它是 no-op，
    而且 n3 起跑（n2 不跑）時 origin 也是從 base_state 搬過來的。
    """
    fx = load_case("synthetic-ordinary-01")
    unconfirmed = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    assert_eq(unconfirmed.intake_origin["d2"], "llm", "前提不成立：沒確認時 d2 應為 llm")
    base = run_case(
        "synthetic-ordinary-01", mode="fixture", persist=False, confirmed_intake=_confirmed_of(fx)
    )
    assert_eq(base.intake_origin["d2"], "human", "前提不成立：確認後 d2 應為 human")
    assert_true(base.intake_confirmed, "前提不成立：base 的 intake_confirmed 不應為空")

    for from_node in ("n2", "n3"):
        again = run_case(
            "synthetic-ordinary-01", mode="fixture", base_state=base, from_node=from_node, persist=False
        )
        assert_eq(again.intake_origin, base.intake_origin, f"從 {from_node} 續跑 origin 被改了")
        assert_eq(again.intake_confirmed, base.intake_confirmed, f"從 {from_node} 續跑確認清單被清掉了")
        assert_true(
            again.screen.get("procedural_inputs_confirmed") is True,
            f"從 {from_node} 續跑後 N3 看不到已確認的 origin",
        )


def test_save_run_failure_still_emits_run_failed():
    """存檔失敗也是失敗：一次執行一定以 run_done 或 run_failed 結束。

    用不合 `RUN_ID_RE` 的 run_id 觸發 `save_run` 的 ValueError——這是呼叫端真的做得到的事
    （run_id 之後會從 HTTP body 進來），不是硬造一個永遠不會發生的錯。
    """
    events: list[tuple[str, dict]] = []
    try:
        run_case(
            "synthetic-ordinary-01",
            mode="fixture",
            run_id="bad id with spaces",
            persist=True,
            on_event=lambda k, d: events.append((k, d)),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("run_id 不合格式仍存檔成功？save_run 的白名單失效了")
    assert_eq(events[-1][0], "run_failed", f"最後一個事件必須是 run_failed，實得 {[k for k, _ in events]}")
    assert_true(events[-1][1]["node"] is None, "存檔失敗不屬於任何節點，node 必須是 None")
    assert_eq([k for k, _ in events].count("run_done"), 0, "失敗的執行不得同時報 run_done")


def test_on_event_exception_does_not_break_the_pipeline():
    """訂閱者壞掉不該讓分析失敗——事件是旁路，不是流水線的一環。"""
    seen: list[str] = []

    def hostile(kind, data):
        seen.append(kind)
        if kind == "node_done":
            raise RuntimeError("SSE 端爆了")

    st = run_case("synthetic-ordinary-01", mode="fixture", persist=False, on_event=hostile)
    assert_eq(st.state, "VERIFIED", "callback 丟例外時流水線仍須跑完")
    assert_eq(seen[-1], "run_done", "run_done 仍要發出去")


def test_resume_keeps_upstream_agents_and_degraded():
    """續跑的 agents[]／degraded[] 要含沒重跑的節點——否則畫面像是 N1～N4 沒跑過。"""
    base = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    base_n4 = [d for d in base.run_meta["degraded"] if d["node"] == "n4"]
    assert_eq(len(base_n4), 1, "前提不成立：fixture 模式的 N4 本來會降級（通道 B 不可用）")

    again = run_case(
        "synthetic-ordinary-01", mode="fixture", base_state=base, from_node="n5", persist=False
    )
    for k in ("clerk", "clf", "proc", "law", "case"):
        assert_in(k, again.agents_narrative, f"沒重跑的節點 agent {k} 不見了")
    for k in ("draft", "qc"):
        assert_in(k, again.agents_narrative, f"這次重跑的節點 agent {k} 不見了")
    assert_eq(again.agents_narrative["law"], base.agents_narrative["law"], "上游敘事必須原樣沿用")
    assert_true(
        again.agents_narrative["law"] is not base.agents_narrative["law"],
        "沿用要深拷貝，不能與 base_state 共用同一份物件",
    )

    nodes = [d["node"] for d in again.run_meta["degraded"]]
    assert_eq(sorted(nodes), ["n4", "n5"], f"degraded 應含沿用的 n4 與本次重跑的 n5，實得 {nodes}")
    assert_eq(
        [d for d in again.run_meta["degraded"] if d["node"] == "n4"][0]["reason"],
        base_n4[0]["reason"],
        "沿用的降級理由不得被改寫",
    )


# ── I-1：紅線守門本身要被守（scan_llm_import_graph 的繞過面）────────────
# 覆核實測：絕對 import 抓得到，相對 import 與 importlib 兩條路都靜默通過。
# 這兩條測試把那兩個洞釘住——用臨時檔注入，不動 repo 內任何檔案。


def _probe(tmpdir: str, source: str) -> pathlib.Path:
    p = pathlib.Path(tmpdir) / "probe.py"
    p.write_text(source, encoding="utf-8")
    return p


def _mid(provider: str, rest: str, region: str = "") -> str:
    """組出一個 model id，而**不在原始碼裡留下完整字串**。

    `scan_model_id_literals` 掃的就是包含本檔的目錄樹，把完整 id 寫死在這裡會讓
    這個測試自己觸發那道掃描。把本檔加進掃描例外是更糟的解——那等於讓守門對
    「最容易犯這個錯的地方」失明（2026-09-07 真的就是在這個檔案裡犯的）。
    """
    return f"{region}{provider}.{rest}"


def test_model_id_scan_catches_bedrock_and_openai_literals():
    """model id 的實際值寫進 repo 必須被抓到（`llm/client.py` 的規矩，過去只有註解在守）。"""
    cases = [
        (_mid("anthropic", "claude-sonnet-5"), "無區域前綴的 Bedrock model id"),
        (_mid("anthropic", "claude-haiku-4-5-20251001-v1:0", region="jp."), "區域推論設定檔 id"),
        (_mid("amazon", "nova-micro-v1:0", region="apac."), "非 Anthropic 的 Bedrock id"),
        (_mid("amazon", "titan-embed-text-v2:0"), "嵌入模型 id"),
        ("gpt" + "-4.1-mini", "OpenAI model id"),
    ]
    for mid, why in cases:
        probe = f'MODEL = "{mid}"'
        assert_true(run_all._model_id_hits(probe), f"沒抓到{why}：{probe}")

    # 最貼近真實犯錯形狀的一個：拿 model id 當環境變數的預設值
    default_arg = f'os.environ.get("OPENAI_MODEL_ID", "{"gpt" + "-4.1-mini"}")'
    assert_true(run_all._model_id_hits(default_arg), f"沒抓到當預設值的 model id：{default_arg}")


def test_model_id_scan_does_not_flag_variable_names_or_fake_ids():
    """會誤中的掃描器等於沒有掃描器：變數名、讀取器、假 id、agent 別名都不是 model id。"""
    for probe in [
        "BEDROCK_MODEL_ID_EXTRACT=",
        "OPENAI_MODEL_ID=",
        '_FAKE_OPENAI_MODEL = "fake-openai-model-for-tests"',
        'settings.bedrock_model_id("extract")',
        '"model_id": "m"',
        "重要決策 → fable；開發 → opus；其他 → sonnet",
        "anthropic",  # 光是廠商名不是 id
        "claude-sonnet-5",  # 光是後半段也不是（沒有廠商前綴就對不回任何服務）
    ]:
        assert_eq(run_all._model_id_hits(probe), [], f"誤中：{probe}")


def test_llm_import_graph_catches_relative_import():
    """`from ..llm import client` 寫在 backend/nodes/ 底下＝直接 import backend.llm。

    舊版 `_imports_of` 只看 `node.module`（"llm"），不以 "backend" 開頭就被丟掉，
    於是一行手滑的相對 import 就能讓 CONSTITUTION §4 的唯一自動守門靜默失守。
    """
    with tempfile.TemporaryDirectory() as d:
        problems = run_all._check_forbidden_node(
            _probe(d, "from ..llm import client\n"), "backend.nodes.probe"
        )
        assert_true(problems, "相對 import `from ..llm import client` 必須被抓到")
        assert_in("backend.llm", " ".join(problems))


def test_llm_import_graph_catches_importlib_backdoor():
    """`importlib.import_module("backend.llm.client")` 在 ast 的 import 邊上完全看不見。

    禁單節點（及其遞迴到的 backend 檔）出現動態 import 一律視為違規——
    不是因為動態 import 本身有罪，而是它讓「這個檔案依賴什麼」無法靜態判定，
    守門就沒有東西可以守。
    """
    with tempfile.TemporaryDirectory() as d:
        problems = run_all._check_forbidden_node(
            _probe(d, 'import importlib\n\nc = importlib.import_module("backend.llm.client")\n'),
            "backend.nodes.probe",
        )
        assert_true(problems, "importlib 動態 import 必須被抓到")
        assert_in("importlib", " ".join(problems))


def test_llm_import_graph_catches_dunder_import_backdoor():
    """`__import__("backend.llm.client")` 是同一個洞的另一種寫法。"""
    with tempfile.TemporaryDirectory() as d:
        problems = run_all._check_forbidden_node(
            _probe(d, 'c = __import__("backend.llm.client")\n'), "backend.nodes.probe"
        )
        assert_true(problems, "__import__ 動態 import 必須被抓到")


def test_llm_import_graph_still_passes_a_clean_relative_import():
    """相對 import 本身不違規——解析後不是 backend.llm 就該放行，否則守門會變噪音。"""
    with tempfile.TemporaryDirectory() as d:
        problems = run_all._check_forbidden_node(
            _probe(d, "from .n2_classify import run\nfrom ..gate import lamps\n"),
            "backend.nodes.probe",
        )
        assert_eq(problems, [], "解析成 backend.nodes.n2_classify／backend.gate 的相對 import 不該被誤判")


# ── I-2：KB 呼叫失敗要降級，不得打死整次執行（spec §7 第 2 列）──────────


def test_n4_degrades_when_kb_call_raises():
    """KB throttle／權限／網路例外只該讓通道 B 降級，通道 A 照常。

    修之前 `similar.search(...)` 沒有 try/except，boto3 例外會一路冒到
    `run_case` → `run_failed` → 502：demo 時 KB 抖一下就全紅。
    降級理由必須寫出「是 KB 炸了」，不能吞成「查無相似案」或「無資料集」。
    """
    class ExplodingKB:
        name = "bedrock_kb"

        def search(self, query, filters=None, top_k=5):
            raise RuntimeError("ThrottlingException: rate exceeded")

        def meta(self):
            return {"backend": "bedrock_kb", "available": True, "label": "KB 命中"}

    st = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
    st.facts_excerpt = [{"text": "訴願人擅自變更建物使用。", "page": 1}]
    st.classification = {"class": {"case_type": "違反建築法事件", "law_hits": ["建築法"]}}
    st.screen = {"art77": {"clause": "77-2"}, "deadline": {"steps": []}}
    r = n4_retrieval.run(
        st, NodeCtx(run_mode="bedrock", snapshot=load_snapshot(), retriever=ExplodingKB())
    )

    assert_true(r.ok is True, "KB 失敗屬降級，不是節點失敗")
    assert_true(r.degraded is True, "KB 失敗必須外顯為降級")
    assert_in("KB 不可用", r.degrade_reason or "")
    assert_in("ThrottlingException", r.degrade_reason or "")
    assert_eq(st.retrieval["cases"], [], "查不了就回空，不編造相似案")
    assert_true(len(st.retrieval["laws"]) > 0, "通道 A（法條查表）必須照常有結果")

    meta = st.retrieval["retrieval_meta"]["similar_case_channel"]
    assert_eq(meta["available"], False)
    assert_in("ThrottlingException", str(meta.get("error")), "similar_case_channel 要帶出錯誤本文")
    assert_in("未驗證", meta.get("label", ""), "不可用的通道一律標「庫外／未驗證」")
    assert_true(bool(meta.get("reason")), "不可用卻沒說原因")

    case_logs = [l for l in r.narrative["case"]["logs"] if l[1] == "r"]
    assert_true(case_logs, "KB 失敗必須有一條紅色 log")
    red = " ".join(l[0] for l in case_logs)
    assert_in("檢索失敗", red)
    assert_true("查無相似案" not in r.narrative["case"]["out"], "不得說成查無")
    assert_true("庫外，未驗證）。" not in r.narrative["case"]["out"], "不得說成無資料集")


# ── I-5：overrides.n4_query 必須同時進兩條通道 ──────────────────────────


def test_n4_query_override_reaches_the_similar_case_channel():
    """「重新檢索」卡輸入的查詢詞，畫面上就在相似案旁邊——它必須真的打到 KB。

    修之前 override 只被當 `cited_laws` 併進通道 A 的 `query_text`，而通道 B 的
    `case_query` 由事實段＋note＋案型組成，只有在三者全空時才會退回 `query_text`。
    承辦人補撈相似案的動作因此完全沒有效果（舊測試只驗到「有記錄在 query_text」）。
    """
    class RecordingKB:
        name = "bedrock_kb"

        def __init__(self):
            self.queries: list[tuple] = []

        def search(self, query, filters=None, top_k=5):
            self.queries.append((query, filters, top_k))
            return []

        def meta(self):
            return {"backend": "bedrock_kb", "available": True, "label": "KB 命中"}

    kb = RecordingKB()
    orig = graph_mod.build_retriever
    graph_mod.build_retriever = lambda kind, *, exclude_case=None: kb
    try:
        base = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
        r = run_case(
            "synthetic-ordinary-01",
            mode="fixture",
            base_state=base,
            from_node="n4",
            overrides={"n4_query": "建築法第25條"},
            persist=False,
        )
    finally:
        graph_mod.build_retriever = orig

    assert_true(kb.queries, "通道 B 完全沒有被呼叫？")
    assert_in("建築法第25條", kb.queries[-1][0], "承辦人指定的查詢詞沒有進到相似案通道")
    meta = r.retrieval["retrieval_meta"]
    assert_in("建築法第25條", meta["case_query_text"], "case_query_text 要看得出含指定詞")
    assert_in("建築法第25條", meta["query_text"], "通道 A 原有行為不得退化")


# ── I-4：跨模式續跑要擋；model_ids 不得虛報沒跑過的節點 ────────────────


def test_resume_across_run_modes_is_refused():
    """fixture 跑過的 base 不能接到 bedrock 續跑（反之亦然）。

    伺服器先以 fixture 跑過、重啟成 bedrock 後 `from_node=n5` 續跑，會得到
    「N1–N4 是重播、N5 是真模型」而 `run_meta.run_mode="bedrock"` 的混血結果——
    那份 run_meta 對它自己的來源說謊（CONSTITUTION §1）。
    """
    base = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    try:
        run_case(
            "synthetic-ordinary-01",
            mode="bedrock",
            base_state=base,
            from_node="n5",
            persist=False,
        )
    except ValueError as e:
        assert_in("fixture", str(e))
        assert_in("bedrock", str(e))
    else:
        raise AssertionError("跨模式續跑必須 raise ValueError")


def test_model_ids_only_report_nodes_that_actually_ran():
    """`from_node=n5` 續跑時 N1 根本沒跑，`model_ids.extract` 不得填環境變數的值。

    spec §5.1：「run_meta.model_ids 在 bedrock 模式填實際值」——沒呼叫就不是實際值。
    測試環境沒有 strands，所以 monkeypatch `graph_mod.model_ids`（唯一接縫）。
    """
    orig = graph_mod.model_ids
    graph_mod.model_ids = lambda: {"provider": "bedrock", "extract": "m-extract", "draft": "m-draft"}
    try:
        full = graph_mod._model_ids_if_live("bedrock", set(graph_mod.NODE_ORDER))
        assert_eq(full["extract"], "m-extract", "全跑時抽取模型 id 要填")
        assert_eq(full["draft"], "m-draft")

        resumed = graph_mod._model_ids_if_live("bedrock", {"n5", "n6"})
        assert_true(resumed["extract"] is None, "沒重跑 N1 就不得報 extract 的 model id")
        assert_eq(resumed["draft"], "m-draft", "本次真的重跑的 N5 要報")

        note = graph_mod._model_ids_note("bedrock", {"n5", "n6"})
        assert_in("N1", note or "")
        assert_in("base_run", note or "")
        assert_true(graph_mod._model_ids_note("bedrock", set(graph_mod.NODE_ORDER)) is None,
                    "全跑沒有沿用，就沒有要說明的事")
        assert_true(graph_mod._model_ids_if_live("fixture", set(graph_mod.NODE_ORDER)) is None,
                    "fixture 沒呼叫任何模型")
    finally:
        graph_mod.model_ids = orig


# ── I-6：base_state.case_id 守衛（覆核的 M3 突變唯一存活者）─────────────


def test_resume_refuses_a_base_run_from_another_case():
    """拿 A 案已確認的 base 接到 B 案：兩案的 intake／screen 完全不同，
    沿用等於把另一案的人工確認結果搬過來用。訊息要指出兩個 case_id。"""
    base = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    try:
        run_case(
            "synthetic-blocked-01",
            mode="fixture",
            base_state=base,
            from_node="n5",
            persist=False,
        )
    except ValueError as e:
        assert_in("synthetic-ordinary-01", str(e))
        assert_in("synthetic-blocked-01", str(e))
    else:
        raise AssertionError("跨案件續跑必須 raise ValueError")


# ── I-3：模型引用檢索結果外的來源 → N6 判紅並擋送出 ──────────────────────
# spec §5.3 原本同時寫「標 unsupported 交 N6 判紅」與「N6 邏輯不變」（spec 自身矛盾）。
# 控制端裁定：N6 端要有真的消費者，否則「結構層第二道」是空的。


def _n6_ready_state(requires_human: bool = False) -> CaseState:
    st = _screened_state(requires_human=requires_human)
    # 給 laws 結構化鍵，否則 N6 會另外掛 retrieval_law_unkeyed，測試就分不清是哪一條在擋
    st.retrieval = {
        "laws": [
            {"id": "L1", "t": "訴願法第14條", "law": "訴願法", "article": "14", "verified": True},
            {"id": "L4", "t": "訴願法第77條", "law": "訴願法", "article": "77", "verified": True},
        ],
        "cases": [],
        "retrieval_meta": {},
    }
    return st


def _screen_with_unconfirmed(st, fields=("d2", "transit_days")):
    """把 state 的期間輸入標成「未經承辦人確認」（N1 抽的預設就是這樣）。"""
    st.screen["unconfirmed_procedural_fields"] = list(fields)
    st.screen["procedural_inputs_confirmed"] = False
    return st


def test_engine_sentences_say_their_inputs_are_unconfirmed():
    """HACK-S-17：期間計算那幾句是全畫面最像「已經驗證過」的東西——綠燈、標「可驗算」。

    2026-09-08 真模型實測把 `d2`（送達日）抽成提起日的值、conf 給 1.0，使逾期判定
    由「逾期」翻成「未逾期」。引擎沒有錯（同輸入必同輸出），錯的是輸入；但畫面上
    那六句看起來完全可信。算式可驗算 ≠ 輸入可信，這兩件事必須在**同一句話**裡講清楚。
    """
    st = _screen_with_unconfirmed(run_case("synthetic-ordinary-01", mode="fixture", persist=False))
    n6_gate.run(st, NodeCtx(run_mode="fixture", snapshot=load_snapshot()))
    engine = [s for b in st.gate["doc"] for s in b.get("ss", []) if s["origin"] == "engine"]
    assert_true(engine, "前提不成立：doc[] 沒有 origin=engine 的句子")
    for s in engine:
        assert_in("未經承辦人確認", s["why"] or "", f"{s['id']} 的 why 沒講出輸入未確認")
        assert_in("d2", s["why"] or "", f"{s['id']} 的 why 沒指名是哪些欄位")
        assert_eq(s["l"], "g", "燈號不變——算式本身仍然可逐步覆核，改燈會把兩件事混為一談")


def test_engine_sentences_drop_the_warning_once_inputs_are_confirmed():
    """承辦人確認過就不該再吵——警告要能消失，否則它會變成背景雜訊。"""
    st = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    st.screen["unconfirmed_procedural_fields"] = []
    st.screen["procedural_inputs_confirmed"] = True
    n6_gate.run(st, NodeCtx(run_mode="fixture", snapshot=load_snapshot()))
    engine = [s for b in st.gate["doc"] for s in b.get("ss", []) if s["origin"] == "engine"]
    assert_true(engine)
    for s in engine:
        assert_true("未經承辦人確認" not in (s["why"] or ""), f"{s['id']} 確認後仍掛著警告")


def test_unconfirmed_deadline_inputs_appear_in_the_human_tier():
    """除了逐句的 why，三層誠實的「請人工判斷」層也要有一條，讓人掃一眼就看到。"""
    st = _screen_with_unconfirmed(run_case("synthetic-ordinary-01", mode="fixture", persist=False))
    payload = build_payload(st)
    human = payload["tiers"]["請人工判斷"]
    hits = [x for x in human if x.get("slot") == "caveat" and "未經承辦人確認" in x["t"]]
    assert_eq(len(hits), 1, "請人工判斷層要有且只有一條輸入未確認的提醒")
    assert_eq(hits[0]["origin"], "rule", "這是規則層算出來的，不是模型講的")
    assert_in("d2", hits[0]["t"])


def test_missing_conclusion_blocks_submit_on_a_non_blocked_case():
    """HACK-S-21：模型把 `conclusion` 回空 list → 一份沒有主文的決定書被判可送出。

    2026-09-08 上傳案實測命中：模型把不受理的**理由**寫完了（reasoning 5 句，其中
    一句已寫出「訴願法第77條第2款，應為不受理之決定」），卻沒寫**主文**，
    而 N6 只有「整份草稿全空」（`empty_draft`）這道檢查，擋不住「有內容但沒有結論段」。

    fixture 檔位驗不到：它的草稿資料一定有主文（未封鎖時是「訴願不受理。」，
    封鎖時是佔位句），空 list 只有真模型給得出來。
    """
    def fake_draft(context, slots, retrieve_fn=None, **kw):
        out = {s: [{"t": f"{s}：本件依訴願法第14條所定期間審認。", "cite_ids": [],
                    "basis": "訴願法第14條", "source_kind": "law"}] for s in slots}
        out["conclusion"] = []          # ← 模型沒寫主文
        return {"slots": out, "tool_calls": [], "usage": None, "model_id": "m"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _n6_ready_state(requires_human=False)
        n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
        n6_gate.run(st, NodeCtx(run_mode="bedrock", snapshot=load_snapshot()))
    finally:
        client.draft_sentences = orig

    concl = [s for b in st.gate["doc"] for s in b.get("ss", []) if s.get("slot") == "conclusion"]
    assert_eq(concl, [], "前提不成立：這個 fake 應該產不出結論句")
    hits = [b for b in st.gate["blockers"] if b["reason"] == "conclusion_missing"]
    assert_eq(len(hits), 1, "沒有主文的決定書必須有一條 conclusion_missing blocker")
    assert_eq(hits[0].get("severity"), "P0")
    assert_true(st.gate["submit_allowed"] is False, "沒有主文不得標為可送出")


def test_present_conclusion_does_not_trigger_the_missing_blocker():
    """有主文就不該吵——誤報會讓這條 blocker 變成永遠亮著的雜訊。"""
    st = run_case(
        "synthetic-ordinary-01", mode="fixture", persist=False,
        confirmed_intake=_confirmed_of(load_case("synthetic-ordinary-01")),
    )
    concl = [s for b in st.gate["doc"] for s in b.get("ss", []) if s.get("slot") == "conclusion"]
    assert_true(concl, "前提不成立：確認後的 ordinary 應該有主文")
    assert_eq([b for b in st.gate["blockers"] if b["reason"] == "conclusion_missing"], [])
    assert_true(st.gate["submit_allowed"], "有主文且無其他 blocker 時應可送出")


def test_blocked_case_reports_only_the_c_type_blocker_not_missing_conclusion():
    """C 型案本來就沒有主文（那是設計），不得再多報一條 conclusion_missing——
    同一件事兩條 blocker 會讓清單失去訊號（第三輪覆核的教訓）。"""
    st = run_case("synthetic-blocked-01", mode="fixture", persist=False)
    reasons = [b["reason"] for b in st.gate["blockers"]]
    assert_in("conclusion_requires_human", reasons)
    assert_true("conclusion_missing" not in reasons, f"C 型案不該多報，實得 {reasons}")


def test_unsupported_citation_reaches_n6_and_blocks_submit():
    """client 端白名單清掉的引用必須一路帶到 doc[] 與 N6，不能只留在 draft.slots。"""
    def fake_draft(context, slots, retrieve_fn=None, **kw):
        return {
            "slots": {
                s: [{
                    "t": f"{s}：本件依相關規定辦理。",
                    "cite_ids": [],
                    "basis": None,
                    "source_kind": "law",
                    "unsupported": True,
                    "dropped_cite_ids": ["L9"],
                }]
                for s in slots
            },
            "tool_calls": [], "usage": None, "model_id": "m",
        }

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _n6_ready_state()
        n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
        n6_gate.run(st, NodeCtx(run_mode="bedrock", snapshot=load_snapshot()))
    finally:
        client.draft_sentences = orig

    flagged = [
        s
        for block in st.gate["doc"]
        for s in block.get("ss", [])
        if s.get("unsupported")
    ]
    assert_true(flagged, "unsupported 旗標沒有帶進 doc[]（N6 看不到就等於沒有這道防線）")
    for s in flagged:
        assert_eq(s["l"], "r", "引用被清掉的句子必須紅燈")
        assert_in("L9", s["why"] or "", "why 要指出被清掉的是哪個 id")
        assert_in("檢索結果之外", s["why"] or "")
        assert_eq(s["l_origin"], "rule", "燈號仍由規則產出，不是模型")

    hits = [b for b in st.gate["blockers"] if b["reason"] == "cite_id_unsupported"]
    assert_eq(len(hits), 1, "unsupported 要匯總成一條 blocker")
    assert_eq(sorted(hits[0]["sentence_ids"]), sorted(s["id"] for s in flagged))
    assert_in("L9", hits[0]["detail"])
    assert_true(st.gate["submit_allowed"] is False, "帶不可驗引用的草稿不得標為可送出")


def test_unsupported_why_survives_c_type_blocking():
    """C 型案下，unsupported 句的 why 不得被「已封鎖且無出處」文案蓋掉。

    兩件事同時成立時（結論段封鎖 ＋ 模型引了檢索結果外的來源），承辦人看到的紅燈
    必須說得出「模型引了 L9、已清除」，而不是只說「本案已封鎖」——後者把可查證性
    的破口藏起來了（CONSTITUTION §2）。
    """
    def fake_draft(context, slots, retrieve_fn=None, **kw):
        return {
            "slots": {
                s: [{
                    "t": f"{s}：本件卷內資料經核閱後彙整如下。",
                    "cite_ids": [],
                    "basis": None,
                    "source_kind": "law",
                    "unsupported": True,
                    "dropped_cite_ids": ["L9"],
                }]
                for s in slots
            },
            "tool_calls": [], "usage": None, "model_id": "m",
        }

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _n6_ready_state(requires_human=True)
        n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
        n6_gate.run(st, NodeCtx(run_mode="bedrock", snapshot=load_snapshot()))
    finally:
        client.draft_sentences = orig

    flagged = [
        s
        for block in st.gate["doc"]
        for s in block.get("ss", [])
        if s.get("unsupported")
    ]
    assert_true(flagged, "前提不成立：C 型案下沒有 unsupported 句")
    for s in flagged:
        assert_eq(s["l"], "r", "引用被清掉的句子必須紅燈")
        assert_in("L9", s["why"] or "", "why 要指名被清掉的 id，不得被封鎖文案蓋掉")
        assert_in("檢索結果之外", s["why"] or "")
        assert_eq(s["tier"], "請人工判斷", "仍須落在請人工判斷層")
        assert_eq(s["l_origin"], "rule", "燈號仍由規則產出")

    hits = [b for b in st.gate["blockers"] if b["reason"] == "cite_id_unsupported"]
    assert_eq(len(hits), 1, "C 型案下 unsupported 仍要匯總成一條 blocker")
    assert_true(st.gate["submit_allowed"] is False)


def test_fixture_doc_sentences_have_no_unsupported_key():
    """AC1：fixture 模式的 doc[] 句子形狀零變化（不得多出 unsupported／dropped_cite_ids）。"""
    st = run_case("synthetic-ordinary-01", mode="fixture", persist=False)
    n = 0
    for block in st.draft["doc_skeleton"]:
        for s in block.get("ss", []):
            n += 1
            assert_true("unsupported" not in s, f"{s['id']} 多了 unsupported 鍵")
            assert_true("dropped_cite_ids" not in s, f"{s['id']} 多了 dropped_cite_ids 鍵")
            assert_eq(s["cite_ids"], [], "fixture 的手寫 cite_ids 仍不得帶進 doc[]")
    assert_true(n > 0, "前提不成立：doc[] 沒有句子")


# ── provenance 的兩個維度：執行模式 × 資料性質（CONSTITUTION §1）──────────


def test_provenance_note_says_live_inference_in_bedrock_mode():
    """`RUN_MODE=bedrock` 下 note 不得自稱離線重播——那是對評審說謊。

    2026-09-12 實測踩到：note 被寫死成 fixture 那句，bedrock 檔位照樣輸出。
    """
    prov = settings.provenance(mode="bedrock")
    assert_true("離線重播" not in prov["note"], "bedrock 檔位的 note 不得出現「離線重播」")
    assert_in("Bedrock", prov["note"])
    assert_eq(prov["run_mode"], "bedrock")


def test_provenance_note_keeps_the_synthetic_declaration_in_every_mode():
    """執行模式換檔不得把「案例是合成測資」這半句一起換掉——兩者是獨立維度。"""
    for mode in ("fixture", "bedrock"):
        prov = settings.provenance(mode=mode)
        assert_in("合成測資", prov["note"], f"{mode} 檔位漏掉合成測資聲明")
        assert_in("合成測資", prov["data_note"], f"{mode} 檔位的 data_note 漏掉合成測資聲明")


def test_provenance_note_reflects_fixture_replay_in_fixture_mode():
    prov = settings.provenance(mode="fixture")
    assert_in("離線重播", prov["note"], "fixture 檔位仍要說自己是重播")
    assert_true("Bedrock" not in prov["note"], "fixture 檔位不得宣稱呼叫了 Bedrock")


def test_uploaded_case_is_not_described_as_synthetic():
    """上傳的真實卷證不是合成測資：資料性質那半句要跟著換，執行模式那半句不動。"""
    prov = settings.provenance(up.PROVENANCE_UPLOADED, mode="bedrock")
    assert_true(
        prov["data_note"] != settings.DATA_NOTES["synthetic"], "上傳案不得沿用合成測資那句描述"
    )
    assert_in("非合成測資", prov["data_note"], "上傳案要明說自己不是合成測資")
    assert_in("承辦人上傳", prov["data_note"])
    assert_in("Bedrock", prov["execution_note"], "上傳案的執行模式描述不得被案件層蓋掉")
    assert_in("人工確認", prov["note"], "上傳案特有的提醒要留在 note 裡")


def test_case_file_cannot_freeze_a_stale_note_into_provenance():
    """案件層寫死的 note／execution_note 一律被當下 RUN_MODE 算出來的版本取代。"""
    prov = settings.provenance(
        {"kind": "synthetic", "note": "本系統目前執行於 fixture（離線重播）模式。"}, mode="bedrock"
    )
    assert_true("離線重播" not in prov["note"], "案件層寫死的過時 note 不得覆蓋算出來的版本")


def test_retrieval_note_is_a_separate_dimension_from_run_mode_and_data_kind():
    """檢索來源是第三個維度：換 RETRIEVER 只動 retrieval_note，另外兩句不動。"""
    with env(RETRIEVER="kb"):
        kb_prov = settings.provenance(mode="bedrock")
    with env(RETRIEVER="lawtable_only"):
        lt_prov = settings.provenance(mode="bedrock")
    assert_eq(kb_prov["execution_note"], lt_prov["execution_note"], "換檢索器不該動到執行模式那句")
    assert_eq(kb_prov["data_note"], lt_prov["data_note"], "換檢索器不該動到資料性質那句")
    assert_true(kb_prov["retrieval_note"] != lt_prov["retrieval_note"], "檢索來源那句必須跟著 RETRIEVER 走")
    assert_eq(kb_prov["retriever"], "kb")
    assert_in("Knowledge Base", kb_prov["retrieval_note"])
    assert_in("未接上", lt_prov["retrieval_note"], "沒接 KB 就要說自己查不到，不是查無相似案")


def test_retrieval_note_counts_come_from_the_manifest_not_a_hardcoded_string():
    """筆數現算自 data/manifest.json，兩批分開講；manifest 讀不到就不報數字。"""
    counts = settings.kb_corpus_counts()
    assert_true(counts, "前提不成立：data/manifest.json 讀不到")
    note = settings.retrieval_note("kb")
    assert_in(str(sum(counts.values())), note, "總筆數要跟 manifest 對得起來")
    assert_in(str(counts["kb/official/歷史訴願決定書"]), note, "賽方資料集那批要單獨報筆數")
    assert_in(str(counts["kb/public/新北訴願決定書_全量"]), note, "公開爬蟲那批要單獨報筆數")
    assert_in("賽方資料集", note)
    assert_in("公開全量爬蟲", note)

    missing = settings.MANIFEST_PATH.parent / "manifest-does-not-exist.json"
    orig = settings.MANIFEST_PATH
    settings.MANIFEST_PATH = missing
    try:
        assert_true(settings.kb_corpus_counts() is None, "manifest 不存在不得回估計值")
        assert_in("不報各批筆數", settings.retrieval_note("kb"), "讀不到就明說讀不到，不猜")
    finally:
        settings.MANIFEST_PATH = orig


def test_dataset_scope_no_longer_claims_to_bound_similar_case_retrieval():
    """dataset_scope 只管引用驗證；相似案的庫另算，必須指向 retrieval_note。"""
    scope = settings.PROVENANCE["dataset_scope"]
    assert_in("引用驗證", scope)
    assert_in("retrieval_note", scope, "要指出相似案的檢索範圍在另一個欄位")


def test_unknown_data_kind_is_not_guessed():
    """分不出資料性質就中性描述，不得預設當成合成測資（CONSTITUTION §3 不編造）。"""
    prov = settings.provenance({"kind": "something-else"}, mode="fixture")
    assert_in("無法判定", prov["data_note"])


# --- Bedrock 1 RPS 主動節流（2026-09-12 賽方規範） ---------------------------


class _FakeClock:
    """假時鐘：讓節流可驗又不真的睡。記下每次被要求睡多久。"""

    def __init__(self, start: float = 1000.0):
        self.now = start
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@contextmanager
def _throttle_clock(interval, provider="bedrock"):
    """把 client 模組的 time 換成假時鐘，並固定節流旋鈕與 provider。"""
    orig = (client.time, client.MIN_INTERVAL_S, client._last_call_at,
            os.environ.get("MODEL_PROVIDER"))
    clock = _FakeClock()
    client.time = clock
    client.MIN_INTERVAL_S = interval
    client._last_call_at = 0.0
    os.environ["MODEL_PROVIDER"] = provider
    try:
        yield clock
    finally:
        client.time, client.MIN_INTERVAL_S, client._last_call_at = orig[0], orig[1], orig[2]
        if orig[3] is None:
            os.environ.pop("MODEL_PROVIDER", None)
        else:
            os.environ["MODEL_PROVIDER"] = orig[3]


def test_throttle_first_call_does_not_wait():
    """第一次呼叫不該被罰等——節流是間隔，不是固定延遲。"""
    with _throttle_clock(1.1) as clock:
        client._throttle()
    assert_eq(clock.slept, [], "第一次呼叫不應該 sleep")


def test_throttle_waits_only_the_remaining_time():
    """已經過了 0.4 秒就只補 0.7 秒，不是每次都睡滿 1.1。

    0.4／0.7／1.1 三個值互不相同：若實作改成「每次固定睡 interval」或「不扣已過時間」，
    這條都會紅。
    """
    with _throttle_clock(1.1) as clock:
        client._throttle()        # 記下起點，不等
        clock.now += 0.4          # 呼叫端自己花掉 0.4 秒
        client._throttle()
    assert_eq(len(clock.slept), 1, "第二次才需要等")
    assert_true(abs(clock.slept[0] - 0.7) < 1e-9,
                f"應補等 0.7 秒（1.1 扣掉已過的 0.4），實際 {clock.slept}")


def test_throttle_knob_zero_disables():
    """MIN_INTERVAL_S=0 要能完全關掉——測試套件靠這個旋鈕不被拖慢。"""
    with _throttle_clock(0) as clock:
        client._throttle()
        client._throttle()
    assert_eq(clock.slept, [], "旋鈕歸零時不應該 sleep")


def test_throttle_skips_non_bedrock_provider():
    """openai 只供開發期調 prompt，不受賽方 1 RPS 規範約束，不該被節流。"""
    with _throttle_clock(1.1, provider="openai") as clock:
        client._throttle()
        client._throttle()
    assert_eq(clock.slept, [], "provider 不是 bedrock 時不該節流")


def test_bedrock_min_interval_default_is_under_one_rps():
    """預設值必須讓速率低於 1 RPS，否則這個閘等於沒開。"""
    orig = os.environ.pop("BEDROCK_MIN_INTERVAL_S", None)
    try:
        assert_true(settings.bedrock_min_interval_s() > 1.0,
                    f"預設間隔要 >1 秒才壓得到 1 RPS 以下，實際 {settings.bedrock_min_interval_s()}")
    finally:
        if orig is not None:
            os.environ["BEDROCK_MIN_INTERVAL_S"] = orig
