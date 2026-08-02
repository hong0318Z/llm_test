import gradio as gr

import core
from tag_db import TagDB

with gr.Blocks(title="WD14 Tag Analyzer") as demo:
    gr.Markdown("# WD14 Tag Analyzer (이미지 태그 분석 / EXIF)")

    db_state = gr.State(TagDB())

    with gr.Accordion("설정", open=True):
        tag_db_file = gr.File(label="단부루 태그 CSV 업로드 (선택, 서버에 저장되어 재시작 후에도 유지됨)", file_types=[".csv"])
        tag_db_status = gr.Markdown("태그 DB가 로드되지 않았습니다. (선택 사항)")

        demo.load(
            lambda: core.load_saved_state()[1:3],
            inputs=None,
            outputs=[db_state, tag_db_status],
        )
        tag_db_file.change(core.load_tag_db, inputs=tag_db_file, outputs=[db_state, tag_db_status])

    with gr.Tab("이미지 태그 분석"):
        with gr.Row():
            with gr.Column():
                image_input = gr.Image(type="pil", label="이미지 업로드")
                general_threshold = gr.Slider(0, 1, value=0.35, label="General tag threshold")
                character_threshold = gr.Slider(0, 1, value=0.85, label="Character tag threshold")
                filter_existing = gr.Checkbox(
                    label="업로드한 태그 DB에 존재하는 태그만 표시", value=False
                )
                tag_btn = gr.Button("태그 분석", variant="primary")
            with gr.Column():
                tag_output = gr.Textbox(label="태그 (복사해서 사용)", lines=4)
                tag_detail = gr.Textbox(label="상세 결과 (확률 포함)", lines=15)

        tag_btn.click(
            core.tag_image,
            inputs=[image_input, general_threshold, character_threshold, db_state, filter_existing],
            outputs=[tag_output, tag_detail],
        )

    with gr.Tab("EXIF / PNG 메타데이터 분석"):
        with gr.Row():
            with gr.Column():
                exif_image = gr.Image(type="filepath", label="이미지 업로드 (원본 메타데이터 보존)")
                exif_btn = gr.Button("메타데이터 분석")
            with gr.Column():
                exif_prompt = gr.Textbox(label="추출된 프롬프트 (있는 경우)", lines=4)
                exif_raw = gr.Textbox(label="원본 메타데이터", lines=12)

        exif_btn.click(
            core.analyze_image_metadata,
            inputs=[exif_image],
            outputs=[exif_raw, exif_prompt],
        )


if __name__ == "__main__":
    demo.launch(server_port=7860)
