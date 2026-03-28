import os
import threading
import csv
import io
import json as _json
from flask import Flask, jsonify, request, render_template, abort, Response
from models import db, WorldEntry, SimulationConfig, SimulationRun, SimulationLog, WorldSnapshot, CATEGORIES
from datetime import datetime

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///worldbuilding.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

db.init_app(app)

with app.app_context():
    db.create_all()
    # 기존 DB에 누락된 컬럼 자동 추가 (마이그레이션)
    _migrate_columns = [
        ("world_entries",    "is_summarized",           "BOOLEAN DEFAULT 0"),
        ("simulation_runs",  "total_tokens_in",         "INTEGER DEFAULT 0"),
        ("simulation_runs",  "total_tokens_out",        "INTEGER DEFAULT 0"),
        ("simulation_runs",  "total_tokens",            "INTEGER DEFAULT 0"),
        ("simulation_runs",  "selected_entry_ids_json",  "TEXT"),
        ("simulation_runs",  "exclude_llm_entries",       "BOOLEAN DEFAULT 0"),
        ("simulation_configs","max_content_chars",        "INTEGER DEFAULT 500"),
        ("simulation_logs",  "tokens_in",               "INTEGER DEFAULT 0"),
        ("simulation_logs",  "tokens_out",              "INTEGER DEFAULT 0"),
        ("simulation_logs",  "tokens_total",            "INTEGER DEFAULT 0"),
    ]
    with db.engine.connect() as conn:
        for table, col, col_def in _migrate_columns:
            try:
                conn.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"))
                conn.commit()
            except Exception:
                pass  # 이미 존재하면 무시


# ─────────────────────────────────────────
#  페이지 라우트
# ─────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", categories=CATEGORIES)


@app.route("/simulation")
def simulation_page():
    return render_template("simulation.html")


@app.route("/logs")
def logs_page():
    return render_template("logs.html")


@app.route("/graph")
def graph_page():
    return render_template("graph.html", categories=CATEGORIES)


# ─────────────────────────────────────────
#  세계관 엔트리 API
# ─────────────────────────────────────────

@app.route("/api/entries", methods=["GET"])
def list_entries():
    category = request.args.get("category")
    active_only = request.args.get("active", "true") == "true"
    q = WorldEntry.query
    if category:
        q = q.filter_by(category=category)
    if active_only:
        q = q.filter_by(is_active=True)
    entries = q.order_by(WorldEntry.created_at.desc()).all()
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
        title=data["title"],
        category=data["category"],
        content=data["content"],
        created_by="user",
        tick_created=0,
        is_active=True,
    )
    entry.references = data.get("references", [])
    db.session.add(entry)
    db.session.commit()
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
    entry.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(entry.to_dict())


@app.route("/api/entries/<int:entry_id>", methods=["DELETE"])
def delete_entry(entry_id):
    entry = WorldEntry.query.get_or_404(entry_id)
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────
#  시뮬레이션 설정 API
# ─────────────────────────────────────────

@app.route("/api/configs", methods=["GET"])
def list_configs():
    configs = SimulationConfig.query.order_by(SimulationConfig.created_at.desc()).all()
    return jsonify([c.to_dict() for c in configs])


@app.route("/api/configs", methods=["POST"])
def create_config():
    data = request.json
    if not data.get("name"):
        return jsonify({"error": "name은 필수입니다."}), 400
    config = SimulationConfig(
        name=data["name"],
        prompt_level_1=data.get("prompt_level_1", ""),
        prompt_level_2=data.get("prompt_level_2", ""),
        prompt_level_3=data.get("prompt_level_3", ""),
        tick_count=int(data.get("tick_count", 10)),
        max_content_chars=int(data.get("max_content_chars", 500)),
    )
    db.session.add(config)
    db.session.commit()
    return jsonify(config.to_dict()), 201


@app.route("/api/configs/<int:config_id>", methods=["PUT"])
def update_config(config_id):
    config = SimulationConfig.query.get_or_404(config_id)
    data = request.json
    for field in ["name", "prompt_level_1", "prompt_level_2", "prompt_level_3", "tick_count", "max_content_chars"]:
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
#  시뮬레이션 실행 API
# ─────────────────────────────────────────

@app.route("/api/runs", methods=["GET"])
def list_runs():
    runs = SimulationRun.query.order_by(SimulationRun.started_at.desc()).limit(50).all()
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
    run = SimulationRun(
        config_id=config.id,
        status="pending",
        current_tick=0,
        total_ticks=config.tick_count,
        selected_entry_ids_json=_json.dumps(entry_ids) if entry_ids else None,
        exclude_llm_entries=exclude_llm,
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

    q = SimulationLog.query
    if run_id:
        q = q.filter_by(run_id=int(run_id))
    if tick:
        q = q.filter_by(tick_number=int(tick))
    if event_type:
        q = q.filter_by(event_type=event_type)

    total = q.count()
    logs = q.order_by(SimulationLog.created_at.desc()).offset(offset).limit(limit).all()
    return jsonify({"total": total, "logs": [l.to_dict() for l in logs]})


# ─────────────────────────────────────────
#  세계관 스냅샷 API
# ─────────────────────────────────────────

@app.route("/api/snapshots", methods=["GET"])
def list_snapshots():
    snaps = WorldSnapshot.query.order_by(WorldSnapshot.created_at.desc()).all()
    return jsonify([s.to_dict() for s in snaps])


@app.route("/api/snapshots", methods=["POST"])
def save_snapshot():
    """현재 활성 엔트리 전체를 레이블 붙여 저장"""
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name은 필수입니다."}), 400

    entries = WorldEntry.query.filter_by(is_active=True, is_summarized=False).all()
    import json as _json
    entries_data = [e.to_dict() for e in entries]

    snap = WorldSnapshot(
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

    # 기존 엔트리 전체 비활성화
    WorldEntry.query.update({"is_active": False})
    db.session.flush()

    # 스냅샷 엔트리 복원
    entries_data = _json.loads(snap.entries_json)
    id_map = {}  # 구 id → 새 id
    for e in entries_data:
        new_entry = WorldEntry(
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
            messages=[{"role": "user", "content": "한 문장으로 대답하세요: 연결 테스트입니다. 현재 몇 가지 세계관 분류를 사용하나요?"}],
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
                "first_log_at": log.created_at.isoformat() if log.created_at else None,
                "last_log_at": log.created_at.isoformat() if log.created_at else None,
            }
        td = ticks_dict[t]
        td["last_log_at"] = log.created_at.isoformat() if log.created_at else None

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
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "ended_at": run.ended_at.isoformat() if run.ended_at else None,
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
    return render_template("export.html")


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
    q = WorldEntry.query.filter_by(is_active=True, is_summarized=False)
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

    result = lc.estimate_world_tokens(entries, config)
    return jsonify(result)


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
    app.run(debug=True, host="0.0.0.0", port=5000)
