"""卷宗層測試：manifest 持久化（原子寫）、母庫查、16 支端點。

全部 stdlib（與 `run_all.py` 其餘部分一致，見 `harness.py` 檔頭：Phase 0 紅線
要求測試路徑不新增外部依賴）。要打 AWS 的那幾條**不在這裡**——它們是
實跑驗收，不是單元測試；這裡一律注入假 client，斷言「我們送出去的請求長什麼樣」。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
from contextlib import contextmanager

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.retrieval.kb as kb_module  # noqa: E402
from backend.dossier import corpus, store  # noqa: E402
from backend.tests.harness import assert_eq, assert_in, assert_true  # noqa: E402


def _tmp() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix="dossier-test-"))


def _kb_module():
    """`backend.retrieval.kb`，順便把試錯出來的搜尋設定鍵快取清掉——
    快取跨測試殘留會讓「第幾次呼叫」的斷言隨執行順序變化。"""
    kb_module.reset_search_key_cache()
    return kb_module


@contextmanager
def _env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _seed(cases_dir: pathlib.Path, case_id: str = "upload-0123456789ab") -> dict:
    m = {
        "case_id": case_id,
        "name": "原始案名",
        "created_at": "2026-09-12T21:40:00+08:00",
        "latest_run_id": None,
        "files": [], "laws": [], "references": [], "artifacts": [],
    }
    store.save(m, cases_dir)
    return m


# ── 路徑白名單 ────────────────────────────────────────────────────


def test_case_id_whitelist_rejects_path_traversal():
    for bad in ("../../etc/passwd", "upload-../x", "real-case-001", "", "upload-XYZ"):
        try:
            store.case_dir(bad, _tmp())
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} 應該被 case_id 白名單擋下來，卻拼出了路徑")


def test_case_id_whitelist_accepts_the_two_real_prefixes():
    d = _tmp()
    assert_eq(store.case_dir("upload-0123456789ab", d).name, "upload-0123456789ab")
    assert_eq(store.case_dir("synthetic-ordinary-01", d).name, "synthetic-ordinary-01")


# ── 原子寫：這是本變更的核心保證 ───────────────────────────────────


def test_write_failure_midway_leaves_the_old_manifest_intact():
    """寫到一半爆掉，磁碟上的舊檔要完好無缺，而且不留半份 tmp。

    這一條分得出 `tmp + os.replace` 與 `p.write_text`：後者是「先整份序列化再一次寫」，
    寫入本身失敗時目標檔會被截成半份。所以這裡注入的假 dump **先寫出真的位元組**
    再拋例外——只斷言「序列化失敗」是測不出差別的（那種情況 write_text 也不會弄髒檔案）。
    """
    d = _tmp()
    seeded = _seed(d)
    p = store.manifest_path(seeded["case_id"], d)
    before = p.read_text(encoding="utf-8")

    def exploding_dump(obj, f):
        f.write('{"case_id": "upload-0123456789ab", "name": "半份')  # 真的寫進去了
        f.flush()
        raise OSError("模擬磁碟寫到一半失敗")

    bad = dict(seeded, name="不該被寫進去的新名字")
    try:
        store.save(bad, d, dump=exploding_dump)
    except OSError:
        pass
    else:
        raise AssertionError("注入的 dump 應該把例外往上丟，不該被吞掉")

    assert_eq(p.read_text(encoding="utf-8"), before, "舊 manifest 被寫壞了")
    assert_eq(json.loads(p.read_text(encoding="utf-8"))["name"], "原始案名")
    leftovers = [x.name for x in p.parent.iterdir() if x.name.endswith(".tmp")]
    assert_eq(leftovers, [], "失敗後留下 .tmp 殘骸")


def test_successful_write_leaves_no_tmp_behind():
    d = _tmp()
    m = _seed(d)
    store.rename(m["case_id"], "改過的名字", d)
    p = store.manifest_path(m["case_id"], d)
    assert_eq([x.name for x in p.parent.iterdir()], ["manifest.json"])


def test_manifest_survives_a_new_python_process():
    """證明資料在磁碟上，不是靠進程內狀態撐著（驗收條件：重啟 process 再讀還在）。"""
    d = _tmp()
    m = _seed(d)
    store.rename(m["case_id"], "跨 process 的名字", d)
    store.add_items(m["case_id"], "laws",
                    [{"id": "kb/public/相關法規_全量/廢棄物清理法.txt", "t": "廢棄物清理法"}], d)
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from backend.dossier import store\n"
        "import pathlib, json\n"
        "m = store.load(%r, pathlib.Path(%r))\n"
        "print(json.dumps({'name': m['name'], 'laws': [x['id'] for x in m['laws']]}, ensure_ascii=False))\n"
        % (str(ROOT), m["case_id"], str(d))
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    got = json.loads(out.stdout.strip())
    assert_eq(got["name"], "跨 process 的名字")
    assert_eq(got["laws"], ["kb/public/相關法規_全量/廢棄物清理法.txt"])


# ── 四組清單的 C/R/D 語意 ─────────────────────────────────────────


def test_adding_the_same_item_twice_does_not_duplicate_it():
    d = _tmp()
    m = _seed(d)
    item = {"id": "kb/public/相關法規_全量/廢棄物清理法.txt", "t": "廢棄物清理法", "note": ""}
    assert_eq(len(store.add_items(m["case_id"], "laws", [item], d)), 1)
    assert_eq(store.add_items(m["case_id"], "laws", [item], d), [], "第二次加入應該回空（沒有新增）")
    assert_eq(len(store.load(m["case_id"], d)["laws"]), 1)


def test_adding_duplicates_within_one_request_collapses_them():
    d = _tmp()
    m = _seed(d)
    item = {"id": "kb/public/x.txt", "t": "x"}
    assert_eq(len(store.add_items(m["case_id"], "laws", [item, dict(item)], d)), 1)


def test_adding_does_not_overwrite_a_note_the_user_already_wrote():
    d = _tmp()
    m = _seed(d)
    store.add_items(m["case_id"], "laws", [{"id": "kb/public/x.txt", "t": "x", "note": "人寫的"}], d)
    store.add_items(m["case_id"], "laws", [{"id": "kb/public/x.txt", "t": "x", "note": ""}], d)
    assert_eq(store.load(m["case_id"], d)["laws"][0]["note"], "人寫的")


def test_removing_an_unknown_id_reports_false_instead_of_pretending():
    d = _tmp()
    m = _seed(d)
    assert_eq(store.remove_item(m["case_id"], "laws", "沒有這一筆", d), False)


def test_remove_takes_the_item_out_and_keeps_the_rest():
    d = _tmp()
    m = _seed(d)
    store.add_items(m["case_id"], "references",
                    [{"id": "a"}, {"id": "b"}, {"id": "c"}], d)
    assert_eq(store.remove_item(m["case_id"], "references", "b", d), True)
    assert_eq([x["id"] for x in store.load(m["case_id"], d)["references"]], ["a", "c"])


def test_unknown_group_is_refused():
    d = _tmp()
    m = _seed(d)
    for fn, args in ((store.add_items, (m["case_id"], "folders", [{"id": "x"}], d)),
                     (store.remove_item, (m["case_id"], "folders", "x", d))):
        try:
            fn(*args)
        except ValueError as e:
            assert_in("folders", str(e))
        else:
            raise AssertionError("未知群組應該被拒絕")


def test_rename_refuses_a_blank_name():
    d = _tmp()
    m = _seed(d)
    try:
        store.rename(m["case_id"], "   ", d)
    except ValueError:
        return
    raise AssertionError("空白案名應該被拒絕")


def test_delete_case_removes_the_manifest_but_reports_false_when_absent():
    d = _tmp()
    m = _seed(d)
    assert_eq(store.delete_case(m["case_id"], d), True)
    assert_eq(store.exists(m["case_id"], d), False)
    assert_eq(store.delete_case(m["case_id"], d), False, "刪不存在的案子要照實回 False，不是靜默成功")


def test_the_same_run_is_only_recorded_as_one_artifact():
    d = _tmp()
    m = _seed(d)
    first = store.record_draft_artifact(m["case_id"], "run-abc", "訴願決定書草稿 v1", cases_dir=d)
    assert_true(first is not None)
    assert_true(store.record_draft_artifact(m["case_id"], "run-abc", "訴願決定書草稿 v1",
                                            cases_dir=d) is None)
    assert_eq(len(store.load(m["case_id"], d)["artifacts"]), 1)
    assert_eq(first["run_id"], "run-abc")
    assert_eq(first["kind"], "draft")


def test_latest_run_id_is_persisted():
    d = _tmp()
    m = _seed(d)
    store.set_latest_run(m["case_id"], "run-xyz", d)
    assert_eq(store.load(m["case_id"], d)["latest_run_id"], "run-xyz")


def test_ensure_builds_a_manifest_for_a_synthetic_case_from_its_own_file():
    """合成案的卷證是內嵌文字、沒有實體檔。**要從測資的 `files[]` 推出來，不是回空。**"""
    d = _tmp()
    m = store.ensure("synthetic-ordinary-01", d)
    assert_eq(m["case_id"], "synthetic-ordinary-01")
    assert_true(m["name"] and m["name"] != "synthetic-ordinary-01", "案名應該取自測資的 label")
    assert_eq(len(m["files"]), 2, "合成測資有兩份卷證，不得靜默吃掉")
    assert_true(all(f["readable"] for f in m["files"]))
    assert_true(all(f["id"].startswith("f-") for f in m["files"]))
    assert_true(store.exists("synthetic-ordinary-01", d), "ensure 要落地，不只是回記憶體物件")


def test_load_of_a_missing_case_raises_instead_of_returning_an_empty_shell():
    d = _tmp()
    try:
        store.load("upload-aaaaaaaaaaaa", d)
    except store.CaseManifestNotFound:
        return
    raise AssertionError("不存在的案子要 raise，不能回一份空殼冒充")


def test_a_manifest_missing_keys_is_repaired_not_rejected():
    d = _tmp()
    p = store.manifest_path("upload-0123456789ab", d)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"case_id": "upload-0123456789ab", "laws": [{"id": "x"}]}),
                 encoding="utf-8")
    m = store.load("upload-0123456789ab", d)
    for g in store.GROUPS:
        assert_true(isinstance(m[g], list))
    assert_eq([x["id"] for x in m["laws"]], ["x"], "既有內容不得在補欄位時被丟掉")


def test_list_manifests_skips_a_corrupt_file_without_killing_the_listing():
    d = _tmp()
    _seed(d, "upload-0123456789ab")
    bad = store.manifest_path("upload-ffffffffffff", d)
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ 這不是 JSON", encoding="utf-8")
    got = store.list_manifests(d)
    assert_eq(sorted(got), ["upload-0123456789ab"])


# ── 母庫查：假 client，斷言「我們送出去的請求長什麼樣」 ─────────────


class FakeKB:
    """記下 retrieve 的參數並回預先排好的結果。"""

    def __init__(self, results: list[dict], *, results_when_filtered: list[dict] | None = None):
        self.results = results
        self.results_when_filtered = results_when_filtered
        self.calls: list[dict] = []

    def retrieve(self, **kw):
        self.calls.append(kw)
        cfg = next(iter(kw["retrievalConfiguration"].values()))
        if "filter" in cfg and self.results_when_filtered is not None:
            return {"retrievalResults": self.results_when_filtered}
        return {"retrievalResults": self.results}


def _hit(rel: str, score: float, doc_kind: str, *, kind: str = "public", **md):
    return {
        "score": score,
        "content": {"text": "內文"},
        "metadata": {"_source_uri": f"s3://a-bucket/kb/{kind}/{rel}",
                     "doc_kind": doc_kind, **md},
    }


def test_corpus_search_sends_the_doc_kind_filter_server_side():
    kb_mod = _kb_module()
    fake = FakeKB([_hit("相關法規_全量/廢棄物清理法.txt", 0.77, "statute")])
    kb_mod.reset_search_key_cache()
    kb_mod.search_corpus(fake, "KB1", "廢棄物", ["statute"], limit=10)
    cfg = next(iter(fake.calls[0]["retrievalConfiguration"].values()))
    assert_eq(cfg["filter"], {"equals": {"key": "doc_kind", "value": "statute"}})


def test_corpus_search_dedupes_chunks_of_the_same_document():
    """實測：filter 後 10 筆只有 5 份不同法規。不去重會把讀的人騙成 10 部法律。"""
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    chunks = [_hit("相關法規_全量/廢棄物清理法.txt", s, "statute") for s in (0.77, 0.76, 0.73)]
    chunks.append(_hit("相關法規_全量/有害事業廢棄物認定標準.txt", 0.72, "statute"))
    got = kb_mod.search_corpus(FakeKB(chunks), "KB1", "廢棄物", ["statute"], limit=10)
    assert_eq(len(got), 2, "同一部法規的三個 chunk 應該收斂成一筆")
    assert_eq(got[0]["score"], 0.77, "留的要是最高分那個 chunk")
    assert_eq([x["id"] for x in got],
              ["kb/public/相關法規_全量/廢棄物清理法.txt",
               "kb/public/相關法規_全量/有害事業廢棄物認定標準.txt"])


def test_corpus_search_never_falls_back_to_an_unfiltered_query():
    """`_retrieve` 有「篩了全空就退回不篩」的防呆；母庫查**刻意沒有**。

    退了就會把法院裁判書當法規端給承辦人——2026-09-12 實測「廢棄物」不篩抓 20 筆，
    法規佔 0 筆（15 筆裁判書 + 5 筆決定書）。查無就要回空。
    """
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    fake = FakeKB([_hit("新北裁判書_環保局全量/X.txt", 0.88, "court_ruling")],
                  results_when_filtered=[])
    got = kb_mod.search_corpus(fake, "KB1", "廢棄物", ["statute"], limit=10)
    assert_eq(got, [], "查無就回空")
    assert_eq(len(fake.calls), 1, "不得在篩不到時再打一次不帶 filter 的查詢")


def test_corpus_search_id_is_the_full_s3_key_not_the_relative_path():
    """`id` 要能直接餵 `s3.get_object`，而且要分得出 official 與 public 兩批。"""
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    got = kb_mod.search_corpus(
        FakeKB([_hit("歷史訴願決定書/113年/01.txt", 0.8, "decision", kind="official")]),
        "KB1", "廢棄物", ["decision"], limit=10)
    assert_eq(got[0]["id"], "kb/official/歷史訴願決定書/113年/01.txt")
    assert_eq(got[0]["src"], "歷史訴願決定書/113年/01.txt")
    assert_eq(got[0]["provenance"], "official")


def test_corpus_search_honours_the_limit_after_deduping_not_before():
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    chunks = [_hit("相關法規_全量/A.txt", 0.9, "statute"),
              _hit("相關法規_全量/A.txt", 0.8, "statute"),
              _hit("相關法規_全量/B.txt", 0.7, "statute")]
    got = kb_mod.search_corpus(FakeKB(chunks), "KB1", "q", ["statute"], limit=2)
    assert_eq([x["t"] for x in got], ["A", "B"], "去重要在截斷之前，否則 B 會被 A 的重複 chunk 擠掉")


def test_decision_search_drops_court_rulings_even_if_the_filter_let_one_through():
    """雙保險：filter 是第一道，這是第二道。filter 失效時不得靜默開始端裁判書。"""
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    fake = FakeKB([_hit("新北訴願決定書_環保局全量/1151090848_駁回.txt", 0.87, "decision",
                        outcome="駁回", category="廢棄物清理法"),
                   _hit("新北裁判書_環保局全量/KLDM_107.txt", 0.86, "court_ruling")])
    with _env(BEDROCK_KB_ID="KB1"):
        got = corpus.search_decisions("廢棄物", kb=fake)
    assert_eq([x["doc_kind"] for x in got], ["decision"])
    assert_eq(got[0]["verdict"], "駁回")
    assert_eq(got[0]["category"], "廢棄物清理法")


def test_a_decision_without_a_sidecar_reports_null_instead_of_guessing_the_case_type():
    kb_mod = _kb_module()
    kb_mod.reset_search_key_cache()
    fake = FakeKB([_hit("新北訴願決定書_環保局全量/1151090848_駁回.txt", 0.87, "decision")])
    with _env(BEDROCK_KB_ID="KB1"):
        got = corpus.search_decisions("廢棄物", kb=fake)
    assert_eq(got[0]["category"], None, "案型沒有退路：側檔缺席就留 null，不從檔名或內文猜")
    assert_eq(got[0]["verdict"], None)


# ── 母庫全文：S3 key 白名單與「全文不是 chunk」 ────────────────────


def test_corpus_key_whitelist_blocks_everything_outside_the_two_kb_prefixes():
    for bad in ("kb/public/../../secret.txt", "backend/output/runs/run-1.json",
                "kb/public/x.txt.metadata.json", "kb/other/x.txt",
                "kb/public/x.pdf", "", "kb/public/"):
        try:
            corpus.safe_key(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} 不該通過母庫 key 白名單")


def test_corpus_key_whitelist_accepts_both_batches():
    for good in ("kb/public/相關法規_全量/廢棄物清理法.txt",
                 "kb/official/歷史訴願決定書/113年/01.113年-違反廢棄物清理法事件-不受理.txt"):
        assert_eq(corpus.safe_key(good), good)


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.keys_read: list[str] = []

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 - boto3 的參數名就是大寫
        self.keys_read.append(Key)
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        return {"Body": _Body(self.objects[Key])}


class _NoSuchKey(Exception):
    pass


_NoSuchKey.__name__ = "NoSuchKey"


class _Body:
    def __init__(self, data: bytes):
        self.data = data

    def read(self) -> bytes:
        return self.data


def test_statute_full_text_comes_from_s3_in_one_piece():
    """全文不得從 KB chunk 拼（CONSTITUTION §1）：拼出來是一份殘缺卻看起來完整的法規。"""
    body = "第一條 …\n第二條 …\n" * 500
    s3 = FakeS3({"kb/public/相關法規_全量/廢棄物清理法.txt": body.encode("utf-8"),
                 "kb/public/相關法規_全量/廢棄物清理法.txt.metadata.json":
                     json.dumps({"metadataAttributes": {"provenance": "public_crawl",
                                                        "doc_kind": "statute",
                                                        "category": "廢棄物"}}).encode("utf-8")})
    with _env(S3_KB_BUCKET="a-bucket"):
        got = corpus.get_statute("kb/public/相關法規_全量/廢棄物清理法.txt", s3=s3)
    assert_eq(got["body"], body)
    assert_eq(got["verified"], False, "KB 法規全文恆 verified:false（查表通道才是可驗的那條）")
    assert_eq(got["relevance"], "unknown")
    assert_eq(got["t"], "廢棄物清理法")
    assert_eq(got["src"], "相關法規_全量/廢棄物清理法.txt")


def test_a_missing_object_is_a_404_not_a_500():
    with _env(S3_KB_BUCKET="a-bucket"):
        try:
            corpus.get_statute("kb/public/相關法規_全量/不存在.txt", s3=FakeS3({}))
        except corpus.DocumentNotFound:
            return
    raise AssertionError("找不到的物件要翻成 DocumentNotFound")


def test_a_court_ruling_requested_by_id_is_refused_with_a_reason():
    key = "kb/public/新北裁判書_環保局全量/KLDM_107.txt"
    s3 = FakeS3({key: b"judgment",
                 key + ".metadata.json":
                     json.dumps({"metadataAttributes": {"doc_kind": "court_ruling"}}).encode("utf-8")})
    with _env(S3_KB_BUCKET="a-bucket"):
        try:
            corpus.get_decision(key, s3=s3)
        except corpus.OutOfScope as e:
            assert_in("court_ruling", str(e))
            assert_true(key not in [k for k in s3.keys_read if not k.endswith(".metadata.json")],
                        "被擋下來就不該再去讀全文")
            return
    raise AssertionError("法院裁判書要被明確擋下來並說明理由，不是靜默回一份")


def test_missing_bucket_setting_says_so_instead_of_returning_empty():
    with _env(S3_KB_BUCKET=None):
        try:
            corpus.get_statute("kb/public/x/y.txt", s3=FakeS3({}))
        except corpus.CorpusUnavailable as e:
            assert_in("S3_KB_BUCKET", str(e))
            return
    raise AssertionError("缺設定要照實說缺什麼，不得回空清單冒充查無")
