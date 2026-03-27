"""
시뮬레이션 엔진 - 틱 루프 실행 및 DB 반영
"""
from datetime import datetime
from models import db, WorldEntry, SimulationRun, SimulationLog, CREATOR_LLM
import llm_client


def run_simulation(run_id: int, app):
    """백그라운드에서 시뮬레이션 실행 (Flask app context 필요)"""
    with app.app_context():
        run = SimulationRun.query.get(run_id)
        if not run:
            return

        config = run.config
        run.status = "running"
        db.session.commit()

        try:
            for tick in range(1, run.total_ticks + 1):
                run.current_tick = tick
                db.session.commit()

                # 현재 활성 엔트리 조회
                entries = [
                    e.to_dict()
                    for e in WorldEntry.query.filter_by(is_active=True).all()
                ]

                # LLM 호출
                result = llm_client.run_tick(config.to_dict(), tick, entries)
                _apply_tick_result(run, tick, result)
                db.session.commit()

            run.status = "done"
            run.ended_at = datetime.utcnow()
            db.session.commit()

        except Exception as e:
            run.status = "error"
            run.ended_at = datetime.utcnow()
            db.session.commit()
            _log_error(run, str(e))


def _apply_tick_result(run: SimulationRun, tick: int, result: dict):
    """LLM 결과를 DB에 반영하고 로그 기록"""
    reasoning = result.get("reasoning", "")
    raw = result.get("_raw", "")

    # 1. 세계 사건 로그
    for event in result.get("events", []):
        log = SimulationLog(
            run_id=run.id,
            tick_number=tick,
            event_type="world_event",
            description=event.get("description", ""),
            affected_entries_json=str(event.get("affected_entry_ids", [])),
            llm_reasoning=reasoning,
            raw_llm_output=raw,
        )
        db.session.add(log)

    # 2. 기존 엔트리 업데이트
    for update in result.get("entry_updates", []):
        entry = WorldEntry.query.get(update.get("id"))
        if not entry:
            continue
        old_content = entry.content
        entry.content = update.get("new_content", entry.content)
        entry.updated_at = datetime.utcnow()
        log = SimulationLog(
            run_id=run.id,
            tick_number=tick,
            event_type="entry_updated",
            description=f"[{entry.category}] {entry.title}: {update.get('reason', '')}",
            affected_entries_json=f"[{entry.id}]",
            llm_reasoning=reasoning,
            raw_llm_output=f"이전: {old_content[:200]}",
        )
        db.session.add(log)

    # 3. 새 엔트리 생성
    for new in result.get("new_entries", []):
        entry = WorldEntry(
            title=new.get("title", "이름없음"),
            category=new.get("category", "관념"),
            content=new.get("content", ""),
            created_by=CREATOR_LLM,
            tick_created=tick,
            is_active=True,
        )
        entry.references = new.get("references", [])
        db.session.add(entry)
        db.session.flush()  # ID 확보
        log = SimulationLog(
            run_id=run.id,
            tick_number=tick,
            event_type="entry_created",
            description=f"[{entry.category}] '{entry.title}' 생성됨",
            affected_entries_json=f"[{entry.id}]",
            llm_reasoning=reasoning,
            raw_llm_output=raw,
        )
        db.session.add(log)

    # 4. 엔트리 소멸
    for deact in result.get("deactivated_entries", []):
        entry = WorldEntry.query.get(deact.get("id"))
        if not entry:
            continue
        entry.is_active = False
        entry.updated_at = datetime.utcnow()
        log = SimulationLog(
            run_id=run.id,
            tick_number=tick,
            event_type="entry_deactivated",
            description=f"[{entry.category}] '{entry.title}' 소멸: {deact.get('reason', '')}",
            affected_entries_json=f"[{entry.id}]",
            llm_reasoning=reasoning,
            raw_llm_output=raw,
        )
        db.session.add(log)


def _log_error(run: SimulationRun, error_msg: str):
    with db.session.begin_nested():
        log = SimulationLog(
            run_id=run.id,
            tick_number=run.current_tick,
            event_type="error",
            description=error_msg,
        )
        db.session.add(log)
    db.session.commit()
