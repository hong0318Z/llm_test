"""
시뮬레이션 엔진 - 틱 루프 실행 및 DB 반영
토큰 사용량 추적 및 컨텍스트 한계 시 자동 요약 포함
"""
from datetime import datetime
from models import db, WorldEntry, SimulationRun, SimulationLog, AppSettings, CREATOR_LLM, CREATOR_USER
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

        import json as _json
        selected_ids = None
        if run.selected_entry_ids_json:
            try:
                selected_ids = _json.loads(run.selected_entry_ids_json)
            except Exception:
                selected_ids = None
        exclude_llm = bool(run.exclude_llm_entries)

        def _query_entries(extra_ids=None):
            q = WorldEntry.query.filter_by(is_active=True, is_summarized=False)
            ids = extra_ids or selected_ids
            if ids:
                q = q.filter(WorldEntry.id.in_(ids))
            if exclude_llm:
                q = q.filter_by(created_by="user")
            return [e.to_dict() for e in q.all()]

        try:
            for tick in range(1, run.total_ticks + 1):
                # 매 틱 시작 전 취소 여부 확인
                db.session.refresh(run)
                if run.status == "cancelled":
                    break

                run.current_tick = tick
                db.session.commit()

                entries = _query_entries()

                # 컨텍스트 한계 근접 시 자동 요약 먼저 실행
                settings = AppSettings.get()
                max_chars = settings.max_llm_entry_chars or 500
                rag_budget = settings.rag_token_budget or 0
                cfg_dict = {**config.to_dict(), "max_content_chars": max_chars, "rag_token_budget": rag_budget}
                if llm_client.needs_summary(entries, max_chars=max_chars):
                    _run_auto_summary(run, tick, entries, cfg_dict)
                    db.session.commit()
                    entries = _query_entries()

                # 최근 틱 로그 → RAG 컨텍스트 문자열 생성
                recent_logs = (
                    SimulationLog.query
                    .filter_by(run_id=run.id)
                    .order_by(SimulationLog.id.desc())
                    .limit(12)
                    .all()
                )
                recent_context = " ".join(
                    l.description for l in reversed(recent_logs) if l.description
                )

                # 일반 틱 실행
                result = llm_client.run_tick(cfg_dict, tick, entries, recent_context=recent_context)
                # RAG가 적용됐으면 로그 기록
                rag_info = result.get("_rag_info")
                if rag_info and rag_info.get("rag_applied"):
                    rag_log = SimulationLog(
                        run_id=run.id,
                        tick_number=tick,
                        event_type="rag_filter",
                        description=(
                            f"RAG 필터 적용: 전체 {rag_info['total']}개 중 "
                            f"{rag_info['selected']}개 선택 "
                            f"(유저 {rag_info['user_entries']}개 필수 + "
                            f"LLM {rag_info['llm_selected']}/{rag_info['llm_total']}개)"
                        ),
                    )
                    db.session.add(rag_log)

                _apply_tick_result(run, tick, result)
                db.session.commit()

            if run.status != "cancelled":
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

        # 유저 엔트리는 직접 수정 금지 → 새 LLM 엔트리로 대체
        if entry.created_by == CREATOR_USER:
            derived = WorldEntry(
                title=f"{entry.title} (변화 - 틱 {tick})",
                category=entry.category,
                content=update.get("new_content", ""),
                created_by=CREATOR_LLM,
                tick_created=tick,
                is_active=True,
            )
            derived.references = [entry.id]
            db.session.add(derived)
            db.session.flush()
            log = SimulationLog(
                run_id=run.id,
                tick_number=tick,
                event_type="entry_created",
                description=f"[{entry.category}] '{entry.title}' 파생 엔트리 생성 (유저 원본 보호): {update.get('reason', '')}",
                affected_entries_json=f"[{derived.id}]",
                llm_reasoning=reasoning,
                raw_llm_output=raw,
                tokens_in=0, tokens_out=0, tokens_total=0,
            )
            db.session.add(log)
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

        # 유저 엔트리는 비활성화 금지 → 로그만 기록하고 무시
        if entry.created_by == CREATOR_USER:
            log = SimulationLog(
                run_id=run.id,
                tick_number=tick,
                event_type="entry_protected",
                description=f"[{entry.category}] '{entry.title}' 비활성화 시도 차단 (유저 원본 보호): {deact.get('reason', '')}",
                affected_entries_json=f"[{entry.id}]",
                llm_reasoning=reasoning,
                raw_llm_output=raw,
                tokens_in=0, tokens_out=0, tokens_total=0,
            )
            db.session.add(log)
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
