"""
시뮬레이션 엔진 - 틱 루프 실행 및 DB 반영
토큰 사용량 추적 및 컨텍스트 한계 시 자동 요약 포함
"""
from datetime import datetime
from models import db, WorldEntry, SimulationRun, SimulationLog, TimelineEvent, StoryBeat, Timeline, AppSettings, CREATOR_LLM, CREATOR_USER
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

        world_id = run.world_id  # 세계관 컨텍스트

        def _query_entries(extra_ids=None):
            q = WorldEntry.query.filter_by(is_active=True, is_summarized=False).filter(
                db.or_(WorldEntry.is_superseded.is_(False), WorldEntry.is_superseded.is_(None))
            )
            if world_id:
                q = q.filter_by(world_id=world_id)
            ids = extra_ids or selected_ids
            if ids:
                q = q.filter(WorldEntry.id.in_(ids))
            if exclude_llm:
                q = q.filter_by(created_by="user")
            return [e.to_dict() for e in q.all()]

        # 연결된 타임라인의 스토리 비트 로드
        story_beats = []
        narrative_goal = ""
        if run.timeline_id:
            tl = Timeline.query.get(run.timeline_id)
            if tl:
                narrative_goal = tl.narrative_goal or ""
                beats_raw = StoryBeat.query.filter_by(timeline_id=run.timeline_id).order_by(StoryBeat.tick_number).all()
                for b in beats_raw:
                    d = b.to_dict()
                    d["_narrative_goal"] = narrative_goal  # 전체 서사 목표도 주입
                    story_beats.append(d)

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

                # 일반 틱 실행 (스토리 비트 가이드 포함)
                result = llm_client.run_tick(
                    cfg_dict, tick, entries,
                    recent_context=recent_context,
                    story_beats=story_beats if story_beats else None,
                )
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

                # 타임라인 연결된 경우 에피소드 자동 기록
                if run.timeline_id:
                    _record_timeline_event(run, tick, result)

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
            world_id=run.world_id,
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

    # 2. 기존 엔트리 업데이트 → 새 버전 엔트리 생성 (원본 불변)
    for update in result.get("entry_updates", []):
        entry = WorldEntry.query.get(update.get("id"))
        if not entry or entry.is_superseded:
            continue

        if entry.created_by == CREATOR_USER:
            # 유저 엔트리: 직접 수정 금지 → 파생 버전 생성 (원본 is_superseded 유지)
            new_ver = WorldEntry(
                world_id=run.world_id,
                title=entry.title,
                category=entry.category,
                content=update.get("new_content", ""),
                created_by=CREATOR_LLM,
                tick_created=tick,
                is_active=True,
                parent_entry_id=entry.id,
                version_note=f"틱{tick} 파생",
            )
            new_ver.references = list(entry.references or []) + [entry.id]
            db.session.add(new_ver)
            db.session.flush()
            log = SimulationLog(
                run_id=run.id,
                tick_number=tick,
                event_type="entry_versioned",
                description=f"[{entry.category}] '{entry.title}' 파생 버전 생성 (유저 원본 보호): {update.get('reason', '')}",
                affected_entries_json=f"[{entry.id}, {new_ver.id}]",
                llm_reasoning=reasoning,
                raw_llm_output=raw,
                tokens_in=0, tokens_out=0, tokens_total=0,
            )
            db.session.add(log)
        else:
            # LLM 엔트리: 구버전을 superseded 처리 → 새 버전 생성
            entry.is_superseded = True
            entry.updated_at = datetime.utcnow()
            new_ver = WorldEntry(
                world_id=run.world_id,
                title=entry.title,
                category=entry.category,
                content=update.get("new_content", ""),
                created_by=CREATOR_LLM,
                tick_created=tick,
                is_active=True,
                parent_entry_id=entry.id,
                version_note=f"틱{tick} 수정",
            )
            new_ver.references = list(entry.references or [])
            db.session.add(new_ver)
            db.session.flush()
            log = SimulationLog(
                run_id=run.id,
                tick_number=tick,
                event_type="entry_versioned",
                description=f"[{entry.category}] '{entry.title}' 새 버전 생성 (틱{tick}): {update.get('reason', '')}",
                affected_entries_json=f"[{entry.id}, {new_ver.id}]",
                llm_reasoning=reasoning,
                raw_llm_output=raw,
                tokens_in=0, tokens_out=0, tokens_total=0,
            )
            db.session.add(log)

    # 3. 새 엔트리 생성
    for new in result.get("new_entries", []):
        entry = WorldEntry(
            world_id=run.world_id,
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

    # 4. 엔트리 소멸 → 소멸 버전 엔트리 생성 (원본 삭제/비활성화 없음)
    for deact in result.get("deactivated_entries", []):
        entry = WorldEntry.query.get(deact.get("id"))
        if not entry or entry.is_superseded:
            continue

        reason = deact.get("reason", "")

        if entry.created_by == CREATOR_USER:
            # 유저 엔트리: 원본 유지, 소멸 기록만 파생 생성
            end_entry = WorldEntry(
                world_id=run.world_id,
                title=entry.title,
                category=entry.category,
                content=f"[소멸 기록] {reason}",
                created_by=CREATOR_LLM,
                tick_created=tick,
                is_active=True,
                parent_entry_id=entry.id,
                version_note=f"틱{tick} 소멸",
            )
            end_entry.references = [entry.id]
            db.session.add(end_entry)
            db.session.flush()
            log = SimulationLog(
                run_id=run.id,
                tick_number=tick,
                event_type="entry_versioned",
                description=f"[{entry.category}] '{entry.title}' 소멸 기록 생성 (유저 원본 보호): {reason}",
                affected_entries_json=f"[{entry.id}, {end_entry.id}]",
                llm_reasoning=reasoning,
                raw_llm_output=raw,
                tokens_in=0, tokens_out=0, tokens_total=0,
            )
            db.session.add(log)
        else:
            # LLM 엔트리: 구버전 superseded 처리 → 소멸 버전 생성
            entry.is_superseded = True
            entry.updated_at = datetime.utcnow()
            end_entry = WorldEntry(
                world_id=run.world_id,
                title=entry.title,
                category=entry.category,
                content=f"[소멸] {reason}",
                created_by=CREATOR_LLM,
                tick_created=tick,
                is_active=True,
                parent_entry_id=entry.id,
                version_note=f"틱{tick} 소멸",
            )
            end_entry.references = list(entry.references or [])
            db.session.add(end_entry)
            db.session.flush()
            log = SimulationLog(
                run_id=run.id,
                tick_number=tick,
                event_type="entry_versioned",
                description=f"[{entry.category}] '{entry.title}' 소멸 (틱{tick}): {reason}",
                affected_entries_json=f"[{entry.id}, {end_entry.id}]",
                llm_reasoning=reasoning,
                raw_llm_output=raw,
                tokens_in=0, tokens_out=0, tokens_total=0,
            )
            db.session.add(log)


def _record_timeline_event(run: SimulationRun, tick: int, result: dict):
    """틱 결과를 타임라인 에피소드로 자동 기록"""
    reasoning = result.get("reasoning", "")
    events = result.get("events", [])
    new_entries = result.get("new_entries", [])
    deactivated = result.get("deactivated_entries", [])

    # 이번 틱에서 영향받은 엔트리 ID 수집
    affected_ids = set()
    for ev in events:
        affected_ids.update(ev.get("affected_entry_ids", []))
    for upd in result.get("entry_updates", []):
        if upd.get("id"):
            affected_ids.add(upd["id"])
    for deact in deactivated:
        if deact.get("id"):
            affected_ids.add(deact["id"])

    # 에피소드 제목: 첫 번째 이벤트 설명 또는 기본값
    if events:
        title = events[0].get("description", f"틱 {tick} 이벤트")[:120]
    elif new_entries:
        title = f"틱 {tick}: {new_entries[0].get('title', '새 엔트리')} 등 {len(new_entries)}개 생성"
    else:
        title = f"틱 {tick} 진행"

    # 설명: LLM 추론 요약
    description = reasoning or ""
    if len(events) > 1:
        extras = [e.get("description", "") for e in events[1:4]]
        description += "\n\n" + "\n".join(f"• {d}" for d in extras if d)

    # order_index: 같은 tick 내 순서
    existing = TimelineEvent.query.filter_by(
        timeline_id=run.timeline_id, tick_number=tick
    ).count()

    ev = TimelineEvent(
        timeline_id=run.timeline_id,
        run_id=run.id,
        tick_number=tick,
        order_index=existing,
        title=title.strip(),
        description=description.strip(),
        event_type="auto",
    )
    ev.affected_entry_ids = list(affected_ids)
    db.session.add(ev)


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
