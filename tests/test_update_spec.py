"""WORLDLLM_UPDATE_SPEC 핵심 회귀 테스트 (외부 LLM 호출 없음)."""
import os
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret"

from app import app, db, _ensure_world_metadata  # noqa: E402
from models import (  # noqa: E402
    EntryAttributeValue,
    EntryRevealState,
    EntrySkillLink,
    NovelChapter,
    NovelEntityMention,
    User,
    World,
    WorldAttributeSchema,
    WorldEntry,
    WorldEntryTemplate,
    NovelPart,
    WorldSkillRegistry,
)


class UpdateSpecTest(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self.world = World(name="test-" + uuid.uuid4().hex[:8])
        db.session.add(self.world)
        db.session.commit()
        _ensure_world_metadata(self.world.id)
        self.client = app.test_client()
        admin = User.query.filter_by(username="admin").first()
        with self.client.session_transaction() as sess:
            sess["user_id"] = admin.id
            sess["world_id"] = self.world.id

    def tearDown(self):
        db.session.rollback()
        self.ctx.pop()

    def add_entry(self, title="보바", aliases=None):
        row = WorldEntry(
            world_id=self.world.id,
            title=title,
            category="인물",
            content="테스트 인물",
            aliases_json=__import__("json").dumps(aliases or [], ensure_ascii=False),
        )
        db.session.add(row)
        db.session.commit()
        return row

    def test_default_templates_and_metadata_page(self):
        self.assertEqual(WorldEntryTemplate.query.filter_by(world_id=self.world.id).count(), 12)
        self.assertEqual(self.client.get("/api/metadata").status_code, 200)
        self.assertEqual(self.client.get("/metadata").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/graph").status_code, 200)

    def test_detailed_attribute_metadata_round_trip(self):
        entry = self.add_entry()
        response = self.client.put("/api/metadata/attributes", json={"attributes": [{
            "axis_name": "힘", "min_tier": 1, "max_tier": 20,
            "description": "근력 판정과 운반 능력", "tier_labels": {"14": "단련됨"},
        }]})
        self.assertEqual(response.status_code, 200)
        axis = WorldAttributeSchema.query.filter_by(world_id=self.world.id, axis_name="힘").one()
        self.client.put("/api/metadata/attribute-values", json={"values": [{
            "entry_id": entry.id, "axis_id": axis.id, "value": 14,
            "description": "용병 생활로 단련된 근력",
        }]})
        stat = self.client.get(f"/api/entries/{entry.id}/stat-block").get_json()["attributes"][0]
        self.assertEqual(stat["value"], 14)
        self.assertEqual(stat["label"], "단련됨")
        self.assertEqual(stat["description"], "용병 생활로 단련된 근력")
        self.assertEqual(stat["axis_description"], "근력 판정과 운반 능력")

    def test_attribute_schema_has_every_tier_description_key(self):
        response = self.client.put("/api/metadata/attributes", json={"attributes": [{
            "axis_name": "근력", "min_tier": 1, "max_tier": 10,
            "tier_descriptions": {str(i): f"{i}단계 행동 서술" for i in range(1, 11)},
        }]})
        self.assertEqual(response.status_code, 200)
        axis = WorldAttributeSchema.query.filter_by(world_id=self.world.id, axis_name="근력").one()
        data = axis.to_dict()
        self.assertEqual(set(data["tier_descriptions"]), {str(i) for i in range(1, 11)})
        self.assertEqual(data["tier_descriptions"]["10"], "10단계 행동 서술")
        self.assertEqual(data["tier_labels"], data["tier_descriptions"])

    def test_llm_metadata_apply_accepts_detailed_values(self):
        entry = self.add_entry()
        response = self.client.post("/api/metadata/migration-apply", json={
            "attribute_schemas": [{"axis_name": "통찰", "min_tier": 1, "max_tier": 5,
                                   "description": "숨은 의도를 읽는 능력", "tier_labels": {"4": "예리함"}}],
            "entries": [{"entry_id": entry.id, "attributes": {
                "통찰": {"value": 4, "description": "오랜 협상 경험에서 비롯됨"}
            }}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["applied"], 1)
        value = EntryAttributeValue.query.filter_by(entry_id=entry.id).one()
        self.assertEqual(value.value, 4)
        self.assertEqual(value.description, "오랜 협상 경험에서 비롯됨")

    def test_metadata_apply_normalizes_axis_names_and_reports_skips(self):
        entry = self.add_entry()
        response = self.client.post("/api/metadata/migration-apply", json={
            "attribute_schemas": [{"axis_name": "정신 력", "min_tier": 1, "max_tier": 10}],
            "entries": [{"entry_id": entry.id, "attributes": {
                "정신력": {"value": 8, "description": "강한 의지"},
                "없는 축": {"value": 3},
            }}],
        })
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["created_schemas"], 1)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(EntryAttributeValue.query.filter_by(entry_id=entry.id).one().description, "강한 의지")

    def test_schema_fill_retries_an_omitted_axis(self):
        import llm_client
        responses = [
            {"attribute_schemas": [{"axis_name": "지능", "description": "사고 판정", "tier_descriptions": {"1": "기초 사고", "2": "복합 사고"}}]},
            {"attribute_schemas": [{"axis_name": "힘", "description": "근력 판정", "tier_descriptions": {"1": "가벼운 짐", "2": "무거운 짐"}}]},
        ]
        completion = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=__import__("json").dumps(responses.pop(0), ensure_ascii=False)))]
        ))
        client = SimpleNamespace(chat=SimpleNamespace(completions=completion))
        axes = [
            {"axis_name": "힘", "min_tier": 1, "max_tier": 2},
            {"axis_name": "지능", "min_tier": 1, "max_tier": 2},
        ]
        with patch.object(llm_client, "get_llm_client", return_value=(client, "test-model")):
            result = llm_client.propose_attribute_schema_fill(axes)
        self.assertEqual({x["axis_name"] for x in result["attribute_schemas"]}, {"힘", "지능"})
        self.assertEqual(result["missing_axes_after_retry"], [])
        self.assertIn("자동 보충 재요청", result["_raw"])

    def test_schema_fill_preserves_existing_fields(self):
        import llm_client
        response = {"attribute_schemas": [{
            "axis_name": "지능", "description": "덮어쓰면 안 됨",
            "tier_descriptions": {"1": "덮어쓰면 안 됨", "2": "복합 사고"},
        }]}
        completion = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=__import__("json").dumps(response, ensure_ascii=False)))]
        ))
        client = SimpleNamespace(chat=SimpleNamespace(completions=completion))
        axes = [{
            "axis_name": "지능", "min_tier": 1, "max_tier": 2,
            "description": "기존 판정 설명", "tier_descriptions": {"1": "기존 기초 사고", "2": ""},
        }]
        with patch.object(llm_client, "get_llm_client", return_value=(client, "test-model")):
            result = llm_client.propose_attribute_schema_fill(axes)
        schema = result["attribute_schemas"][0]
        self.assertEqual(schema["description"], "기존 판정 설명")
        self.assertEqual(schema["tier_descriptions"]["1"], "기존 기초 사고")
        self.assertEqual(schema["tier_descriptions"]["2"], "복합 사고")

    def test_schema_fill_plan_limits_request_to_selected_active_axis(self):
        self.client.put("/api/metadata/attributes", json={"attributes": [
            {"axis_name": "힘", "min_tier": 1, "max_tier": 2, "is_active": True},
            {"axis_name": "지능", "min_tier": 1, "max_tier": 2, "is_active": True},
        ]})
        proposed = {"attribute_schemas": [], "missing_axes_after_retry": [], "_raw": ""}
        with patch("llm_client.propose_attribute_schema_fill", return_value=proposed) as mocked:
            response = self.client.post("/api/metadata/schema-fill-plan", json={"axis_names": ["지능"]})
        self.assertEqual(response.status_code, 200)
        requested_axes = mocked.call_args.args[0]
        self.assertEqual([axis["axis_name"] for axis in requested_axes], ["지능"])

    def test_character_assignment_plan_limits_request_to_one_character(self):
        first = self.add_entry("첫 인물")
        self.add_entry("둘째 인물")
        self.client.put("/api/metadata/attributes", json={"attributes": [{
            "axis_name": "힘", "min_tier": 1, "max_tier": 2, "is_active": True,
        }]})
        proposed = {"attribute_schemas": [], "entries": [], "missing_values_after_retry": {}, "_raw": ""}
        with patch("llm_client.propose_character_attribute_fill", return_value=proposed) as mocked:
            response = self.client.post("/api/metadata/migration-plan", json={
                "entry_ids": [first.id], "axis_names": ["힘"],
            })
        self.assertEqual(response.status_code, 200)
        requested_entries, requested_axes, _ = mocked.call_args.args
        self.assertEqual([entry["id"] for entry in requested_entries], [first.id])
        self.assertEqual([axis["axis_name"] for axis in requested_axes], ["힘"])

    def test_generated_character_attributes_are_saved_and_visible_in_sheet(self):
        self.client.put("/api/metadata/attributes", json={"attributes":[{
            "axis_name":"힘", "min_tier":1, "max_tier":10,
            "tier_descriptions":{str(i):f"힘 {i}단계" for i in range(1,11)},
        }]})
        response=self.client.post("/api/entries",json={
            "title":"생성 전사","category":"인물","content":"전사",
            "attributes":{"힘":{"value":7,"description":"용병 훈련"}},
        })
        self.assertEqual(response.status_code,201)
        entry_id=response.get_json()["id"]
        stat=self.client.get(f"/api/entries/{entry_id}/stat-block").get_json()["attributes"][0]
        self.assertEqual(stat["value"],7)
        self.assertEqual(stat["tier_description"],"힘 7단계")
        self.assertEqual(stat["description"],"용병 훈련")

    def test_single_entry_generation_unwraps_nested_metadata_json(self):
        import json
        import llm_client
        inner = {
            "content": "공개 인물 설명",
            "secret": "숨겨진 혈통",
            "attributes": {"힘": {"value": 7, "description": "오랜 훈련"}},
        }
        raw = json.dumps({"content": json.dumps(inner, ensure_ascii=False)}, ensure_ascii=False)
        completion = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
        ))
        client = SimpleNamespace(chat=SimpleNamespace(completions=completion))
        with patch.object(llm_client, "get_llm_client", return_value=(client, "test-model")):
            result = llm_client.generate_entry("인물", "인물", "", [], "", max_chars=0)
        self.assertEqual(result["content"], "공개 인물 설명")
        self.assertNotIn("숨겨진 혈통", result["content"])
        self.assertEqual(result["secret"], "숨겨진 혈통")
        self.assertEqual(result["attributes"]["힘"]["value"], 7)

    def test_single_entry_generation_removes_metadata_appended_to_content(self):
        import json
        import llm_client
        appended = json.dumps({
            "secret": "감춰진 계약",
            "attributes": {"지능": {"value": 6, "description": "학자 교육"}},
        }, ensure_ascii=False)
        payload = {"content": f"공개 설명입니다.\n```json\n{appended}\n```"}
        raw = json.dumps(payload, ensure_ascii=False)
        normalized = llm_client._normalize_generated_entry_response(raw, payload)
        self.assertEqual(normalized["content"], "공개 설명입니다.")
        self.assertNotIn("감춰진 계약", normalized["content"])
        self.assertEqual(normalized["secret"], "감춰진 계약")
        self.assertEqual(normalized["attributes"]["지능"]["value"], 6)

    def test_generated_attribute_axis_ignores_spacing_difference(self):
        self.client.put("/api/metadata/attributes", json={"attributes": [{
            "axis_name": "카리스마/화술", "min_tier": 1, "max_tier": 10,
        }]})
        response = self.client.post("/api/entries", json={
            "title": "외교관", "category": "인물", "content": "공개 설명",
            "secret": "비밀 협상가",
            "attributes": {"카리스마 / 화술": {"value": 8, "description": "능숙한 협상"}},
        })
        self.assertEqual(response.status_code, 201)
        entry_id = response.get_json()["id"]
        stat = self.client.get(f"/api/entries/{entry_id}/stat-block").get_json()["attributes"][0]
        self.assertEqual(stat["axis_name"], "카리스마/화술")
        self.assertEqual(stat["value"], 8)
        self.assertEqual(self.client.get(f"/api/entries/{entry_id}/secret").get_json()["secret"], "비밀 협상가")

    def test_secret_requires_explicit_endpoint_and_is_backed_up(self):
        response=self.client.post("/api/entries",json={"title":"비밀 인물","category":"인물","content":"공개 정보","secret":"왕위 계승자"})
        entry_id=response.get_json()["id"]
        public=self.client.get(f"/api/entries/{entry_id}").get_json()
        self.assertNotIn("secret",public)
        self.assertNotIn("has_secret",public)
        secret=self.client.get(f"/api/entries/{entry_id}/secret").get_json()
        self.assertEqual(secret["secret"],"왕위 계승자")
        backup=self.client.get(f"/api/worlds/{self.world.id}/backup").get_json()
        self.assertEqual(backup["extensions"]["secrets"][0]["secret"],"왕위 계승자")

    def test_same_identity_and_family_tree_links(self):
        person=self.add_entry("현재 이름")
        former=self.add_entry("과거 이름")
        father=self.add_entry("아버지")
        mother=self.add_entry("어머니")
        child=self.add_entry("자식")
        response=self.client.put(f"/api/entries/{person.id}/character-links",json={
            "identity_ids":[former.id],"father_id":father.id,"mother_id":mother.id,
            "children":[{"entry_id":child.id,"role":"부모"}],
        })
        self.assertEqual(response.status_code,200)
        links=response.get_json()
        self.assertEqual({x["person"]["title"] for x in links["parents"]},{"아버지","어머니"})
        self.assertEqual(links["identities"][0]["title"],"과거 이름")
        tree=self.client.get(f"/api/entries/{person.id}/character-links?recursive=true").get_json()
        self.assertEqual({x["title"] for x in tree["entries"]},{"현재 이름","과거 이름","아버지","어머니","자식"})

    def test_item_multiple_owners_and_ranked_search(self):
        owner1=self.add_entry("김철수",aliases=["철의 기사"])
        owner2=self.add_entry("박영희")
        item=WorldEntry(world_id=self.world.id,title="왕의 검",category="물건",content="검")
        db.session.add(item);db.session.commit()
        response=self.client.put(f"/api/entries/{item.id}/owners",json={"owner_ids":[owner2.id,owner1.id]})
        self.assertEqual(response.status_code,200)
        self.assertEqual({x["title"] for x in response.get_json()},{"김철수","박영희"})
        ranked=self.client.get(f"/api/entries/{item.id}/owner-candidates?q=철의").get_json()
        self.assertTrue(ranked[0]["selected"])
        self.assertEqual(ranked[0]["entry"]["title"],"김철수")
        self.assertEqual(set(WorldEntry.query.get(item.id).references),{owner1.id,owner2.id})

    def test_output_token_setting_supports_256k_and_no_entry_char_limit(self):
        settings=self.client.get("/api/settings").get_json()
        self.assertEqual(settings["max_llm_entry_chars"],0)
        response=self.client.put("/api/settings",json={"llm_max_output_tokens":262144,"max_llm_entry_chars":0})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()["llm_max_output_tokens"],262144)

    def test_master_entry_char_limit_is_enforced_for_llm_apply(self):
        self.client.put("/api/settings",json={"max_llm_entry_chars":5})
        response=self.client.post("/api/world-design/apply",json={"entries":[{
            "title":"제한 테스트","category":"관념","content":"1234567890",
        }]})
        self.assertEqual(response.status_code,200)
        restored=WorldEntry.query.filter_by(world_id=self.world.id,title="제한 테스트").one()
        self.assertEqual(restored.content,"12345")
        self.client.put("/api/settings",json={"max_llm_entry_chars":0})

    @patch("llm_client.propose_entry_from_chat")
    def test_chat_can_create_reviewable_markdown_entry_draft(self, propose):
        propose.return_value = {
            "title": "검은 탑", "category": "장소",
            "content": "## 개요\n\n**마력**이 흐르는 탑입니다.",
            "secret": "탑 자체가 생명체다.", "aliases": ["흑탑"],
        }
        response = self.client.post("/api/chat/entry-draft", json={
            "history": [
                {"role": "user", "content": "검은 탑을 설정하자."},
                {"role": "assistant", "content": "마력이 흐르는 장소로 정리할까요?"},
            ]
        })
        self.assertEqual(response.status_code, 200)
        draft = response.get_json()
        self.assertEqual(draft["category"], "장소")
        self.assertIn("## 개요", draft["content"])
        self.assertEqual(draft["secret"], "탑 자체가 생명체다.")

    def test_title_and_alias_keyword_tagging(self):
        entry = self.add_entry(aliases=["피투성이 보바"])
        chapter = self.client.post("/api/novel/chapters", json={"title": "1장"}).get_json()
        self.client.put(f"/api/novel/parts/{chapter['part_id']}", json={"entry_ids": [entry.id]})
        response = self.client.put(
            f"/api/novel/chapters/{chapter['id']}",
            json={"content": "보바가 왔다. 피투성이 보바는 웃었다."},
        )
        self.assertEqual(response.status_code, 200)
        mentions = response.get_json()["mentions"]
        self.assertEqual({m["matched_text"] for m in mentions}, {"보바", "피투성이 보바"})
        self.assertTrue(all(m["entry_id"] == entry.id for m in mentions))
        self.assertTrue(all(m["category"] == "인물" for m in mentions))

    def test_novel_style_is_global_master_setting(self):
        chapter = self.client.post("/api/novel/chapters", json={"title": "스타일"}).get_json()
        self.client.put("/api/settings", json={"novel_pov": "전지적", "novel_tone_guide": "건조"})
        base = self.client.get("/api/settings").get_json()
        legacy = self.client.get(f"/api/novel/style?chapter_id={chapter['id']}").get_json()
        self.assertEqual(base["novel_pov"], "전지적")
        self.assertEqual(base["novel_tone_guide"], "건조")
        self.assertEqual(legacy["pov"], "전지적")
        self.assertIsNone(legacy["chapter_id"])

    def test_novel_part_selected_entries_limit_generation_context(self):
        selected = self.add_entry("선택 인물")
        self.add_entry("제외 인물")
        part = self.client.post("/api/novel/parts", json={
            "title": "선택 이야기", "entry_ids": [selected.id],
        }).get_json()
        self.assertEqual(part["entry_ids"], [selected.id])
        catalog = self.client.get("/api/novel/entry-catalog?q=선택").get_json()
        self.assertEqual([row["id"] for row in catalog], [selected.id])
        chapter = self.client.post("/api/novel/chapters", json={
            "part_id": part["id"], "title": "선택 장",
        }).get_json()
        proposed = {"proposal": "본문", "new_entity_proposals": []}
        with patch("llm_client.generate_novel_text", return_value=proposed) as mocked:
            response = self.client.post("/api/novel/generate", json={
                "chapter_id": chapter["id"], "instruction": "계속",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked.call_args.args[3], [selected.id])
        self.assertEqual(mocked.call_args.args[2]["part"]["id"], part["id"])

    def test_novel_parts_and_public_reader_without_login(self):
        part = self.client.post("/api/novel/parts", json={
            "title": "보바의 이야기", "description": "보바의 몰락과 귀환",
        }).get_json()
        entry = self.add_entry("보바")
        entry.content = "공개되면 안 되는 작가 전용 DB 본문"
        entry.secret_content = "절대로 공개되면 안 되는 비밀"
        db.session.commit()
        self.client.put(f"/api/novel/parts/{part['id']}", json={"entry_ids": [entry.id]})
        chapter = self.client.post("/api/novel/chapters", json={
            "part_id": part["id"], "title": "첫 장", "content": "## 시작\n\n보바가 돌아왔다.",
        }).get_json()
        self.client.put(f"/api/novel/chapters/{chapter['id']}", json={"content": "## 시작\n\n보바가 돌아왔다."})
        self.client.put(f"/api/entries/{entry.id}/reveal", json={
            "states": [{"field_path": "content", "visibility": "작가전용"}],
        })
        chapter_share = self.client.post(f"/api/novel/chapters/{chapter['id']}/publish", json={"public": True}).get_json()
        part_share = self.client.post(f"/api/novel/parts/{part['id']}/publish", json={"public": True}).get_json()
        with self.client.session_transaction() as sess:
            sess.clear()
        chapter_page = self.client.get(urlsplit(chapter_share["url"]).path)
        part_page = self.client.get(urlsplit(part_share["url"]).path)
        self.assertEqual(chapter_page.status_code, 200)
        self.assertEqual(part_page.status_code, 200)
        page_text = part_page.get_data(as_text=True)
        self.assertIn("보바의 이야기", page_text)
        self.assertIn("보바의 몰락과 귀환", page_text)
        self.assertIn(f'"id": {chapter["id"]}', page_text)
        self.assertNotIn("작가 전용 DB 본문", page_text)
        self.assertNotIn("절대로 공개되면 안 되는 비밀", page_text)
        with self.client.session_transaction() as sess:
            admin = User.query.filter_by(username="admin").first()
            sess["user_id"] = admin.id; sess["world_id"] = self.world.id
        self.client.post(f"/api/novel/parts/{part['id']}/publish", json={"public": False})
        with self.client.session_transaction() as sess:
            sess.clear()
        self.assertEqual(self.client.get(urlsplit(part_share["url"]).path).status_code, 404)

    def test_entry_delete_cleans_extension_rows(self):
        entry = self.add_entry()
        axis = WorldAttributeSchema(world_id=self.world.id, axis_name="힘")
        skill = WorldSkillRegistry(world_id=self.world.id, name="검술")
        db.session.add_all([axis, skill]); db.session.flush()
        chapter = self.client.post("/api/novel/chapters", json={"title": "삭제"}).get_json()
        part = NovelPart.query.get(chapter["part_id"]);part.entry_ids=[entry.id]
        db.session.add_all([
            EntryAttributeValue(entry_id=entry.id, axis_id=axis.id, value=3),
            EntrySkillLink(entry_id=entry.id, skill_id=skill.id),
            EntryRevealState(entry_id=entry.id, visibility="작가전용", field_path="content"),
            NovelEntityMention(chapter_id=chapter["id"], entry_id=entry.id, matched_text="보바"),
        ])
        db.session.commit()
        self.assertEqual(self.client.delete(f"/api/entries/{entry.id}").status_code, 200)
        self.assertEqual(EntryAttributeValue.query.filter_by(entry_id=entry.id).count(), 0)
        self.assertEqual(EntrySkillLink.query.filter_by(entry_id=entry.id).count(), 0)
        self.assertEqual(EntryRevealState.query.filter_by(entry_id=entry.id).count(), 0)
        self.assertEqual(NovelEntityMention.query.filter_by(entry_id=entry.id).count(), 0)
        self.assertEqual(NovelPart.query.get(part.id).entry_ids, [])

    def test_world_backup_contains_extensions(self):
        payload = self.client.get(f"/api/worlds/{self.world.id}/backup").get_json()
        self.assertEqual(payload["version"], 4)
        self.assertIn("extensions", payload)
        self.assertIn("templates", payload["extensions"])

    def test_world_restore_remaps_extension_references(self):
        entry = self.add_entry(aliases=["별칭"])
        axis = WorldAttributeSchema(world_id=self.world.id, axis_name="판단력", description="상황을 읽는 축")
        skill = WorldSkillRegistry(world_id=self.world.id, name="추적")
        db.session.add_all([axis, skill]); db.session.flush()
        db.session.add_all([
            EntryAttributeValue(entry_id=entry.id, axis_id=axis.id, value=4, description="추적 경험으로 예리함"),
            EntrySkillLink(entry_id=entry.id, skill_id=skill.id, rank=2),
        ]); db.session.commit()
        chapter = self.client.post("/api/novel/chapters", json={"title": "복원 장"}).get_json()
        self.client.put(f"/api/novel/parts/{chapter['part_id']}", json={"entry_ids": [entry.id]})
        self.client.put(f"/api/novel/chapters/{chapter['id']}", json={"content": "별칭이 나타났다."})
        self.client.put("/api/novel/style", json={"chapter_id": chapter["id"], "pov": "1인칭"})
        self.client.put(f"/api/entries/{entry.id}/reveal", json={"states": [{"field_path": "content", "visibility": "챕터공개", "reveal_chapter_id": chapter["id"]}]})
        payload = self.client.get(f"/api/worlds/{self.world.id}/backup").get_json()
        response = self.client.post(f"/api/worlds/{self.world.id}/restore", json=payload)
        self.assertEqual(response.status_code, 200)
        restored = WorldEntry.query.filter_by(world_id=self.world.id, title="보바").one()
        restored_value = EntryAttributeValue.query.filter_by(entry_id=restored.id).one()
        self.assertEqual(restored_value.description, "추적 경험으로 예리함")
        self.assertEqual(WorldAttributeSchema.query.filter_by(world_id=self.world.id, axis_name="판단력").one().description, "상황을 읽는 축")
        self.assertEqual(EntrySkillLink.query.filter_by(entry_id=restored.id).count(), 1)
        self.assertEqual(EntryRevealState.query.filter_by(entry_id=restored.id).count(), 1)
        self.assertEqual(NovelEntityMention.query.filter_by(entry_id=restored.id).count(), 1)
        restored_chapter = NovelChapter.query.filter_by(world_id=self.world.id, title="복원 장").one()
        self.assertIsNotNone(restored_chapter.part_id)
        restored_part = NovelPart.query.get(restored_chapter.part_id)
        self.assertIsNotNone(restored_part)
        self.assertEqual(restored_part.entry_ids, [restored.id])


if __name__ == "__main__":
    unittest.main()
