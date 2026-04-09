from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json

db = SQLAlchemy()

CATEGORIES = ["세력", "인물", "관념", "물건", "종족", "사건", "장소", "마법/기술", "신화/종교", "역사/기록", "규칙/법"]
CREATOR_USER = "user"
CREATOR_LLM = "llm"


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
    is_active = db.Column(db.Boolean, default=True)
    is_summarized = db.Column(db.Boolean, default=False)  # 요약으로 대체된 항목
    keywords = db.Column(db.Text, default="")  # 쉼표 구분 핵심 키워드 (최대 5개)
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
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "content": self.content,
            "references": self.references,
            "created_by": self.created_by,
            "tick_created": self.tick_created,
            "is_active": self.is_active,
            "is_summarized": self.is_summarized,
            "keywords": self.keywords or "",
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
    # 유저 입력 엔트리 1개당 최대 글자수 (UI 카운터용, 0 = 제한 없음)
    max_user_entry_chars = db.Column(db.Integer, default=1000)
    # RAG 토큰 예산: 세계관이 이 값의 50%를 초과하면 관련 엔트리만 선택 (0 = RAG 비활성화)
    rag_token_budget = db.Column(db.Integer, default=0)

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
            "max_user_entry_chars": self.max_user_entry_chars if self.max_user_entry_chars is not None else 1000,
            "rag_token_budget": self.rag_token_budget if self.rag_token_budget is not None else 0,
        }


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
