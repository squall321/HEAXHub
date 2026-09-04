# materialtwin SQLite merge — 자연키 매칭 + id 재매핑으로 dev 재료를 cae00 운영 DB에 비파괴 병합.
# id 는 정수 autoincrement 라 dev/cae00 에서 같은 id 가 다른 행일 수 있다 → 자연키로 대응행을
# 찾고, 없으면 새로 INSERT(새 id 부여) + 자식의 FK 를 그 새 id 로 재매핑한다. cae00 기존 행은 유지.
# 사용: python3 _materialtwin_merge.py <src.db(dev)> <dst.db(cae00 운영)>
import sqlite3
import sys, json

SRC, DST = sys.argv[1], sys.argv[2]

# FK 순서(부모→자식)와 각 테이블의 자연키. 자연키 없는 test/constitutive_fit 은 부모FK+식별필드 조합.
# (table, natural_key_cols, parent_fk[(col, parent_table)])
PLAN = [
    # material_code 는 70종 중 50종 NULL(실측) → 신뢰 자연키는 name(NULL 0·중복 0).
    ("material",          ["name"],                              []),
    ("specimen",          ["material_id", "label"],              [("material_id", "material")]),
    ("test",              ["specimen_id", "test_type", "tested_at"], [("specimen_id", "specimen")]),
    ("raw_curve_ref",     ["test_id"],                           [("test_id", "test")]),
    ("processed_result",  ["test_id"],                           [("test_id", "test")]),
    ("constitutive_fit",  ["test_id", "model"],                  [("test_id", "test")]),
    # ── 물성 카탈로그 계보 ────────────────────────────────────────────────────
    # 이 셋이 PLAN 에 없어서 cae00 물성 카탈로그가 재료 548건인데 물성값·도메인·출처가
    # 전부 0 이었다(실측). 재료 카드만 가고 그 안의 내용은 안 간 셈이다.
    #   property_definition — key 가 UNIQUE·NULL 0(161/161) → 깨끗한 자연키.
    #   source — content_hash·local_path 가 전부 NULL 이라 선언된 UNIQUE 가 무용지물이고,
    #            doi 는 1401건 NULL, kind+title+year 도 2121/2134 로 유일하지 않다.
    #            의미컬럼 전체를 대조하면 2134/2134 완전 유일이라 그걸 자연키로 쓴다
    #            ('내용이 같으면 같은 출처'). 재실행해도 중복 삽입이 없다.
    #   property_value — 의미컬럼 전체로 21249/21279. 남는 30건은 값·조건·출처가 모두 같은
    #            진짜 중복 행이라 합쳐도 정보가 사라지지 않는다.
    #            property_key 는 id 가 아니라 key 를 참조하므로 재매핑이 필요 없고,
    #            material_id·source_id 만 새 id 로 재매핑한다.
    ("property_definition", ["key"], []),
    # **`authors`·`year` 를 자연키에서 뺐다**(2026-08-18).
    #   그 둘은 **나중에 채워지는 보강 필드**다. 실제로 코퍼스 메타에서 397건을 백필했더니
    #   이미 cae00 으로 간 출처 36건의 자연키가 바뀌어, 병합하면 같은 논문이 새 출처로 또
    #   들어가고 그 출처를 쓰는 물성값 197행까지 중복될 참이었다.
    #   실측(8/11 스냅샷 2,134건 대조) — 둘 포함 시 매칭 2,094(유실 40) · **둘 제외 시 2,130(유실 4)**.
    #   빼도 유일성은 온전하다(2,776/2,776). **가변 보강 필드는 자연키에 넣지 마라.**
    ("source",              ["kind", "doi", "isbn", "url", "title",
                             "publisher", "license"], []),
    ("property_value",      ["material_id", "property_key", "value_num", "value_text",
                             "unit", "conditions", "method", "source_id"],
                            [("material_id", "material"), ("source_id", "source")]),
    # ── 시험장비 카탈로그 ──────────────────────────────────────────────────────
    # **PLAN 에 없어서 750행이 통째로 안 가고 있었다**(2026-08-18에 넣었다).
    # 위 주석의 "재료 카드만 가고 내용은 안 갔다" 와 **같은 실패**다 — 표를 새로 만들면
    # 이 목록에 넣었는지 반드시 확인해라.
    #   instrument            — 선언된 UNIQUE(vendor, model)가 그대로 자연키다(218/218, NULL 0).
    #                           source_id 는 출처를 가리키므로 재매핑이 필요하다.
    #   instrument_capability — 선언된 UNIQUE(instrument_id, property_key, technique)(532/532, NULL 0).
    #                           property_key 는 id 가 아니라 key 참조라 재매핑이 필요 없다.
    ("instrument",            ["vendor", "model"],
                              [("source_id", "source")]),
    ("instrument_capability", ["instrument_id", "property_key", "technique"],
                              [("instrument_id", "instrument")]),
]

# ── 전역 유일 식별자 우선 매칭 ────────────────────────────────────────────────
# 자연키(의미컬럼 전체)만 쓰면 **제목이 교정된 출처가 새 행으로 들어가려다 UNIQUE 에 걸려 죽는다.**
# 실측(2026-08-18 예행) — dev/cae00 이 같은 `src 2279`·같은 URL 인데 제목만 다른 건이 둘 있었고
# `UNIQUE constraint failed: source.doi` 로 병합이 중단됐다. **수정 전 원본 스크립트도 똑같이 죽는다** —
# 새로 생긴 문제가 아니라 원래 있던 것이다.
#
# DOI·ISBN·content_hash 는 **그 자체가 전역 유일 식별자**다(그게 존재 이유다).
# 제목·URL·발행처는 나중에 교정되는 표시 정보라 자연키로만 보면 같은 문헌을 놓친다.
# → 값이 있으면 **이 축으로 먼저** 대응행을 찾고, 없을 때만 자연키로 간다.
UNIQUE_FIRST = {
    "source": [["doi"], ["isbn"], ["content_hash"]],
}

# ── 재료 큐레이션 키 ─────────────────────────────────────────────────────────
# 이 키들만 **이미 있는 재료에도** 반영한다. 값이 아니라 **판정**이다 —
# "이게 제품인가 실험실 시편인가"(role)·"어느 계통 부품인가"(subsystem)는
# 물성처럼 출처에서 나오는 게 아니라 dev 에서 사람이 정한다. 정본이 한쪽뿐이다.
# 나머지 속성(제조사·규격·조성·측정 메모…)은 건드리지 않는다 — 병합의 비파괴 약속을 지킨다.
CURATION_KEYS = {"role", "role_reason", "role_basis", "role_confidence",
                 "subsystem", "subsystem_basis",
                 # 57차 SA — 등급 미확정(V4) 판정이 운영까지 가지 않고 있었다.
                 # V1·V2·V6 은 `core_` 접두어라 전파되는데 V4 를 정하는 이 둘만 밖에 있어,
                 # 빈칸 갈래 여섯 중 하나만 dev 에 갇히는 어긋남이 났다.
                 "identification_status", "anonymised_code"}
# 판정은 role·subsystem 만이 아니다. 45·46차에 배치들이 같은 성격의 키를 더 만들었다 —
# `core_not_applicable`(그 물성이 이 재료에 의미 없다) · `core_fill_sheet`(무엇을 열어야 하나) ·
# `same_alloy_as`·`merge_verdict`·`merge_plan`(같은 재료인가). 전부 **dev 에서만 정하고
# 운영에 입력 경로가 없는 판정**이라 위와 똑같은 이유로 전파해야 하는데 목록에서 빠져 있었다
# (46차 HC 가 짚었다 — "이번에 남긴 결과는 dev 에만 있다").
#
# 파동마다 새 이름이 생기므로 **접두어로 받는다.** 배치가 판정 키를 만들 때는
# 이 접두어를 쓰라는 뜻이기도 하다. 접두어를 좁게 유지하는 이유는 명세(두께·조성 같은 것)가
# 섞이면 병합이 값을 덮기 때문이다 — 여기 있는 넷은 전부 '판정' 계열이다.
CURATION_PREFIXES = ("core_", "merge_", "same_alloy_", "verdict_")


def is_curation(k: str) -> bool:
    return k in CURATION_KEYS or k.startswith(CURATION_PREFIXES)


# ── 물성값 정정의 전파 ────────────────────────────────────────────────────────
# **464번의 두 번째 얼굴이다.** 재료 attributes 는 고쳤는데 `property_value` 는 그대로였다.
# 이 표의 자연키는 `value_num`·`conditions`·`method` 를 포함한다 — **정정이 바꾸는 바로 그 칸들**이다.
# 그래서 dev 에서 값을 고치면 운영에서 대응행을 못 찾고 **새 행으로 들어간다.**
# 실측(2026-08-28 예행, 48차 JA) — 재료 199 의 두 행을 고치고 병합했더니
# `property_value added 2` 였고, 운영에는 **틀린 옛 행과 고친 새 행이 나란히** 남았다.
# 등급만 고친 경우는 반대로 자연키가 그대로라 매칭되고 **정정이 조용히 버려진다**
# (tier·notes 는 자연키에 없다). 어느 쪽이든 정정은 운영에 못 간다.
#
# **적재기가 되돌리기용으로 남기는 표시가 그대로 열쇠다.** 정정된 행은 조건에
# `<칸>_before_correction` 을 달고 있으므로, 그것으로 **정정 전 값**을 복원해
# (재료·물성키·출처·옛 값)으로 운영행을 찾는다. 찾으면 그 행을 **갱신**한다.
#
# **조건 텍스트로는 대조하지 않는다** — JSON 직렬화가 양쪽에서 글자까지 같으리라 기대할 수 없다.
# 대신 옛 `value_num`/`value_text`(+ 정정이 실제로 바꾼 경우의 옛 unit·method)로 좁힌다.
# **유일하지 않으면 갱신하지 않는다** — 어느 행인지 병합기가 고르면 그건 병합기의 판정이 된다.
# 그때는 값을 버리지 않고 평소대로 삽입하되 `correction_misses` 로 **크게 보고한다.**
# id 동일성에는 기대지 않는다(운영 id 는 dev 와 다르다 — 이 파일 머리말 그대로다).
CORRECTION_SUFFIX = "_before_correction"
# 조건 안에 사는 **살림살이 키** — 정정이 스스로 남기는 것이라 옛 행에는 없다.
# 조건을 사전으로 대조할 때 양쪽에서 똑같이 벗겨야 한다.
CORRECTION_HOUSEKEEPING = ("correction_reason", "corrected_by", "correction_evidence")
# 적재기가 **컬럼의 옛 값**으로 남기는 표시 이름들(`ingest_agent_json.COLUMN_MARKER_NAMES`).
# 조건 칸 이름이 여기 겹치면 적재기가 `conditions.` 네임스페이스를 붙인다(§558 ③).
# **두 목록은 적재기와 같아야 한다** — 한쪽만 바뀌면 조건 복원이 조용히 어긋난다.
COLUMN_MARKER_NAMES = ("value", "value_text", "unit", "method", "tier", "notes")
CONDITION_MARKER_NS = "conditions."


def correction_prior(cond_text):
    """정정 표시가 있으면 {칸: 정정 전 값} 을, 없으면 None 을 돌려준다."""
    if not cond_text:
        return None
    try:
        d = json.loads(cond_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    # **`*_before_correction` 만 보면 안 된다.** 적재기의 중앙값 가드도 같은 이름으로
    # `method_before_correction` 을 남기는데(브리프 451·455) 그건 정정이 아니라 정상 삽입이다
    # — 실측 211행이 그렇게 들어 있다. 그 행들까지 이 분기로 끌면 멀쩡한 삽입이 망가진다.
    # **선언된 정정만 본다** — 정정 경로는 `correction_reason` 을 반드시 남긴다.
    if not d.get("correction_reason"):
        return None
    prior = {k[: -len(CORRECTION_SUFFIX)]: v
             for k, v in d.items() if k.endswith(CORRECTION_SUFFIX)}
    return prior or None


# ── 정정 전 **조건**의 복원 (54차 PA) ─────────────────────────────────────────
# 위 탐색축 `(재료·물성키·출처·옛 값)` 은 **유일하지 않다** — 라이브 41,383행에서
# 4,265행 1,832군이 겹친다(§548·564 실측). 그래서 조건 어휘 통일 같은 전수 정정은
# 691군 5,265행이 통째로 막혀 있었다(52차 NA 가 포기한 수 그대로다).
#
# **분기 ⑤ 가 같은 문제를 이미 풀었다**(§579) — 값만으로 안 갈릴 때 **조건을 파싱해
# 사전으로** 대조한다. 글자 대조가 아니다(직렬화가 양쪽에서 같으리라 기대할 수 없다, §522).
# 여기서는 한 걸음 더 필요하다 — 정정이 바꾼 칸이 **조건 그 자체**라, 지금 조건을 그대로
# 대조할 수 없고 **정정 전 조건을 표시로 되돌려** 대조해야 한다.
#
# 되돌리는 규칙은 적재기(`plan_correction`)가 남기는 모양의 역이다.
#   · `<칸>_before_correction` 의 이름이 컬럼 표시 이름이면 **조건 칸이 아니다**(건너뛴다).
#   · `conditions.<칸>_before_correction` 은 네임스페이스를 벗겨 그 조건 칸으로 읽는다(§558 ③).
#   · 표시값이 `null` 이면 그 칸은 **정정 전에 없었다** — 지운다.
#   · 살림살이 키(표시·사유·정정자)는 양쪽에서 벗긴다. 옛 행에는 없는 것들이다.
#
# **`null` 칸은 양쪽에서 지운다.** 적재기가 "칸이 없다" 와 "칸이 null 이다" 를 같은 표시로
# 남기기 때문에(§558 ②) 둘을 가를 수 없다 — 가를 수 없는 것은 **합쳐서 대조**한다.
# 그래야 틀린 행을 고르는 대신 **여러 건에 걸려 거부**된다. 거부는 안전한 방향이다.
#
# **한계 — 두 세대 표시는 복원하지 못한다.** 이미 정정된 행을 또 고치면 앞 세대의 표시가
# 그대로 남아 있어 한 세대가 아니라 두 세대를 되돌린다. 그 결과는 대응행 **0건**이라
# `correction_misses` 로 뜬다(틀린 행을 고르지 않는다). 54차 PA 의 표적 4,455행에는
# 앞 정정이 붙은 행이 0행이라 이 한계가 닿지 않았다.
def _condition_core(d):
    """조건 dict → 대조용 사전. 살림살이 키와 `null` 칸을 벗긴다."""
    if not isinstance(d, dict):
        return None
    return {k: v for k, v in d.items()
            if v is not None and not (k.endswith(CORRECTION_SUFFIX)
                                      or k in CORRECTION_HOUSEKEEPING)}


def prior_conditions(cond_text):
    """정정 **전** 조건을 표시로 복원한다. 파싱 못 하면 None."""
    try:
        d = json.loads(cond_text) if cond_text else {}
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    base = _condition_core(d)
    for k, v in d.items():
        if not k.endswith(CORRECTION_SUFFIX):
            continue
        nm = k[: -len(CORRECTION_SUFFIX)]
        if nm in COLUMN_MARKER_NAMES:
            continue                                  # 컬럼의 옛 값이다 — 조건 칸이 아니다
        if nm.startswith(CONDITION_MARKER_NS):
            nm = nm[len(CONDITION_MARKER_NS):]
        if v is None:
            base.pop(nm, None)
        else:
            base[nm] = v
    return base


def find_corrected_row(dc, vals, prior):
    """정정 전 값으로 운영행을 찾는다. (행 id, 사유, 후보 수) — 유일하지 않으면 (None, 사유, 수).

    `IS ?` 는 SQLite 의 NULL 안전 비교라 value_text·source_id 가 NULL 인 행도 걸린다.
    **후보 수를 같이 돌려주는 이유**는 부르는 쪽이 "축이 틀렸다(0건)" 와 "안 갈린다(2건 이상)" 를
    갈라야 하기 때문이다 — 앞은 다른 축으로 다시 물어볼 수 있고 뒤는 거부해야 한다.
    """
    where, args = ["material_id IS ?", "property_key IS ?", "source_id IS ?"], [
        vals.get("material_id"), vals.get("property_key"), vals.get("source_id")]
    # 옛 값으로 좁힌다. 정정이 그 칸을 안 건드렸으면 지금 값이 곧 옛 값이다.
    # unit·method 는 **정정이 실제로 바꾼 경우에만** 건다 — 안 바뀐 칸까지 걸면
    # 스키마 드리프트(운영이 옛 코드라 method 어휘가 좁은 경우)에 불필요하게 약해진다.
    for col, pk in (("value_num", "value"), ("value_text", "value_text")):
        where.append(f"{col} IS ?")
        args.append(prior.get(pk, vals.get(col)))
    for col in ("unit", "method"):
        if col in prior:
            where.append(f"{col} IS ?")
            args.append(prior[col])
    rows = dc.execute(f"SELECT id, conditions FROM property_value "
                      f"WHERE {' AND '.join(where)}", args).fetchall()
    if len(rows) == 1:
        return rows[0][0], None, 1
    if not rows:
        return None, "운영에 대응행이 없다", 0
    # 값만으로 안 갈렸다 — **조건을 파싱해 사전으로** 대조한다(분기 ⑤ 와 같은 수법, §579).
    base = prior_conditions(vals.get("conditions"))
    if base is not None:
        hits = []
        for rid, ctext in rows:
            try:
                d = json.loads(ctext) if ctext else {}
            except (TypeError, ValueError):
                continue
            if _condition_core(d) == base:
                hits.append(rid)
        if len(hits) == 1:
            return hits[0], None, len(rows)
        return None, (f"운영행 {len(rows)}건에 걸리고 정정 전 조건으로도 {len(hits)}건이다"
                      f"(id {[r[0] for r in rows][:6]})"), len(rows)
    return None, f"운영행 {len(rows)}건에 걸린다(id {[r[0] for r in rows][:6]})", len(rows)


# ── 물성값 '판정' 의 전파 — 조건 안에 사는 큐레이션 키 ─────────────────────────
# **464·522 의 네 번째 얼굴이다.** 522 가 값 정정에서 잡은 것을 이번엔 **판정**에서 만난다.
# 51차 MA 가 대표값 동률의 사유를 `conditions.verdict_tie_*` 에 적었는데, `conditions` 는
# 자연키 안에 있어(522) **판정 한 칸만 더해도 자연키가 깨지고 운영에 사본이 생긴다.**
#
# 우회로는 정정 경로(③)였다. 그런데 그 경로는 `(재료·키·출처·옛 값)` 이 **유일해야** 돌고,
# 중복 적재가 있는 군은 정의상 유일하지 않다 — MA 가 그 이유로 **128군을 포기했다**(§548).
# 실측(라이브 41,368행) — ③ 의 탐색키로는 **4,265행 1,832군이 유일하지 않다.**
#
# **판정 키를 자연키에서 빼면 그 문제가 통째로 사라진다.** 빼고 남은 것으로 대응행을 찾으면
# 41,368행이 **전부 유일하다**(실측 0건 충돌). 판정은 값이 아니라 dev 에서 사람이 정하는
# 것이고 운영에 입력 화면이 없으므로, 찾은 행에 **그 키만 얹는다** — §464 가 재료 attributes
# 에서 한 것과 같은 모양이고 같은 이유다(정본이 한쪽뿐이라 충돌이 없다).
#
# - **지우지 않는다.** dev 에 없는 판정 키는 운영 값을 그대로 둔다(464 의 비파괴 약속).
# - **판정 키 아닌 칸은 한 칸도 안 건드린다.** 값·조건·등급을 덮으면 그건 정정이지 판정이 아니다.
# - **유일하지 않으면 얹지 않고 `verdict_misses` 로 보고한다**(522·536 과 같은 규율).
# - 접두어를 쓰는 이유는 재료 쪽 `CURATION_PREFIXES` 와 같다 — 파동마다 새 판정 이름이 생긴다.
COND_VERDICT_PREFIX = "verdict_"


def split_verdicts(cond_text):
    """조건 JSON → (판정 키를 뺀 dict, 판정 키 dict). 파싱 불가·판정 없음이면 (None, {})."""
    if not cond_text:
        return None, {}
    try:
        d = json.loads(cond_text)
    except (TypeError, ValueError):
        return None, {}
    if not isinstance(d, dict):
        return None, {}
    verdicts = {k: v for k, v in d.items() if k.startswith(COND_VERDICT_PREFIX)}
    if not verdicts:
        return None, {}
    return {k: v for k, v in d.items() if k not in verdicts}, verdicts


def find_verdict_row(dc, vals, base):
    """판정 키를 뺀 조건으로 운영행을 찾는다. (행 id, 운영 조건 dict, 사유).

    좁히는 축은 **판정이 바꾸지 않는 것 전부**다 — 자연키에서 `conditions` 만 빼고 다 건다.
    조건은 SQL 로 글자 대조하지 않는다(직렬화가 양쪽에서 같으리라 기대할 수 없다, §522) —
    후보를 꺼내 **파싱한 뒤 판정 키를 빼고** 사전으로 비교한다.
    """
    cols = ("material_id", "property_key", "value_num", "value_text", "unit", "method", "source_id")
    where = " AND ".join(f"{c} IS ?" for c in cols)
    cand = dc.execute(f"SELECT id, conditions FROM property_value WHERE {where}",
                      [vals.get(c) for c in cols]).fetchall()
    hits = []
    for rid, ctext in cand:
        try:
            d = json.loads(ctext) if ctext else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        if {k: v for k, v in d.items() if not k.startswith(COND_VERDICT_PREFIX)} == base:
            hits.append((rid, d))
    if len(hits) == 1:
        return hits[0][0], hits[0][1], None
    return None, None, ("운영에 대응행이 없다" if not hits
                        else f"운영행 {len(hits)}건에 걸린다(id {[h[0] for h in hits][:6]})")


# ── 값 이동의 전파 ────────────────────────────────────────────────────────────
# **464·522 의 세 번째 얼굴이다.** 정정은 `value_num`·`conditions`·`method` 를 바꿔 자연키를
# 깨뜨렸는데, **이동은 `material_id` 를 바꾼다** — 그것도 자연키 안에 있다. 그래서 옮긴 행은
# 운영에서 대응행을 못 찾고 **새 행으로 들어가고, 운영에는 옛 재료 밑의 옛 행이 그대로 남는다.**
# 결과는 정정 때와 같다 — 같은 측정이 두 재료에 하나씩, 그리고 옛 재료는 비지 않아 묘비가 거짓이 된다.
#
# **마이그레이션이 운영에서도 돌지 않느냐** — 돈다. 하지만 배포 순서상 **병합이 앱 기동보다 먼저다**
# (위 `[skip]` 분기의 주석과 같은 사실이다). 병합 시점의 운영 DB 는 아직 옛 리비전이라
# 행이 옛 재료 밑에 있다. 여기서 안 잡으면 그 뒤 마이그레이션이 옛 행을 옮겨 **두 벌이 된다.**
#
# 적재기·마이그레이션이 되돌리기용으로 남기는 표시가 그대로 열쇠다 —
# 옮긴 행의 조건에는 `moved_from_material` 에 **옛 재료의 이름**이 들어 있다.
# **id 가 아니라 이름인 이유는 이 파일 머리말 그대로다** — id 는 dev/cae00 에서 다른 행이다.
MOVE_MARKER = "moved_from_material"


def move_prior(cond_text):
    """이동 표시가 있으면 옛 재료 이름을, 없으면 None 을 돌려준다."""
    if not cond_text:
        return None
    try:
        d = json.loads(cond_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    v = d.get(MOVE_MARKER)
    return v if isinstance(v, str) and v else None


def key_move_prior(cond_text):
    """키 이동 표시가 있으면 옛 물성키를, 없으면 None 을 돌려준다."""
    if not cond_text:
        return None
    try:
        d = json.loads(cond_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    v = d.get(KEY_MOVE_MARKER)
    return v if isinstance(v, str) and v else None


def find_key_moved_row(dc, vals, old_key):
    """옛 물성키로 운영행을 찾는다. (행 id, 사유) — 유일하지 않으면 (None, 사유).

    좁히는 축은 **키 이동이 바꾸지 않는 것들**이다 — 재료·출처·값. `find_moved_row` 와 대칭이고
    (거기는 재료가 바뀌므로 물성키로 좁힌다) 조건 텍스트로 대조하지 않는 이유도 같다(§536).
    **유일하지 않으면 옮기지 않고 보고한다** — 어느 행인지 병합기가 고르면 그건 병합기의 판정이다.
    """
    rows = dc.execute(
        "SELECT id FROM property_value WHERE material_id IS ? AND property_key IS ? "
        "AND source_id IS ? AND value_num IS ? AND value_text IS ?",
        (vals.get("material_id"), old_key, vals.get("source_id"),
         vals.get("value_num"), vals.get("value_text"))).fetchall()
    if len(rows) == 1:
        return rows[0][0], None
    return None, ("옛 키 밑에 대응행이 없다" if not rows
                  else f"옛 키 밑 {len(rows)}행에 걸린다(id {[r[0] for r in rows][:6]})")


def find_moved_row(dc, vals, old_name):
    """옛 재료 밑에서 운영행을 찾는다. (행 id, 사유) — 유일하지 않으면 (None, 사유).

    좁히는 축은 **이동이 바꾸지 않는 것들**이다 — 물성키·출처·값. 조건 텍스트로는 대조하지
    않는다(정정 전파와 같은 이유 — 직렬화가 양쪽에서 글자까지 같으리라 기대할 수 없다).
    **유일하지 않으면 옮기지 않고 보고한다** — 어느 행인지 병합기가 고르면 그건 병합기의 판정이다.
    """
    m = dc.execute("SELECT id FROM material WHERE name=?", (old_name,)).fetchone()
    if not m:
        return None, f"운영에 옛 재료 '{old_name}' 가 없다"
    rows = dc.execute(
        "SELECT id FROM property_value WHERE material_id=? AND property_key IS ? "
        "AND source_id IS ? AND value_num IS ? AND value_text IS ?",
        (m[0], vals.get("property_key"), vals.get("source_id"),
         vals.get("value_num"), vals.get("value_text"))).fetchall()
    if len(rows) == 1:
        return rows[0][0], None
    return None, ("옛 재료 밑에 대응행이 없다" if not rows
                  else f"옛 재료 밑 {len(rows)}행에 걸린다(id {[r[0] for r in rows][:6]})")


# ── 출처 병합의 전파 ──────────────────────────────────────────────────────────
# **464·522·536 의 다섯 번째 얼굴이다.** 536 은 이동이 바꾸는 칸이 `material_id` 라 자연키가
# 깨진다고 했다. 출처 병합이 바꾸는 칸은 **`source_id`** — 그것도 자연키 안이다.
# 고치기 전 예행(53차 OA, 옮긴 193행) — 193행이 전부 운영에 **새 행**으로 들어가고
# 옛 출처 밑의 옛 행이 그대로 남았다. 같은 측정이 **두 출처에 하나씩** 생긴다.
#
# **`source_id` 재매핑이 이것을 대신하지 않는다.** 재매핑은 dev 의 출처 id 를 운영 id 로
# 바꿔 줄 뿐이고, 운영 행은 여전히 **옛 출처**를 가리키고 있다.
#
# 열쇠는 마이그레이션이 남긴 `conditions.moved_from_source` 다. 값은 **옛 출처의 자연키
# 전체**다 — §535 가 재료에서 "id 가 아니라 이름" 을 가르쳤는데 출처에는 이름이 없어서
# 자연키가 그 자리다(라이브 2,943행에서 2,943/2,943 유일, 실측). 아래 `PLAN` 의
# `source` 자연키와 **같은 목록이어야 한다** — 한쪽만 바뀌면 이 분기가 조용히 죽는다.
SOURCE_MOVE_MARKER = "moved_from_source"
# **여섯 번째 얼굴이다.** 464·522·536 이 재료 이동을, 위가 출처 병합을 말한다.
# 키 이동이 바꾸는 칸은 `property_key` — 그것도 자연키 안이다. 그런데 ③·④·⑤ 세 분기가
# **전부 `property_key IS ?` 로 좁혀서** 키를 옮긴 행은 어느 분기도 못 받았다.
# 63차 YC 가 잡았다(RP-1121 전방사율 5행). 세어 보니 `migrated_from` 을 단 행이 456 이고
# 47차 IA 의 영률 336행이 같은 자리다 — **역대 키 이동 전부가 dev 에 갇혀 있었다.**
KEY_MOVE_MARKER = "migrated_from"
SOURCE_NATURAL_KEY = ("kind", "doi", "isbn", "url", "title", "publisher", "license")


def source_prior(cond_text):
    """출처 병합 표시가 있으면 옛 출처의 자연키 dict 를, 없으면 None 을 돌려준다."""
    if not cond_text:
        return None
    try:
        d = json.loads(cond_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    m = d.get(SOURCE_MOVE_MARKER)
    if not isinstance(m, dict):
        return None
    # 자연키 칸이 하나도 없으면 지문이 아니다(표시만 흉내 낸 것).
    return m if any(c in m for c in SOURCE_NATURAL_KEY) else None


def find_prior_source(dc, marker):
    """옛 출처를 **자연키 전체**로 운영에서 찾는다. (id, 사유).

    표시에 없는 칸은 NULL 로 건다 — "아무 값이나" 로 읽으면 다른 출처를 잡는다.
    """
    where = " AND ".join(f"{c} IS ?" for c in SOURCE_NATURAL_KEY)
    rows = dc.execute(f"SELECT id FROM source WHERE {where}",
                      [marker.get(c) for c in SOURCE_NATURAL_KEY]).fetchall()
    if len(rows) == 1:
        return rows[0][0], None
    return None, ("운영에 옛 출처가 없다" if not rows
                  else f"옛 출처가 운영행 {len(rows)}건에 걸린다(id {[r[0] for r in rows][:6]})")


def find_source_moved_row(dc, vals, old_sid):
    """옛 출처 밑에서 운영행을 찾는다. (행 id, 사유) — 유일하지 않으면 (None, 사유).

    좁히는 축은 먼저 **출처 병합이 바꾸지 않는 것들**이다 — 재료·물성키·값.
    그것으로 안 갈리면 **조건을 사전으로 대조한다**(표시 한 칸만 빼고).

    조건을 쓰는 것이 536 의 "조건 텍스트로 대조하지 마라" 와 어긋나지 않는다 —
    536 이 금지한 것은 **글자 대조**이고(직렬화가 양쪽에서 같으리라 기대할 수 없다),
    여기서는 `find_verdict_row` 와 똑같이 **파싱해서 사전으로** 비교한다.
    필요한 이유는 실측이 냈다 — Uddeholm Dievar 그래프 디지타이즈는 같은 재료·키에
    **같은 값이 두세 번 인쇄된다**(26.3 J 가 셋, 23.2 J 가 둘, 4.0 J 가 둘).
    값만으로 좁히면 그 7행이 운영에 새 행으로 들어간다(고치기 전 예행 `added 7`).
    """
    rows = dc.execute(
        "SELECT id, conditions FROM property_value WHERE material_id IS ? AND property_key IS ? "
        "AND source_id IS ? AND value_num IS ? AND value_text IS ?",
        (vals.get("material_id"), vals.get("property_key"), old_sid,
         vals.get("value_num"), vals.get("value_text"))).fetchall()
    if len(rows) == 1:
        return rows[0][0], None
    if not rows:
        return None, "옛 출처 밑에 대응행이 없다"
    try:
        mine = json.loads(vals.get("conditions")) if vals.get("conditions") else {}
    except (TypeError, ValueError):
        mine = None
    if isinstance(mine, dict):
        base = {k: v for k, v in mine.items() if k != SOURCE_MOVE_MARKER}
        hits = []
        for rid, ctext in rows:
            try:
                d = json.loads(ctext) if ctext else {}
            except (TypeError, ValueError):
                continue
            if isinstance(d, dict) and d == base:
                hits.append(rid)
        if len(hits) == 1:
            return hits[0], None
    return None, f"옛 출처 밑 {len(rows)}행에 걸린다(id {[r[0] for r in rows][:6]})"


def cols_of(cur, t):
    return [r[1] for r in cur.execute(f"PRAGMA table_info('{t}')")]

def main():
    s = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True); s.row_factory = sqlite3.Row
    d = sqlite3.connect(DST); d.execute("PRAGMA foreign_keys=OFF")  # 재매핑 중 임시 OFF, 끝나고 검증
    dc = d.cursor(); sc = s.cursor()
    remap = {}   # table -> { src_id -> dst_id }
    summary = {}
    # 병합이 조용히 버린 소유권 갱신을 모아 마지막에 보고한다(덮어쓰지는 않는다).
    ownership_diffs: list[dict] = []
    curation_updates: list[dict] = []
    # 물성값 정정의 전파 결과. 조용히 넘기면 안 된다 — 못 간 정정은 운영에 **틀린 값을 남긴다.**
    value_corrections: list[dict] = []
    correction_misses: list[dict] = []
    # 물성값 판정의 전파 결과. 못 간 판정은 운영에 **판정 없는 옛 행 + 판정 붙은 사본**을 남긴다.
    value_verdicts: list[dict] = []
    verdict_misses: list[dict] = []
    # 값 이동의 전파 결과. 못 간 이동은 운영에 **같은 측정을 두 재료에 하나씩** 남긴다.
    value_moves: list[dict] = []
    move_misses: list[dict] = []
    # 출처 병합의 전파 결과. 못 간 병합은 운영에 **같은 측정을 두 출처에 하나씩** 남긴다.
    source_moves: list[dict] = []
    source_move_misses: list[dict] = []
    # 키 이동의 전파 결과. 못 간 이동은 운영에 **같은 측정을 두 키에 하나씩** 남긴다.
    key_moves: list[dict] = []
    key_move_misses: list[dict] = []

    for table, natkey, fks in PLAN:
        # src/dst 에 테이블 없으면 skip.
        # ⚠ 조용히 넘기면 안 된다. 운영 DB 에 표가 없는 흔한 이유는 '앱이 아직 새 마이그레이션을
        # 안 돌렸다' 이고, 배포 순서상 merge 가 앱 기동보다 먼저다 — 즉 새 표는 첫 배포에서
        # 반드시 이 가지로 빠진다. 예전엔 아무 말이 없어서, 데이터를 Drive 에 올리고 PLAN 에
        # 표를 넣었는데도 "왜 업데이트가 안 되나"로 보였다(시험장비 218행, 2026-08-18).
        # 몇 행이 안 갔는지와 무엇을 하면 되는지를 말한다.
        if not sc.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue   # 원본에 없음 = 그 앱이 아직 그 기능을 안 쓴다(정상)
        if not dc.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            n = sc.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"[skip] {table}: 운영 DB 에 표가 없어 {n}행을 넘겼다 "
                  f"— 앱을 새 코드로 재배포·기동해 마이그레이션을 돌린 뒤 이 병합을 한 번 더 하라",
                  file=sys.stderr)
            continue
        scols = cols_of(sc, table); dcols = cols_of(dc, table)
        cols = [c for c in scols if c in dcols]          # 공통 컬럼만(스키마 drift 방어)
        data_cols = [c for c in cols if c != "id"]       # id 는 재부여
        remap[table] = {}
        added = matched = 0

        for row in sc.execute(f"SELECT * FROM {table}"):
            row = dict(row)
            src_id = row.get("id")
            # 부모 FK 를 dst id 로 재매핑(부모가 이미 처리됨)
            skip = False
            vals = {}
            for c in data_cols:
                v = row[c]
                for fc, pt in fks:
                    if c == fc and v is not None:
                        mapped = remap.get(pt, {}).get(v)
                        if mapped is None:
                            skip = True   # 부모가 매핑 안 됨(비정상) → 이 자식 건너뜀
                        v = mapped
                vals[c] = v
            if skip:
                continue

            # ① 전역 유일 식별자(DOI 등)가 있으면 그것으로 먼저 찾는다.
            hit = None
            for ucols in UNIQUE_FIRST.get(table, []):
                if not all(c in data_cols and vals.get(c) not in (None, "") for c in ucols):
                    continue
                hit = dc.execute(
                    f"SELECT id FROM {table} WHERE " + " AND ".join(f"{c}=?" for c in ucols),
                    [vals[c] for c in ucols]).fetchone()
                if hit:
                    break
            # ② 없으면 자연키로 탐색.
            if not hit:
                where = " AND ".join(f"{k} IS ?" if vals.get(k) is None else f"{k}=?" for k in natkey)
                wvals = [vals.get(k) for k in natkey]
                hit = dc.execute(f"SELECT id FROM {table} WHERE {where}", wvals).fetchone()
            # ②-c **출처 병합을 선언한 행은 조회축을 옛 출처로 바꾼다.**
            #    ③·④·⑤ 는 전부 `source_id` 로 좁히는데, 병합이 바꾼 칸이 바로 그것이다.
            #    바꾸지 않으면 정정·이동을 선언한 행이 전부 miss 로 뜬다(실측 — 옮긴 193행 중
            #    **78행이 정정을, 9행이 판정을 함께 달고 있다**). 경고가 매번 뜨면 무시하는 법을
            #    가르친다(§546). **찾는 데만 쓰고, 쓰는 값은 언제나 `vals`(새 출처)다.**
            lookup, src_marker, src_old_sid, src_why = vals, None, None, None
            if table == "property_value":
                src_marker = source_prior(vals.get("conditions"))
                if src_marker is not None:
                    src_old_sid, src_why = find_prior_source(dc, src_marker)
                    if src_old_sid is not None:
                        lookup = dict(vals)
                        lookup["source_id"] = src_old_sid
            # ②-b **판정 키만 더해진 행도 자연키로 못 찾는다** — `conditions` 가 자연키 안이라
            #    `verdict_*` 한 칸이 붙는 순간 깨진다. 그 키를 빼고 다시 찾아 **판정만 얹는다.**
            #    ②가 먼저 도는 순서라 재병합에 멱등하다(이미 얹은 행은 ②에서 걸린다).
            if not hit and table == "property_value":
                base, verdicts = split_verdicts(vals.get("conditions"))
                # **정정을 선언한 행은 ③ 이 맡는다.** 판정을 §521 의 `corrections` 경로로 넣으면
                # 그 경로가 `correction_reason`·`corrected_by` 를 반드시 남기는데, 그 두 칸은
                # 판정 키가 아니라 dev 쪽에만 있다 — 여기서 조건 사전을 대조하면 전건이 안 맞아
                # **결과는 옳은데(③ 이 다 받아낸다) 경고만 수천 건** 뜬다.
                # 실측(52차 NC, 판정 3,400행) — 이 가지를 안 막으면 `verdict_misses` 가 그 수만큼 뜬다.
                # **매번 뜨는 같은 경고는 무시하는 법을 가르친다**(§546). 그래서 아예 안 들어온다.
                # 살림살이 키를 여기서 같이 벗기는 길도 있지만 그러면 ②-b 가 정정 행을 가로채
                # `correction_reason` 이 운영에 안 가고 dev/운영이 조용히 어긋난다 — 그쪽이 더 나쁘다.
                # **출처 병합을 선언한 행도 여기 안 들어온다.** 이 분기는 판정 키만 얹고
                # `source_id` 는 그대로 두는데, 그러면 병합이 운영에 전파되지 않는다. ⑤ 가 맡는다.
                if verdicts and correction_prior(vals.get("conditions")) is None \
                        and src_marker is None:
                    tgt, dst_cond, why = find_verdict_row(dc, vals, base)
                    if tgt is not None:
                        chg = {k: v for k, v in verdicts.items() if dst_cond.get(k) != v}
                        if chg:
                            merged = dict(dst_cond)
                            merged.update(chg)      # dev 에 없는 키는 그대로 둔다(지우지 않는다)
                            dc.execute("UPDATE property_value SET conditions=? WHERE id=?",
                                       (json.dumps(merged, ensure_ascii=False), tgt))
                            value_verdicts.append(
                                {"id": tgt, "material_id": vals.get("material_id"),
                                 "key": vals.get("property_key"),
                                 "판정": {k: [dst_cond.get(k), v] for k, v in chg.items()}})
                        remap[table][src_id] = tgt
                        matched += 1
                        continue
                    # 못 찾았다. 정정·이동과 같은 판단으로 **삽입은 한다** — 운영에 아직 없는
                    # 값일 수 있고 값을 버리는 쪽이 더 위험하다. 다만 조용히 넘기지 않는다:
                    # 옛 행이 있는데 못 찾은 것이면 운영에 **판정 없는 옛 행과 사본이 나란히** 남는다.
                    verdict_misses.append(
                        {"material_id": vals.get("material_id"), "key": vals.get("property_key"),
                         "value": vals.get("value_num"), "판정": sorted(verdicts), "사유": why})
            # ③ **정정된 물성값은 자연키로 못 찾는다** — 자연키가 값·조건·method 를 포함하는데
            #    정정이 바로 그 칸들을 바꾸기 때문이다. 그래서 여기서 **정정 전 값**으로 한 번 더 찾는다.
            #    ②가 먼저 도는 순서라 **재병합에 멱등하다** — 이미 전파된 정정은 ②에서 걸린다.
            if not hit and table == "property_value":
                prior = correction_prior(vals.get("conditions"))
                if prior is not None and not (src_marker is not None and src_old_sid is None):
                    tgt, why, ncand = find_corrected_row(dc, lookup, prior)
                    # **출처 병합이 이미 운영에 전파돼 있으면 옛 출처 밑에는 아무것도 없다**
                    # (54차 PA 실측 69행 — 53차 OA 의 이동이 먼저 운영까지 간 뒤, 같은 행을
                    # 다음 파동이 또 고친 경우다). ②-c 가 조회축을 옛 출처로 바꿔 둔 채라
                    # 0건이 나고, 그대로 두면 값이 운영에 **새 행**으로 들어간다(§522 그 자체).
                    # 그래서 **0건일 때만** 새 출처로 한 번 더 묻는다 — 2건 이상이면 거부가 맞다.
                    if tgt is None and ncand == 0 and lookup is not vals:
                        tgt2, _why2, _n2 = find_corrected_row(dc, vals, prior)
                        if tgt2 is not None:
                            tgt, why = tgt2, None
                    if tgt is not None:
                        dc.execute("UPDATE property_value SET "
                                   + ",".join(f"{c}=?" for c in data_cols) + " WHERE id=?",
                                   [vals[c] for c in data_cols] + [tgt])
                        remap[table][src_id] = tgt
                        matched += 1
                        value_corrections.append(
                            {"id": tgt, "material_id": vals.get("material_id"),
                             "key": vals.get("property_key"), "정정전": prior,
                             "정정후": {c: vals.get(c) for c in
                                      ("value_num", "method", "quality_tier") if c in data_cols}})
                        continue
                    # 옛 행을 못 찾았다. 그래도 **삽입은 한다** — 운영에 아직 없는 값일 수 있고
                    # (새 재료의 행을 적재 직후에 고친 경우), 값을 버리는 쪽이 더 위험하다.
                    # 다만 조용히 넘기지 않는다: 옛 행이 있는데 못 찾은 것이면 운영에 **두 행이 생긴다.**
                    correction_misses.append(
                        {"material_id": vals.get("material_id"), "key": vals.get("property_key"),
                         "value": vals.get("value_num"), "사유": why})
            # ④ **이동한 물성값도 자연키로 못 찾는다** — 자연키가 `material_id` 를 포함하는데
            #    이동이 바꾸는 칸이 바로 그것이다. 옛 재료 이름으로 운영행을 찾아 **재료를 옮긴다.**
            #    ②가 먼저 도는 순서라 재병합에 멱등하다 — 이미 옮긴 행은 ②에서 걸린다.
            if not hit and table == "property_value":
                old_name = move_prior(vals.get("conditions"))
                if old_name and not (src_marker is not None and src_old_sid is None):
                    tgt, why = find_moved_row(dc, lookup, old_name)
                    if tgt is not None:
                        dc.execute("UPDATE property_value SET "
                                   + ",".join(f"{c}=?" for c in data_cols) + " WHERE id=?",
                                   [vals[c] for c in data_cols] + [tgt])
                        remap[table][src_id] = tgt
                        matched += 1
                        value_moves.append(
                            {"id": tgt, "옛재료": old_name, "새재료": vals.get("material_id"),
                             "key": vals.get("property_key"), "value": vals.get("value_num")})
                        continue
                    # 옛 행을 못 찾았다. 정정과 같은 판단으로 **삽입은 한다** — 운영에 아직 없는
                    # 값일 수 있고 값을 버리는 쪽이 더 위험하다. 다만 조용히 넘기지 않는다:
                    # 옛 행이 남아 있는데 못 찾은 것이면 **운영에 두 벌이 생기고 묘비가 거짓이 된다.**
                    move_misses.append(
                        {"옛재료": old_name, "key": vals.get("property_key"),
                         "value": vals.get("value_num"), "사유": why})
            # ⑤ **출처를 합친 물성값도 자연키로 못 찾는다** — 자연키가 `source_id` 를 포함하는데
            #    병합이 바꾸는 칸이 바로 그것이다. 옛 출처의 **자연키**로 운영 출처를 찾고,
            #    그 밑에서 (재료·물성키·값)으로 행을 찾아 **행 전체를 갱신**한다(새 출처 + 표시).
            #    ②가 먼저 도는 순서라 재병합에 멱등하다 — 이미 옮긴 행은 ②에서 걸린다.
            #    ③·④ 가 먼저 도는 이유는 그쪽이 정정·이동까지 함께 되돌려야 하기 때문이고,
            #    둘 다 ②-c 가 바꿔 둔 `lookup` 으로 옛 출처를 보므로 여기까지 안 내려온다.
            if not hit and table == "property_value" and src_marker is not None:
                if src_old_sid is None:
                    source_move_misses.append(
                        {"key": vals.get("property_key"), "value": vals.get("value_num"),
                         "옛출처": str(src_marker.get("title"))[:70], "사유": src_why})
                else:
                    tgt, why = find_source_moved_row(dc, vals, src_old_sid)
                    if tgt is not None:
                        dc.execute("UPDATE property_value SET "
                                   + ",".join(f"{c}=?" for c in data_cols) + " WHERE id=?",
                                   [vals[c] for c in data_cols] + [tgt])
                        remap[table][src_id] = tgt
                        matched += 1
                        source_moves.append(
                            {"id": tgt, "옛출처": src_old_sid, "새출처": vals.get("source_id"),
                             "key": vals.get("property_key"), "value": vals.get("value_num")})
                        continue
                    # 옛 행을 못 찾았다. 정정·이동과 같은 판단으로 **삽입은 한다** — 값을
                    # 버리는 쪽이 더 위험하다. 다만 조용히 넘기지 않는다.
                    source_move_misses.append(
                        {"key": vals.get("property_key"), "value": vals.get("value_num"),
                         "옛출처": str(src_marker.get("title"))[:70], "사유": why})
            # ⑥ **키를 옮긴 물성값도 자연키로 못 찾는다** — 자연키가 `property_key` 를 포함하는데
            #    이동이 바꾸는 칸이 바로 그것이다. ③·④·⑤ 가 전부 `property_key IS ?` 로 좁히므로
            #    어느 분기도 이 행을 못 받았다. 옛 키로 운영행을 찾아 **행 전체를 갱신한다.**
            #    ②가 먼저 도는 순서라 재병합에 멱등하다 — 이미 옮긴 행은 ②에서 걸린다.
            #    ⑤ 뒤에 두는 이유는 여기서 `source_id` 로 좁히기 때문이다 — 출처 병합까지 걸린
            #    행은 ⑤가 먼저 처리해 `continue` 하므로 여기까지 안 내려온다.
            if not hit and table == "property_value":
                old_key = key_move_prior(vals.get("conditions"))
                if old_key and old_key != vals.get("property_key"):
                    tgt, why = find_key_moved_row(dc, lookup, old_key)
                    if tgt is not None:
                        dc.execute("UPDATE property_value SET "
                                   + ",".join(f"{c}=?" for c in data_cols) + " WHERE id=?",
                                   [vals[c] for c in data_cols] + [tgt])
                        remap[table][src_id] = tgt
                        matched += 1
                        key_moves.append(
                            {"id": tgt, "옛키": old_key, "새키": vals.get("property_key"),
                             "material_id": vals.get("material_id"),
                             "value": vals.get("value_num")})
                        continue
                    # 못 찾았다. ③·④·⑤ 와 같은 판단으로 **삽입은 한다** — 운영에 아직 없는 값일
                    # 수 있고 값을 버리는 쪽이 더 위험하다. 다만 조용히 넘기지 않는다: 옛 키 행이
                    # 남아 있는데 못 찾은 것이면 **같은 측정이 두 키에 하나씩 생긴다.**
                    key_move_misses.append(
                        {"옛키": old_key, "새키": vals.get("property_key"),
                         "material_id": vals.get("material_id"),
                         "value": vals.get("value_num"), "사유": why})
            if hit:
                remap[table][src_id] = hit[0]           # cae00 기존행 유지(덮지 않음)
                matched += 1
                # ⚠ instrument 는 이제 쓰기 1회 표가 아니다 — owned·담당자·연락처라는
                # '사람이 갱신하는 상태' 가 붙었다(803417a). 기존행 유지는 그 갱신을
                # 조용히 버린다는 뜻이므로, 어느 쪽이 정본인지 자동으로 정하지 않고
                # 다르다는 사실만 드러낸다. 덮어쓰면 현장에서 등록한 보유가 dev 의
                # 기본값(False)으로 되돌아갈 수 있어 더 위험하다.
                # ⚠ 재료 큐레이션 메타(role·subsystem)는 **반대 방향**이다.
                # 병합기는 삽입만 하므로 **이미 있는 재료의 attributes 는 영원히 안 간다** —
                # 43차 EA 가 계통 태그 346종을 붙였는데 그 재료들은 cae00 에 이미 있어
                # 한 건도 전파되지 않는다(이 지점을 고치기 전까지 실측 0건).
                # 장비 소유권과 달리 이 값은 **dev 에서만 판정한다**(현장 입력이 없다).
                # 그래서 소유권처럼 '다르다고 알리고 유지' 가 아니라 dev 를 정본으로 반영한다.
                # 다만 **지우지는 않는다** — dev 에 없는 키는 운영 값을 그대로 둔다.
                if table == "material" and "attributes" in data_cols:
                    cur = dc.execute("SELECT attributes FROM material WHERE id=?",
                                     (hit[0],)).fetchone()
                    try:
                        old = json.loads(cur[0]) if cur and cur[0] else {}
                        new = json.loads(vals["attributes"]) if vals.get("attributes") else {}
                    except (TypeError, ValueError):
                        old, new = {}, {}
                    if isinstance(old, dict) and isinstance(new, dict):
                        chg = {k: v for k, v in new.items()
                               if is_curation(k) and old.get(k) != v}
                        if chg:
                            merged = dict(old); merged.update(chg)
                            dc.execute("UPDATE material SET attributes=? WHERE id=?",
                                       (json.dumps(merged, ensure_ascii=False), hit[0]))
                            curation_updates.append(
                                {"id": hit[0], "name": str(vals.get("name"))[:60],
                                 "변경": {k: [old.get(k), v] for k, v in chg.items()}})
                if table == "instrument":
                    for col in ("owned", "owner_name", "owner_contact"):
                        if col not in data_cols:
                            continue
                        cur = dc.execute(f"SELECT {col} FROM {table} WHERE id=?",
                                         (hit[0],)).fetchone()
                        if cur is not None and cur[0] != vals.get(col):
                            ownership_diffs.append(
                                {"id": hit[0], "field": col,
                                 "운영": cur[0], "들어온값": vals.get(col),
                                 "장비": " ".join(str(vals.get(k) or "") for k in natkey)})
            else:
                placeholders = ",".join("?" * len(data_cols))
                try:
                    dc.execute(f"INSERT INTO {table} ({','.join(data_cols)}) VALUES ({placeholders})",
                               [vals[c] for c in data_cols])
                except sqlite3.IntegrityError as exc:
                    # 원문 그대로 두면 "CHECK constraint failed: ck_propval_method" 한 줄에
                    # traceback 만 나온다 — 어느 표의 어떤 값이 걸렸는지, 왜 걸렸는지가 없다.
                    # 이 오류의 압도적 다수는 스키마 드리프트다: 운영 앱이 옛 코드라 CHECK 가
                    # 좁고, dev 데이터에 새 값이 들어 있다(실측 2026-08-18: method='digitized'
                    # 95행 — 허용값을 넓히는 마이그레이션이 운영 SIF 에 아직 없었다).
                    bad = {c: vals[c] for c in data_cols
                           if isinstance(vals[c], (str, int, float)) and c != "id"}
                    raise SystemExit(
                        f"[merge 중단] {table} 에 행을 넣다 실패: {exc}\n"
                        f"  넣으려던 값(요약): "
                        f"{ {k: v for k, v in list(bad.items())[:8]} }\n"
                        f"  거의 항상 스키마 드리프트다 — 운영 앱이 옛 코드라 제약이 좁고,\n"
                        f"  dev 데이터에 새 값이 들어 있다. 그 앱 SIF 를 새 소스로 올려\n"
                        f"  마이그레이션을 돌린 뒤 이 병합을 다시 하라."
                    ) from None
                remap[table][src_id] = dc.lastrowid
                added += 1
        summary[table] = {"added": added, "matched": matched}

    d.commit()
    # FK 무결성 검증
    d.execute("PRAGMA foreign_keys=ON")
    viol = d.execute("PRAGMA foreign_key_check").fetchall()
    s.close(); d.close()
    out = {"summary": summary, "fk_violations": len(viol)}
    if curation_updates:
        # 조용히 넘기지 않는다 — 이건 '값' 이 아니라 '판정' 을 덮는 일이라
        # 무엇이 무엇으로 바뀌었는지가 남아야 되돌릴 수 있다.
        out["curation_updates"] = curation_updates[:40]
        out["curation_updates_total"] = len(curation_updates)
    if value_corrections:
        out["value_corrections"] = value_corrections[:40]
        out["value_corrections_total"] = len(value_corrections)
    if correction_misses:
        out["correction_misses"] = correction_misses[:40]
        out["correction_misses_total"] = len(correction_misses)
    if value_verdicts:
        out["value_verdicts"] = value_verdicts[:40]
        out["value_verdicts_total"] = len(value_verdicts)
    if verdict_misses:
        out["verdict_misses"] = verdict_misses[:40]
        out["verdict_misses_total"] = len(verdict_misses)
    if value_moves:
        out["value_moves"] = value_moves[:40]
        out["value_moves_total"] = len(value_moves)
    if move_misses:
        out["move_misses"] = move_misses[:40]
        out["move_misses_total"] = len(move_misses)
    if source_moves:
        out["source_moves"] = source_moves[:40]
        out["source_moves_total"] = len(source_moves)
    if source_move_misses:
        out["source_move_misses"] = source_move_misses[:40]
        out["source_move_misses_total"] = len(source_move_misses)
    if key_moves:
        out["key_moves"] = key_moves[:40]
        out["key_moves_total"] = len(key_moves)
    if key_move_misses:
        out["key_move_misses"] = key_move_misses[:40]
        out["key_move_misses_total"] = len(key_move_misses)
    if ownership_diffs:
        # 조용히 버리지 않는다 — 현장에서 등록한 보유/담당자가 병합에 묻히면 시험 계획이
        # 있지도 않은 장비를 전제하거나, 반대로 있는 장비를 없다고 센다.
        out["ownership_diffs"] = ownership_diffs[:40]
        out["ownership_diffs_total"] = len(ownership_diffs)
    print(json.dumps(out, ensure_ascii=False))
    if curation_updates:
        print(f"· 재료 큐레이션(role·subsystem·core_*·merge_*)을 갱신한 행 "
              f"{len(curation_updates)}건 — 이 경로가 없으면 이미 있는 재료의 판정은 "
              f"영원히 전파되지 않는다.", file=sys.stderr)
    if value_corrections:
        print(f"· 물성값 정정을 운영행에 반영한 건수 {len(value_corrections)} — "
              f"이 경로가 없으면 정정은 새 행으로 들어가 운영에 틀린 옛 행이 남는다.", file=sys.stderr)
    if correction_misses:
        print(f"⚠ 정정 {len(correction_misses)}건이 운영에서 옛 행을 못 찾았다 — 값은 넣었지만 "
              f"운영에 옛 행이 남아 있으면 **같은 측정이 두 행**이 된다. correction_misses 를 확인해라.",
              file=sys.stderr)
    if value_verdicts:
        print(f"· 물성값 판정(verdict_*)을 운영행에 얹은 건수 {len(value_verdicts)} — 이 경로가 없으면 "
              f"판정 한 칸이 자연키를 깨서 **판정 없는 옛 행과 판정 붙은 사본**이 나란히 남는다.",
              file=sys.stderr)
    if verdict_misses:
        print(f"⚠ 판정 {len(verdict_misses)}건이 운영에서 대응행을 못 찾았다 — 값은 넣었지만 "
              f"옛 행이 남아 있으면 **같은 측정이 두 행**이 된다. verdict_misses 를 확인해라.",
              file=sys.stderr)
    if value_moves:
        print(f"· 값 이동을 운영행에 반영한 건수 {len(value_moves)} — 이 경로가 없으면 옮긴 값이 "
              f"새 행으로 들어가고 **옛 재료 밑의 옛 행이 그대로 남아** 묘비가 거짓이 된다.",
              file=sys.stderr)
    if move_misses:
        print(f"⚠ 이동 {len(move_misses)}건이 운영에서 옛 행을 못 찾았다 — 값은 넣었지만 "
              f"옛 행이 남아 있으면 **같은 측정이 두 재료에 하나씩** 생긴다. move_misses 를 확인해라.",
              file=sys.stderr)
    if source_moves:
        print(f"· 출처 병합을 운영행에 반영한 건수 {len(source_moves)} — 이 경로가 없으면 합친 값이 "
              f"새 행으로 들어가고 **옛 출처 밑의 옛 행이 그대로 남아** 같은 측정이 두 벌이 된다.",
              file=sys.stderr)
    if source_move_misses:
        print(f"⚠ 출처 병합 {len(source_move_misses)}건이 운영에서 옛 행을 못 찾았다 — 값은 넣었지만 "
              f"옛 행이 남아 있으면 **같은 측정이 두 출처에 하나씩** 생긴다. source_move_misses 를 확인해라.",
              file=sys.stderr)
    if ownership_diffs:
        print(f"⚠ 장비 소유권이 다른 행 {len(ownership_diffs)}건 — 운영 값을 유지했다. "
              f"어느 쪽이 맞는지 확인하라(set_instrument_ownership 로 정정).", file=sys.stderr)
    if viol:
        print(f"FK VIOLATION: {viol[:5]}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
