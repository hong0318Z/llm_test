from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json

db = SQLAlchemy()

CATEGORIES = ["세력", "인물", "관념", "물건", "종족", "사건"]
CREATOR_USER = "user"
CREATOR_LLM = "llm"


class WorldEntry(db.Model):
    __tablename__ = "world_entries"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text, nullable=False)
    references_json = db.Column(db.Text, default="[]")
    created_by = db.Column(db.String(20), default=CREATOR_USER)
    tick_created = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    is_summarized = db.Column(db.Boolean, default=False)  # 요약으로 대체된 항목
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
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SimulationConfig(db.Model):
    __tablename__ = "simulation_configs"

    id = db.Column(db.Integer, primary_key=True)
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
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SimulationRun(db.Model):
    __tablename__ = "simulation_runs"

    id = db.Column(db.Integer, primary_key=True)
    config_id = db.Column(db.Integer, db.ForeignKey("simulation_configs.id"))
    status = db.Column(db.String(50), default="pending")
    current_tick = db.Column(db.Integer, default=0)
    total_ticks = db.Column(db.Integer, default=0)
    # 토큰 집계
    total_tokens_in = db.Column(db.Integer, default=0)
    total_tokens_out = db.Column(db.Integer, default=0)
    total_tokens = db.Column(db.Integer, default=0)
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
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
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
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WorldSnapshot(db.Model):
    """세계관 전체 상태를 레이블 붙여 저장하는 스냅샷"""
    __tablename__ = "world_snapshots"

    id = db.Column(db.Integer, primary_key=True)
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
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_entries:
            try:
                d["entries"] = json.loads(self.entries_json)
            except Exception:
                d["entries"] = []
        return d
