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
from backend.config.settings import load_snapshot
from backend.engine import deadline as deadline_engine
from backend.intake.documents import CJK_RATIO_THRESHOLD, cjk_ratio, route_documents
from backend.intake.uploads import (
    MAX_BYTES,
    list_upload_cases,
    load_upload_case,
    save_upload,
)
from backend.llm import client
from backend.nodes import n1_extract, n4_retrieval, n5_draft, n6_gate
from backend.orchestrator import runstore
from backend.orchestrator.graph import (
    digest_from_state,
    list_synthetic_cases,
    load_case,
    run_case,
)
from backend.orchestrator.state import CaseState, NodeCtx
from backend.retrieval.base import Hit
from backend.retrieval.kb import KBRetriever, build_retriever
from backend.tests import run_all
from backend.tests.harness import assert_eq, assert_in, assert_true


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
    assert_eq(call["retrievalConfiguration"]["managedSearchConfiguration"]["numberOfResults"], 15, "多抓三倍再後過濾")


def test_kb_retriever_parses_public_prefix_and_outcome_from_filename():
    res = _kb_result("x", 0.9, "t")
    res["location"]["s3Location"]["uri"] = "s3://b/kb/public/新北訴願決定書_全量/1121051256_不受理.txt"
    res["metadata"]["_source_uri"] = res["location"]["s3Location"]["uri"]
    r = KBRetriever(kb_id="k", region="r", client=_FakeBedrockAgentRuntime([res]))
    hits = r.search("q", filters={"prefix": ["新北訴願決定書_全量/"]})
    assert_eq(hits[0].payload["provenance"], "public_crawl")
    assert_eq(hits[0].payload["outcome"], "不受理")


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
    assert_eq(kb.queries[0][1]["prefix"], ["歷史訴願決定書/"])
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
