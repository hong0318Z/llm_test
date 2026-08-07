from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

CATEGORIES = ["세력", "인물", "관념", "물건", "종족", "사건", "장소", "마법/기술", "신화/종교", "역사/기록", "규칙/법", "연도"]
CREATOR_USER = "user"
CREATOR_LLM = "llm"


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)
    def to_dict(self): return {"id": self.id, "username": self.username, "name": self.name, "is_admin": bool(self.is_admin), "is_approved": bool(self.is_approved), "created_at": self.created_at.isoformat() + "Z" if self.created_at else None}


class UserLlmSettings(db.Model):
    """계정별 LLM 연결 정보. 키 원문은 API 응답에 포함하지 않는다."""
    __tablename__ = "user_llm_settings"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    llm_base_url = db.Column(db.String(500), default="")
    llm_provider = db.Column(db.String(50), default="custom")
    llm_api_key = db.Column(db.Text, default="")
    llm_model = db.Column(db.String(200), default="")
    nai_model = db.Column(db.String(200), default="")
    novelai_api_key = db.Column(db.Text, default="")
    embedding_enabled = db.Column(db.Boolean, default=False)
    embedding_base_url = db.Column(db.String(500), default="")
    embedding_api_key = db.Column(db.Text, default="")
    embedding_model = db.Column(db.String(200), default="nomic-embed-text")
    rag_reference_limit = db.Column(db.Integer, default=8)

    @staticmethod
    def get_for_user(user_id):
        s = UserLlmSettings.query.filter_by(user_id=user_id).first()
        if not s:
            s = UserLlmSettings(user_id=user_id)
            db.session.add(s); db.session.commit()
        return s

    def to_dict(self):
        return {"llm_provider": self.llm_provider or "custom", "llm_base_url": self.llm_base_url or "", "llm_api_key_saved": bool(self.llm_api_key), "llm_model": self.llm_model or "", "nai_model": self.nai_model or "", "novelai_api_key_saved": bool(self.novelai_api_key), "embedding_enabled": bool(self.embedding_enabled), "embedding_base_url": self.embedding_base_url or "", "embedding_api_key_saved": bool(self.embedding_api_key), "embedding_model": self.embedding_model or "nomic-embed-text", "rag_reference_limit": self.rag_reference_limit or 8}


class World(db.Model):
    """세계관 컨테이너 - 모든 데이터의 최상위 그룹"""
    __tablename__ = "worlds"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, with_counts=False):
        d = {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "created_at": self.created_at.isoformat() + 'Z' if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + 'Z' if self.updated_at else None,
        }
        if with_counts:
            d["entry_count"] = WorldEntry.query.filter_by(world_id=self.id).filter(
                db.or_(WorldEntry.is_superseded.is_(False), WorldEntry.is_superseded.is_(None))
            ).count()
            d["timeline_count"] = Timeline.query.filter_by(world_id=self.id).count()
            d["run_count"] = SimulationRun.query.filter_by(world_id=self.id).count()
        return d


class WorldEntry(db.Model):
    __tablename__ = "world_entries"

    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=True)  # 소속 세계관
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text, nullable=False)
    references_json = db.Column(db.Text, default="[]")
    created_by = db.Column(db.String(20), default=CREATOR_USER)
    tick_created = db.Column(db.Integer, default=0)
    primary_year = db.Column(db.String(100), default="")
    year_notes_json = db.Column(db.Text, default="[]")
    aliases_json = db.Column(db.Text, default="[]")
    is_active = db.Column(db.Boolean, default=True)
    is_summarized = db.Column(db.Boolean, default=False)  # 요약으로 대체된 항목
    keywords = db.Column(db.Text, default="")  # 쉼표 구분 핵심 키워드 (최대 5개)
    # 선택적 임베딩: 제공자와 모델이 바뀌면 다시 생성할 수 있도록 메타데이터도 저장
    embedding_json = db.Column(db.Text, default="")
    embedding_model = db.Column(db.String(200), default="")
    auto_tags_json = db.Column(db.Text, default="[]")
    image_filename = db.Column(db.String(300), nullable=True)  # 첨부 이미지 경로
    # 버전 관리
    parent_entry_id = db.Column(db.Integer, db.ForeignKey("world_entries.id"), nullable=True)
    version_note = db.Column(db.String(100), default="")   # 예: "원본", "틱5 수정", "틱8 소멸"
    is_superseded = db.Column(db.Boolean, default=False)   # True: 더 새 버전 존재 (컨텍스트/목록에서 제외)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def references(self):
        try:
            return json.loads(self.references_json or "[]")
        except Exception:
            return []

    @references.setter
    def references(self, value):
        self.references_json = json.dumps(value or [])

    def to_dict(self):
        try:
            auto_tags = json.loads(self.auto_tags_json or "[]")
        except Exception:
            auto_tags = []
        try:
            year_notes = json.loads(self.year_notes_json or "[]")
        except Exception:
            year_notes = []
        try:
            aliases = json.loads(self.aliases_json or "[]")
        except Exception:
            aliases = []
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "content": self.content,
            "references": self.references,
            "created_by": self.created_by,
            "tick_created": self.tick_created,
            "primary_year": self.primary_year or "",
            "year_notes": year_notes,
            "aliases": aliases,
            "is_active": self.is_active,
            "is_summarized": self.is_summarized,
            "keywords": self.keywords or "",
            "auto_tags": auto_tags,
            "has_embedding": bool(self.embedding_json),
            "embedding_model": self.embedding_model or "",
            "image_filename": self.image_filename or None,
            "parent_entry_id": self.parent_entry_id,
            "version_note": self.version_note or "",
            "is_superseded": self.is_superseded or False,
            "world_id": self.world_id,
            "created_at": self.created_at.isoformat() + 'Z' if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + 'Z' if self.updated_at else None,
        }


class LlmPromptConfig(db.Model):
    """LLM 프롬프트 템플릿 설정 — Web UI에서 편집 가능"""
    __tablename__ = "llm_prompt_configs"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)   # 식별자 (예: simulation_system)
    label = db.Column(db.String(200), nullable=False)              # 표시 이름
    description = db.Column(db.Text, default="")                   # 용도 설명
    content = db.Column(db.Text, nullable=False)                   # 현재 내용 (편집 가능)
    default_content = db.Column(db.Text, nullable=False)           # 원본 기본값 (리셋용)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, include_default=False):
        d = {
            "id": self.id,
            "key": self.key,
            "label": self.label,
            "description": self.description or "",
            "content": self.content,
            "is_modified": self.content != self.default_content,
            "updated_at": self.updated_at.isoformat() + 'Z' if self.updated_at else None,
        }
        if include_default:
            d["default_content"] = self.default_content
        return d


class AppSettings(db.Model):
    """전역 마스터 설정 (싱글톤)"""
    __tablename__ = "app_settings"

    id = db.Column(db.Integer, primary_key=True, default=1)
    # LLM이 생성하는 엔트리 1개당 최대 글자수 (0 = 제한 없음)
    max_llm_entry_chars = db.Column(db.Integer, default=500)
    entries_per_tick = db.Column(db.Integer, default=1)
    # 유저 입력 엔트리 1개당 최대 글자수 (UI 카운터용, 0 = 제한 없음)
    max_user_entry_chars = db.Column(db.Integer, default=1000)
    # RAG 토큰 예산: 세계관이 이 값의 50%를 초과하면 관련 엔트리만 선택 (0 = RAG 비활성화)
    rag_token_budget = db.Column(db.Integer, default=0)
    # 모델 선택: 세계관 시뮬레이션용 / NAI 프롬프트 생성용
    llm_model_simulation = db.Column(db.String(100), default="claude-sonnet-4.5")
    llm_model_nai = db.Column(db.String(100), default="claude-sonnet-4.5")
    # 임베딩은 비용/성능에 따라 선택적으로 사용한다.
    embedding_enabled = db.Column(db.Boolean, default=False)
    embedding_model = db.Column(db.String(200), default="nomic-embed-text")
    rag_reference_limit = db.Column(db.Integer, default=8)
    # 웹 UI에서 관리하는 OpenAI 호환 연결 설정. 키는 응답에 원문을 노출하지 않는다.
    llm_base_url = db.Column(db.String(500), default="")
    llm_api_key = db.Column(db.Text, default="")
    embedding_base_url = db.Column(db.String(500), default="")
    embedding_api_key = db.Column(db.Text, default="")

    @staticmethod
    def get():
        s = AppSettings.query.first()
        if not s:
            s = AppSettings(id=1)
            db.session.add(s)
            db.session.commit()
        return s

    def to_dict(self):
        return {
            "max_llm_entry_chars": self.max_llm_entry_chars if self.max_llm_entry_chars is not None else 500,
            "entries_per_tick": self.entries_per_tick if self.entries_per_tick is not None else 1,
            "max_user_entry_chars": self.max_user_entry_chars if self.max_user_entry_chars is not None else 1000,
            "rag_token_budget": self.rag_token_budget if self.rag_token_budget is not None else 0,
            "llm_model_simulation": self.llm_model_simulation or "claude-sonnet-4.5",
            "llm_model_nai": self.llm_model_nai or "claude-sonnet-4.5",
            "embedding_enabled": bool(self.embedding_enabled),
            "embedding_model": self.embedding_model or "nomic-embed-text",
            "rag_reference_limit": self.rag_reference_limit if self.rag_reference_limit is not None else 8,
            "llm_base_url": self.llm_base_url or "",
            "llm_api_key_saved": bool(self.llm_api_key),
            "embedding_base_url": self.embedding_base_url or "",
            "embedding_api_key_saved": bool(self.embedding_api_key),
        }


class EntryRelationship(db.Model):
    """엔트리 간 명시적 관계. references는 그래프 호환을 위해 함께 유지한다."""
    __tablename__ = "entry_relationships"
    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=True)
    source_entry_id = db.Column(db.Integer, db.ForeignKey("world_entries.id"), nullable=False)
    target_entry_id = db.Column(db.Integer, db.ForeignKey("world_entries.id"), nullable=False)
    relation_type = db.Column(db.String(100), default="관련")
    description = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class WorldAttributeSchema(db.Model):
    __tablename__ = "world_attribute_schema"
    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=False, index=True)
    axis_name = db.Column(db.String(100), nullable=False)
    axis_order = db.Column(db.Integer, default=0)
    min_tier = db.Column(db.Integer, default=1)
    max_tier = db.Column(db.Integer, default=5)
    tier_labels_json = db.Column(db.Text, default="{}")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    def to_dict(self):
        try: labels = json.loads(self.tier_labels_json or "{}")
        except Exception: labels = {}
        return {"id":self.id,"world_id":self.world_id,"axis_name":self.axis_name,"axis_order":self.axis_order,"min_tier":self.min_tier,"max_tier":self.max_tier,"tier_labels":labels,"is_active":bool(self.is_active)}


class EntryAttributeValue(db.Model):
    __tablename__ = "entry_attribute_value"
    entry_id = db.Column(db.Integer, db.ForeignKey("world_entries.id"), primary_key=True)
    axis_id = db.Column(db.Integer, db.ForeignKey("world_attribute_schema.id"), primary_key=True)
    value = db.Column(db.Integer)


class WorldSkillRegistry(db.Model):
    __tablename__ = "world_skill_registry"
    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=False, index=True)
    type = db.Column(db.String(20), default="스킬")
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, default="")
    tags_json = db.Column(db.Text, default="[]")
    rarity = db.Column(db.String(30), default="일반")
    embedding_json = db.Column(db.Text, default="")
    created_by = db.Column(db.String(20), default="user")
    tick_created = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    def to_dict(self):
        try: tags=json.loads(self.tags_json or "[]")
        except Exception: tags=[]
        return {"id":self.id,"world_id":self.world_id,"type":self.type,"name":self.name,"description":self.description or "","tags":tags,"rarity":self.rarity or "일반","created_by":self.created_by,"tick_created":self.tick_created,"has_embedding":bool(self.embedding_json)}


class EntrySkillLink(db.Model):
    __tablename__ = "entry_skill_link"
    entry_id = db.Column(db.Integer, db.ForeignKey("world_entries.id"), primary_key=True)
    skill_id = db.Column(db.Integer, db.ForeignKey("world_skill_registry.id"), primary_key=True)
    rank = db.Column(db.Integer)


class WorldGuideline(db.Model):
    __tablename__ = "world_guideline"
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), primary_key=True)
    skill_generation_guide = db.Column(db.Text, default="")
    trait_generation_guide = db.Column(db.Text, default="")
    def to_dict(self): return {"world_id":self.world_id,"skill_generation_guide":self.skill_generation_guide or "","trait_generation_guide":self.trait_generation_guide or ""}


class WorldEntryTemplate(db.Model):
    __tablename__ = "world_entry_template"
    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=False, index=True)
    category = db.Column(db.String(50), nullable=False)
    fields_json = db.Column(db.Text, default="[]")
    is_active = db.Column(db.Boolean, default=True)
    def to_dict(self):
        try: fields=json.loads(self.fields_json or "[]")
        except Exception: fields=[]
        return {"id":self.id,"world_id":self.world_id,"category":self.category,"fields":fields,"is_active":bool(self.is_active)}


class NovelChapter(db.Model):
    __tablename__ = "novel_chapter"
    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=False, index=True)
    title = db.Column(db.String(250), default="새 챕터")
    order_no = db.Column(db.Integer, default=0)
    content = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="초안")
    reveal_chapter_ref = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    def to_dict(self, include_content=True):
        d={"id":self.id,"world_id":self.world_id,"title":self.title,"order_no":self.order_no,"status":self.status,"reveal_chapter_ref":self.reveal_chapter_ref,"created_at":self.created_at.isoformat()+"Z" if self.created_at else None}
        if include_content: d["content"]=self.content or ""
        return d


class NovelEntityMention(db.Model):
    __tablename__ = "novel_entity_mention"
    id = db.Column(db.Integer, primary_key=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey("novel_chapter.id"), nullable=False, index=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("world_entries.id"), nullable=False)
    span_start = db.Column(db.Integer, default=0)
    span_end = db.Column(db.Integer, default=0)
    matched_text = db.Column(db.String(250), default="")
    source = db.Column(db.String(20), default="keyword")
    def to_dict(self): return {"id":self.id,"chapter_id":self.chapter_id,"entry_id":self.entry_id,"span_start":self.span_start,"span_end":self.span_end,"matched_text":self.matched_text,"source":self.source}


class WorldWritingStyle(db.Model):
    __tablename__ = "world_writing_style"
    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=False, index=True)
    novel_id = db.Column(db.Integer, nullable=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey("novel_chapter.id"), nullable=True)
    pov = db.Column(db.String(100), default="3인칭 관찰자")
    tone_guide = db.Column(db.Text, default="")
    forbidden_expressions = db.Column(db.Text, default="")
    sample_text = db.Column(db.Text, default="")
    def to_dict(self): return {"id":self.id,"world_id":self.world_id,"novel_id":self.novel_id,"chapter_id":self.chapter_id,"pov":self.pov or "","tone_guide":self.tone_guide or "","forbidden_expressions":self.forbidden_expressions or "","sample_text":self.sample_text or ""}


class EntryRevealState(db.Model):
    __tablename__ = "entry_reveal_state"
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("world_entries.id"), nullable=False, index=True)
    field_path = db.Column(db.String(250), nullable=False)
    reveal_chapter_id = db.Column(db.Integer, db.ForeignKey("novel_chapter.id"), nullable=True)
    visibility = db.Column(db.String(20), default="작가전용")
    def to_dict(self): return {"id":self.id,"entry_id":self.entry_id,"field_path":self.field_path,"reveal_chapter_id":self.reveal_chapter_id,"visibility":self.visibility}


class SimulationConfig(db.Model):
    __tablename__ = "simulation_configs"

    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=True)
    name = db.Column(db.String(200), nullable=False)
    prompt_level_1 = db.Column(db.Text, default="")
    prompt_level_2 = db.Column(db.Text, default="")
    prompt_level_3 = db.Column(db.Text, default="")
    tick_count = db.Column(db.Integer, default=10)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "prompt_level_1": self.prompt_level_1,
            "prompt_level_2": self.prompt_level_2,
            "prompt_level_3": self.prompt_level_3,
            "tick_count": self.tick_count,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() + 'Z' if self.created_at else None,
        }


class SimulationRun(db.Model):
    __tablename__ = "simulation_runs"

    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    config_id = db.Column(db.Integer, db.ForeignKey("simulation_configs.id"))
    status = db.Column(db.String(50), default="pending")
    current_tick = db.Column(db.Integer, default=0)
    total_ticks = db.Column(db.Integer, default=0)
    # 토큰 집계
    total_tokens_in = db.Column(db.Integer, default=0)
    total_tokens_out = db.Column(db.Integer, default=0)
    total_tokens = db.Column(db.Integer, default=0)
    selected_entry_ids_json = db.Column(db.Text, nullable=True)  # None=전체
    exclude_llm_entries = db.Column(db.Boolean, default=False)   # LLM 생성 엔트리 제외 여부
    timeline_id = db.Column(db.Integer, db.ForeignKey("timelines.id"), nullable=True)  # 연결된 타임라인
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)

    config = db.relationship("SimulationConfig", backref="runs")

    def to_dict(self):
        return {
            "id": self.id,
            "config_id": self.config_id,
            "status": self.status,
            "current_tick": self.current_tick,
            "total_ticks": self.total_ticks,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_tokens": self.total_tokens,
            "exclude_llm_entries": self.exclude_llm_entries,
            "timeline_id": self.timeline_id,
            "started_at": self.started_at.isoformat() + 'Z' if self.started_at else None,
            "ended_at": self.ended_at.isoformat() + 'Z' if self.ended_at else None,
        }


class SimulationLog(db.Model):
    __tablename__ = "simulation_logs"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("simulation_runs.id"))
    tick_number = db.Column(db.Integer, default=0)
    event_type = db.Column(db.String(100))
    description = db.Column(db.Text)
    affected_entries_json = db.Column(db.Text, default="[]")
    llm_reasoning = db.Column(db.Text, default="")
    raw_llm_output = db.Column(db.Text, default="")
    # 틱당 토큰 사용량
    tokens_in = db.Column(db.Integer, default=0)
    tokens_out = db.Column(db.Integer, default=0)
    tokens_total = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    run = db.relationship("SimulationRun", backref="logs")

    @property
    def affected_entries(self):
        try:
            return json.loads(self.affected_entries_json or "[]")
        except Exception:
            return []

    def to_dict(self):
        return {
            "id": self.id,
            "run_id": self.run_id,
            "tick_number": self.tick_number,
            "event_type": self.event_type,
            "description": self.description,
            "affected_entries": self.affected_entries,
            "llm_reasoning": self.llm_reasoning,
            "raw_llm_output": self.raw_llm_output,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_total,
            "created_at": self.created_at.isoformat() + 'Z' if self.created_at else None,
        }


class WorldSnapshot(db.Model):
    """세계관 전체 상태를 레이블 붙여 저장하는 스냅샷"""
    __tablename__ = "world_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=True)
    name = db.Column(db.String(200), nullable=False)   # "세계관 1", "초기 설정" 등
    description = db.Column(db.Text, default="")
    entries_json = db.Column(db.Text, nullable=False)  # WorldEntry 목록 전체 JSON
    entry_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self, include_entries=False):
        d = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "entry_count": self.entry_count,
            "created_at": self.created_at.isoformat() + 'Z' if self.created_at else None,
        }
        if include_entries:
            try:
                d["entries"] = json.loads(self.entries_json)
            except Exception:
                d["entries"] = []
        return d


# ─────────────────────────────────────────────────────────────
#  타임라인 시스템
# ─────────────────────────────────────────────────────────────

class Timeline(db.Model):
    """대 타임라인 (스토리 아크 단위)"""
    __tablename__ = "timelines"

    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id"), nullable=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    narrative_goal = db.Column(db.Text, default="")  # 이 타임라인의 큰 서사 목표
    main_entry_id = db.Column(db.Integer, db.ForeignKey("world_entries.id"), nullable=True)  # 종속 엔트리
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    events = db.relationship(
        "TimelineEvent",
        backref="timeline",
        order_by="TimelineEvent.tick_number, TimelineEvent.order_index",
        cascade="all, delete-orphan",
    )
    beats = db.relationship(
        "StoryBeat",
        backref="timeline",
        order_by="StoryBeat.tick_number",
        cascade="all, delete-orphan",
    )

    def to_dict(self, include_events=False):
        d = {
            "id": self.id,
            "world_id": self.world_id,
            "name": self.name,
            "description": self.description,
            "narrative_goal": self.narrative_goal or "",
            "main_entry_id": self.main_entry_id,
            "event_count": len(self.events),
            "beat_count": len(self.beats),
            "created_at": self.created_at.isoformat() + 'Z' if self.created_at else None,
        }
        if include_events:
            d["events"] = [e.to_dict() for e in self.events]
        return d


class TimelineEvent(db.Model):
    """타임라인 에피소드 (틱 단위 자동 기록 또는 수동 추가)"""
    __tablename__ = "timeline_events"

    id = db.Column(db.Integer, primary_key=True)
    timeline_id = db.Column(db.Integer, db.ForeignKey("timelines.id", ondelete="CASCADE"), nullable=False)
    run_id = db.Column(db.Integer, db.ForeignKey("simulation_runs.id"), nullable=True)
    tick_number = db.Column(db.Integer, default=0)
    order_index = db.Column(db.Integer, default=0)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, default="")
    event_type = db.Column(db.String(20), default="auto")  # auto | user
    affected_entry_ids_json = db.Column(db.Text, default="[]")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def affected_entry_ids(self):
        try:
            return json.loads(self.affected_entry_ids_json or "[]")
        except Exception:
            return []

    @affected_entry_ids.setter
    def affected_entry_ids(self, value):
        self.affected_entry_ids_json = json.dumps(value or [])

    def to_dict(self):
        return {
            "id": self.id,
            "timeline_id": self.timeline_id,
            "run_id": self.run_id,
            "tick_number": self.tick_number,
            "order_index": self.order_index,
            "title": self.title,
            "description": self.description,
            "event_type": self.event_type,
            "affected_entry_ids": self.affected_entry_ids,
            "created_at": self.created_at.isoformat() + 'Z' if self.created_at else None,
        }


class StoryBeat(db.Model):
    """스토리 아크의 핵심 비트 (기-승-전-결 가이드라인)"""
    __tablename__ = "story_beats"

    id = db.Column(db.Integer, primary_key=True)
    timeline_id = db.Column(db.Integer, db.ForeignKey("timelines.id", ondelete="CASCADE"), nullable=False)
    tick_number = db.Column(db.Integer, nullable=False)       # 이 비트가 발생할 틱
    beat_label = db.Column(db.String(20), default="")         # 기/승/전/결/커스텀
    title = db.Column(db.String(300), nullable=False)          # 이 시점의 사건/목표
    description = db.Column(db.Text, default="")              # 상세 설명
    is_fixed = db.Column(db.Boolean, default=True)            # True: 유저 작성(고정), False: AI 채움
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "timeline_id": self.timeline_id,
            "tick_number": self.tick_number,
            "beat_label": self.beat_label,
            "title": self.title,
            "description": self.description,
            "is_fixed": self.is_fixed,
            "created_at": self.created_at.isoformat() + 'Z' if self.created_at else None,
        }
