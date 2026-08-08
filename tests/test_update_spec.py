"""WORLDLLM_UPDATE_SPEC 핵심 회귀 테스트 (외부 LLM 호출 없음)."""
import os
import unittest
import uuid

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret"

from app import app, db, _ensure_world_metadata  # noqa: E402
from models import (  # noqa: E402
    EntryAttributeValue,
    EntryRevealState,
    EntrySkillLink,
    NovelEntityMention,
    User,
    World,
    WorldAttributeSchema,
    WorldEntry,
    WorldEntryTemplate,
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

    def test_title_and_alias_keyword_tagging(self):
        entry = self.add_entry(aliases=["피투성이 보바"])
        chapter = self.client.post("/api/novel/chapters", json={"title": "1장"}).get_json()
        response = self.client.put(
            f"/api/novel/chapters/{chapter['id']}",
            json={"content": "보바가 왔다. 피투성이 보바는 웃었다."},
        )
        self.assertEqual(response.status_code, 200)
        mentions = response.get_json()["mentions"]
        self.assertEqual({m["matched_text"] for m in mentions}, {"보바", "피투성이 보바"})
        self.assertTrue(all(m["entry_id"] == entry.id for m in mentions))
        self.assertTrue(all(m["category"] == "인물" for m in mentions))

    def test_world_and_chapter_styles_do_not_overwrite_each_other(self):
        chapter = self.client.post("/api/novel/chapters", json={"title": "스타일"}).get_json()
        self.client.put("/api/novel/style", json={"chapter_id": None, "pov": "전지적", "tone_guide": "건조"})
        self.client.put("/api/novel/style", json={"chapter_id": chapter["id"], "pov": "1인칭", "tone_guide": ""})
        base = self.client.get("/api/novel/style").get_json()
        override = self.client.get(f"/api/novel/style?chapter_id={chapter['id']}").get_json()
        self.assertEqual(base["pov"], "전지적")
        self.assertEqual(override["pov"], "1인칭")

    def test_entry_delete_cleans_extension_rows(self):
        entry = self.add_entry()
        axis = WorldAttributeSchema(world_id=self.world.id, axis_name="힘")
        skill = WorldSkillRegistry(world_id=self.world.id, name="검술")
        db.session.add_all([axis, skill]); db.session.flush()
        chapter = self.client.post("/api/novel/chapters", json={"title": "삭제"}).get_json()
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


if __name__ == "__main__":
    unittest.main()
