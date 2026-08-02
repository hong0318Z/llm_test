import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gradio as gr

import core
import embedding_client
import llm_client
import presets as preset_store
from tag_db import TagDB

def _base_prompt_editor(key, label):
    """An accordion exposing a tab's real system prompt (what's actually sent to the AI)
    for direct viewing/editing, instead of only being appendable via standing_notes/
    extra_system_prompt. Edits are saved and persist across restarts. Returns the textbox
    to pass as that tab's `base_prompt` input."""
    default = core.BASE_PROMPT_DEFAULTS[key]
    with gr.Accordion(f"🔧 {label} 기본 프롬프트 보기/수정 (영어, 고급)", open=False):
        gr.Markdown("AI에게 실제로 전달되는 시스템 지침입니다. 직접 보고 내용을 추가/수정하세요 - 저장되어 유지됩니다.")
        box = gr.Textbox(value=core.get_base_prompt(key, default), lines=16, show_label=False)
        with gr.Row():
            reset_btn = gr.Button("기본값으로 초기화", size="sm")
            prompt_status = gr.Markdown("")
        box.change(lambda v, k=key: core.save_base_prompt(v, k), inputs=box)
        reset_btn.click(lambda k=key, d=default: core.reset_base_prompt(k, d), outputs=[box, prompt_status])
    return box


with gr.Blocks(title="NAI Prompt Generator") as demo:
    gr.Markdown(
        "# NAI Prompt Generator\n"
        "LLM으로 NAI용 태그/씬 프롬프트를 기획·생성하는 도구입니다. "
        "① 태그 조합 → ② 에셋 네이밍/가이드 → ③ 다중 씬 시리즈 → ④ 대화로 다듬기 → ⑤ JSON 통합 순으로 사용하세요."
    )

    db_state = gr.State(TagDB())
    history_state = gr.State([])

    with gr.Accordion("⚙️ 설정", open=True):
        # Provider
        provider_radio = gr.Radio(
            choices=list(llm_client.PROVIDERS.keys()),
            value=list(llm_client.PROVIDERS.keys())[0],
            label="프로바이더",
        )
        with gr.Row():
            api_key = gr.Textbox(
                label="API Key (로컬 서버는 빈값 가능, 저장됨)",
                type="password",
                placeholder="sk-... 또는 비워두기",
                scale=2,
            )
            base_url = gr.Textbox(
                label="서버 URL",
                value=llm_client.DEFAULT_BASE_URL,
                scale=3,
            )
            model_select = gr.Dropdown(
                label="모델",
                choices=llm_client.AVAILABLE_MODELS,
                value=llm_client.DEFAULT_MODEL,
                allow_custom_value=True,
                scale=2,
            )
            model_refresh_btn = gr.Button("모델 목록 조회", scale=1)
        model_status = gr.Markdown("")

        def _on_provider_change(provider):
            saved_key = core.get_api_key_for_provider(provider)
            saved_url = core.get_base_url_for_provider(provider)
            models, saved_model = core.get_model_choices_for_provider(provider)
            core.save_provider(provider)
            return saved_url, gr.update(choices=models, value=saved_model), saved_key

        provider_radio.change(
            _on_provider_change,
            inputs=provider_radio,
            outputs=[base_url, model_select, api_key],
        )
        model_refresh_btn.click(
            core.list_main_models,
            inputs=[api_key, base_url, model_select],
            outputs=[model_select, model_status],
        )

        with gr.Row():
            tag_db_file = gr.File(
                label="단부루 태그 CSV (여러 개 동시 상주 가능, 저장됨)",
                file_types=[".csv"], file_count="multiple", scale=3,
            )
            tag_db_status = gr.Markdown("태그 DB 없음", scale=2)

        gr.Markdown("##### 임베딩 (태그 DB 검색용, 항상 로컬 시스템으로 호출됨)")
        with gr.Row():
            embedding_base_url = gr.Textbox(
                label="임베딩 서버 URL (저장됨)",
                value=embedding_client.DEFAULT_EMBEDDING_BASE_URL,
                scale=3,
            )
            embedding_api_key = gr.Textbox(
                label="임베딩 API Key (선택, 저장됨)",
                type="password",
                placeholder="로컬 서버는 빈값 가능",
                scale=2,
            )
            embedding_model_select = gr.Dropdown(
                label="임베딩 모델 (저장됨)",
                choices=embedding_client.DEFAULT_EMBEDDING_MODELS,
                value=embedding_client.DEFAULT_EMBEDDING_MODEL,
                allow_custom_value=True,
                scale=2,
            )
            embedding_models_refresh_btn = gr.Button("모델 목록 조회", scale=1)
        embedding_models_status = gr.Markdown("")

        standing_notes = gr.Textbox(
            label="고정 지시사항 (모든 생성에 포함, 저장됨)",
            placeholder="예: faceless male은 얼굴 태그 제외. 배경 항상 실내.",
            lines=3,
        )
        extra_system_prompt = gr.Textbox(
            label="추가 시스템 프롬프트 (모든 AI 호출의 시스템 메시지 끝에 추가됨, 저장됨)",
            placeholder="예: 항상 태그를 50개 이상 출력할 것. 배경 태그는 반드시 포함.",
            lines=3,
        )
        with gr.Row():
            accumulate_context = gr.Checkbox(
                label="컨텍스트 누적 (이전 대화 기억)", value=False
            )
            use_db_reference = gr.Checkbox(
                label="DB 참조 (임베딩 검색, 컨텍스트 누적 중 같은 구도 전개 시 끌 수 있음)", value=True
            )
            gemini_thinking = gr.Checkbox(
                label="Gemini 추론(Thinking) 사용 (기본 꺼짐 - Google 프로바이더에만 적용, 켜면 느려지고 토큰이 늘어남)",
                value=False,
            )
            clear_history_btn = gr.Button("대화 기록 초기화", size="sm")
            history_status = gr.Markdown("")

        demo.load(
            core.load_saved_state,
            inputs=None,
            outputs=[api_key, db_state, tag_db_status, standing_notes, base_url, extra_system_prompt, provider_radio,
                     embedding_base_url, embedding_model_select, embedding_api_key, model_select],
        )
        api_key.change(core.save_api_key_for_provider, inputs=[api_key, provider_radio])
        base_url.change(core.save_base_url, inputs=[base_url, provider_radio])
        model_select.change(core.save_model_for_provider, inputs=[model_select, provider_radio])
        embedding_base_url.change(core.save_embedding_base_url, inputs=embedding_base_url)
        embedding_model_select.change(core.save_embedding_model, inputs=embedding_model_select)
        embedding_api_key.change(core.save_embedding_api_key, inputs=embedding_api_key)
        embedding_models_refresh_btn.click(
            core.list_embedding_models,
            inputs=[embedding_api_key, embedding_base_url, embedding_model_select],
            outputs=[embedding_model_select, embedding_models_status],
        )
        tag_db_file.change(core.load_tag_db, inputs=tag_db_file, outputs=[db_state, tag_db_status])
        standing_notes.change(core.save_notes, inputs=standing_notes)
        extra_system_prompt.change(core.save_extra_system_prompt, inputs=extra_system_prompt)

        def _clear_history():
            return [], "대화 기록 초기화됨"

        clear_history_btn.click(_clear_history, outputs=[history_state, history_status])
        accumulate_context.change(
            lambda v: "컨텍스트 누적 ON" if v else "컨텍스트 누적 OFF",
            inputs=accumulate_context, outputs=history_status,
        )

    with gr.Tab("1. 태그 조합 생성"):
        gr.Markdown("자연어로 원하는 이미지를 설명하면 AI가 태그를 조합합니다.")
        combo_base_prompt = _base_prompt_editor("tag_combo", "태그 조합")
        combo_request = gr.Textbox(label="요청 내용 (한국어 가능)", lines=4)
        combo_variant_count = gr.Slider(1, 5, value=1, step=1, label="생성 개수 (variants)")
        combo_btn = gr.Button("태그 조합 생성", variant="primary")
        combo_tags = gr.Textbox(label="결과 태그 (복사해서 사용)", lines=6)
        combo_explanation = gr.Textbox(label="설명 (한국어)", lines=4)
        combo_send_to_chat_btn = gr.Button("이 결과로 4번 탭(AI 대화)에서 계속하기 →", size="sm")
        combo_debug = gr.Textbox(label="검색된 후보 태그 (디버그)", lines=10)

        gr.Markdown("(요청 입력란에서 Ctrl+Enter로 마우스 없이 생성)")
        gr.on(
            triggers=[combo_btn.click, combo_request.submit],
            fn=core.generate_tag_combo,
            inputs=[api_key, combo_request, db_state, combo_variant_count, standing_notes,
                    history_state, accumulate_context, model_select, base_url, extra_system_prompt,
                    embedding_base_url, embedding_model_select, embedding_api_key, use_db_reference,
                    gemini_thinking, combo_base_prompt],
            outputs=[combo_tags, combo_explanation, combo_debug, history_state],
        )

        gr.Markdown("---\n#### 태그 프리셋 저장/불러오기")
        with gr.Row():
            preset_name = gr.Textbox(label="프리셋 이름", scale=2)
            preset_save_btn = gr.Button("현재 결과 태그 저장", scale=1)
        with gr.Row():
            preset_dropdown = gr.Dropdown(
                choices=list(preset_store.load_presets().keys()),
                label="저장된 프리셋", scale=2,
            )
            preset_load_btn = gr.Button("불러오기", scale=1)
            preset_delete_btn = gr.Button("삭제", scale=1)
        preset_status = gr.Markdown("")

        def _save_preset(name, tags):
            if not name or not name.strip():
                return gr.update(), "프리셋 이름을 입력해주세요."
            if not tags or not tags.strip():
                return gr.update(), "저장할 태그가 없습니다."
            new_presets = preset_store.save_preset(name, tags)
            return gr.update(choices=list(new_presets.keys()), value=name.strip()), f"'{name.strip()}' 저장 완료"

        def _load_preset(name):
            if not name:
                return "", "프리셋을 선택해주세요."
            presets = preset_store.load_presets()
            return presets.get(name, ""), f"'{name}' 불러옴"

        def _delete_preset(name):
            if not name:
                return gr.update(), "삭제할 프리셋을 선택해주세요."
            new_presets = preset_store.delete_preset(name)
            return gr.update(choices=list(new_presets.keys()), value=None), f"'{name}' 삭제됨"

        preset_save_btn.click(_save_preset, inputs=[preset_name, combo_tags], outputs=[preset_dropdown, preset_status])
        preset_load_btn.click(_load_preset, inputs=[preset_dropdown], outputs=[combo_tags, preset_status])
        preset_delete_btn.click(_delete_preset, inputs=[preset_dropdown], outputs=[preset_dropdown, preset_status])

    with gr.Tab("2. 에셋 네이밍 · 가이드"):
        gr.Markdown("파일명 정의 / 씬 프롬프트 / 에셋 가이드 작성을 한 곳에서 - 시리즈 생성 전 기획 단계로 활용하세요.")
        asset_base_prompt = _base_prompt_editor("asset", "에셋 네이밍/가이드")
        asset_mode = gr.Radio(
            choices=list(core.MODE_TRIGGERS.keys()),
            value=list(core.MODE_TRIGGERS.keys())[1],
            label="모드 선택",
        )
        asset_chars = gr.Textbox(label="캐릭터 정의 (예: a=Alice, b=Bob)", lines=1)
        asset_input = gr.Textbox(label="요청 내용 (한국어 가능)", lines=6)
        asset_btn = gr.Button("생성", variant="primary")
        asset_output = gr.Textbox(label="결과", lines=12)

        asset_btn.click(
            core.generate_asset_output,
            inputs=[api_key, asset_mode, asset_chars, asset_input, history_state, accumulate_context,
                    model_select, base_url, extra_system_prompt, gemini_thinking, asset_base_prompt],
            outputs=[asset_output, history_state],
        )

    with gr.Tab("3. 다중 씬(시리즈) 생성"):
        gr.Markdown("시리즈 설명 → NAIS 프리셋 JSON (스트리밍). 아래 입력값은 앱을 껐다 켜도 그대로 유지됩니다.")
        series_base_prompt = _base_prompt_editor("multi_scene", "다중 씬 생성")
        series_chars = gr.Textbox(label="캐릭터/카테고리 정의 (예: a=Alice, b=Bob)", lines=1)
        with gr.Row():
            series_fixed_reference = gr.Textbox(
                label="고정 레퍼런스 태그 (모든 씬에 그대로 유지, 예: 캐릭터 외형)",
                placeholder="예: 1girl, silver hair, twintails, red eyes",
                lines=3, scale=1,
            )
            series_flexible_reference = gr.Textbox(
                label="변경 가능 레퍼런스 태그 (씬마다 자유롭게 바뀌어도 되는 풀, 예: 포즈/표정)",
                placeholder="예: smiling, blushing, looking back, sitting, standing",
                lines=3, scale=1,
            )
        series_description = gr.Textbox(label="시리즈 설명 (한국어, 자유 서술)", lines=6)
        series_scene_list = gr.Textbox(
            label="씬 목록 (선택, 씬이름 : 설명 형식으로 한 줄에 하나씩 - 채우면 이 목록의 이름/개수를 그대로 사용)",
            placeholder="m_com_1 : 공원에서 산책하며 웃는 모습\nm_com_2 : 벤치에 앉아 고민하는 모습",
            lines=6,
        )
        with gr.Row():
            series_negative_prompt = gr.Textbox(
                label="네거티브 프롬프트 (모든 씬에 공통 적용)",
                value=core.DEFAULT_NEGATIVE_PROMPT,
                lines=2, scale=3,
            )
            series_width = gr.Number(label="가로(width)", value=core.DEFAULT_SCENE_WIDTH, precision=0, scale=1)
            series_height = gr.Number(label="세로(height)", value=core.DEFAULT_SCENE_HEIGHT, precision=0, scale=1)
        series_btn = gr.Button("시리즈 JSON 생성", variant="primary")
        series_status = gr.Markdown("")
        series_output = gr.Code(label="결과 JSON (NAI 프리셋에 붙여넣기)", language="json", lines=25)
        series_download = gr.DownloadButton(label="JSON 파일 다운로드", visible=False)
        series_send_to_chat_btn = gr.Button("이 결과로 4번 탭(AI 대화)에서 계속하기 →", size="sm")
        series_debug = gr.Textbox(label="디버그 (후보 태그 / AI 원본 응답)", lines=15)

        gr.Markdown("(시리즈 설명 입력란에서 Ctrl+Enter로 마우스 없이 생성)")
        gr.on(
            triggers=[series_btn.click, series_description.submit],
            fn=core.generate_multi_scene,
            inputs=[api_key, series_description, series_chars, db_state, standing_notes,
                    history_state, accumulate_context, model_select, base_url, extra_system_prompt,
                    embedding_base_url, embedding_model_select, embedding_api_key, use_db_reference,
                    series_fixed_reference, series_flexible_reference, series_scene_list,
                    series_negative_prompt, series_width, series_height, gemini_thinking,
                    series_base_prompt],
            outputs=[series_output, series_status, series_debug, history_state],
        ).then(
            core.prepare_json_download,
            inputs=[series_output],
            outputs=[series_download],
        )

        _series_draft_fields = {
            "chars": series_chars,
            "fixed_reference": series_fixed_reference,
            "flexible_reference": series_flexible_reference,
            "description": series_description,
            "scene_list": series_scene_list,
            "negative_prompt": series_negative_prompt,
        }
        for _field_name, _component in _series_draft_fields.items():
            _component.change(
                (lambda v, f=_field_name: core.save_series_draft_field(v, f)),
                inputs=_component,
            )

        demo.load(
            core.load_series_draft,
            inputs=None,
            outputs=[series_chars, series_fixed_reference, series_flexible_reference,
                     series_description, series_scene_list, series_negative_prompt],
        )

    with gr.Tab("4. AI 대화 / 편집"):
        chat_base_prompt = _base_prompt_editor("chat", "AI 대화")
        gr.Markdown(
            "### 💬 AI 대화\n"
            "다른 탭에서 만든 결과를 베이스로 붙여넣거나 \"이 결과로 계속하기\" 버튼으로 가져와서, "
            "대화하듯 다듬으세요. 베이스 없이도 자유 질의가 가능합니다.\n\n"
            "**바로 되는 것**: *\"아까 한 것의 바리에이션 3개 만들어줘\"* (변형 생성) · "
            "*\"표정만 바꿔줘\"* (그 부분만 수정, 나머지는 그대로 유지)"
        )
        with gr.Row(equal_height=True):
            with gr.Column(scale=1):
                with gr.Group():
                    base_content = gr.Textbox(
                        label="📋 베이스 콘텐츠",
                        lines=16,
                        placeholder="다른 탭에서 \"이 결과로 계속하기\"를 누르거나, "
                                     "기존 프롬프트/NAIS JSON을 직접 붙여넣으면 AI가 참고합니다.",
                    )
                    with gr.Row():
                        clear_base_btn = gr.Button("베이스 비우기", size="sm")
            with gr.Column(scale=2):
                chat_display = gr.Chatbot(label="대화", height=420)
                with gr.Row():
                    quick_variation_btn = gr.Button("🔀 바리에이션 3개", size="sm")
                    quick_edit_btn = gr.Button("✏️ 특정 부분만 수정...", size="sm")
                chat_input = gr.Textbox(
                    label="메시지 (Enter로 전송, Shift+Enter로 줄바꿈)",
                    placeholder="예: 이 JSON에 씬 3개 추가해줘 / 아까 한 것의 바리에이션 만들어줘 / 표정만 바꿔줘",
                    lines=2,
                )
                with gr.Row():
                    chat_send_btn = gr.Button("전송", variant="primary", scale=4)
                    chat_clear_btn = gr.Button("대화 초기화", scale=1)

        with gr.Accordion("최근 AI 응답 (복사용 텍스트 보기)", open=False):
            chat_response = gr.Textbox(label="", lines=8, show_label=False)

        def _chat_send(api_key_val, base_url_val, model_val, user_msg, base_cont,
                       notes_val, extra_prompt_val, chatbot_history, history_val, accumulate_val,
                       thinking_val, base_prompt_val):
            if not user_msg or not user_msg.strip():
                yield chatbot_history, "", history_val
                return

            new_display = list(chatbot_history) + [{"role": "user", "content": user_msg}]
            yield new_display, "", history_val

            response = ""
            new_history = history_val
            for response, new_history in core.chat_with_context(
                api_key_val, base_url_val, model_val, user_msg, base_cont,
                notes_val, history_val, accumulate_val, extra_prompt_val, thinking_val, base_prompt_val,
            ):
                display = list(new_display) + [{"role": "assistant", "content": response}]
                yield display, response, new_history

        _chat_inputs = [api_key, base_url, model_select, chat_input, base_content,
                        standing_notes, extra_system_prompt, chat_display, history_state,
                        accumulate_context, gemini_thinking, chat_base_prompt]

        chat_send_btn.click(
            _chat_send, inputs=_chat_inputs, outputs=[chat_display, chat_response, history_state],
        ).then(lambda: "", outputs=chat_input)

        chat_input.submit(
            _chat_send, inputs=_chat_inputs, outputs=[chat_display, chat_response, history_state],
        ).then(lambda: "", outputs=chat_input)

        quick_variation_btn.click(lambda: "아까 한 것의 바리에이션 3개 만들어줘", outputs=chat_input)
        quick_edit_btn.click(lambda: "아까 한 것에서 ", outputs=chat_input)

        chat_clear_btn.click(lambda: ([], ""), outputs=[chat_display, chat_response])
        clear_base_btn.click(lambda: "", outputs=base_content)

        with gr.Accordion("🛠 씬 목록 → 지침 생성 (부가 기능)", open=False):
            gr.Markdown(
                "기존에 정리한 목록(태그/JSON 등)과 씬 이름 리스트를 주면, AI가 그 안의 명명/구조 규칙을 분석해서 "
                "앞으로의 모든 생성 프롬프트에 계속 상주시킬 영어 지침으로 정리해줍니다."
            )
            guideline_base_prompt = _base_prompt_editor("guideline", "지침 생성")
            with gr.Row():
                guideline_existing_list = gr.Textbox(
                    label="기존에 정리한 목록 (태그 조합, JSON 등 붙여넣기)", lines=8, scale=1,
                )
                guideline_scene_names = gr.Textbox(
                    label="씬 이름 리스트", lines=8, scale=1,
                    placeholder="m_com_1\nm_com_2\nm_sex_4\n...",
                )
            guideline_btn = gr.Button("지침 생성 (영어)")
            guideline_status = gr.Markdown("")
            guideline_output = gr.Textbox(label="생성된 지침 (영어, 수정 가능)", lines=10)
            guideline_apply_btn = gr.Button("고정 지시사항에 추가", variant="primary")

            guideline_btn.click(
                core.generate_scene_guideline,
                inputs=[api_key, guideline_existing_list, guideline_scene_names, model_select, base_url,
                        extra_system_prompt, gemini_thinking, guideline_base_prompt],
                outputs=[guideline_output, guideline_status],
            )
            guideline_apply_btn.click(
                core.append_to_standing_notes,
                inputs=[guideline_output, standing_notes],
                outputs=[standing_notes, guideline_status],
            )

        combo_send_to_chat_btn.click(
            lambda tags, expl: (tags or "") + (f"\n\n{expl}" if expl and expl.strip() else ""),
            inputs=[combo_tags, combo_explanation],
            outputs=base_content,
        )
        series_send_to_chat_btn.click(
            lambda json_text: (json_text or "", "이 결과로 4번 탭(AI 대화)에서 계속하기 → 베이스 콘텐츠로 가져왔습니다."),
            inputs=[series_output],
            outputs=[base_content, series_status],
        )

    with gr.Tab("5. JSON 통합"):
        gr.Markdown("NAIS 프리셋 JSON 여러 개를 업로드하면 씬들을 하나의 프리셋으로 합쳐줍니다. (AI 호출 없음)")
        merge_files = gr.File(label="통합할 JSON 파일들", file_count="multiple", file_types=[".json"])
        merge_btn = gr.Button("통합", variant="primary")
        merge_status = gr.Markdown("")
        merge_output = gr.Code(label="통합된 JSON", language="json", lines=25)
        merge_download = gr.DownloadButton(label="JSON 파일 다운로드", visible=False)

        merge_btn.click(
            core.merge_json_presets,
            inputs=[merge_files],
            outputs=[merge_output, merge_status],
        ).then(
            core.prepare_json_download,
            inputs=[merge_output],
            outputs=[merge_download],
        )


if __name__ == "__main__":
    # Windows periodically reserves chunks of the port range for WSL2/Hyper-V (visible via
    # `netsh interface ipv4 show excludedportrange`), and which chunk gets reserved changes
    # across reboots - a port that worked yesterday can be silently unbindable today even
    # with nothing else listening on it. Rather than hardcode one port and tell the user to
    # go dig through netsh output again, just try a spread of ports and launch on whichever
    # one actually binds - Gradio prints the real URL/port it started on either way.
    start_port = int(os.environ.get("GRADIO_SERVER_PORT", 8861))
    candidate_ports = [start_port] + [start_port + i for i in range(1, 30)]

    for i, port in enumerate(candidate_ports):
        try:
            demo.launch(server_port=port)
            break
        except OSError:
            if i == len(candidate_ports) - 1:
                raise
            print(f"[app_generator] 포트 {port} 사용 불가 (Windows 예약 범위일 수 있음) - 다음 포트 시도...")
