"""
시뮬레이션 엔진 - 틱 루프 실행 및 DB 반영
토큰 사용량 추적 및 컨텍스트 한계 시 자동 요약 포함
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

                # 활성 엔트리 조회 (요약된 항목 제외)
                entries = [
                    e.to_dict()
                    for e in WorldEntry.query.filter_by(is_active=True, is_summarized=False).all()
                ]

                # 컨텍스트 한계 근접 시 자동 요약 먼저 실행
                if llm_client.needs_summary(entries):
                    _run_auto_summary(run, tick, entries, config.to_dict())
                    db.session.commit()
                    # 요약 후 갱신된 엔트리 목록으로 재조회
                    entries = [
                        e.to_dict()
                        for e in WorldEntry.query.filter_by(is_active=True, is_summarized=False).all()
                    ]

                # 일반 틱 실행
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


def _run_auto_summary(run: SimulationRun, tick: int, entries: list, config: dict):
    """컨텍스트 한계 근접 시 전체 세계관 자동 요약"""
    result = llm_client.run_summary(config, tick, entries)
    reasoning = result.get("reasoning", "")
    raw = result.get("_raw", "")
    tokens_in = result.get("_tokens_in", 0)
    tokens_out = result.get("_tokens_out", 0)
    tokens_total = result.get("_total_tokens", 0)

    covered_ids = []
    for summary in result.get("summaries", []):
        # 요약 엔트리 생성
        entry = WorldEntry(
            title=summary.get("title", f"세계관 요약 - 틱 {tick}"),
            category=summary.get("category", "관념"),
            content=summary.get("content", ""),
            created_by=CREATOR_LLM,
            tick_created=tick,
            is_active=True,
            is_summarized=False,
        )
        db.session.add(entry)
        covered = summary.get("covered_entry_ids", [])
        covered_ids.extend(covered)

        # 요약 대상 엔트리들을 summarized 처리 (DB에는 남김)
        for eid in covered:
            original = WorldEntry.query.get(eid)
            if original:
                original.is_summarized = True
                original.updated_at = datetime.utcnow()

    # 토큰 집계
    run.total_tokens_in = (run.total_tokens_in or 0) + tokens_in
    run.total_tokens_out = (run.total_tokens_out or 0) + tokens_out
    run.total_tokens = (run.total_tokens or 0) + tokens_total

    log = SimulationLog(
        run_id=run.id,
        tick_number=tick,
        event_type="context_summary",
        description=f"컨텍스트 한계 근접 - {len(covered_ids)}개 엔트리를 요약 압축 (틱 {tick})",
        affected_entries_json=str(covered_ids),
        llm_reasoning=reasoning,
        raw_llm_output=raw,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_total=tokens_total,
    )
    db.session.add(log)


def _apply_tick_result(run: SimulationRun, tick: int, result: dict):
    """LLM 결과를 DB에 반영하고 로그 기록"""
    reasoning = result.get("reasoning", "")
    raw = result.get("_raw", "")
    tokens_in = result.get("_tokens_in", 0)
    tokens_out = result.get("_tokens_out", 0)
    tokens_total = result.get("_total_tokens", 0)

    # 런 전체 토큰 집계
    run.total_tokens_in = (run.total_tokens_in or 0) + tokens_in
    run.total_tokens_out = (run.total_tokens_out or 0) + tokens_out
    run.total_tokens = (run.total_tokens or 0) + tokens_total

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
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_total=tokens_total,
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
            tokens_in=0, tokens_out=0, tokens_total=0,
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
        db.session.flush()
        log = SimulationLog(
            run_id=run.id,
            tick_number=tick,
            event_type="entry_created",
            description=f"[{entry.category}] '{entry.title}' 생성됨",
            affected_entries_json=f"[{entry.id}]",
            llm_reasoning=reasoning,
            raw_llm_output=raw,
            tokens_in=0, tokens_out=0, tokens_total=0,
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
            tokens_in=0, tokens_out=0, tokens_total=0,
        )
        db.session.add(log)


def _log_error(run: SimulationRun, error_msg: str):
    try:
        log = SimulationLog(
            run_id=run.id,
            tick_number=run.current_tick,
            event_type="error",
            description=error_msg,
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        pass
