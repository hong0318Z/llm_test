import os
import re
import uuid
import threading
import csv
import io
import json as _json
from collections import Counter
from flask import Flask, jsonify, request, render_template, abort, Response, send_from_directory, session, redirect, url_for
from werkzeug.utils import secure_filename
from models import db, User, UserLlmSettings, World, WorldEntry, EntryRelationship, WorldAttributeSchema, EntryAttributeValue, WorldSkillRegistry, EntrySkillLink, WorldGuideline, WorldEntryTemplate, NovelPart, NovelChapter, NovelEntityMention, WorldWritingStyle, EntryRevealState, SimulationConfig, SimulationRun, SimulationLog, WorldSnapshot, AppSettings, LlmPromptConfig, Timeline, TimelineEvent, StoryBeat, CATEGORIES
from datetime import datetime

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///worldbuilding.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

db.init_app(app)

with app.app_context():
    db.create_all()
    # 기존 DB에 누락된 컬럼 자동 추가 (마이그레이션)
    _migrate_columns = [
        ("world_entries",       "is_summarized",           "BOOLEAN DEFAULT 0"),
        ("simulation_runs",     "total_tokens_in",         "INTEGER DEFAULT 0"),
        ("simulation_runs",     "total_tokens_out",        "INTEGER DEFAULT 0"),
        ("simulation_runs",     "total_tokens",            "INTEGER DEFAULT 0"),
        ("simulation_runs",     "selected_entry_ids_json",  "TEXT"),
        ("simulation_runs",     "exclude_llm_entries",      "BOOLEAN DEFAULT 0"),
        ("simulation_logs",     "tokens_in",               "INTEGER DEFAULT 0"),
        ("simulation_logs",     "tokens_out",              "INTEGER DEFAULT 0"),
        ("simulation_logs",     "tokens_total",            "INTEGER DEFAULT 0"),
        ("world_entries",       "keywords",                "TEXT DEFAULT ''"),
        ("world_entries",       "image_filename",          "TEXT"),
        ("app_settings",        "rag_token_budget",        "INTEGER DEFAULT 0"),
        ("simulation_runs",     "timeline_id",             "INTEGER"),
        ("timelines",           "narrative_goal",          "TEXT DEFAULT ''"),
        ("world_entries",       "parent_entry_id",         "INTEGER"),
        ("world_entries",       "version_note",            "TEXT DEFAULT ''"),
        ("world_entries",       "is_superseded",           "BOOLEAN DEFAULT 0"),
        ("world_entries",       "embedding_json",          "TEXT DEFAULT ''"),
        ("world_entries",       "embedding_model",         "TEXT DEFAULT ''"),
        ("world_entries",       "auto_tags_json",          "TEXT DEFAULT '[]'"),
        ("world_entries",       "primary_year",            "TEXT DEFAULT ''"),
        ("world_entries",       "year_notes_json",         "TEXT DEFAULT '[]'"),
        ("world_entries",       "aliases_json",            "TEXT DEFAULT '[]'"),
        ("world_entries",       "secret_content",          "TEXT DEFAULT ''"),
        ("timelines",           "main_entry_id",           "INTEGER"),
        # 세계관 컨테이너 마이그레이션
        ("world_entries",       "world_id",                "INTEGER"),
        ("timelines",           "world_id",                "INTEGER"),
        ("simulation_configs",  "world_id",                "INTEGER"),
        ("simulation_runs",     "world_id",                "INTEGER"),
        ("simulation_runs",     "user_id",                 "INTEGER"),
        ("user_llm_settings",   "nai_model",               "TEXT DEFAULT ''"),
        ("user_llm_settings",   "novelai_api_key",         "TEXT DEFAULT ''"),
        ("user_llm_settings",   "llm_provider",            "TEXT DEFAULT 'custom'"),
        ("world_snapshots",     "world_id",                "INTEGER"),
        # 모델 선택 컬럼
        ("app_settings",        "llm_model_simulation",    "TEXT DEFAULT 'claude-sonnet-4.5'"),
        ("app_settings",        "llm_model_nai",           "TEXT DEFAULT 'claude-sonnet-4.5'"),
        ("app_settings",        "embedding_enabled",       "BOOLEAN DEFAULT 0"),
        ("app_settings",        "embedding_model",         "TEXT DEFAULT 'nomic-embed-text'"),
        ("app_settings",        "rag_reference_limit",     "INTEGER DEFAULT 8"),
        ("app_settings",        "llm_base_url",            "TEXT DEFAULT ''"),
        ("app_settings",        "llm_api_key",             "TEXT DEFAULT ''"),
        ("app_settings",        "embedding_base_url",      "TEXT DEFAULT ''"),
        ("app_settings",        "embedding_api_key",       "TEXT DEFAULT ''"),
        ("app_settings",        "entries_per_tick",        "INTEGER DEFAULT 1"),
        ("app_settings",        "llm_max_output_tokens",   "INTEGER DEFAULT 262144"),
        ("world_attribute_schema", "description",          "TEXT DEFAULT ''"),
        ("entry_attribute_value",  "description",          "TEXT DEFAULT ''"),
        ("novel_chapter",          "part_id",              "INTEGER"),
        ("novel_chapter",          "is_public",            "BOOLEAN DEFAULT 0"),
        ("novel_chapter",          "public_token",         "TEXT"),
        ("novel_chapter",          "summary",              "TEXT DEFAULT ''"),
        ("novel_part",             "entry_ids_json",       "TEXT DEFAULT '[]'"),
        ("app_settings",           "novel_pov",            "TEXT DEFAULT '3인칭 관찰자'"),
        ("app_settings",           "novel_tone_guide",     "TEXT DEFAULT ''"),
        ("app_settings",           "novel_forbidden_expressions", "TEXT DEFAULT ''"),
        ("app_settings",           "novel_sample_text",    "TEXT DEFAULT ''"),
    ]
    with db.engine.connect() as conn:
        for table, col, col_def in _migrate_columns:
            try:
                conn.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"))
                conn.commit()
            except Exception:
                pass  # 이미 존재하면 무시

    # LLM 프롬프트 기본값 시드 (최초 1회)
    from llm_client import (
        SYSTEM_PROMPT_TEMPLATE, USER_PROMPT_TEMPLATE,
        SUMMARY_PROMPT_TEMPLATE, TIMELINE_GEN_PROMPT,
        NAI_PROMPT_GENERATOR_TEMPLATE,
    )
    _default_prompts = [
        {
            "key": "simulation_system",
            "label": "시뮬레이션 시스템 프롬프트",
            "description": "틱 시뮬레이션 시 LLM에게 전달되는 시스템 역할 지침. {prompt_level_1}, {prompt_level_2}, {prompt_level_3} 플레이스홀더 유지 필요.",
            "content": SYSTEM_PROMPT_TEMPLATE,
        },
        {
            "key": "simulation_user",
            "label": "시뮬레이션 유저 프롬프트",
            "description": "각 틱마다 LLM에게 전달되는 실제 요청 메시지. {tick_number}, {world_state}, {story_beat_section} 플레이스홀더 유지 필요.",
            "content": USER_PROMPT_TEMPLATE,
        },
        {
            "key": "summary",
            "label": "컨텍스트 요약 프롬프트",
            "description": "세계관이 컨텍스트 한계에 근접했을 때 압축 요약을 위해 사용. {world_state}, {tick_number} 플레이스홀더 유지 필요.",
            "content": SUMMARY_PROMPT_TEMPLATE,
        },
        {
            "key": "timeline_generate",
            "label": "타임라인 LLM 생성 프롬프트",
            "description": "타임라인 LLM 생성 기능에서 사용. {category}, {title}, {content}, {world_state}, {extra_prompt} 플레이스홀더 유지 필요.",
            "content": TIMELINE_GEN_PROMPT,
        },
        {
            "key": "nai_base_positive",
            "label": "NAI 기본 긍정 태그",
            "description": "NovelAI 이미지 생성 시 모든 프롬프트 앞에 자동으로 추가되는 기본 긍정 태그. (예: masterpiece, best quality)",
            "content": "{{masterpiece}}, {{best quality}}, {{ultra-detailed}}",
        },
        {
            "key": "nai_base_negative",
            "label": "NAI 기본 부정 태그",
            "description": "NovelAI 이미지 생성 시 기본으로 사용되는 네거티브 프롬프트 태그.",
            "content": "lowres, bad anatomy, bad hands, worst quality, blurry, text, error, missing fingers, extra digit, fewer digits, cropped, jpeg artifacts, signature, watermark, username",
        },
        {
            "key": "nai_auto_generator",
            "label": "NAI 프롬프트 자동생성 지침",
            "description": "엔트리 정보로 NAI 프롬프트를 자동생성할 때 LLM에게 전달되는 지침. {category}, {title}, {content}, {keywords} 플레이스홀더 유지 필요.",
            "content": NAI_PROMPT_GENERATOR_TEMPLATE,
        },
    ]
    for p in _default_prompts:
        if not LlmPromptConfig.query.filter_by(key=p["key"]).first():
            row = LlmPromptConfig(
                key=p["key"],
                label=p["label"],
                description=p["description"],
                content=p["content"],
                default_content=p["content"],
            )
            db.session.add(row)
    db.session.commit()

    # 기본 세계관 생성 및 기존 데이터 마이그레이션
    if World.query.count() == 0:
        default_world = World(name="기본 세계관", description="기존 데이터가 포함된 기본 세계관입니다.")
        db.session.add(default_world)
        db.session.commit()
        wid = default_world.id
        with db.engine.connect() as conn:
            for tbl in ["world_entries", "timelines", "simulation_configs", "simulation_runs", "world_snapshots"]:
                try:
                    conn.execute(db.text(f"UPDATE {tbl} SET world_id = {wid} WHERE world_id IS NULL"))
                    conn.commit()
                except Exception:
                    pass

    # 앱 재시작 시 고아 런(running/pending) 정리
    orphans = SimulationRun.query.filter(SimulationRun.status.in_(["running", "pending"])).all()
    for r in orphans:
        r.status = "cancelled"
        r.ended_at = datetime.utcnow()
    if orphans:
        db.session.commit()

    # 복구: 이전 마이그레이션이 잘못 표시한 유저 엔트리의 is_superseded 원복
    # (유저 엔트리는 is_superseded=True로 설정하지 않는 것이 원칙)
    _wrongly_superseded = WorldEntry.query.filter_by(
        created_by="user", is_superseded=True
    ).all()
    if _wrongly_superseded:
        for e in _wrongly_superseded:
            e.is_superseded = False
        db.session.commit()

    # 최초 설치 시 마스터 계정 생성. 이후 비밀번호는 DB 해시로만 보관된다.
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", name="Master", is_admin=True, is_approved=True)
        admin.set_password("kevin0318")
        db.session.add(admin)
        db.session.commit()

STORAGE_DIR = os.path.join(os.path.dirname(__file__), "storage")
ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB


def _allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXT


def get_world_id():
    """현재 선택된 세계관 ID (세션에서)"""
    return session.get("world_id")


def current_user():
    uid = session.get("user_id")
    return User.query.get(uid) if uid else None


@app.before_request
def require_login():
    allowed = {"login", "register", "logout", "static"}
    if request.endpoint in allowed or request.path.startswith("/storage/") or request.path.startswith("/read/"):
        return None
    user = current_user()
    if user and user.is_approved:
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "로그인이 필요하거나 관리자 승인을 기다리는 계정입니다."}), 401
    return redirect(url_for("login"))


@app.context_processor
def inject_world():
    """모든 템플릿에 current_world 주입"""
    wid = session.get("world_id")
    world = World.query.get(wid) if wid else None
    return {"current_world": world, "current_user": current_user()}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(request.form.get("password") or ""):
            return render_template("login.html", error="아이디 또는 비밀번호가 올바르지 않습니다.")
        if not user.is_approved:
            return render_template("login.html", error="관리자 승인 대기 중입니다.")
        session.clear(); session["user_id"] = user.id
        return redirect(url_for("worlds_page"))
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username, name, password = [(request.form.get(k) or "").strip() for k in ("username", "name", "password")]
        if len(username) < 3 or len(password) < 6 or not name:
            return render_template("register.html", error="아이디 3자 이상, 비밀번호 6자 이상, 이름을 입력하세요.")
        if User.query.filter_by(username=username).first():
            return render_template("register.html", error="이미 사용 중인 아이디입니다.")
        user = User(username=username, name=name, is_approved=False); user.set_password(password)
        db.session.add(user); db.session.commit()
        return render_template("login.html", message="가입 요청이 완료되었습니다. 관리자 승인 후 로그인할 수 있습니다.")
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))


@app.route("/admin/users")
def admin_users():
    if not current_user() or not current_user().is_admin: abort(403)
    return render_template("admin_users.html", users=User.query.order_by(User.created_at.desc()).all())


@app.route("/admin/users/<int:user_id>/approve", methods=["POST"])
def approve_user(user_id):
    if not current_user() or not current_user().is_admin: abort(403)
    user = User.query.get_or_404(user_id); user.is_approved = True; db.session.commit()
    return redirect(url_for("admin_users"))


def _require_world_redirect():
    """세계관 미선택 시 /worlds로 리다이렉트 (페이지 라우트에서 사용)"""
    if not session.get("world_id"):
        return redirect(url_for("worlds_page"))
    return None


DEFAULT_ENTRY_TEMPLATE_FIELDS = {
    "인물": [("종족",1,"종족/혈통"),("능력치",1,"세계관 능력치 스키마 참조"),("배경",1,"과거와 현재"),("이상/유대/결점",1,"TRPG 동기"),("소속",1,"세력/조직"),("스킬/특성",0,"레지스트리 링크"),("말투",0,"대사 스타일"),("예시 대사",0,"few-shot"),("비밀",0,"reveal 대상")],
    "세력": [("power_tier",1,"1~5"),("목표",1,"장기/단기 목표"),("자원",1,"군사/재정/정보"),("지도자",0,""),("동맹/적대",0,""),("현재 계획",0,""),("약점",0,"")],
    "장소": [("danger_level",1,"1~5"),("통치 세력",1,""),("분위기 태그",1,""),("인구",0,""),("접근 경로",0,""),("비밀",0,""),("모험 훅",0,"")],
    "사건": [("발생 틱/연도",1,""),("원인→결과",1,""),("관련 대상",0,""),("파급범위",0,"개인/가문/제국")],
    "관념": [("정의",1,"한 줄"),("사회적 영향력",1,"tier"),("기원",0,""),("신봉/반대 세력",0,""),("관련 사건",0,"")],
    "물건": [("등급",1,"일반/희귀/유일"),("효과",1,""),("제작자/기원",0,""),("대표 주인",0,"인물 DB 복수 링크"),("소유 이력",0,""),("대가/제약",0,"")],
    "종족": [("신체 특성",1,""),("사회 구조",1,""),("평균 수명",0,""),("서식지",0,""),("타 종족 관계",0,""),("고유 능력",0,"")],
    "마법/기술": [("희귀도",1,""),("효과",1,""),("원리",0,"계통"),("습득 조건",0,""),("리스크/대가",0,""),("관련 스킬",0,"")],
    "신화/종교": [("주신/개념",1,""),("교리 요약",1,""),("신도 세력",0,""),("상징/의식",0,""),("진실과의 괴리",0,"reveal 대상")],
    "역사/기록": [("시대 구간",1,""),("신뢰도",1,"공식/왜곡/은폐"),("기록 주체",0,""),("관련 사건/인물",0,"")],
    "규칙/법": [("적용 범위",1,""),("조항 요약",1,""),("제정 주체",0,""),("처벌",0,""),("실효성",0,"")],
    "연도": [("primary_year",1,""),("연도별 특기사항",0,"## 연도별 특기사항")],
}

def _ensure_world_metadata(world_id, commit=True):
    if WorldEntryTemplate.query.filter_by(world_id=world_id).count() == 0:
        for category in CATEGORIES:
            fields = [{"field":f,"required":bool(req),"description":desc} for f,req,desc in DEFAULT_ENTRY_TEMPLATE_FIELDS.get(category, [])]
            db.session.add(WorldEntryTemplate(world_id=world_id, category=category, fields_json=_json.dumps(fields, ensure_ascii=False)))
    if not WorldGuideline.query.get(world_id): db.session.add(WorldGuideline(world_id=world_id))
    if commit:
        db.session.commit()


# ─────────────────────────────────────────
#  페이지 라우트
# ─────────────────────────────────────────

@app.route("/worlds")
def worlds_page():
    return render_template("worlds.html")


@app.route("/")
def index():
    redir = _require_world_redirect()
    if redir:
        return redir
    return render_template("index.html", categories=CATEGORIES)


@app.route("/simulation")
def simulation_page():
    redir = _require_world_redirect()
    if redir:
        return redir
    return render_template("simulation.html")


@app.route("/logs")
def logs_page():
    redir = _require_world_redirect()
    if redir:
        return redir
    return render_template("logs.html")


@app.route("/graph")
def graph_page():
    redir = _require_world_redirect()
    if redir:
        return redir
    return render_template("graph.html", categories=CATEGORIES)


@app.route("/stats")
def stats_page():
    redir = _require_world_redirect()
    if redir:
        return redir
    return render_template("stats.html")


@app.route("/timeline")
def timeline_page():
    redir = _require_world_redirect()
    if redir:
        return redir
    return render_template("timeline.html")


@app.route("/metadata")
def metadata_page():
    redir = _require_world_redirect()
    if redir: return redir
    _ensure_world_metadata(get_world_id())
    return render_template("metadata.html", categories=CATEGORIES)


@app.route("/novel")
def novel_page():
    redir = _require_world_redirect()
    if redir: return redir
    return render_template("novel.html")


@app.route("/storage/<path:filename>")
def serve_storage(filename):
    """엔트리 이미지 정적 파일 서빙"""
    return send_from_directory(STORAGE_DIR, filename)


# ─────────────────────────────────────────
#  세계관(World) 관리 API
# ─────────────────────────────────────────

@app.route("/api/worlds", methods=["GET"])
def list_worlds():
    worlds = World.query.order_by(World.created_at.asc()).all()
    return jsonify([w.to_dict(with_counts=True) for w in worlds])


@app.route("/api/worlds", methods=["POST"])
def create_world():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name은 필수입니다."}), 400
    world = World(name=name, description=data.get("description", ""))
    db.session.add(world)
    db.session.commit()

    _ensure_world_metadata(world.id)
    return jsonify(world.to_dict()), 201


@app.route("/api/worlds/<int:world_id>", methods=["PUT"])
def update_world(world_id):
    world = World.query.get_or_404(world_id)
    data = request.json or {}
    if "name" in data:
        name = data["name"].strip()
        if not name:
            return jsonify({"error": "name은 필수입니다."}), 400
        world.name = name
    if "description" in data:
        world.description = data["description"]
    world.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(world.to_dict())


@app.route("/api/worlds/<int:world_id>", methods=["DELETE"])
def delete_world(world_id):
    world = World.query.get_or_404(world_id)
    # 외래키의 자식부터 삭제한다.
    entry_ids=[x.id for x in WorldEntry.query.filter_by(world_id=world_id).all()]
    run_ids=[x.id for x in SimulationRun.query.filter_by(world_id=world_id).all()]
    if run_ids:SimulationLog.query.filter(SimulationLog.run_id.in_(run_ids)).delete(synchronize_session=False)
    SimulationRun.query.filter_by(world_id=world_id).delete()
    timeline_ids=[x.id for x in Timeline.query.filter_by(world_id=world_id).all()]
    if timeline_ids:
        TimelineEvent.query.filter(TimelineEvent.timeline_id.in_(timeline_ids)).delete(synchronize_session=False)
        StoryBeat.query.filter(StoryBeat.timeline_id.in_(timeline_ids)).delete(synchronize_session=False)
    WorldSnapshot.query.filter_by(world_id=world_id).delete()
    SimulationConfig.query.filter_by(world_id=world_id).delete()
    _delete_world_extensions(world_id,entry_ids)
    Timeline.query.filter_by(world_id=world_id).delete()
    _delete_entry_dependents(entry_ids)
    WorldEntry.query.filter_by(world_id=world_id).delete()
    db.session.delete(world)
    db.session.commit()
    # 현재 선택된 세계관이었다면 세션 클리어
    if session.get("world_id") == world_id:
        session.pop("world_id", None)
    return jsonify({"ok": True})


@app.route("/api/worlds/<int:world_id>/select", methods=["POST"])
def select_world(world_id):
    world = World.query.get_or_404(world_id)
    session["world_id"] = world.id
    return jsonify({"ok": True, "world": world.to_dict()})


@app.route("/api/worlds/deselect", methods=["POST"])
def deselect_world():
    session.pop("world_id", None)
    return jsonify({"ok": True})


# ─────────────────────────────────────────
#  세계관 엔트리 API
# ─────────────────────────────────────────

@app.route("/api/entries", methods=["GET"])
def list_entries():
    category = request.args.get("category")
    active_only = request.args.get("active", "true") == "true"
    include_superseded = request.args.get("include_superseded", "false") == "true"
    # dedupe=true: 같은 (title, category)에서 최신 1개만 반환 (표시용)
    dedupe = request.args.get("dedupe", "false") == "true"
    wid = get_world_id()
    q = WorldEntry.query
    if wid:
        q = q.filter_by(world_id=wid)
    if category:
        q = q.filter_by(category=category)
    if active_only:
        q = q.filter_by(is_active=True)
    if not include_superseded:
        q = q.filter(
            db.or_(WorldEntry.is_superseded.is_(False), WorldEntry.is_superseded.is_(None))
        )
    entries = q.order_by(WorldEntry.tick_created.desc(), WorldEntry.created_at.desc()).all()
    if dedupe:
        seen = {}
        deduped = []
        for e in entries:
            key = (e.title.strip().lower(), e.category)
            if key not in seen:
                seen[key] = True
                deduped.append(e)
        entries = deduped
    return jsonify([e.to_dict() for e in entries])


@app.route("/api/entries/<int:entry_id>", methods=["GET"])
def get_entry(entry_id):
    entry = WorldEntry.query.get_or_404(entry_id)
    return jsonify(entry.to_dict())


@app.route("/api/entries", methods=["POST"])
def create_entry():
    data = request.json
    if not data.get("title") or not data.get("category") or not data.get("content"):
        return jsonify({"error": "title, category, content는 필수입니다."}), 400
    if data["category"] not in CATEGORIES:
        return jsonify({"error": f"category는 {CATEGORIES} 중 하나여야 합니다."}), 400

    entry = WorldEntry(
        world_id=get_world_id(),
        title=data["title"],
        category=data["category"],
        content=data["content"],
        created_by="user",
        tick_created=int(data.get("tick_created", 0)),
        is_active=True,
    )
    entry.references = data.get("references", [])
    entry.primary_year = (data.get("primary_year") or "").strip()[:100]
    entry.year_notes_json = _json.dumps(data.get("year_notes") or [], ensure_ascii=False)
    entry.aliases_json = _json.dumps(data.get("aliases") or [], ensure_ascii=False)
    entry.secret_content = str(data.get("secret") or "")[:10000]
    db.session.add(entry)
    db.session.flush()
    _apply_generated_attributes(entry, data.get("attributes") or {})
    db.session.commit()

    _enrich_entry(entry)

    return jsonify(entry.to_dict()), 201


@app.route("/api/entries/<int:entry_id>", methods=["PUT"])
def update_entry(entry_id):
    entry = WorldEntry.query.get_or_404(entry_id)
    data = request.json
    if "title" in data:
        entry.title = data["title"]
    if "category" in data:
        if data["category"] not in CATEGORIES:
            return jsonify({"error": f"유효하지 않은 category"}), 400
        entry.category = data["category"]
    if "content" in data:
        entry.content = data["content"]
    if "references" in data:
        entry.references = data["references"]
    if "is_active" in data:
        entry.is_active = data["is_active"]
    if "tick_created" in data:
        entry.tick_created = int(data["tick_created"])
    if "primary_year" in data: entry.primary_year = str(data["primary_year"] or "").strip()[:100]
    if "year_notes" in data: entry.year_notes_json = _json.dumps(data["year_notes"] or [], ensure_ascii=False)
    if "aliases" in data: entry.aliases_json = _json.dumps(data["aliases"] or [], ensure_ascii=False)
    entry.updated_at = datetime.utcnow()
    db.session.commit()

    if any(k in data for k in ("title", "category", "content")):
        _enrich_entry(entry)

    return jsonify(entry.to_dict())


def _apply_generated_attributes(entry, attributes):
    """LLM 제안 능력치를 현재 세계관의 활성 스키마 범위에 맞춰 저장한다."""
    if entry.category != "인물" or not isinstance(attributes, dict):
        return
    axes=WorldAttributeSchema.query.filter_by(world_id=entry.world_id,is_active=True).all()
    key=lambda value:"".join(str(value or "").split()).casefold()
    axis_by_key={key(axis.axis_name):axis for axis in axes}
    for axis_name, value_data in attributes.items():
        axis=axis_by_key.get(key(axis_name))
        if not axis:
            continue
        value = value_data.get("value", axis.min_tier) if isinstance(value_data, dict) else value_data
        description = value_data.get("description", "") if isinstance(value_data, dict) else ""
        try:
            value = max(axis.min_tier, min(axis.max_tier, int(value)))
        except (TypeError, ValueError):
            continue
        row = EntryAttributeValue.query.get((entry.id, axis.id)) or EntryAttributeValue(entry_id=entry.id, axis_id=axis.id)
        row.value = value
        row.description = str(description or "")[:4000]
        db.session.add(row)


@app.route("/api/entries/<int:entry_id>/secret", methods=["GET", "PUT"])
def entry_secret(entry_id):
    """비밀은 사용자가 이 경로를 명시적으로 호출했을 때만 반환한다."""
    entry = WorldEntry.query.get_or_404(entry_id)
    if entry.world_id != get_world_id(): abort(403)
    if request.method == "PUT":
        entry.secret_content = str((request.json or {}).get("secret") or "")[:10000]
        entry.updated_at = datetime.utcnow()
        db.session.commit()
    return jsonify({"entry_id":entry.id,"secret":entry.secret_content or "","has_secret":bool(entry.secret_content)})


@app.route("/api/entries/<int:entry_id>/revision-plan", methods=["POST"])
def entry_revision_plan(entry_id):
    """수정 제안만 만들며 DB는 변경하지 않는다."""
    entry = WorldEntry.query.get_or_404(entry_id)
    if entry.world_id != get_world_id(): return jsonify({"error": "다른 세계관 엔트리입니다."}), 403
    data = request.json or {}
    instruction = (data.get("instruction") or "").strip()
    if not instruction: return jsonify({"error": "수정 요청을 입력하세요."}), 400
    try:
        import llm_client
        result = llm_client.propose_entry_revision(entry.to_dict(), instruction, data.get("history") or [])
        proposal = result.get("proposal")
        if proposal and proposal.get("category") not in CATEGORIES: proposal["category"] = entry.category
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/relationships", methods=["POST"])
def create_relationship():
    """선택 엔트리의 방향성 관계를 저장하고 기존 관계도에서도 보이도록 references를 동기화한다."""
    data = request.json or {}
    source_id, target_ids = data.get("source_id"), data.get("target_ids") or []
    source = WorldEntry.query.get_or_404(source_id)
    if source.world_id != get_world_id(): return jsonify({"error": "다른 세계관 엔트리입니다."}), 403
    created = []
    for target_id in target_ids:
        if target_id == source.id: continue
        target = WorldEntry.query.get(target_id)
        if not target or target.world_id != source.world_id: continue
        if not EntryRelationship.query.filter_by(source_entry_id=source.id, target_entry_id=target.id, relation_type=(data.get("relation_type") or "관련")[:100]).first():
            db.session.add(EntryRelationship(world_id=source.world_id, source_entry_id=source.id, target_entry_id=target.id, relation_type=(data.get("relation_type") or "관련")[:100], description=(data.get("description") or "")[:2000]))
            created.append(target.id)
        refs = source.references
        if target.id not in refs: source.references = refs + [target.id]
    db.session.commit()
    return jsonify({"ok": True, "created_target_ids": created})


CHARACTER_RELATION_TYPES = ("동일인물", "아버지", "어머니", "부모")


def _add_character_relation(world_id, source_id, target_id, relation_type):
    if source_id == target_id or relation_type not in CHARACTER_RELATION_TYPES:
        return None
    if relation_type == "동일인물" and source_id > target_id:
        source_id, target_id = target_id, source_id
    row = EntryRelationship.query.filter_by(
        world_id=world_id, source_entry_id=source_id,
        target_entry_id=target_id, relation_type=relation_type,
    ).first()
    if not row:
        row = EntryRelationship(world_id=world_id, source_entry_id=source_id, target_entry_id=target_id, relation_type=relation_type)
        db.session.add(row)
    source = WorldEntry.query.get(source_id)
    if source and target_id not in source.references:
        source.references = source.references + [target_id]
    return row


@app.route("/api/entries/<int:entry_id>/character-links", methods=["GET", "PUT"])
def character_links(entry_id):
    """동일 인물 정체성과 부모·자식 관계를 구조화해 관리한다."""
    entry = WorldEntry.query.get_or_404(entry_id)
    if entry.world_id != get_world_id(): abort(403)
    if entry.category != "인물": return jsonify({"error":"인물 엔트리만 연결할 수 있습니다."}),400
    wid = entry.world_id
    if request.method == "PUT":
        data = request.json or {}
        focused = EntryRelationship.query.filter_by(world_id=wid).filter(db.or_(
            db.and_(EntryRelationship.relation_type == "동일인물", db.or_(EntryRelationship.source_entry_id == entry.id, EntryRelationship.target_entry_id == entry.id)),
            db.and_(EntryRelationship.relation_type.in_(("아버지","어머니","부모")), db.or_(EntryRelationship.source_entry_id == entry.id, EntryRelationship.target_entry_id == entry.id)),
        )).all()
        removed_pairs={(r.source_entry_id,r.target_entry_id) for r in focused}
        for row in focused: db.session.delete(row)
        db.session.flush()
        def valid_person(raw_id):
            try: candidate=WorldEntry.query.get(int(raw_id))
            except (TypeError,ValueError): return None
            return candidate if candidate and candidate.world_id==wid and candidate.category=="인물" and candidate.id!=entry.id else None
        for other_id in data.get("identity_ids") or []:
            other=valid_person(other_id)
            if other:_add_character_relation(wid,entry.id,other.id,"동일인물")
        for field,role in (("father_id","아버지"),("mother_id","어머니")):
            parent=valid_person(data.get(field))
            if parent:_add_character_relation(wid,parent.id,entry.id,role)
        for child_data in data.get("children") or []:
            child=valid_person(child_data.get("entry_id") if isinstance(child_data,dict) else child_data)
            role=(child_data.get("role") if isinstance(child_data,dict) else "부모")
            if child:_add_character_relation(wid,entry.id,child.id,role if role in ("아버지","어머니","부모") else "부모")
        db.session.flush()
        for source_id,target_id in removed_pairs:
            if not EntryRelationship.query.filter_by(world_id=wid,source_entry_id=source_id,target_entry_id=target_id).first():
                source=WorldEntry.query.get(source_id)
                if source and target_id in source.references:source.references=[x for x in source.references if x!=target_id]
        db.session.commit()

    rows=EntryRelationship.query.filter_by(world_id=wid).filter(EntryRelationship.relation_type.in_(CHARACTER_RELATION_TYPES)).all()
    if request.args.get("recursive","false")=="true":
        ids={entry.id};changed=True
        while changed:
            changed=False
            for row in rows:
                if row.source_entry_id in ids or row.target_entry_id in ids:
                    before=len(ids);ids.update((row.source_entry_id,row.target_entry_id));changed=changed or len(ids)>before
        tree_rows=[r for r in rows if r.source_entry_id in ids and r.target_entry_id in ids]
        people=WorldEntry.query.filter(WorldEntry.id.in_(ids)).all()
        return jsonify({"entries":[p.to_dict() for p in people],"links":[{"source_id":r.source_entry_id,"target_id":r.target_entry_id,"type":r.relation_type} for r in tree_rows]})
    entry_map={p.id:p.to_dict() for p in WorldEntry.query.filter_by(world_id=wid,category="인물").all()}
    identities=[];parents=[];children=[]
    for row in rows:
        if row.relation_type=="동일인물" and entry.id in (row.source_entry_id,row.target_entry_id):
            other=row.target_entry_id if row.source_entry_id==entry.id else row.source_entry_id
            if other in entry_map:identities.append(entry_map[other])
        elif row.target_entry_id==entry.id and row.relation_type in ("아버지","어머니","부모") and row.source_entry_id in entry_map:
            parents.append({"person":entry_map[row.source_entry_id],"role":row.relation_type})
        elif row.source_entry_id==entry.id and row.relation_type in ("아버지","어머니","부모") and row.target_entry_id in entry_map:
            children.append({"person":entry_map[row.target_entry_id],"role":row.relation_type})
    return jsonify({"identities":identities,"parents":parents,"children":children})


@app.route("/api/entries/<int:entry_id>/owners", methods=["GET", "PUT"])
def item_owners(entry_id):
    """물건의 복수 대표 주인을 직접 관계로 관리한다."""
    item=WorldEntry.query.get_or_404(entry_id)
    if item.world_id!=get_world_id():abort(403)
    if item.category!="물건":return jsonify({"error":"물건 엔트리만 대표 주인을 설정할 수 있습니다."}),400
    if request.method=="PUT":
        data=request.json or {};owner_ids=[]
        for raw_id in data.get("owner_ids") or []:
            try:owner_id=int(raw_id)
            except (TypeError,ValueError):continue
            owner=WorldEntry.query.get(owner_id)
            if owner and owner.world_id==item.world_id and owner.category=="인물" and owner_id not in owner_ids:owner_ids.append(owner_id)
        old=EntryRelationship.query.filter_by(world_id=item.world_id,source_entry_id=item.id,relation_type="대표 주인").all()
        old_ids={x.target_entry_id for x in old}
        for row in old:db.session.delete(row)
        for owner_id in owner_ids:
            db.session.add(EntryRelationship(world_id=item.world_id,source_entry_id=item.id,target_entry_id=owner_id,relation_type="대표 주인"))
        # 대표 주인은 직접 참조이기도 하므로 관계도와 LLM 공개 컨텍스트에 연결한다.
        preserved=[x for x in item.references if x not in old_ids]
        item.references=list(dict.fromkeys(preserved+owner_ids))
        db.session.commit()
    rows=EntryRelationship.query.filter_by(world_id=item.world_id,source_entry_id=item.id,relation_type="대표 주인").order_by(EntryRelationship.id).all()
    people={p.id:p.to_dict() for p in WorldEntry.query.filter_by(world_id=item.world_id,category="인물").all()}
    return jsonify([people[x.target_entry_id] for x in rows if x.target_entry_id in people])


@app.route("/api/entries/<int:entry_id>/owner-candidates", methods=["GET"])
def item_owner_candidates(entry_id):
    """현재 직접 주인을 먼저, 이후 검색 적합도 순으로 인물 후보를 반환한다."""
    item=WorldEntry.query.get_or_404(entry_id)
    if item.world_id!=get_world_id():abort(403)
    if item.category!="물건":return jsonify({"error":"물건 엔트리만 조회할 수 있습니다."}),400
    q=str(request.args.get("q") or "").strip().casefold()
    selected={x.target_entry_id for x in EntryRelationship.query.filter_by(world_id=item.world_id,source_entry_id=item.id,relation_type="대표 주인").all()}
    candidates=[]
    for person in WorldEntry.query.filter_by(world_id=item.world_id,category="인물",is_active=True).all():
        try:aliases=[str(x) for x in _json.loads(person.aliases_json or "[]")]
        except Exception:aliases=[]
        title=person.title.casefold();alias_fold=[x.casefold() for x in aliases];haystack=" ".join([title,(person.keywords or "").casefold(),person.content[:1000].casefold(),*alias_fold])
        if q and q not in haystack and person.id not in selected:continue
        score=10000 if person.id in selected else 0
        if q:
            if title==q:score+=1000
            elif title.startswith(q):score+=800
            elif q in title:score+=600
            if q in alias_fold:score+=500
            elif any(x.startswith(q) for x in alias_fold):score+=400
            elif q in haystack:score+=100
        candidates.append((score,person.title,person,aliases))
    candidates.sort(key=lambda x:(-x[0],x[1]))
    return jsonify([{"entry":person.to_dict(),"aliases":aliases,"selected":person.id in selected,"score":score} for score,_,person,aliases in candidates[:50]])


@app.route("/api/entries/<int:entry_id>", methods=["DELETE"])
def delete_entry(entry_id):
    entry = WorldEntry.query.get_or_404(entry_id)
    if entry.world_id != get_world_id():
        return jsonify({"error": "다른 세계관 엔트리입니다."}), 403
    _delete_entry_dependents([entry.id])
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/entries/<int:entry_id>/keywords", methods=["POST"])
def refresh_entry_keywords(entry_id):
    """엔트리 키워드를 LLM으로 재생성"""
    entry = WorldEntry.query.get_or_404(entry_id)
    try:
        from llm_client import generate_keywords
        kws = generate_keywords(entry.title, entry.category, entry.content)
        entry.keywords = ", ".join(kws) if kws else ""
        db.session.commit()
        return jsonify({"keywords": entry.keywords, "ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _enrich_entry(entry):
    """자동 태그/키워드와 선택적 임베딩을 저장한다. LLM 실패는 엔트리 저장을 막지 않는다."""
    try:
        import llm_client
        kws = llm_client.generate_keywords(entry.title, entry.category, entry.content)
        if kws:
            entry.keywords = ", ".join(kws)
        tags = llm_client.generate_auto_tags(entry.title, entry.category, entry.content)
        if tags:
            entry.auto_tags_json = _json.dumps(tags, ensure_ascii=False)
        settings = UserLlmSettings.get_for_user(current_user().id) if current_user() else AppSettings.get()
        if settings.embedding_enabled:
            text = f"{entry.title}\n{entry.category}\n{entry.content}\n태그: {entry.auto_tags_json}"
            entry.embedding_json = _json.dumps(llm_client.generate_embedding(text, settings.embedding_model))
            entry.embedding_model = settings.embedding_model
        db.session.commit()
    except Exception:
        db.session.rollback()


@app.route("/api/entries/<int:entry_id>/embed", methods=["POST"])
def embed_entry(entry_id):
    entry = WorldEntry.query.get_or_404(entry_id)
    user_settings = UserLlmSettings.get_for_user(current_user().id)
    if not user_settings.embedding_enabled:
        return jsonify({"error": "임베딩이 비활성화되어 있습니다."}), 400
    try:
        import llm_client
        entry.embedding_json = _json.dumps(llm_client.generate_embedding(
            f"{entry.title}\n{entry.category}\n{entry.content}\n태그: {entry.auto_tags_json}", user_settings.embedding_model))
        entry.embedding_model = user_settings.embedding_model
        db.session.commit()
        return jsonify({"ok": True, "entry": entry.to_dict()})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/search/semantic", methods=["POST"])
def semantic_search():
    data = request.json or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query는 필수입니다."}), 400
    settings = AppSettings.get()
    if not settings.embedding_enabled:
        return jsonify({"error": "임베딩 검색이 비활성화되어 있습니다."}), 400
    try:
        import llm_client
        qvec = llm_client.generate_embedding(query, settings.embedding_model)
        entries = WorldEntry.query.filter_by(world_id=get_world_id(), is_active=True).all()
        ranked = []
        for entry in entries:
            try:
                vec = _json.loads(entry.embedding_json or "[]")
                score = llm_client.cosine_similarity(qvec, vec)
                if score >= 0: ranked.append((score, entry))
            except Exception: pass
        ranked.sort(key=lambda item: item[0], reverse=True)
        return jsonify([dict(entry.to_dict(), score=round(score, 4)) for score, entry in ranked[:int(data.get("limit", settings.rag_reference_limit))]])
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/entries/<int:entry_id>/versions", methods=["GET"])
def get_entry_versions(entry_id):
    """엔트리의 전체 버전 체인 반환 (부모 → 자식 순서)"""
    entry = WorldEntry.query.get_or_404(entry_id)

    # 루트(최상위 원본)까지 거슬러 올라가기
    root = entry
    while root.parent_entry_id:
        parent = WorldEntry.query.get(root.parent_entry_id)
        if not parent:
            break
        root = parent

    # 루트부터 모든 자손을 BFS로 수집
    chain = []
    queue = [root]
    visited = set()
    while queue:
        current = queue.pop(0)
        if current.id in visited:
            continue
        visited.add(current.id)
        chain.append(current.to_dict())
        children = WorldEntry.query.filter_by(parent_entry_id=current.id).order_by(WorldEntry.tick_created).all()
        queue.extend(children)

    return jsonify(chain)


@app.route("/api/entries/bulk-keywords", methods=["POST"])
def bulk_generate_keywords():
    """키워드 없는 엔트리들 일괄 키워드 생성"""
    from llm_client import generate_keywords
    wid = get_world_id()
    bq = WorldEntry.query.filter(
        db.or_(WorldEntry.keywords == None, WorldEntry.keywords == "")
    ).filter(WorldEntry.is_active == True)
    if wid:
        bq = bq.filter_by(world_id=wid)
    entries = bq.all()

    results = {"success": 0, "failed": 0, "total": len(entries)}
    for entry in entries:
        try:
            kws = generate_keywords(entry.title, entry.category, entry.content)
            entry.keywords = ", ".join(kws) if kws else ""
            db.session.commit()
            results["success"] += 1
        except Exception:
            results["failed"] += 1

    return jsonify(results)


@app.route("/api/entries/<int:entry_id>/image", methods=["POST"])
def upload_entry_image(entry_id):
    """엔트리 이미지 업로드 (multipart/form-data, field: 'image')"""
    entry = WorldEntry.query.get_or_404(entry_id)
    if "image" not in request.files:
        return jsonify({"error": "image 필드가 없습니다."}), 400
    f = request.files["image"]
    if not f.filename or not _allowed_image(f.filename):
        return jsonify({"error": "허용되지 않는 파일 형식입니다 (jpg/png/gif/webp)."}), 400

    ext = f.filename.rsplit(".", 1)[1].lower()
    img_dir = os.path.join(STORAGE_DIR, f"{entry_id}-img")
    os.makedirs(img_dir, exist_ok=True)

    # 기존 이미지 삭제
    if entry.image_filename:
        old_path = os.path.join(STORAGE_DIR, entry.image_filename)
        if os.path.exists(old_path):
            os.remove(old_path)

    new_name = f"{uuid.uuid4().hex}.{ext}"
    rel_path = f"{entry_id}-img/{new_name}"
    f.save(os.path.join(STORAGE_DIR, rel_path))

    entry.image_filename = rel_path
    db.session.commit()
    return jsonify({"image_filename": rel_path, "ok": True})


@app.route("/api/entries/<int:entry_id>/image", methods=["DELETE"])
def delete_entry_image(entry_id):
    """엔트리 이미지 삭제"""
    entry = WorldEntry.query.get_or_404(entry_id)
    if entry.image_filename:
        path = os.path.join(STORAGE_DIR, entry.image_filename)
        if os.path.exists(path):
            os.remove(path)
        entry.image_filename = None
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/entries/<int:entry_id>/generate-image-nai", methods=["POST"])
def generate_image_nai(entry_id):
    """NovelAI API로 엔트리 이미지 생성"""
    import requests
    import zipfile
    import io

    NAI_TOKEN = UserLlmSettings.get_for_user(current_user().id).novelai_api_key or os.environ.get("NOVELAI_API_KEY", "")
    if not NAI_TOKEN:
        return jsonify({"error": "NOVELAI_API_KEY 환경변수가 설정되지 않았습니다."}), 400

    entry = WorldEntry.query.get_or_404(entry_id)
    data = request.json or {}
    prompt = data.get("prompt", entry.title)
    negative_prompt = data.get("negative_prompt", "lowres, bad anatomy, bad hands, worst quality, blurry")
    model = data.get("model", "nai-diffusion-3")
    width = int(data.get("width", 832))
    height = int(data.get("height", 1216))
    sampler = data.get("sampler", "k_euler_ancestral")
    scale = float(data.get("scale", 6))
    steps = int(data.get("steps", 28))

    # v4/v4.5 모델은 구조화된 프롬프트 포맷이 필요
    _is_v4 = "4" in model  # nai-diffusion-4-*, nai-diffusion-4-5-* 등

    if _is_v4:
        params = {
            "params_version": 3,
            "width": width,
            "height": height,
            "scale": scale,
            "sampler": sampler,
            "steps": steps,
            "n_samples": 1,
            "seed": 0,
            "qualityToggle": True,
            "dynamic_thresholding": False,
            "legacy": False,
            "add_original_image": True,
            "cfg_rescale": 0,
            "noise_schedule": "karras",
            "negative_prompt": negative_prompt,
            "v4_prompt": {
                "caption": {
                    "base_caption": prompt,
                    "char_captions": [],
                },
                "use_coords": False,
                "use_order": True,
            },
            "v4_negative_prompt": {
                "caption": {
                    "base_caption": negative_prompt,
                    "char_captions": [],
                }
            },
        }
    else:
        params = {
            "width": width,
            "height": height,
            "scale": scale,
            "sampler": sampler,
            "steps": steps,
            "n_samples": 1,
            "seed": 0,
            "negative_prompt": negative_prompt,
        }

    payload = {
        "input": prompt,
        "model": model,
        "action": "generate",
        "parameters": params,
    }

    try:
        resp = requests.post(
            "https://image.novelai.net/ai/generate-image",
            headers={
                "Authorization": f"Bearer {NAI_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )
    except requests.RequestException as e:
        return jsonify({"error": f"NovelAI 연결 실패: {str(e)}"}), 502

    if resp.status_code != 200:
        try:
            err = resp.json()
        except Exception:
            err = {"message": resp.text[:200]}
        return jsonify({"error": f"NovelAI 오류 {resp.status_code}: {err.get('message', resp.text[:200])}"}), 502

    # 응답은 zip — image(s) 추출
    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        img_name = next((n for n in zf.namelist() if n.lower().endswith(('.png', '.jpg', '.webp'))), None)
        if not img_name:
            return jsonify({"error": "생성된 이미지를 찾을 수 없습니다."}), 500
        img_data = zf.read(img_name)
    except Exception as e:
        return jsonify({"error": f"이미지 추출 실패: {str(e)}"}), 500

    # 기존 이미지 삭제
    if entry.image_filename:
        old_path = os.path.join(STORAGE_DIR, entry.image_filename)
        if os.path.exists(old_path):
            os.remove(old_path)

    # 저장
    os.makedirs(STORAGE_DIR, exist_ok=True)
    filename = f"nai_{entry_id}_{uuid.uuid4().hex[:8]}.png"
    save_path = os.path.join(STORAGE_DIR, filename)
    with open(save_path, "wb") as f:
        f.write(img_data)

    entry.image_filename = filename
    db.session.commit()
    return jsonify({"ok": True, "image_filename": filename})


@app.route("/api/entries/<int:entry_id>/generate-nai-prompt", methods=["POST"])
def generate_nai_prompt_api(entry_id):
    """LLM으로 NAI 이미지 프롬프트 자동생성"""
    from llm_client import generate_nai_prompt
    entry = WorldEntry.query.get_or_404(entry_id)
    settings = AppSettings.get()
    user_settings = UserLlmSettings.get_for_user(current_user().id)

    # 프롬프트 설정 로드
    prompt_overrides = {p.key: p.content for p in LlmPromptConfig.query.all()}
    nai_tmpl = prompt_overrides.get("nai_auto_generator")
    base_positive = prompt_overrides.get("nai_base_positive", "")

    wid = entry.world_id
    world_entries = [e.to_dict() for e in WorldEntry.query.filter_by(
        world_id=wid, is_active=True
    ).filter(
        db.or_(WorldEntry.is_superseded.is_(False), WorldEntry.is_superseded.is_(None))
    ).limit(30).all()]

    try:
        tags = generate_nai_prompt(
            entry=entry.to_dict(),
            world_entries=world_entries,
            prompt_template=nai_tmpl,
            base_positive=base_positive,
            model_override=user_settings.nai_model or user_settings.llm_model or settings.llm_model_nai or None,
        )
        return jsonify({"ok": True, "prompt": tags})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/entries", methods=["DELETE"])
def delete_all_entries():
    """현재 세계관 엔트리 전체 삭제"""
    wid = get_world_id()
    q = WorldEntry.query
    if wid:
        q = q.filter_by(world_id=wid)
    entry_ids = [row.id for row in q.all()]
    _delete_entry_dependents(entry_ids)
    if entry_ids:
        WorldEntry.query.filter(WorldEntry.id.in_(entry_ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True})


def _export_world_extensions(world_id):
    entry_ids=[e.id for e in WorldEntry.query.filter_by(world_id=world_id).all()]
    attrs=WorldAttributeSchema.query.filter_by(world_id=world_id).all();skills=WorldSkillRegistry.query.filter_by(world_id=world_id).all();chapters=NovelChapter.query.filter_by(world_id=world_id).all()
    chapter_ids=[c.id for c in chapters]
    return {
        "attributes":[a.to_dict() for a in attrs],
        "attribute_values":[{"entry_id":v.entry_id,"axis_id":v.axis_id,"value":v.value,"description":v.description or ""} for v in EntryAttributeValue.query.filter(EntryAttributeValue.entry_id.in_(entry_ids or [-1])).all()],
        "skills":[s.to_dict() for s in skills],
        "skill_links":[{"entry_id":x.entry_id,"skill_id":x.skill_id,"rank":x.rank} for x in EntrySkillLink.query.filter(EntrySkillLink.entry_id.in_(entry_ids or [-1])).all()],
        "guideline":(WorldGuideline.query.get(world_id).to_dict() if WorldGuideline.query.get(world_id) else None),
        "templates":[t.to_dict() for t in WorldEntryTemplate.query.filter_by(world_id=world_id).all()],
        "novel_parts":[p.to_dict() for p in NovelPart.query.filter_by(world_id=world_id).all()],
        "chapters":[c.to_dict() for c in chapters],
        "mentions":[m.to_dict() for m in NovelEntityMention.query.filter(NovelEntityMention.chapter_id.in_(chapter_ids or [-1])).all()],
        "styles":[s.to_dict() for s in WorldWritingStyle.query.filter_by(world_id=world_id).all()],
        "reveals":[r.to_dict() for r in EntryRevealState.query.filter(EntryRevealState.entry_id.in_(entry_ids or [-1])).all()],
        "relationships":[{"source_entry_id":r.source_entry_id,"target_entry_id":r.target_entry_id,"relation_type":r.relation_type,"description":r.description or ""} for r in EntryRelationship.query.filter_by(world_id=world_id).all()],
        "secrets":[{"entry_id":e.id,"secret":e.secret_content or ""} for e in WorldEntry.query.filter_by(world_id=world_id).filter(WorldEntry.secret_content != "").all()],
    }

def _delete_world_extensions(world_id, entry_ids=None):
    entry_ids=entry_ids if entry_ids is not None else [e.id for e in WorldEntry.query.filter_by(world_id=world_id).all()]
    chapter_ids=[c.id for c in NovelChapter.query.filter_by(world_id=world_id).all()]
    if entry_ids:
        EntryRevealState.query.filter(EntryRevealState.entry_id.in_(entry_ids)).delete(synchronize_session=False);EntryAttributeValue.query.filter(EntryAttributeValue.entry_id.in_(entry_ids)).delete(synchronize_session=False);EntrySkillLink.query.filter(EntrySkillLink.entry_id.in_(entry_ids)).delete(synchronize_session=False)
    if chapter_ids:NovelEntityMention.query.filter(NovelEntityMention.chapter_id.in_(chapter_ids)).delete(synchronize_session=False)
    EntryRelationship.query.filter_by(world_id=world_id).delete();WorldWritingStyle.query.filter_by(world_id=world_id).delete();NovelChapter.query.filter_by(world_id=world_id).delete();NovelPart.query.filter_by(world_id=world_id).delete();WorldAttributeSchema.query.filter_by(world_id=world_id).delete();WorldSkillRegistry.query.filter_by(world_id=world_id).delete();WorldGuideline.query.filter_by(world_id=world_id).delete();WorldEntryTemplate.query.filter_by(world_id=world_id).delete()

def _delete_entry_dependents(entry_ids):
    """엔트리를 물리 삭제할 때 신규 메타데이터/소설 참조를 함께 정리한다."""
    entry_ids = [int(x) for x in (entry_ids or [])]
    if not entry_ids:
        return
    EntryRevealState.query.filter(EntryRevealState.entry_id.in_(entry_ids)).delete(synchronize_session=False)
    EntryAttributeValue.query.filter(EntryAttributeValue.entry_id.in_(entry_ids)).delete(synchronize_session=False)
    EntrySkillLink.query.filter(EntrySkillLink.entry_id.in_(entry_ids)).delete(synchronize_session=False)
    NovelEntityMention.query.filter(NovelEntityMention.entry_id.in_(entry_ids)).delete(synchronize_session=False)
    for part in NovelPart.query.all():
        kept=[entry_id for entry_id in part.entry_ids if entry_id not in entry_ids]
        if kept!=part.entry_ids:part.entry_ids=kept
    EntryRelationship.query.filter(db.or_(
        EntryRelationship.source_entry_id.in_(entry_ids),
        EntryRelationship.target_entry_id.in_(entry_ids),
    )).delete(synchronize_session=False)

def _restore_world_extensions(world_id, ext, entry_map):
    axis_map={};skill_map={};part_map={};chapter_map={}
    for a in ext.get("attributes",[]):
        row=WorldAttributeSchema(world_id=world_id,axis_name=a.get("axis_name","능력치"),axis_order=a.get("axis_order",0),min_tier=a.get("min_tier",1),max_tier=a.get("max_tier",5),description=a.get("description",""),tier_labels_json=_json.dumps(a.get("tier_labels",{}),ensure_ascii=False),is_active=a.get("is_active",True));db.session.add(row);db.session.flush();axis_map[a.get("id")]=row.id
    for s in ext.get("skills",[]):
        row=WorldSkillRegistry(world_id=world_id,type=s.get("type","스킬"),name=s.get("name",""),description=s.get("description",""),tags_json=_json.dumps(s.get("tags",[]),ensure_ascii=False),rarity=s.get("rarity","일반"),created_by=s.get("created_by","user"),tick_created=s.get("tick_created",0));db.session.add(row);db.session.flush();skill_map[s.get("id")]=row.id
    g=ext.get("guideline")
    if g:db.session.add(WorldGuideline(world_id=world_id,skill_generation_guide=g.get("skill_generation_guide",""),trait_generation_guide=g.get("trait_generation_guide","")))
    for t in ext.get("templates",[]):db.session.add(WorldEntryTemplate(world_id=world_id,category=t.get("category","관념"),fields_json=_json.dumps(t.get("fields",[]),ensure_ascii=False),is_active=t.get("is_active",True)))
    for v in ext.get("attribute_values",[]):
        if v.get("entry_id") in entry_map and v.get("axis_id") in axis_map:db.session.add(EntryAttributeValue(entry_id=entry_map[v["entry_id"]],axis_id=axis_map[v["axis_id"]],value=v.get("value"),description=v.get("description","")))
    for x in ext.get("skill_links",[]):
        if x.get("entry_id") in entry_map and x.get("skill_id") in skill_map:db.session.add(EntrySkillLink(entry_id=entry_map[x["entry_id"]],skill_id=skill_map[x["skill_id"]],rank=x.get("rank")))
    for p in ext.get("novel_parts",[]):
        token=p.get("public_token")
        if token and NovelPart.query.filter_by(public_token=token).first():token=None
        row=NovelPart(world_id=world_id,title=p.get("title","기본 이야기"),description=p.get("description",""),order_no=p.get("order_no",0),is_public=bool(p.get("is_public",False)),public_token=token);row.entry_ids=[entry_map[x] for x in p.get("entry_ids",[]) if x in entry_map];db.session.add(row);db.session.flush();part_map[p.get("id")]=row.id
    default_part=None
    if not part_map and ext.get("chapters"):
        default_part=NovelPart(world_id=world_id,title="기본 이야기",order_no=1);db.session.add(default_part);db.session.flush()
    for c in ext.get("chapters",[]):
        token=c.get("public_token")
        if token and NovelChapter.query.filter_by(public_token=token).first():token=None
        row=NovelChapter(world_id=world_id,part_id=part_map.get(c.get("part_id")) or (default_part.id if default_part else None),title=c.get("title","새 챕터"),order_no=c.get("order_no",0),content=c.get("content",""),status=c.get("status","초안"),is_public=bool(c.get("is_public",False)),public_token=token);db.session.add(row);db.session.flush();chapter_map[c.get("id")]=row.id
    for m in ext.get("mentions",[]):
        if m.get("chapter_id") in chapter_map and m.get("entry_id") in entry_map:db.session.add(NovelEntityMention(chapter_id=chapter_map[m["chapter_id"]],entry_id=entry_map[m["entry_id"]],span_start=m.get("span_start",0),span_end=m.get("span_end",0),matched_text=m.get("matched_text",""),source=m.get("source","keyword")))
    for s in ext.get("styles",[]):db.session.add(WorldWritingStyle(world_id=world_id,novel_id=part_map.get(s.get("novel_id")),chapter_id=chapter_map.get(s.get("chapter_id")),pov=s.get("pov",""),tone_guide=s.get("tone_guide",""),forbidden_expressions=s.get("forbidden_expressions",""),sample_text=s.get("sample_text","")))
    for r in ext.get("reveals",[]):
        if r.get("entry_id") in entry_map:db.session.add(EntryRevealState(entry_id=entry_map[r["entry_id"]],field_path=r.get("field_path","content"),reveal_chapter_id=chapter_map.get(r.get("reveal_chapter_id")),visibility=r.get("visibility","작가전용")))
    for r in ext.get("relationships",[]):
        if r.get("source_entry_id") in entry_map and r.get("target_entry_id") in entry_map:db.session.add(EntryRelationship(world_id=world_id,source_entry_id=entry_map[r["source_entry_id"]],target_entry_id=entry_map[r["target_entry_id"]],relation_type=r.get("relation_type","관련"),description=r.get("description","")))
    for s in ext.get("secrets",[]):
        if s.get("entry_id") in entry_map:
            restored=WorldEntry.query.get(entry_map[s["entry_id"]]);restored.secret_content=str(s.get("secret") or "")[:10000]
    if not ext.get("templates"):_ensure_world_metadata(world_id,commit=False)

@app.route("/api/backup", methods=["GET"])
def backup_db():
    """전체 DB (모든 세계관) JSON 백업"""
    data = {
        "version": 4,
        "type": "full",
        "exported_at": datetime.utcnow().isoformat() + 'Z',
        "worlds": [w.to_dict() for w in World.query.order_by(World.id).all()],
        "entries": [e.to_dict() for e in WorldEntry.query.order_by(WorldEntry.id).all()],
        "configs": [c.to_dict() for c in SimulationConfig.query.order_by(SimulationConfig.id).all()],
        "snapshots": [s.to_dict(include_entries=True) for s in WorldSnapshot.query.order_by(WorldSnapshot.id).all()],
        "settings": AppSettings.get().to_dict(),
        "extensions": {str(w.id):_export_world_extensions(w.id) for w in World.query.all()},
    }
    filename = f"worldllm_full_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        _json.dumps(data, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/restore", methods=["POST"])
def restore_db():
    """전체 DB 복원 (모든 세계관 포함)"""
    data = request.json or {}
    worlds_data = data.get("worlds", [])
    entries_data = data.get("entries", [])
    configs_data = data.get("configs", [])
    settings_data = data.get("settings", {})
    extensions_data = data.get("extensions", {})

    for existing_world in World.query.all():
        _delete_world_extensions(existing_world.id)

    # 하위 테이블부터 비운 뒤 세계관을 교체한다. SQLite 외래키가 켜져 있어도 안전한 순서다.
    SimulationLog.query.delete()
    SimulationRun.query.delete()
    SimulationConfig.query.delete()
    WorldSnapshot.query.delete()
    TimelineEvent.query.delete()
    StoryBeat.query.delete()
    Timeline.query.delete()
    _delete_entry_dependents([row.id for row in WorldEntry.query.all()])
    WorldEntry.query.delete()
    db.session.flush()

    # 세계관 복원
    if worlds_data:
        World.query.delete()
        db.session.flush()
        for w in worlds_data:
            world = World(
                id=w.get("id"),
                name=w.get("name", "복원된 세계관"),
                description=w.get("description", ""),
            )
            db.session.add(world)

    # 엔트리 복원
    for e in entries_data:
        entry = WorldEntry(
            id=e.get("id"),
            world_id=e.get("world_id"),
            title=e.get("title", ""),
            category=e.get("category", "세력"),
            content=e.get("content", ""),
            references_json=_json.dumps(e.get("references", [])),
            created_by=e.get("created_by", "user"),
            tick_created=e.get("tick_created", 0),
            primary_year=e.get("primary_year", ""),
            year_notes_json=_json.dumps(e.get("year_notes", []),ensure_ascii=False),
            aliases_json=_json.dumps(e.get("aliases", []),ensure_ascii=False),
            is_active=e.get("is_active", True),
            is_summarized=e.get("is_summarized", False),
            keywords=e.get("keywords", ""),
            parent_entry_id=e.get("parent_entry_id"),
            version_note=e.get("version_note", ""),
            is_superseded=e.get("is_superseded", False),
        )
        db.session.add(entry)

    db.session.flush()
    entry_identity={e.get("id"):e.get("id") for e in entries_data if e.get("id") is not None}
    for world_row in worlds_data:
        wid=world_row.get("id")
        _restore_world_extensions(wid,extensions_data.get(str(wid),{}),entry_identity)

    # 시뮬레이션 설정 복원
    if configs_data:
        for c in configs_data:
            cfg = SimulationConfig(
                id=c.get("id"),
                world_id=c.get("world_id"),
                name=c.get("name", "복원된 설정"),
                prompt_level_1=c.get("prompt_level_1", ""),
                prompt_level_2=c.get("prompt_level_2", ""),
                prompt_level_3=c.get("prompt_level_3", ""),
                tick_count=c.get("tick_count", 10),
            )
            db.session.add(cfg)

    # 마스터 설정 복원
    if settings_data:
        s = AppSettings.get()
        if "max_llm_entry_chars" in settings_data:
            s.max_llm_entry_chars = settings_data["max_llm_entry_chars"]
        if "max_user_entry_chars" in settings_data:
            s.max_user_entry_chars = settings_data["max_user_entry_chars"]
        if "rag_token_budget" in settings_data:
            s.rag_token_budget = settings_data["rag_token_budget"]
        for field in ("entries_per_tick", "llm_max_output_tokens", "llm_model_simulation", "llm_model_nai",
                      "embedding_enabled", "embedding_model", "rag_reference_limit",
                      "llm_base_url", "embedding_base_url"):
            if field in settings_data:
                setattr(s, field, settings_data[field])

    db.session.commit()
    # 세션의 world_id 초기화 (복원 후 재선택 유도)
    session.pop("world_id", None)
    return jsonify({"ok": True, "worlds_restored": len(worlds_data),
                    "entries_restored": len(entries_data), "configs_restored": len(configs_data)})


# ─── 세계관별 백업 / 복원 ────────────────────────────────────────

@app.route("/api/worlds/<int:world_id>/backup", methods=["GET"])
def backup_world(world_id):
    """특정 세계관 데이터만 JSON 백업"""
    world = World.query.get_or_404(world_id)
    entries = WorldEntry.query.filter_by(world_id=world_id).order_by(WorldEntry.id).all()
    configs = SimulationConfig.query.filter_by(world_id=world_id).order_by(SimulationConfig.id).all()
    snapshots = WorldSnapshot.query.filter_by(world_id=world_id).order_by(WorldSnapshot.id).all()
    timelines = Timeline.query.filter_by(world_id=world_id).order_by(Timeline.id).all()
    data = {
        "version": 4,
        "type": "world",
        "exported_at": datetime.utcnow().isoformat() + 'Z',
        "world": world.to_dict(),
        "entries": [e.to_dict() for e in entries],
        "configs": [c.to_dict() for c in configs],
        "snapshots": [s.to_dict(include_entries=True) for s in snapshots],
        "timelines": [t.to_dict(include_events=True) for t in timelines],
        "extensions": _export_world_extensions(world_id),
    }
    safe_name = world.name.replace(" ", "_")[:20]
    filename = f"world_{safe_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        _json.dumps(data, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/worlds/<int:world_id>/restore", methods=["POST"])
def restore_world(world_id):
    """특정 세계관 데이터 복원 (해당 세계관 데이터만 교체)"""
    world = World.query.get_or_404(world_id)
    data = request.json or {}

    # 기존 세계관 데이터 삭제
    _delete_world_extensions(world_id)
    WorldEntry.query.filter_by(world_id=world_id).delete()
    SimulationConfig.query.filter_by(world_id=world_id).delete()
    WorldSnapshot.query.filter_by(world_id=world_id).delete()
    Timeline.query.filter_by(world_id=world_id).delete()
    db.session.flush()

    entries_data = data.get("entries", [])
    configs_data = data.get("configs", [])
    snapshots_data = data.get("snapshots", [])

    # 엔트리 복원 (ID는 새로 할당, world_id 강제 설정)
    id_map = {}  # old_id → new_entry (for parent_entry_id remapping)
    first_pass = []
    for e in entries_data:
        entry = WorldEntry(
            world_id=world_id,
            title=e.get("title", ""),
            category=e.get("category", "세력"),
            content=e.get("content", ""),
            references_json=_json.dumps(e.get("references", [])),
            created_by=e.get("created_by", "user"),
            tick_created=e.get("tick_created", 0),
            primary_year=e.get("primary_year", ""),
            year_notes_json=_json.dumps(e.get("year_notes", []),ensure_ascii=False),
            aliases_json=_json.dumps(e.get("aliases", []),ensure_ascii=False),
            is_active=e.get("is_active", True),
            is_summarized=e.get("is_summarized", False),
            keywords=e.get("keywords", ""),
            version_note=e.get("version_note", ""),
            is_superseded=e.get("is_superseded", False),
        )
        db.session.add(entry)
        db.session.flush()
        id_map[e.get("id")] = entry
        first_pass.append((e, entry))

    # parent_entry_id 재매핑
    for e, entry in first_pass:
        old_parent = e.get("parent_entry_id")
        if old_parent and old_parent in id_map:
            entry.parent_entry_id = id_map[old_parent].id
        entry.references=[id_map[x].id for x in e.get("references",[]) if x in id_map]

    _restore_world_extensions(world_id,data.get("extensions",{}),{old:new.id for old,new in id_map.items()})

    # 시뮬레이션 설정 복원
    for c in configs_data:
        cfg = SimulationConfig(
            world_id=world_id,
            name=c.get("name", "복원된 설정"),
            prompt_level_1=c.get("prompt_level_1", ""),
            prompt_level_2=c.get("prompt_level_2", ""),
            prompt_level_3=c.get("prompt_level_3", ""),
            tick_count=c.get("tick_count", 10),
        )
        db.session.add(cfg)

    # 스냅샷 복원
    for s in snapshots_data:
        snap = WorldSnapshot(
            world_id=world_id,
            name=s.get("name", "복원된 스냅샷"),
            description=s.get("description", ""),
            entries_json=_json.dumps(s.get("entries", [])),
            entry_count=s.get("entry_count", 0),
        )
        db.session.add(snap)

    db.session.commit()
    return jsonify({"ok": True, "entries_restored": len(entries_data),
                    "configs_restored": len(configs_data)})


# ─────────────────────────────────────────
#  시뮬레이션 설정 API
# ─────────────────────────────────────────

@app.route("/api/configs", methods=["GET"])
def list_configs():
    wid = get_world_id()
    q = SimulationConfig.query
    if wid:
        q = q.filter_by(world_id=wid)
    configs = q.order_by(SimulationConfig.created_at.desc()).all()
    return jsonify([c.to_dict() for c in configs])


@app.route("/api/configs", methods=["POST"])
def create_config():
    data = request.json
    if not data.get("name"):
        return jsonify({"error": "name은 필수입니다."}), 400
    config = SimulationConfig(
        world_id=get_world_id(),
        name=data["name"],
        prompt_level_1=data.get("prompt_level_1", ""),
        prompt_level_2=data.get("prompt_level_2", ""),
        prompt_level_3=data.get("prompt_level_3", ""),
        tick_count=int(data.get("tick_count", 10)),
    )
    db.session.add(config)
    db.session.commit()
    return jsonify(config.to_dict()), 201


@app.route("/api/configs/<int:config_id>", methods=["PUT"])
def update_config(config_id):
    config = SimulationConfig.query.get_or_404(config_id)
    data = request.json
    for field in ["name", "prompt_level_1", "prompt_level_2", "prompt_level_3", "tick_count"]:
        if field in data:
            setattr(config, field, data[field])
    db.session.commit()
    return jsonify(config.to_dict())


@app.route("/api/configs/<int:config_id>", methods=["DELETE"])
def delete_config(config_id):
    config = SimulationConfig.query.get_or_404(config_id)
    db.session.delete(config)
    db.session.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────
#  마스터 설정 API
# ─────────────────────────────────────────

def _normalize_tier_descriptions(item, min_tier, max_tier):
    """단계 범위의 모든 키를 보장하고 기존 tier_labels 입력도 받아들인다."""
    raw = item.get("tier_descriptions")
    if not isinstance(raw, dict):
        raw = item.get("tier_labels")
    if not isinstance(raw, dict):
        raw = {}
    return {
        str(tier): str(raw.get(str(tier), raw.get(tier, "")) or "").strip()[:2000]
        for tier in range(min_tier, max_tier + 1)
    }

@app.route("/api/metadata", methods=["GET"])
def get_metadata():
    wid=get_world_id(); _ensure_world_metadata(wid)
    attrs=WorldAttributeSchema.query.filter_by(world_id=wid).order_by(WorldAttributeSchema.axis_order).all()
    skills=WorldSkillRegistry.query.filter_by(world_id=wid).order_by(WorldSkillRegistry.name).all()
    templates=WorldEntryTemplate.query.filter_by(world_id=wid).order_by(WorldEntryTemplate.category).all()
    guide=WorldGuideline.query.get(wid)
    characters=WorldEntry.query.filter_by(world_id=wid,category="인물",is_active=True).all()
    values=EntryAttributeValue.query.filter(EntryAttributeValue.entry_id.in_([e.id for e in characters] or [-1])).all()
    return jsonify({"attributes":[a.to_dict() for a in attrs],"skills":[s.to_dict() for s in skills],"templates":[t.to_dict() for t in templates],"guideline":guide.to_dict(),"characters":[e.to_dict() for e in characters],"attribute_values":[{"entry_id":v.entry_id,"axis_id":v.axis_id,"value":v.value,"description":v.description or ""} for v in values]})

@app.route("/api/metadata/attributes", methods=["PUT"])
def save_attributes():
    wid=get_world_id(); data=request.json or {}; seen=[]
    for order,item in enumerate(data.get("attributes") or []):
        row=WorldAttributeSchema.query.filter_by(id=item.get("id"),world_id=wid).first() if item.get("id") else WorldAttributeSchema(world_id=wid)
        row.axis_name=str(item.get("axis_name") or "새 능력치")[:100]; row.axis_order=order; row.min_tier=max(0,min(100,int(item.get("min_tier",1)))); row.max_tier=max(row.min_tier,min(100,int(item.get("max_tier",5)))); row.description=str(item.get("description") or "")[:2000]; row.tier_labels_json=_json.dumps(_normalize_tier_descriptions(item,row.min_tier,row.max_tier),ensure_ascii=False); row.is_active=bool(item.get("is_active",True)); db.session.add(row); db.session.flush(); seen.append(row.id)
    removed=[x.id for x in WorldAttributeSchema.query.filter_by(world_id=wid).filter(~WorldAttributeSchema.id.in_(seen or [-1])).all()]
    if removed:EntryAttributeValue.query.filter(EntryAttributeValue.axis_id.in_(removed)).delete(synchronize_session=False)
    WorldAttributeSchema.query.filter_by(world_id=wid).filter(~WorldAttributeSchema.id.in_(seen or [-1])).delete(synchronize_session=False); db.session.commit()
    return jsonify({"ok":True})

@app.route("/api/metadata/attribute-values", methods=["PUT"])
def save_attribute_values():
    wid=get_world_id()
    for item in (request.json or {}).get("values",[]):
        entry=WorldEntry.query.get(item.get("entry_id")); axis=WorldAttributeSchema.query.get(item.get("axis_id"))
        if not entry or not axis or entry.world_id!=wid or axis.world_id!=wid: continue
        row=EntryAttributeValue.query.get((entry.id,axis.id)) or EntryAttributeValue(entry_id=entry.id,axis_id=axis.id)
        row.value=max(axis.min_tier,min(axis.max_tier,int(item.get("value",axis.min_tier)))); row.description=str(item.get("description") or "")[:4000]; db.session.add(row)
    db.session.commit(); return jsonify({"ok":True})

@app.route("/api/metadata/skills", methods=["POST"])
def create_skill():
    wid=get_world_id(); d=request.json or {}; name=str(d.get("name") or "").strip()
    if not name:return jsonify({"error":"name은 필수입니다."}),400
    row=WorldSkillRegistry(world_id=wid,type=d.get("type") if d.get("type") in ("스킬","특성") else "스킬",name=name[:150],description=d.get("description","") ,tags_json=_json.dumps(d.get("tags") or [],ensure_ascii=False),rarity=d.get("rarity","일반"),created_by="user")
    db.session.add(row); db.session.commit()
    try:
        settings=UserLlmSettings.get_for_user(current_user().id)
        if settings.embedding_enabled:
            import llm_client
            row.embedding_json=_json.dumps(llm_client.generate_embedding(f"{row.type} {row.name} {row.description} {' '.join(_json.loads(row.tags_json))}",settings.embedding_model));db.session.commit()
    except Exception: db.session.rollback()
    return jsonify(row.to_dict()),201

@app.route("/api/metadata/skills/<int:skill_id>", methods=["PUT","DELETE"])
def change_skill(skill_id):
    row=WorldSkillRegistry.query.get_or_404(skill_id)
    if row.world_id!=get_world_id():abort(403)
    if request.method=="DELETE": EntrySkillLink.query.filter_by(skill_id=row.id).delete();db.session.delete(row);db.session.commit();return jsonify({"ok":True})
    d=request.json or {}; row.type=d.get("type",row.type);row.name=str(d.get("name",row.name))[:150];row.description=d.get("description",row.description);row.tags_json=_json.dumps(d.get("tags",[]),ensure_ascii=False);row.rarity=d.get("rarity",row.rarity);db.session.commit();return jsonify(row.to_dict())

@app.route("/api/metadata/guideline", methods=["PUT"])
def save_guideline():
    wid=get_world_id(); row=WorldGuideline.query.get(wid) or WorldGuideline(world_id=wid);d=request.json or {};row.skill_generation_guide=d.get("skill_generation_guide","");row.trait_generation_guide=d.get("trait_generation_guide","");db.session.add(row);db.session.commit();return jsonify(row.to_dict())

@app.route("/api/metadata/templates/<int:template_id>", methods=["PUT"])
def save_entry_template(template_id):
    row=WorldEntryTemplate.query.get_or_404(template_id)
    if row.world_id!=get_world_id():abort(403)
    d=request.json or {};row.fields_json=_json.dumps(d.get("fields") or [],ensure_ascii=False);row.is_active=bool(d.get("is_active",True));db.session.commit();return jsonify(row.to_dict())

@app.route("/api/metadata/migration-plan", methods=["POST"])
def metadata_migration_plan():
    wid=get_world_id();data=request.json or {};ids=data.get("entry_ids") or [];axis_names=data.get("axis_names") or []
    q=WorldEntry.query.filter_by(world_id=wid,is_active=True,category="인물")
    if ids:q=q.filter(WorldEntry.id.in_(ids))
    entries=[e.to_dict() for e in q.limit(30).all()]
    if not entries:return jsonify({"error":"자동 배정할 활성 인물을 찾지 못했습니다."}),400
    try:
        import llm_client
        axes_q=WorldAttributeSchema.query.filter_by(world_id=wid,is_active=True)
        if axis_names:axes_q=axes_q.filter(WorldAttributeSchema.axis_name.in_([str(x)[:100] for x in axis_names]))
        required_axes=[a.to_dict() for a in axes_q.order_by(WorldAttributeSchema.axis_order).all()]
        if not required_axes:return jsonify({"error":"자동 배정할 활성 능력치 축을 찾지 못했습니다."}),400
        return jsonify(llm_client.propose_character_attribute_fill(entries,required_axes,str(data.get("instruction") or "")[:2000]))
    except Exception as e:return jsonify({"error":str(e)}),502


@app.route("/api/metadata/schema-fill-plan", methods=["POST"])
def metadata_schema_fill_plan():
    """현재 축 이름·범위를 고정한 채 설명과 모든 단계 서술만 제안한다."""
    wid=get_world_id();data=request.json or {};axis_names=data.get("axis_names") or []
    q=WorldAttributeSchema.query.filter_by(world_id=wid,is_active=True)
    if axis_names:q=q.filter(WorldAttributeSchema.axis_name.in_([str(x)[:100] for x in axis_names]))
    axes=[a.to_dict() for a in q.order_by(WorldAttributeSchema.axis_order).all()]
    if not axes:return jsonify({"error":"선택한 활성 능력치 축을 찾지 못했습니다."}),400
    try:
        import llm_client
        return jsonify(llm_client.propose_attribute_schema_fill(axes,llm_client._world_metadata_rules(wid),str(data.get("instruction") or "")[:2000]))
    except Exception as e:return jsonify({"error":str(e)}),502

@app.route("/api/metadata/migration-apply", methods=["POST"])
def metadata_migration_apply():
    wid=get_world_id();data=request.json or {};count=0;created_schemas=0;updated_schemas=0;skipped=[]
    for order,item in enumerate(data.get("attribute_schemas") or []):
        name=str(item.get("axis_name") or "").strip()[:100]
        if not name:
            skipped.append({"type":"schema","reason":"axis_name이 비어 있음"});continue
        row=WorldAttributeSchema.query.filter_by(world_id=wid,axis_name=name).first()
        existed=bool(row)
        if not row:row=WorldAttributeSchema(world_id=wid,axis_name=name)
        try:
            min_tier=max(0,min(100,int(item.get("min_tier",1))))
            max_tier=max(min_tier,min(100,int(item.get("max_tier",5))))
        except (TypeError,ValueError):
            skipped.append({"type":"schema","name":name,"reason":"최소/최대 단계가 숫자가 아님"});continue
        row.axis_order=order;row.min_tier=min_tier;row.max_tier=max_tier;row.description=str(item.get("description") or "")[:2000];row.tier_labels_json=_json.dumps(_normalize_tier_descriptions(item,row.min_tier,row.max_tier),ensure_ascii=False);row.is_active=True;db.session.add(row)
        if existed:updated_schemas+=1
        else:created_schemas+=1
    db.session.flush()
    axes=WorldAttributeSchema.query.filter_by(world_id=wid,is_active=True).all()
    axis_by_key={"".join(a.axis_name.split()).casefold():a for a in axes}
    for item in data.get("entries",[]):
        entry=WorldEntry.query.get(item.get("entry_id"))
        if not entry or entry.world_id!=wid:
            skipped.append({"type":"entry","entry_id":item.get("entry_id"),"reason":"현재 세계관의 인물을 찾지 못함"});continue
        attributes=item.get("attributes") or {}
        if not isinstance(attributes,dict):
            skipped.append({"type":"entry","entry_id":entry.id,"reason":"attributes가 객체 형식이 아님"});continue
        for name,value_data in attributes.items():
            axis=axis_by_key.get("".join(str(name).split()).casefold())
            if not axis:
                skipped.append({"type":"attribute","entry_id":entry.id,"name":str(name),"reason":"일치하는 활성 능력치 축이 없음"});continue
            value=value_data.get("value",axis.min_tier) if isinstance(value_data,dict) else value_data
            description=value_data.get("description","") if isinstance(value_data,dict) else ""
            try:value=max(axis.min_tier,min(axis.max_tier,int(value)))
            except (TypeError,ValueError):
                skipped.append({"type":"attribute","entry_id":entry.id,"name":str(name),"reason":f"수치가 정수가 아님: {value}"});continue
            row=EntryAttributeValue.query.get((entry.id,axis.id)) or EntryAttributeValue(entry_id=entry.id,axis_id=axis.id);row.value=value;row.description=str(description or "")[:4000];db.session.add(row);count+=1
        for sid in item.get("reused_skill_ids") or []:
            if WorldSkillRegistry.query.filter_by(id=sid,world_id=wid).first() and not EntrySkillLink.query.get((entry.id,sid)):db.session.add(EntrySkillLink(entry_id=entry.id,skill_id=sid))
    db.session.commit();return jsonify({"ok":True,"applied":count,"created_schemas":created_schemas,"updated_schemas":updated_schemas,"skipped":skipped})

@app.route("/api/entries/<int:entry_id>/skills", methods=["GET","PUT"])
def entry_skills(entry_id):
    entry=WorldEntry.query.get_or_404(entry_id)
    if entry.world_id!=get_world_id():abort(403)
    if request.method=="PUT":
        EntrySkillLink.query.filter_by(entry_id=entry.id).delete()
        for x in (request.json or {}).get("skills",[]):
            skill=WorldSkillRegistry.query.get(x.get("skill_id"))
            if skill and skill.world_id==entry.world_id:db.session.add(EntrySkillLink(entry_id=entry.id,skill_id=skill.id,rank=x.get("rank")))
        db.session.commit()
    links=EntrySkillLink.query.filter_by(entry_id=entry.id).all(); return jsonify([{"skill":WorldSkillRegistry.query.get(x.skill_id).to_dict(),"rank":x.rank} for x in links if WorldSkillRegistry.query.get(x.skill_id)])

@app.route("/api/entries/<int:entry_id>/stat-block", methods=["GET"])
def entry_stat_block(entry_id):
    entry=WorldEntry.query.get_or_404(entry_id)
    if entry.world_id!=get_world_id():abort(403)
    values=EntryAttributeValue.query.filter_by(entry_id=entry.id).all();attributes=[]
    for v in values:
        axis=WorldAttributeSchema.query.get(v.axis_id)
        if axis:
            try:labels=_json.loads(axis.tier_labels_json or "{}")
            except Exception:labels={}
            tier_description=labels.get(str(v.value),"")
            attributes.append({"axis_id":axis.id,"axis_name":axis.axis_name,"value":v.value,"tier_description":tier_description,"label":tier_description,"description":v.description or "","axis_description":axis.description or "","min_tier":axis.min_tier,"max_tier":axis.max_tier})
    links=EntrySkillLink.query.filter_by(entry_id=entry.id).all();skills=[]
    for link in links:
        skill=WorldSkillRegistry.query.get(link.skill_id)
        if skill:skills.append({"skill":skill.to_dict(),"rank":link.rank})
    return jsonify({"attributes":attributes,"skills":skills})

@app.route("/api/entries/<int:entry_id>/skill-proposals", methods=["POST"])
def skill_proposals(entry_id):
    entry=WorldEntry.query.get_or_404(entry_id);wid=get_world_id()
    if entry.world_id!=wid:abort(403)
    skills=WorldSkillRegistry.query.filter_by(world_id=wid).all();settings=UserLlmSettings.get_for_user(current_user().id)
    query_words=set(re.findall(r"[0-9A-Za-z가-힣_]{2,}",(entry.title+" "+entry.category+" "+entry.content[:2000]).lower()))
    def keyword_score(skill):
        try: tags=" ".join(_json.loads(skill.tags_json or "[]"))
        except Exception: tags=""
        words=set(re.findall(r"[0-9A-Za-z가-힣_]{2,}",(skill.name+" "+skill.description+" "+tags).lower()))
        return len(query_words & words)
    candidates=sorted(skills,key=lambda s:(keyword_score(s),s.name),reverse=True)[:30]
    if settings.embedding_enabled:
        try:
            import llm_client
            vec=llm_client.generate_embedding(entry.title+" "+entry.content[:1000],settings.embedding_model);scored=[]
            for s in skills:
                score=llm_client.cosine_similarity(vec,_json.loads(s.embedding_json or "[]"));scored.append((score,s))
            candidates=[s for _,s in sorted(scored,key=lambda x:x[0],reverse=True)[:20]]
        except Exception:pass
    guide=WorldGuideline.query.get(wid) or WorldGuideline(world_id=wid)
    try:
        import llm_client
        return jsonify(llm_client.propose_entry_skills(entry.to_dict(),[s.to_dict() for s in candidates],guide.to_dict()))
    except Exception as e:return jsonify({"error":str(e)}),502

def _tag_chapter_entities(chapter):
    NovelEntityMention.query.filter_by(chapter_id=chapter.id,source="keyword").delete()
    part=NovelPart.query.get(chapter.part_id);selected_ids=part.entry_ids if part else []
    entries=WorldEntry.query.filter_by(world_id=chapter.world_id,is_active=True).filter(WorldEntry.id.in_(selected_ids or [-1])).all(); content=chapter.content or "";folded=content.casefold()
    trie={}
    for entry in entries:
        try: aliases=_json.loads(entry.aliases_json or "[]")
        except Exception: aliases=[]
        for name in [entry.title]+aliases:
            if not name:continue
            node=trie
            for char in str(name).casefold():node=node.setdefault(char,{})
            node.setdefault("_matches",[]).append((entry.id,len(str(name))))
    occupied=[];found=[]
    for start in range(len(folded)):
        node=trie;end=start;best=None
        while end<len(folded) and folded[end] in node:
            node=node[folded[end]];end+=1
            for eid,length in node.get("_matches",[]):
                best=(start,end,eid,length)
        if best:found.append(best)
    for start,end,eid,_ in sorted(found,key=lambda x:(x[0],-(x[1]-x[0]))):
        if any(start<b and end>a for a,b in occupied):continue
        occupied.append((start,end));db.session.add(NovelEntityMention(chapter_id=chapter.id,entry_id=eid,span_start=start,span_end=end,matched_text=content[start:end],source="keyword"))

def _ensure_default_novel_part(world_id):
    part=NovelPart.query.filter_by(world_id=world_id).order_by(NovelPart.order_no,NovelPart.id).first()
    if not part:
        part=NovelPart(world_id=world_id,title="기본 이야기",order_no=1)
        db.session.add(part);db.session.flush()
    NovelChapter.query.filter_by(world_id=world_id,part_id=None).update({"part_id":part.id},synchronize_session=False)
    db.session.commit()
    return part


def _valid_novel_entry_ids(world_id, values):
    try:requested=list(dict.fromkeys(int(x) for x in (values or [])))[:500]
    except (TypeError,ValueError):return []
    if not requested:return []
    valid={x.id for x in WorldEntry.query.filter_by(world_id=world_id,is_active=True).filter(WorldEntry.id.in_(requested)).all()}
    return [x for x in requested if x in valid]


@app.route("/api/novel/entry-catalog", methods=["GET"])
def novel_entry_catalog():
    """이야기/부에서 참조할 DB 엔트리를 검색하거나 선택 목록을 다시 읽는다."""
    wid=get_world_id();raw_ids=request.args.get("ids","");q=WorldEntry.query.filter_by(world_id=wid,is_active=True)
    if raw_ids:
        ids=_valid_novel_entry_ids(wid,[x for x in raw_ids.split(",") if x.strip()])
        q=q.filter(WorldEntry.id.in_(ids or [-1]))
    else:
        term=str(request.args.get("q") or "").strip()[:100]
        if term:
            like=f"%{term}%";q=q.filter(db.or_(WorldEntry.title.ilike(like),WorldEntry.category.ilike(like),WorldEntry.keywords.ilike(like)))
        else:return jsonify([])
    return jsonify([{"id":e.id,"title":e.title,"category":e.category} for e in q.order_by(WorldEntry.title).limit(80).all()])


@app.route("/api/novel/parts", methods=["GET","POST"])
def novel_parts():
    wid=get_world_id();_ensure_default_novel_part(wid)
    if request.method=="GET":
        return jsonify([p.to_dict(with_count=True) for p in NovelPart.query.filter_by(world_id=wid).order_by(NovelPart.order_no,NovelPart.id).all()])
    d=request.json or {};order=db.session.query(db.func.max(NovelPart.order_no)).filter_by(world_id=wid).scalar() or 0
    part=NovelPart(world_id=wid,title=str(d.get("title") or "새 이야기")[:250],description=str(d.get("description") or ""),order_no=order+1);part.entry_ids=_valid_novel_entry_ids(wid,d.get("entry_ids") or [])
    db.session.add(part);db.session.commit();return jsonify(part.to_dict(with_count=True)),201


@app.route("/api/novel/parts/<int:part_id>", methods=["GET","PUT","DELETE"])
def novel_part(part_id):
    part=NovelPart.query.get_or_404(part_id)
    if part.world_id!=get_world_id():abort(403)
    if request.method=="DELETE":
        fallback=NovelPart.query.filter_by(world_id=part.world_id).filter(NovelPart.id!=part.id).order_by(NovelPart.order_no,NovelPart.id).first()
        if not fallback:return jsonify({"error":"이야기/부는 최소 한 개가 필요합니다."}),400
        NovelChapter.query.filter_by(part_id=part.id).update({"part_id":fallback.id},synchronize_session=False)
        db.session.delete(part);db.session.commit();return jsonify({"ok":True,"moved_to":fallback.id})
    if request.method=="PUT":
        d=request.json or {};part.title=str(d.get("title",part.title) or "새 이야기")[:250];part.description=str(d.get("description",part.description) or "");part.order_no=int(d.get("order_no",part.order_no))
        if "entry_ids" in d:part.entry_ids=_valid_novel_entry_ids(part.world_id,d.get("entry_ids") or [])
        db.session.commit()
    return jsonify(part.to_dict(with_count=True))


@app.route("/api/novel/parts/<int:part_id>/publish", methods=["POST"])
def publish_novel_part(part_id):
    part=NovelPart.query.get_or_404(part_id)
    if part.world_id!=get_world_id():abort(403)
    enabled=bool((request.json or {}).get("public",True));part.is_public=enabled
    if enabled and not part.public_token:part.public_token=uuid.uuid4().hex
    db.session.commit()
    return jsonify({"ok":True,"is_public":bool(part.is_public),"url":url_for("public_novel_part",token=part.public_token,_external=True) if enabled else None})


@app.route("/api/novel/chapters", methods=["GET","POST"])
def novel_chapters():
    wid=get_world_id();default_part=_ensure_default_novel_part(wid);part_id=request.args.get("part_id",type=int)
    if request.method=="GET":
        q=NovelChapter.query.filter_by(world_id=wid)
        if part_id:q=q.filter_by(part_id=part_id)
        return jsonify([c.to_dict() for c in q.order_by(NovelChapter.order_no,NovelChapter.id).all()])
    d=request.json or {};part=NovelPart.query.filter_by(id=d.get("part_id"),world_id=wid).first() or default_part
    order=db.session.query(db.func.max(NovelChapter.order_no)).filter_by(world_id=wid,part_id=part.id).scalar() or 0
    c=NovelChapter(world_id=wid,part_id=part.id,title=d.get("title","새 챕터"),order_no=order+1,content=d.get("content",""),status=d.get("status","초안"));db.session.add(c);db.session.commit();return jsonify(c.to_dict()),201

@app.route("/api/novel/chapters/<int:chapter_id>", methods=["GET","PUT","DELETE"])
def novel_chapter(chapter_id):
    c=NovelChapter.query.get_or_404(chapter_id)
    if c.world_id!=get_world_id():abort(403)
    if request.method=="DELETE":NovelEntityMention.query.filter_by(chapter_id=c.id).delete();EntryRevealState.query.filter_by(reveal_chapter_id=c.id).update({"reveal_chapter_id":None});db.session.delete(c);db.session.commit();return jsonify({"ok":True})
    if request.method=="PUT":
        d=request.json or {};c.title=d.get("title",c.title);c.content=d.get("content",c.content);c.status=d.get("status",c.status);c.order_no=int(d.get("order_no",c.order_no));c.reveal_chapter_ref=d.get("reveal_chapter_ref",c.reveal_chapter_ref)
        if "part_id" in d:
            part=NovelPart.query.filter_by(id=d.get("part_id"),world_id=c.world_id).first()
            if part:c.part_id=part.id
        _tag_chapter_entities(c);db.session.commit()
    mentions=NovelEntityMention.query.filter_by(chapter_id=c.id).all();out=c.to_dict();enriched=[]
    for m in mentions:
        item=m.to_dict();entry=WorldEntry.query.get(m.entry_id)
        item["entry_title"]=entry.title if entry else item.get("matched_text","");item["category"]=entry.category if entry else "관념";enriched.append(item)
    out["mentions"]=enriched;return jsonify(out)


@app.route("/api/novel/chapters/<int:chapter_id>/summarize", methods=["POST"])
def summarize_chapter(chapter_id):
    c=NovelChapter.query.get_or_404(chapter_id)
    if c.world_id!=get_world_id():abort(403)
    if not (c.content or "").strip():return jsonify({"error":"본문이 비어 있습니다."}),400
    try:
        import llm_client
        c.summary=llm_client.summarize_chapter(c.title,c.content);db.session.commit()
        return jsonify(c.to_dict())
    except Exception as e:return jsonify({"error":str(e)}),502


@app.route("/api/novel/chapters/<int:chapter_id>/publish", methods=["POST"])
def publish_novel_chapter(chapter_id):
    chapter=NovelChapter.query.get_or_404(chapter_id)
    if chapter.world_id!=get_world_id():abort(403)
    enabled=bool((request.json or {}).get("public",True));chapter.is_public=enabled
    if enabled:
        chapter.status="공개"
        if not chapter.public_token:chapter.public_token=uuid.uuid4().hex
    db.session.commit()
    return jsonify({"ok":True,"is_public":bool(chapter.is_public),"url":url_for("public_novel_chapter",token=chapter.public_token,_external=True) if enabled else None})

@app.route("/api/novel/style", methods=["GET","PUT"])
def novel_style():
    """이전 클라이언트 호환용. 문체는 이제 마스터 설정 한 곳에만 저장한다."""
    s=AppSettings.get();d=(request.json or {}) if request.method=="PUT" else {}
    if request.method=="PUT":
        s.novel_pov=str(d.get("pov",s.novel_pov) or "3인칭 관찰자")[:100];s.novel_tone_guide=str(d.get("tone_guide",s.novel_tone_guide) or "");s.novel_forbidden_expressions=str(d.get("forbidden_expressions",s.novel_forbidden_expressions) or "");s.novel_sample_text=str(d.get("sample_text",s.novel_sample_text) or "");db.session.commit()
    return jsonify({"world_id":get_world_id(),"chapter_id":None,"pov":s.novel_pov or "3인칭 관찰자","tone_guide":s.novel_tone_guide or "","forbidden_expressions":s.novel_forbidden_expressions or "","sample_text":s.novel_sample_text or ""})

@app.route("/api/novel/chapters/<int:chapter_id>/mentions", methods=["POST"])
def add_manual_mention(chapter_id):
    c=NovelChapter.query.get_or_404(chapter_id);d=request.json or {};e=WorldEntry.query.get_or_404(d.get("entry_id"))
    if c.world_id!=get_world_id() or e.world_id!=c.world_id:abort(403)
    part=NovelPart.query.get(c.part_id)
    if not part or e.id not in part.entry_ids:return jsonify({"error":"이 이야기/부의 참조 DB 목록에 없는 엔트리입니다."}),400
    source=d.get("source") if d.get("source") in ("manual","llm") else "manual";span_start=int(d.get("span_start",0));span_end=int(d.get("span_end",0))
    existing=NovelEntityMention.query.filter_by(chapter_id=c.id,entry_id=e.id,span_start=span_start,span_end=span_end).first()
    if existing:return jsonify(existing.to_dict()),200
    row=NovelEntityMention(chapter_id=c.id,entry_id=e.id,span_start=span_start,span_end=span_end,matched_text=d.get("matched_text",""),source=source);db.session.add(row);db.session.commit();return jsonify(row.to_dict()),201

@app.route("/api/novel/chapters/<int:chapter_id>/mention-proposals", methods=["POST"])
def mention_proposals(chapter_id):
    c=NovelChapter.query.get_or_404(chapter_id)
    if c.world_id!=get_world_id():abort(403)
    try:
        import llm_client
        part=NovelPart.query.get(c.part_id);selected_ids=part.entry_ids if part else []
        entries=[e.to_dict() for e in WorldEntry.query.filter_by(world_id=c.world_id,is_active=True).filter(WorldEntry.id.in_(selected_ids or [-1])).limit(100).all()]
        if not entries:return jsonify({"mentions":[]})
        return jsonify(llm_client.propose_novel_mentions(c.content or "",entries))
    except Exception as e:return jsonify({"error":str(e)}),502

@app.route("/api/entries/<int:entry_id>/reveal", methods=["GET","PUT"])
def entry_reveal(entry_id):
    entry=WorldEntry.query.get_or_404(entry_id)
    if entry.world_id!=get_world_id():abort(403)
    if request.method=="PUT":
        EntryRevealState.query.filter_by(entry_id=entry.id).delete()
        for x in (request.json or {}).get("states",[]):
            visibility=x.get("visibility","작가전용")
            if visibility not in ("작가전용","챕터공개","완전공개"):continue
            reveal_chapter_id=x.get("reveal_chapter_id") if visibility=="챕터공개" else None
            chapter=NovelChapter.query.get(reveal_chapter_id) if reveal_chapter_id else None
            if reveal_chapter_id and (not chapter or chapter.world_id!=entry.world_id):continue
            field_path=str(x.get("field_path") or "content").strip()[:150]
            db.session.add(EntryRevealState(entry_id=entry.id,field_path=field_path,reveal_chapter_id=reveal_chapter_id,visibility=visibility))
        db.session.commit()
    return jsonify([x.to_dict() for x in EntryRevealState.query.filter_by(entry_id=entry.id).all()])

@app.route("/api/novel/generate", methods=["POST"])
def novel_generate():
    d=request.json or {};chapter=NovelChapter.query.get(d.get("chapter_id")) if d.get("chapter_id") else None
    try:
        import llm_client
        chapter_data=chapter.to_dict() if chapter else None
        selected_ids=[]
        if chapter_data:
            part=NovelPart.query.get(chapter.part_id)
            chapter_data["part"]={"id":part.id,"title":part.title,"description":part.description or ""} if part else None
            selected_ids=part.entry_ids if part else []
        mode=d.get("mode") or "continue"
        context_mode=d.get("context_mode") if d.get("context_mode") in ("none","summary","full") else "summary"
        ref_chapter_ids=d.get("ref_chapter_ids") if isinstance(d.get("ref_chapter_ids"),list) else None
        result=llm_client.generate_novel_text(get_world_id(),d.get("instruction",""),chapter_data,selected_ids,mode=mode,context_text=d.get("context",""),situation_text=d.get("situation",""),context_mode=context_mode,ref_chapter_ids=ref_chapter_ids)
        result["new_entity_proposals"]=[x for x in result.get("new_entity_proposals",[]) if x.get("title") and x.get("content") and x.get("category") in CATEGORIES]
        return jsonify(result)
    except Exception as e:return jsonify({"error":str(e)}),502


def _public_field_visible(entry_id, field_path, chapter):
    states=EntryRevealState.query.filter_by(entry_id=entry_id).all()
    state=next((x for x in states if x.field_path==field_path),None) or next((x for x in states if field_path.startswith(x.field_path+".")),None)
    if not state or state.visibility=="완전공개":return True
    if state.visibility=="작가전용" or not state.reveal_chapter_id:return False
    reveal=NovelChapter.query.get(state.reveal_chapter_id)
    if not reveal or reveal.world_id!=chapter.world_id:return False
    reveal_part=NovelPart.query.get(reveal.part_id);current_part=NovelPart.query.get(chapter.part_id)
    reveal_pos=((reveal_part.order_no if reveal_part else 0),reveal.order_no,reveal.id)
    current_pos=((current_part.order_no if current_part else 0),chapter.order_no,chapter.id)
    return current_pos>=reveal_pos


def _public_chapter_payload(chapter):
    payload=chapter.to_dict();payload.pop("public_token",None);entities=[];seen=set()
    mentions=NovelEntityMention.query.filter_by(chapter_id=chapter.id).order_by(NovelEntityMention.span_start).all()
    for mention in mentions:
        if mention.entry_id in seen:continue
        seen.add(mention.entry_id);entry=WorldEntry.query.get(mention.entry_id)
        if not entry or entry.world_id!=chapter.world_id or not entry.is_active:continue
        content_visible=_public_field_visible(entry.id,"content",chapter)
        attrs=[]
        for value in EntryAttributeValue.query.filter_by(entry_id=entry.id).all():
            axis=WorldAttributeSchema.query.get(value.axis_id)
            if axis and axis.is_active and _public_field_visible(entry.id,"stat_block."+axis.axis_name,chapter):
                try:labels=_json.loads(axis.tier_labels_json or "{}")
                except Exception:labels={}
                attrs.append({"name":axis.axis_name,"value":value.value,"description":value.description or "","tier_description":labels.get(str(value.value),"")})
        skills=[]
        if _public_field_visible(entry.id,"stat_block.skills",chapter):
            for link in EntrySkillLink.query.filter_by(entry_id=entry.id).all():
                skill=WorldSkillRegistry.query.get(link.skill_id)
                if skill:skills.append({"name":skill.name,"rank":link.rank,"description":skill.description or ""})
        entities.append({"id":entry.id,"title":entry.title,"category":entry.category,"content":entry.content if content_visible else "","content_visible":content_visible,"primary_year":entry.primary_year or "","aliases":entry.to_dict().get("aliases",[]),"image_url":url_for("serve_storage",filename=entry.image_filename) if entry.image_filename else None,"attributes":attrs,"skills":skills})
    payload["entities"]=entities;return payload


@app.route("/read/chapter/<token>")
def public_novel_chapter(token):
    chapter=NovelChapter.query.filter_by(public_token=token,is_public=True).first_or_404()
    part=NovelPart.query.get(chapter.part_id);world=World.query.get(chapter.world_id)
    return render_template("public_novel.html",world=world,part=part,chapters=[_public_chapter_payload(chapter)],share_scope="chapter")


@app.route("/read/part/<token>")
def public_novel_part(token):
    part=NovelPart.query.filter_by(public_token=token,is_public=True).first_or_404();world=World.query.get(part.world_id)
    chapters=NovelChapter.query.filter_by(world_id=part.world_id,part_id=part.id).order_by(NovelChapter.order_no,NovelChapter.id).all()
    return render_template("public_novel.html",world=world,part=part,chapters=[_public_chapter_payload(c) for c in chapters],share_scope="part")

@app.route("/api/settings", methods=["GET"])
def get_settings():
    base = AppSettings.get().to_dict()
    user_settings = UserLlmSettings.get_for_user(current_user().id)
    base.update(user_settings.to_dict())
    base["llm_model_simulation"] = user_settings.llm_model or base["llm_model_simulation"]
    base["llm_model_nai"] = user_settings.nai_model or base["llm_model_nai"]
    return jsonify(base)


@app.route("/api/settings", methods=["PUT"])
def update_settings():
    data = request.json or {}
    s = AppSettings.get()
    us = UserLlmSettings.get_for_user(current_user().id)
    if "max_llm_entry_chars" in data:
        s.max_llm_entry_chars = max(0, int(data["max_llm_entry_chars"]))
    if "llm_max_output_tokens" in data:
        s.llm_max_output_tokens = max(1, min(262144, int(data["llm_max_output_tokens"])))
    if "entries_per_tick" in data:
        s.entries_per_tick = max(0, min(20, int(data["entries_per_tick"])))
    if "max_user_entry_chars" in data:
        s.max_user_entry_chars = int(data["max_user_entry_chars"])
    if "rag_token_budget" in data:
        s.rag_token_budget = int(data["rag_token_budget"])
    if "llm_model_simulation" in data and str(data["llm_model_simulation"]).strip():
        s.llm_model_simulation = str(data["llm_model_simulation"]).strip()[:100]
    if "llm_model_nai" in data and str(data["llm_model_nai"]).strip():
        s.llm_model_nai = str(data["llm_model_nai"]).strip()[:100]
    if "embedding_enabled" in data: s.embedding_enabled = bool(data["embedding_enabled"])
    if "embedding_model" in data: s.embedding_model = str(data["embedding_model"]).strip()[:200]
    if "rag_reference_limit" in data: s.rag_reference_limit = max(1, min(50, int(data["rag_reference_limit"])))
    if "novel_pov" in data:s.novel_pov=str(data["novel_pov"] or "3인칭 관찰자")[:100]
    if "novel_tone_guide" in data:s.novel_tone_guide=str(data["novel_tone_guide"] or "")
    if "novel_forbidden_expressions" in data:s.novel_forbidden_expressions=str(data["novel_forbidden_expressions"] or "")
    if "novel_sample_text" in data:s.novel_sample_text=str(data["novel_sample_text"] or "")
    if "llm_base_url" in data: us.llm_base_url = str(data["llm_base_url"]).strip()[:500]
    if "llm_provider" in data: us.llm_provider = str(data["llm_provider"]).strip()[:50]
    if "embedding_base_url" in data: us.embedding_base_url = str(data["embedding_base_url"]).strip()[:500]
    # 빈 값은 기존 키 유지, clear_* 플래그로만 명시적으로 제거한다.
    if str(data.get("llm_api_key") or "").strip(): us.llm_api_key = str(data["llm_api_key"]).strip()
    if str(data.get("embedding_api_key") or "").strip(): us.embedding_api_key = str(data["embedding_api_key"]).strip()
    if data.get("clear_llm_api_key"): us.llm_api_key = ""
    if data.get("clear_embedding_api_key"): us.embedding_api_key = ""
    if "llm_model_simulation" in data: us.llm_model = str(data["llm_model_simulation"]).strip()[:200]
    if "llm_model_nai" in data: us.nai_model = str(data["llm_model_nai"]).strip()[:200]
    if str(data.get("novelai_api_key") or "").strip(): us.novelai_api_key = str(data["novelai_api_key"]).strip()
    if data.get("clear_novelai_api_key"): us.novelai_api_key = ""
    if "embedding_enabled" in data: us.embedding_enabled = bool(data["embedding_enabled"])
    if "embedding_model" in data: us.embedding_model = str(data["embedding_model"]).strip()[:200]
    if "rag_reference_limit" in data: us.rag_reference_limit = max(1, min(50, int(data["rag_reference_limit"])))
    db.session.commit()
    out = s.to_dict(); out.update(us.to_dict()); out["llm_model_simulation"] = us.llm_model or out["llm_model_simulation"]; out["llm_model_nai"] = us.nai_model or out["llm_model_nai"]
    return jsonify(out)


@app.route("/api/models", methods=["GET"])
def list_available_models():
    """현재 사용자의 OpenAI 호환 endpoint에서 모델 목록을 읽는다."""
    try:
        import llm_client
        client, _ = llm_client.get_llm_client()
        models = client.models.list()
        ids = sorted({m.id for m in models.data if getattr(m, "id", None)})
        return jsonify({"models": ids})
    except Exception as e:
        return jsonify({"error": str(e), "models": []}), 502


@app.route("/api/markdown/render", methods=["POST"])
def render_markdown():
    """허용 목록으로 정화된 Markdown HTML을 반환한다."""
    import markdown, bleach
    source = (request.json or {}).get("text", "")
    html = markdown.markdown(str(source), extensions=["extra", "sane_lists", "nl2br"])
    allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + ["p", "h1", "h2", "h3", "h4", "hr", "br", "pre", "code", "table", "thead", "tbody", "tr", "th", "td", "blockquote"]
    return jsonify({"html": bleach.clean(html, tags=allowed_tags, attributes={"a": ["href", "title"]}, protocols=["http", "https", "mailto"])})


# ─────────────────────────────────────────
#  LLM 프롬프트 설정 API
# ─────────────────────────────────────────

@app.route("/api/prompts", methods=["GET"])
def list_prompts():
    prompts = LlmPromptConfig.query.order_by(LlmPromptConfig.id).all()
    include_default = request.args.get("defaults") == "true"
    return jsonify([p.to_dict(include_default=include_default) for p in prompts])


@app.route("/api/prompts/<string:key>", methods=["GET"])
def get_prompt(key):
    p = LlmPromptConfig.query.filter_by(key=key).first_or_404()
    return jsonify(p.to_dict(include_default=True))


@app.route("/api/prompts/<string:key>", methods=["PUT"])
def update_prompt(key):
    p = LlmPromptConfig.query.filter_by(key=key).first_or_404()
    data = request.json or {}
    if "content" in data:
        p.content = data["content"]
    p.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(p.to_dict())


@app.route("/api/prompts/<string:key>/reset", methods=["POST"])
def reset_prompt(key):
    p = LlmPromptConfig.query.filter_by(key=key).first_or_404()
    p.content = p.default_content
    p.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(p.to_dict())


def _load_prompt_overrides() -> dict:
    """DB에서 커스텀 프롬프트를 로드해 key→content 딕셔너리로 반환"""
    try:
        return {p.key: p.content for p in LlmPromptConfig.query.all()}
    except Exception:
        return {}


# ─────────────────────────────────────────
#  타임라인 API
# ─────────────────────────────────────────

@app.route("/api/timelines", methods=["GET"])
def list_timelines():
    wid = get_world_id()
    q = Timeline.query
    if wid:
        q = q.filter_by(world_id=wid)
    tls = q.order_by(Timeline.created_at.desc()).all()
    return jsonify([t.to_dict() for t in tls])


@app.route("/api/timelines", methods=["POST"])
def create_timeline():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name은 필수입니다."}), 400
    tl = Timeline(
        world_id=get_world_id(),
        name=name,
        description=data.get("description", ""),
        main_entry_id=data.get("main_entry_id") or None,
    )
    db.session.add(tl)
    db.session.commit()
    return jsonify(tl.to_dict()), 201


@app.route("/api/timelines/generate", methods=["POST"])
def generate_timeline_llm():
    """특정 엔트리 중심으로 LLM 1회 호출로 타임라인 자동 생성"""
    from llm_client import generate_timeline
    data = request.json or {}
    entry_id = data.get("entry_id")
    extra_prompt = (data.get("extra_prompt") or "").strip()
    episode_count = int(data.get("episode_count") or 5)
    timeline_name = (data.get("timeline_name") or "").strip()

    if not entry_id:
        return jsonify({"error": "entry_id는 필수입니다."}), 400

    wid = get_world_id()
    entry = WorldEntry.query.get_or_404(entry_id)
    eq = WorldEntry.query.filter_by(is_active=True, is_summarized=False).filter(
        db.or_(WorldEntry.is_superseded.is_(False), WorldEntry.is_superseded.is_(None))
    )
    if wid:
        eq = eq.filter_by(world_id=wid)
    world_entries = [e.to_dict() for e in eq.all()]

    try:
        prompt_overrides = _load_prompt_overrides()
        result = generate_timeline(entry.to_dict(), world_entries, extra_prompt, episode_count,
                                   prompt_overrides=prompt_overrides)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if result.get("_parse_error"):
        return jsonify({"error": "LLM 응답 파싱 실패", "raw": result.get("_raw", "")}), 500

    name = timeline_name or result.get("timeline_name") or f"{entry.title} 타임라인"
    desc = result.get("timeline_description", "")
    tl = Timeline(world_id=wid, name=name, description=desc, main_entry_id=entry_id)
    db.session.add(tl)
    db.session.flush()

    for i, ep in enumerate(result.get("episodes", [])):
        ev = TimelineEvent(
            timeline_id=tl.id,
            tick_number=int(ep.get("tick_number") or (i + 1)),
            order_index=i,
            title=(ep.get("title") or f"에피소드 {i+1}")[:300],
            description=ep.get("description", ""),
            event_type="auto",
        )
        ev.affected_entry_ids = [entry_id]
        db.session.add(ev)

    db.session.commit()
    return jsonify({
        "timeline": tl.to_dict(),
        "episode_count": len(result.get("episodes", [])),
        "tokens_in": result.get("_tokens_in", 0),
        "tokens_out": result.get("_tokens_out", 0),
    }), 201


@app.route("/api/entries/<int:entry_id>/timelines", methods=["GET"])
def get_entry_timelines(entry_id):
    """특정 엔트리에 종속된 타임라인 목록"""
    WorldEntry.query.get_or_404(entry_id)
    tls = Timeline.query.filter_by(main_entry_id=entry_id).order_by(Timeline.created_at.desc()).all()
    return jsonify([t.to_dict() for t in tls])


@app.route("/api/timelines/<int:tl_id>", methods=["GET"])
def get_timeline(tl_id):
    tl = Timeline.query.get_or_404(tl_id)
    # 이벤트에 엔트리 상세 포함
    events = []
    entry_ids_all = set()
    for ev in tl.events:
        entry_ids_all.update(ev.affected_entry_ids)
    entry_map = {e.id: e.to_dict() for e in WorldEntry.query.filter(WorldEntry.id.in_(entry_ids_all)).all()} if entry_ids_all else {}
    for ev in tl.events:
        d = ev.to_dict()
        d["affected_entries"] = [entry_map[eid] for eid in d["affected_entry_ids"] if eid in entry_map]
        events.append(d)
    result = tl.to_dict()
    result["events"] = events
    result["beats"] = [b.to_dict() for b in tl.beats]
    return jsonify(result)


@app.route("/api/timelines/<int:tl_id>", methods=["PUT"])
def update_timeline(tl_id):
    tl = Timeline.query.get_or_404(tl_id)
    data = request.json or {}
    if "name" in data:
        tl.name = data["name"]
    if "description" in data:
        tl.description = data["description"]
    if "narrative_goal" in data:
        tl.narrative_goal = data["narrative_goal"]
    if "main_entry_id" in data:
        tl.main_entry_id = data["main_entry_id"] or None
    db.session.commit()
    return jsonify(tl.to_dict())


@app.route("/api/timelines/<int:tl_id>", methods=["DELETE"])
def delete_timeline(tl_id):
    tl = Timeline.query.get_or_404(tl_id)
    db.session.delete(tl)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/timelines/<int:tl_id>/events", methods=["POST"])
def add_timeline_event(tl_id):
    """타임라인에 에피소드 수동 추가"""
    tl = Timeline.query.get_or_404(tl_id)
    data = request.json or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title은 필수입니다."}), 400
    # order_index: 같은 tick 내 마지막 순서
    max_order = db.session.query(db.func.max(TimelineEvent.order_index)).filter_by(
        timeline_id=tl_id, tick_number=data.get("tick_number", 0)
    ).scalar() or 0
    ev = TimelineEvent(
        timeline_id=tl_id,
        tick_number=int(data.get("tick_number", 0)),
        order_index=max_order + 1,
        title=title,
        description=data.get("description", ""),
        event_type="user",
    )
    ev.affected_entry_ids = data.get("affected_entry_ids", [])
    db.session.add(ev)
    db.session.commit()
    return jsonify(ev.to_dict()), 201


@app.route("/api/timeline-events/<int:ev_id>", methods=["PUT"])
def update_timeline_event(ev_id):
    ev = TimelineEvent.query.get_or_404(ev_id)
    data = request.json or {}
    if "title" in data:
        ev.title = data["title"]
    if "description" in data:
        ev.description = data["description"]
    if "tick_number" in data:
        ev.tick_number = int(data["tick_number"])
    if "affected_entry_ids" in data:
        ev.affected_entry_ids = data["affected_entry_ids"]
    db.session.commit()
    return jsonify(ev.to_dict())


@app.route("/api/timeline-events/<int:ev_id>", methods=["DELETE"])
def delete_timeline_event(ev_id):
    ev = TimelineEvent.query.get_or_404(ev_id)
    db.session.delete(ev)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/timelines/<int:tl_id>/beats", methods=["GET"])
def list_story_beats(tl_id):
    Timeline.query.get_or_404(tl_id)
    beats = StoryBeat.query.filter_by(timeline_id=tl_id).order_by(StoryBeat.tick_number).all()
    return jsonify([b.to_dict() for b in beats])


@app.route("/api/timelines/<int:tl_id>/beats", methods=["POST"])
def create_story_beat(tl_id):
    Timeline.query.get_or_404(tl_id)
    data = request.json or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title은 필수입니다."}), 400
    beat = StoryBeat(
        timeline_id=tl_id,
        tick_number=int(data.get("tick_number", 1)),
        beat_label=(data.get("beat_label") or "").strip(),
        title=title,
        description=data.get("description", ""),
        is_fixed=bool(data.get("is_fixed", True)),
    )
    db.session.add(beat)
    db.session.commit()
    return jsonify(beat.to_dict()), 201


@app.route("/api/story-beats/<int:beat_id>", methods=["PUT"])
def update_story_beat(beat_id):
    beat = StoryBeat.query.get_or_404(beat_id)
    data = request.json or {}
    if "tick_number" in data:
        beat.tick_number = int(data["tick_number"])
    if "beat_label" in data:
        beat.beat_label = data["beat_label"]
    if "title" in data:
        beat.title = data["title"]
    if "description" in data:
        beat.description = data["description"]
    if "is_fixed" in data:
        beat.is_fixed = bool(data["is_fixed"])
    db.session.commit()
    return jsonify(beat.to_dict())


@app.route("/api/story-beats/<int:beat_id>", methods=["DELETE"])
def delete_story_beat(beat_id):
    beat = StoryBeat.query.get_or_404(beat_id)
    db.session.delete(beat)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/entries/<int:entry_id>/timeline-events", methods=["GET"])
def get_entry_timeline_events(entry_id):
    """특정 엔트리에 관련된 모든 타임라인 이벤트"""
    # affected_entry_ids_json에 entry_id가 포함된 이벤트를 JSON like 검색
    events = TimelineEvent.query.filter(
        TimelineEvent.affected_entry_ids_json.contains(str(entry_id))
    ).order_by(TimelineEvent.tick_number).all()
    # 정확한 ID 매칭 (문자열 포함 검색이므로 재확인)
    result = [ev.to_dict() for ev in events if entry_id in ev.affected_entry_ids]
    # 타임라인 이름 추가
    tl_ids = {ev["timeline_id"] for ev in result}
    tl_map = {t.id: t.name for t in Timeline.query.filter(Timeline.id.in_(tl_ids)).all()}
    for ev in result:
        ev["timeline_name"] = tl_map.get(ev["timeline_id"], "")
    return jsonify(result)


# ─────────────────────────────────────────
#  연구 통계 API
# ─────────────────────────────────────────

@app.route("/api/stats", methods=["GET"])
def get_stats():
    """연구용 통계: 런별 작업시간/토큰/틱, 참조 Top10, 단어 Top10"""
    wid = get_world_id()
    rq = SimulationRun.query
    eq = WorldEntry.query
    if wid:
        rq = rq.filter_by(world_id=wid)
        eq = eq.filter_by(world_id=wid)
    runs = rq.order_by(SimulationRun.started_at.desc()).all()
    run_ids = [r.id for r in runs]
    logs = SimulationLog.query.filter(SimulationLog.run_id.in_(run_ids)).all() if run_ids else []
    entries = eq.all()

    # ── 런별 통계 ───────────────────────────────────────────────
    run_stats = []
    for run in runs:
        run_logs = [l for l in logs if l.run_id == run.id]
        duration = None
        if run.started_at and run.ended_at:
            duration = (run.ended_at - run.started_at).total_seconds()
        completed = run.current_tick or 0
        avg_tick_sec = round(duration / completed, 2) if duration and completed > 0 else None
        rag_ticks = sum(1 for l in run_logs if l.event_type == "rag_filter")
        run_stats.append({
            "id": run.id,
            "status": run.status,
            "total_ticks": run.total_ticks,
            "completed_ticks": completed,
            "tokens_in": run.total_tokens_in or 0,
            "tokens_out": run.total_tokens_out or 0,
            "tokens_total": run.total_tokens or 0,
            "duration_sec": round(duration, 1) if duration else None,
            "avg_tick_sec": avg_tick_sec,
            "rag_ticks": rag_ticks,
            "started_at": run.started_at.isoformat() + 'Z' if run.started_at else None,
            "ended_at": run.ended_at.isoformat() + 'Z' if run.ended_at else None,
        })

    # ── 총계 ─────────────────────────────────────────────────────
    done_runs = [r for r in run_stats if r["status"] == "done"]
    total_dur = sum(r["duration_sec"] or 0 for r in run_stats)
    avg_run_dur = round(total_dur / len(done_runs), 1) if done_runs else None
    totals = {
        "tokens_in": sum(r["tokens_in"] for r in run_stats),
        "tokens_out": sum(r["tokens_out"] for r in run_stats),
        "tokens_total": sum(r["tokens_total"] for r in run_stats),
        "run_count": len(runs),
        "done_run_count": len(done_runs),
        "entry_count": len(entries),
        "log_count": len(logs),
        "total_duration_sec": round(total_dur, 1),
        "avg_run_duration_sec": avg_run_dur,
        "user_entries": sum(1 for e in entries if e.created_by == "user"),
        "llm_entries": sum(1 for e in entries if e.created_by == "llm"),
    }

    # ── 가장 많이 참조된 엔트리 Top10 ───────────────────────────
    entry_ref_count = Counter()
    for log in logs:
        raw_ids = log.affected_entries_json or "[]"
        try:
            ids = _json.loads(raw_ids)
        except Exception:
            try:
                import ast as _ast
                ids = _ast.literal_eval(raw_ids)
            except Exception:
                ids = []
        for eid in ids:
            try:
                entry_ref_count[int(eid)] += 1
            except Exception:
                pass

    entry_map = {e.id: {"title": e.title, "category": e.category} for e in entries}
    top_refs = [
        {
            "id": eid,
            "title": entry_map.get(eid, {}).get("title", f"삭제됨 #{eid}"),
            "category": entry_map.get(eid, {}).get("category", ""),
            "count": cnt,
        }
        for eid, cnt in entry_ref_count.most_common(10)
    ]

    # ── 가장 많이 언급된 한국어 단어 Top10 ──────────────────────
    _stopwords = {
        '있다', '없다', '하다', '이다', '되다', '않다', '것이', '위해', '통해', '대한',
        '로서', '에서', '으로', '이를', '그의', '그녀', '그들', '이것', '저것', '때문',
        '이후', '이전', '현재', '시작', '통한', '이번', '발생', '세계', '세력', '인물',
        '사건', '관념', '물건', '종족', '관련', '상태', '변화', '생성', '엔트리', '틱',
        '소멸', '비활성', '활성', '수정', '삭제', '보호', '유저', '원본', '내용', '이유',
        '대해', '통해', '위한', '결과', '새로운', '기존', '전체', '각각', '이라', '그리고',
        '하여', '하며', '하지', '이며', '으며', '지만', '하면', '이면', '에게', '에게서',
    }
    all_text = " ".join(
        (l.description or "") + " " + (l.llm_reasoning or "")
        for l in logs
        if l.event_type not in ("rag_filter", "error", "context_summary", "entry_protected")
    )
    word_counter = Counter()
    for w in re.findall(r'[가-힣]{2,}', all_text):
        if w not in _stopwords:
            word_counter[w] += 1
    top_words = [{"word": w, "count": c} for w, c in word_counter.most_common(10)]

    return jsonify({
        "runs": run_stats,
        "totals": totals,
        "top_referenced": top_refs,
        "top_words": top_words,
    })


# ─────────────────────────────────────────
#  시뮬레이션 실행 API
# ─────────────────────────────────────────

@app.route("/api/runs", methods=["GET"])
def list_runs():
    wid = get_world_id()
    q = SimulationRun.query
    if wid:
        q = q.filter_by(world_id=wid)
    runs = q.order_by(SimulationRun.started_at.desc()).limit(50).all()
    return jsonify([r.to_dict() for r in runs])


@app.route("/api/runs", methods=["POST"])
def start_run():
    data = request.json
    config_id = data.get("config_id")
    if not config_id:
        return jsonify({"error": "config_id는 필수입니다."}), 400

    config = SimulationConfig.query.get_or_404(config_id)
    import json as _json
    entry_ids = data.get("entry_ids")  # None이면 전체
    exclude_llm = bool(data.get("exclude_llm_entries", False))

    # 타임라인 연결 처리
    timeline_id = data.get("timeline_id")  # 기존 타임라인 ID
    new_timeline_name = (data.get("new_timeline_name") or "").strip()
    wid = get_world_id()
    if new_timeline_name:
        tl = Timeline(world_id=wid, name=new_timeline_name, description=f"시뮬레이션 '{config.name}' 자동 생성")
        db.session.add(tl)
        db.session.flush()
        timeline_id = tl.id
    elif timeline_id:
        if not Timeline.query.get(timeline_id):
            timeline_id = None

    run = SimulationRun(
        world_id=wid,
        user_id=current_user().id,
        config_id=config.id,
        status="pending",
        current_tick=0,
        total_ticks=config.tick_count,
        selected_entry_ids_json=_json.dumps(entry_ids) if entry_ids else None,
        exclude_llm_entries=exclude_llm,
        timeline_id=timeline_id,
    )
    db.session.add(run)
    db.session.commit()

    # 백그라운드 스레드로 실행
    from simulation_engine import run_simulation
    t = threading.Thread(target=run_simulation, args=(run.id, app), daemon=True)
    t.start()

    return jsonify(run.to_dict()), 202


@app.route("/api/runs/<int:run_id>", methods=["GET"])
def get_run(run_id):
    run = SimulationRun.query.get_or_404(run_id)
    return jsonify(run.to_dict())


@app.route("/api/runs/<int:run_id>/cancel", methods=["PUT"])
def cancel_run(run_id):
    run = SimulationRun.query.get_or_404(run_id)
    if run.status not in ("running", "pending"):
        return jsonify({"error": "실행 중인 런이 아닙니다."}), 400
    run.status = "cancelled"
    run.ended_at = datetime.utcnow()
    db.session.commit()
    return jsonify(run.to_dict())


@app.route("/api/runs/<int:run_id>", methods=["DELETE"])
def delete_run(run_id):
    run = SimulationRun.query.get_or_404(run_id)
    if run.status in ("running", "pending"):
        return jsonify({"error": "실행 중인 런은 삭제할 수 없습니다. 먼저 중지하세요."}), 400
    SimulationLog.query.filter_by(run_id=run_id).delete()
    db.session.delete(run)
    db.session.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────
#  로그 API
# ─────────────────────────────────────────

@app.route("/api/logs", methods=["GET"])
def list_logs():
    run_id = request.args.get("run_id")
    tick = request.args.get("tick")
    event_type = request.args.get("event_type")
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))

    wid = get_world_id()
    q = SimulationLog.query
    if wid and not run_id:
        # 현재 세계관의 런에 속한 로그만 조회
        run_ids = [r.id for r in SimulationRun.query.filter_by(world_id=wid).with_entities(SimulationRun.id).all()]
        q = q.filter(SimulationLog.run_id.in_(run_ids))
    if run_id:
        q = q.filter_by(run_id=int(run_id))
    if tick:
        q = q.filter_by(tick_number=int(tick))
    if event_type:
        q = q.filter_by(event_type=event_type)

    total = q.count()
    logs = q.order_by(SimulationLog.created_at.desc()).offset(offset).limit(limit).all()
    return jsonify({"total": total, "logs": [l.to_dict() for l in logs]})


@app.route("/api/logs/<int:log_id>", methods=["DELETE"])
def delete_log(log_id):
    log = SimulationLog.query.get_or_404(log_id)
    db.session.delete(log)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/logs", methods=["DELETE"])
def delete_all_logs():
    """현재 세계관의 모든 로그 삭제 (또는 특정 run_id)"""
    run_id = request.args.get("run_id")
    wid = get_world_id()

    if run_id:
        SimulationLog.query.filter_by(run_id=int(run_id)).delete()
    elif wid:
        run_ids = [r.id for r in SimulationRun.query.filter_by(world_id=wid).with_entities(SimulationRun.id).all()]
        if run_ids:
            SimulationLog.query.filter(SimulationLog.run_id.in_(run_ids)).delete(synchronize_session=False)
    else:
        SimulationLog.query.delete()
    db.session.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────
#  세계관 스냅샷 API
# ─────────────────────────────────────────

@app.route("/api/snapshots", methods=["GET"])
def list_snapshots():
    wid = get_world_id()
    q = WorldSnapshot.query
    if wid:
        q = q.filter_by(world_id=wid)
    snaps = q.order_by(WorldSnapshot.created_at.desc()).all()
    return jsonify([s.to_dict() for s in snaps])


@app.route("/api/snapshots", methods=["POST"])
def save_snapshot():
    """현재 활성 엔트리 전체를 레이블 붙여 저장"""
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name은 필수입니다."}), 400

    wid = get_world_id()
    eq = WorldEntry.query.filter_by(is_active=True, is_summarized=False)
    if wid:
        eq = eq.filter_by(world_id=wid)
    entries = eq.all()
    import json as _json
    entries_data = [e.to_dict() for e in entries]

    snap = WorldSnapshot(
        world_id=wid,
        name=name,
        description=data.get("description", ""),
        entries_json=_json.dumps(entries_data, ensure_ascii=False),
        entry_count=len(entries_data),
    )
    db.session.add(snap)
    db.session.commit()
    return jsonify(snap.to_dict()), 201


@app.route("/api/snapshots/<int:snap_id>", methods=["GET"])
def get_snapshot(snap_id):
    snap = WorldSnapshot.query.get_or_404(snap_id)
    return jsonify(snap.to_dict(include_entries=True))


@app.route("/api/snapshots/<int:snap_id>/restore", methods=["POST"])
def restore_snapshot(snap_id):
    """스냅샷을 DB에 복원 (기존 엔트리는 모두 비활성화 후 스냅샷 데이터로 교체)"""
    snap = WorldSnapshot.query.get_or_404(snap_id)
    import json as _json

    wid = get_world_id() or snap.world_id
    # 현재 세계관 엔트리만 비활성화
    eq = WorldEntry.query
    if wid:
        eq = eq.filter_by(world_id=wid)
    eq.update({"is_active": False})
    db.session.flush()

    # 스냅샷 엔트리 복원
    entries_data = _json.loads(snap.entries_json)
    id_map = {}  # 구 id → 새 id
    for e in entries_data:
        new_entry = WorldEntry(
            world_id=wid,
            title=e["title"],
            category=e["category"],
            content=e["content"],
            created_by=e.get("created_by", "user"),
            tick_created=e.get("tick_created", 0),
            is_active=True,
            is_summarized=False,
        )
        new_entry.references = []  # 참조는 id가 바뀌므로 일단 비워둠
        db.session.add(new_entry)
        db.session.flush()
        id_map[e["id"]] = new_entry.id

    db.session.commit()
    return jsonify({"ok": True, "restored": len(entries_data), "snapshot": snap.to_dict()})


@app.route("/api/snapshots/<int:snap_id>", methods=["DELETE"])
def delete_snapshot(snap_id):
    snap = WorldSnapshot.query.get_or_404(snap_id)
    db.session.delete(snap)
    db.session.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────
#  LLM 연결 테스트 API
# ─────────────────────────────────────────

@app.route("/api/test-llm", methods=["POST"])
def test_llm():
    """GitHub Copilot API 연결 및 모델 응답 확인"""
    try:
        import llm_client
        client, model = llm_client.get_llm_client()
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "1+1은 무엇인가요? 숫자만 답하세요."}],
            max_tokens=100,
            temperature=0,
        )
        reply = response.choices[0].message.content
        usage = response.usage
        return jsonify({
            "ok": True,
            "model": model,
            "reply": reply,
            "tokens_in": usage.prompt_tokens if usage else None,
            "tokens_out": usage.completion_tokens if usage else None,
        })
    except EnvironmentError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/chat")
def chat_page():
    redir = _require_world_redirect()
    if redir: return redir
    return render_template("chat.html", categories=CATEGORIES)


@app.route("/world-design")
def world_design_page():
    redir = _require_world_redirect()
    if redir: return redir
    return render_template("world_design.html", categories=CATEGORIES)


@app.route("/api/world-design/plan", methods=["POST"])
def world_design_plan():
    data = request.json or {}
    context = (data.get("context") or "").strip()
    if len(context) < 10:
        return jsonify({"error": "큰 맥락을 10자 이상 입력하세요."}), 400
    wid = get_world_id()
    entries = [e.to_dict() for e in WorldEntry.query.filter_by(world_id=wid, is_active=True).filter(
        db.or_(WorldEntry.is_superseded.is_(False), WorldEntry.is_superseded.is_(None))).limit(100).all()]
    target_count = max(1, min(30, int(data.get("target_count", 10))))
    try:
        import llm_client
        result = llm_client.plan_world_structure(context, target_count, (data.get("answers") or "").strip(), entries, max_chars=AppSettings.get().max_llm_entry_chars or 0)
        items = [item for item in result.get("items", []) if item.get("title") and item.get("category") in CATEGORIES][:target_count]
        # 임베딩이 켜졌다면 후보 제목/속성을 벡터화하여 유사한 기존 DB 항목을 제외한다.
        settings = UserLlmSettings.get_for_user(current_user().id)
        duplicate_titles = []
        if settings.embedding_enabled:
            rows = WorldEntry.query.filter_by(world_id=wid, is_active=True).all()
            for item in list(items):
                query = f"{item['title']}\n{item['category']}\n{' '.join(item.get('attributes', []))}\n{item.get('purpose', '')}"
                try:
                    vector = llm_client.generate_embedding(query, settings.embedding_model)
                    matches = []
                    for row in rows:
                        score = llm_client.cosine_similarity(vector, _json.loads(row.embedding_json or "[]"))
                        if score >= 0.88: matches.append(row.title)
                    if matches:
                        items.remove(item); duplicate_titles.append({"planned": item["title"], "existing": matches[:3]})
                except Exception:
                    break  # 임베딩 오류 시 제목 비교만 적용
        existing_norm = {e["title"].strip().lower() for e in entries}
        items = [i for i in items if i["title"].strip().lower() not in existing_norm]
        return jsonify({"summary": result.get("summary", ""), "questions": result.get("questions", [])[:10], "items": items, "duplicate_matches": duplicate_titles})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/world-design/generate", methods=["POST"])
def world_design_generate():
    """사용자가 확정한 설계 목록을 지정 횟수만큼 나눠 순차적으로 상세 생성한다."""
    data = request.json or {}
    context, items = (data.get("context") or "").strip(), data.get("items") or []
    batches = max(1, min(15, int(data.get("batches", 5))))
    items = [i for i in items if i.get("title") and i.get("category") in CATEGORIES][:30]
    if not context or not items: return jsonify({"error": "큰 맥락과 설계 항목이 필요합니다."}), 400
    wid = get_world_id()
    existing = [e.to_dict() for e in WorldEntry.query.filter_by(world_id=wid, is_active=True).limit(100).all()]
    chunk_size = max(1, (len(items) + batches - 1) // batches)
    try:
        import llm_client
        entries = []
        max_chars = AppSettings.get().max_llm_entry_chars or 0
        for start in range(0, len(items), chunk_size):
            chunk = items[start:start + chunk_size]
            result = llm_client.generate_world_detail_batch(context, chunk, existing + entries, max_chars=max_chars, world_id=wid)
            wanted = {i["title"].strip().lower() for i in chunk}
            for entry in result.get("entries", []):
                if entry.get("title", "").strip().lower() in wanted and entry.get("category") in CATEGORIES and entry.get("content"):
                    entries.append(entry)
        return jsonify({"entries": entries, "batches": (len(items) + chunk_size - 1) // chunk_size, "per_batch": chunk_size})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/world-design/apply", methods=["POST"])
def world_design_apply():
    """미리보기로 확정한 항목만 DB에 저장한다."""
    data = request.json or {}
    proposed = data.get("entries") or []
    if not isinstance(proposed, list) or not proposed:
        return jsonify({"error": "저장할 엔트리가 없습니다."}), 400
    wid = get_world_id()
    existing = {e.title: e.id for e in WorldEntry.query.filter_by(world_id=wid).all()}
    created = []
    for item in proposed[:30]:
        title, category, content = (str(item.get(k) or "").strip() for k in ("title", "category", "content"))
        if not title or not content or category not in CATEGORIES: continue
        refs = [existing[r] for r in item.get("references", []) if r in existing]
        max_chars=AppSettings.get().max_llm_entry_chars or 0
        if max_chars:content=content[:max_chars]
        entry = WorldEntry(world_id=wid, title=title[:200], category=category, content=content, secret_content=str(item.get("secret") or "")[:10000], created_by="llm", tick_created=0, is_active=True)
        entry.references = refs
        db.session.add(entry); db.session.flush(); existing[entry.title] = entry.id; created.append(entry)
        _apply_generated_attributes(entry, item.get("attributes") or {})
        for skill_id in item.get("reused_skill_ids") or []:
            skill=WorldSkillRegistry.query.filter_by(id=skill_id,world_id=wid).first()
            if skill: db.session.add(EntrySkillLink(entry_id=entry.id,skill_id=skill.id))
        for proposal in item.get("new_skill_proposals") or []:
            name=str(proposal.get("name") or "").strip()
            if not name:continue
            skill=WorldSkillRegistry.query.filter_by(world_id=wid,name=name).first()
            if not skill:
                skill=WorldSkillRegistry(world_id=wid,type=proposal.get("type") if proposal.get("type") in ("스킬","특성") else "스킬",name=name[:150],description=proposal.get("description",""),tags_json=_json.dumps(proposal.get("tags") or [],ensure_ascii=False),created_by="llm")
                db.session.add(skill);db.session.flush()
            db.session.add(EntrySkillLink(entry_id=entry.id,skill_id=skill.id))
    db.session.commit()
    # 태그/임베딩은 생성 성공에 영향을 주지 않도록 저장 뒤 각각 보강한다.
    for entry in created: _enrich_entry(entry)
    return jsonify({"ok": True, "created": [e.to_dict() for e in created]})


@app.route("/api/chat", methods=["POST"])
def chat():
    """세계관 컨텍스트를 제한해 전달하는 아이디어 정의용 대화 API."""
    data = request.json or {}
    message = (data.get("message") or "").strip()
    if not message: return jsonify({"error": "message는 필수입니다."}), 400
    history = data.get("history") or []
    settings = AppSettings.get()
    entries = WorldEntry.query.filter_by(world_id=get_world_id(), is_active=True).all()
    context = [e.to_dict() for e in entries[:settings.rag_reference_limit]]
    try:
        import llm_client
        model_override = (settings.llm_model_simulation or None) if settings.llm_base_url else (os.environ.get("LLM_MODEL") if os.environ.get("LLM_BASE_URL") else (settings.llm_model_simulation or None))
        client, model = llm_client.get_llm_client(model_override)
        system = "당신은 세계관 기획 파트너입니다. 사용자가 아이디어를 명확히 정의하도록 질문·대안·일관성 점검을 돕습니다. 확정되지 않은 사실은 단정하지 마세요.\n\n현재 참조 데이터:\n" + llm_client.serialize_world_state(context, settings.max_llm_entry_chars if settings.max_llm_entry_chars is not None else 0)
        messages = [{"role": "system", "content": system}] + [{"role": m.get("role", "user"), "content": str(m.get("content", ""))} for m in history if m.get("role") in ("user", "assistant")] + [{"role": "user", "content": message}]
        response = client.chat.completions.create(model=model, messages=messages, temperature=0.7, max_tokens=llm_client.get_max_output_tokens())
        return jsonify({"reply": response.choices[0].message.content or "", "model": model, "reference_count": len(context)})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/chat/entry-draft", methods=["POST"])
def chat_entry_draft():
    """현재 아이디어 대화를 검토 가능한 엔트리 초안으로 변환한다."""
    data = request.json or {}
    history = [m for m in (data.get("history") or []) if m.get("role") in ("user", "assistant")]
    if not history:
        return jsonify({"error": "먼저 아이디어 대화를 시작하세요."}), 400
    settings = AppSettings.get()
    entries = WorldEntry.query.filter_by(world_id=get_world_id(), is_active=True).limit(settings.rag_reference_limit).all()
    context = [e.to_dict() for e in entries]
    try:
        import llm_client
        model_override = (settings.llm_model_simulation or None) if settings.llm_base_url else (os.environ.get("LLM_MODEL") if os.environ.get("LLM_BASE_URL") else (settings.llm_model_simulation or None))
        draft = llm_client.propose_entry_from_chat(
            get_world_id(), history, context,
            instruction=str(data.get("instruction") or "")[:2000],
            model_override=model_override,
        )
        category = draft.get("category") if draft.get("category") in CATEGORIES else "관념"
        content = str(draft.get("content") or "").strip()
        if settings.max_llm_entry_chars:
            content = content[:settings.max_llm_entry_chars]
        return jsonify({
            "title": str(draft.get("title") or "아이디어 대화 초안").strip()[:200],
            "category": category,
            "content": content,
            "secret": str(draft.get("secret") or "")[:10000],
            "aliases": draft.get("aliases") if isinstance(draft.get("aliases"), list) else [],
            "attributes": draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ─────────────────────────────────────────
#  Export API
# ─────────────────────────────────────────

def _build_export_data(run: SimulationRun) -> dict:
    """연구용 종합 export 데이터 구조 생성"""
    config = run.config
    logs = SimulationLog.query.filter_by(run_id=run.id).order_by(SimulationLog.created_at).all()

    # 총 소요 시간 계산
    duration_sec = None
    if run.started_at and run.ended_at:
        duration_sec = round((run.ended_at - run.started_at).total_seconds(), 2)

    # 틱별 그룹핑
    ticks_dict = {}
    for log in logs:
        t = log.tick_number
        if t not in ticks_dict:
            ticks_dict[t] = {
                "tick_number": t,
                "events": [],
                "entries_created": [],
                "entries_updated": [],
                "entries_deactivated": [],
                "context_summaries": [],
                "errors": [],
                "tokens_in": 0,
                "tokens_out": 0,
                "tokens_total": 0,
                "llm_reasoning": "",
                "first_log_at": log.created_at.isoformat() + 'Z' if log.created_at else None,
                "last_log_at": log.created_at.isoformat() + 'Z' if log.created_at else None,
            }
        td = ticks_dict[t]
        td["last_log_at"] = log.created_at.isoformat() + 'Z' if log.created_at else None

        # 이벤트 타입별 분류
        entry = {
            "description": log.description,
            "affected_entries": log.affected_entries,
            "llm_reasoning": log.llm_reasoning,
        }
        if log.event_type == "world_event":
            td["events"].append(entry)
        elif log.event_type == "entry_created":
            td["entries_created"].append(entry)
        elif log.event_type == "entry_updated":
            td["entries_updated"].append(entry)
        elif log.event_type == "entry_deactivated":
            td["entries_deactivated"].append(entry)
        elif log.event_type == "context_summary":
            td["context_summaries"].append(entry)
        elif log.event_type == "error":
            td["errors"].append({"message": log.description})

        # 토큰은 틱당 LLM 호출 기준 (world_event 또는 context_summary 로그에만 기록)
        if log.tokens_total and log.event_type in ("world_event", "context_summary"):
            td["tokens_in"] = log.tokens_in or 0
            td["tokens_out"] = log.tokens_out or 0
            td["tokens_total"] = log.tokens_total or 0
        if log.llm_reasoning and not td["llm_reasoning"]:
            td["llm_reasoning"] = log.llm_reasoning

    ticks = sorted(ticks_dict.values(), key=lambda x: x["tick_number"])

    # 최종 세계관 상태
    final_entries = [e.to_dict() for e in
        WorldEntry.query.filter_by(is_active=True, is_summarized=False)
        .order_by(WorldEntry.category, WorldEntry.id).all()]

    # LLM 생성 엔트리 vs 유저 작성
    llm_entries = [e for e in WorldEntry.query.filter_by(created_by="llm").all()]
    user_entries = [e for e in WorldEntry.query.filter_by(created_by="user").all()]

    return {
        "export_meta": {
            "exported_at": datetime.utcnow().isoformat(),
            "run_id": run.id,
            "status": run.status,
            "started_at": run.started_at.isoformat() + 'Z' if run.started_at else None,
            "ended_at": run.ended_at.isoformat() + 'Z' if run.ended_at else None,
            "total_duration_seconds": duration_sec,
            "total_ticks_planned": run.total_ticks,
            "total_ticks_completed": run.current_tick,
        },
        "token_summary": {
            "total_tokens_in": run.total_tokens_in or 0,
            "total_tokens_out": run.total_tokens_out or 0,
            "total_tokens": run.total_tokens or 0,
            "avg_tokens_per_tick": round((run.total_tokens or 0) / max(run.current_tick, 1), 1),
        },
        "config": {
            "name": config.name if config else None,
            "prompt_level_1": config.prompt_level_1 if config else None,
            "prompt_level_2": config.prompt_level_2 if config else None,
            "prompt_level_3": config.prompt_level_3 if config else None,
        },
        "entry_summary": {
            "total_active": len(final_entries),
            "user_authored": len(user_entries),
            "llm_generated": len(llm_entries),
            "by_category": {cat: sum(1 for e in final_entries if e["category"] == cat)
                            for cat in CATEGORIES},
        },
        "ticks": ticks,
        "world_state_final": final_entries,
    }


@app.route("/api/runs/<int:run_id>/export", methods=["GET"])
def export_run(run_id):
    fmt = request.args.get("format", "json")
    run = SimulationRun.query.get_or_404(run_id)
    data = _build_export_data(run)

    if fmt == "csv":
        # 틱별 요약 CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "tick", "tokens_in", "tokens_out", "tokens_total",
            "events", "entries_created", "entries_updated", "entries_deactivated",
            "has_summary", "has_error", "llm_reasoning"
        ])
        for t in data["ticks"]:
            writer.writerow([
                t["tick_number"],
                t["tokens_in"], t["tokens_out"], t["tokens_total"],
                len(t["events"]),
                len(t["entries_created"]),
                len(t["entries_updated"]),
                len(t["entries_deactivated"]),
                1 if t["context_summaries"] else 0,
                1 if t["errors"] else 0,
                t["llm_reasoning"][:200].replace("\n", " "),
            ])
        csv_data = output.getvalue()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=run_{run_id}_ticks.csv"}
        )

    # JSON export
    json_str = _json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        json_str,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=run_{run_id}_export.json"}
    )


@app.route("/export")
def export_page():
    redir = _require_world_redirect()
    if redir:
        return redir
    return render_template("export.html")


@app.route("/prompts")
def prompts_page():
    return render_template("prompts.html")


# ─────────────────────────────────────────
#  유틸리티
# ─────────────────────────────────────────

@app.route("/api/categories", methods=["GET"])
def get_categories():
    return jsonify(CATEGORIES)


@app.route("/api/token-estimate", methods=["GET"])
def token_estimate():
    """현재 활성 DB 엔트리의 예상 토큰 수 반환"""
    import llm_client as lc
    entry_ids = request.args.get("ids")  # 콤마 구분 id 목록 (없으면 전체)
    config_id = request.args.get("config_id")

    exclude_llm = request.args.get("exclude_llm") == "true"
    wid = get_world_id()
    q = WorldEntry.query.filter_by(is_active=True, is_summarized=False)
    if wid:
        q = q.filter_by(world_id=wid)
    if entry_ids:
        ids = [int(i) for i in entry_ids.split(",") if i.strip().isdigit()]
        q = q.filter(WorldEntry.id.in_(ids))
    if exclude_llm:
        q = q.filter_by(created_by="user")
    entries = [e.to_dict() for e in q.all()]

    config = None
    if config_id:
        cfg = SimulationConfig.query.get(int(config_id))
        config = cfg.to_dict() if cfg else None

    max_chars = AppSettings.get().max_llm_entry_chars
    if config is None:
        config = {}
    config["max_content_chars"] = max_chars  # llm_client 인터페이스 호환

    result = lc.estimate_world_tokens(entries, config)
    return jsonify(result)


@app.route("/api/entries/bulk-delete", methods=["POST"])
def bulk_delete_entries():
    ids = request.json.get("ids", [])
    if not ids:
        return jsonify({"error": "ids가 필요합니다."}), 400
    valid_ids = [row.id for row in WorldEntry.query.filter(
        WorldEntry.world_id == get_world_id(), WorldEntry.id.in_(ids)
    ).all()]
    _delete_entry_dependents(valid_ids)
    if valid_ids:
        WorldEntry.query.filter(WorldEntry.id.in_(valid_ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True, "deleted": len(valid_ids)})


@app.route("/api/entries/bulk-toggle-active", methods=["POST"])
def bulk_toggle_active():
    ids = request.json.get("ids", [])
    active = request.json.get("is_active")  # True/False 강제 지정, None이면 반전
    if not ids:
        return jsonify({"error": "ids가 필요합니다."}), 400
    entries = WorldEntry.query.filter(WorldEntry.id.in_(ids)).all()
    for e in entries:
        e.is_active = active if active is not None else (not e.is_active)
    db.session.commit()
    return jsonify({"ok": True, "updated": len(entries)})


@app.route("/api/generate-entry", methods=["POST"])
def generate_entry_content():
    """유저 입력 정보를 기반으로 LLM이 엔트리 내용을 생성"""
    import llm_client as lc
    data = request.json or {}
    title = data.get("title", "").strip()
    category = data.get("category", "")
    hint = data.get("hint", "").strip()  # 사용자가 입력한 힌트/초안
    ref_ids = data.get("references", [])

    if not title or not category:
        return jsonify({"error": "제목과 분류는 필수입니다."}), 400

    ref_entries = [WorldEntry.query.get(rid).to_dict()
                   for rid in ref_ids if WorldEntry.query.get(rid)]
    try:
        result = lc.generate_entry(title, category, hint, ref_entries, lc._world_metadata_rules(get_world_id()), max_chars=AppSettings.get().max_llm_entry_chars or 0)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/translate", methods=["POST"])
def translate_entries_api():
    """선택된 엔트리들을 영문으로 번역"""
    import llm_client as lc
    data = request.json or {}
    entry_ids = data.get("entry_ids", [])
    if not entry_ids:
        return jsonify({"error": "entry_ids가 필요합니다."}), 400

    entries = [WorldEntry.query.get(eid).to_dict()
               for eid in entry_ids
               if WorldEntry.query.get(eid)]
    if not entries:
        return jsonify({"error": "유효한 엔트리가 없습니다."}), 400

    try:
        result = lc.translate_entries(entries)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))
