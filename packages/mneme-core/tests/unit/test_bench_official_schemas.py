"""Contract tests for official LongMemEval and LoCoMo schema adapters."""

from __future__ import annotations

import copy

import pytest

from mneme_core.bench.harness import (
    DatasetSchemaError,
    load_locomo_official,
    load_longmemeval_official,
)


def _longmemeval_record() -> dict[str, object]:
    return {
        "question_id": "single-session-user_synthetic-001",
        "question_type": "single-session-user",
        "question": "Where was the synthetic protocol reviewed?",
        "answer": "laboratory delta",
        "question_date": "2026/01/02",
        "haystack_session_ids": ["session-1"],
        "haystack_dates": ["2026/01/01"],
        "haystack_sessions": [
            [
                {
                    "role": "user",
                    "content": "The synthetic protocol was reviewed in laboratory delta.",
                    "has_answer": True,
                }
            ]
        ],
        "answer_session_ids": ["session-1"],
    }


def _locomo_record() -> dict[str, object]:
    return {
        "sample_id": "synthetic-conversation-001",
        "conversation": {
            "speaker_a": "Avery",
            "speaker_b": "Blake",
            "session_1_date_time": "2026/01/01",
            "session_1": [
                {
                    "speaker": "Avery",
                    "dia_id": "D1:1",
                    "text": "The synthetic cedar key is in vault seven.",
                }
            ],
        },
        "qa": [
            {
                "question": "Where is the synthetic cedar key?",
                "answer": "vault seven",
                "category": 1,
                "evidence": ["D1:1"],
            }
        ],
    }


class TestLongMemEvalOfficialSchema:
    def test_accepts_public_record_shape(self) -> None:
        cases = load_longmemeval_official([_longmemeval_record()])
        assert len(cases) == 1
        assert cases[0].case_id == "single-session-user_synthetic-001"
        assert cases[0].relevant_ids == ("session-1",)

    def test_accepts_abstention_with_no_answer_sessions(self) -> None:
        record = _longmemeval_record()
        record["question_id"] = "single-session-user_synthetic-abs_abs"
        record["answer_session_ids"] = []
        assert load_longmemeval_official([record]) == []
        included = load_longmemeval_official([record], include_abstention=True)
        assert included[0].relevant_ids == ()

    def test_rejects_missing_required_field_with_path(self) -> None:
        record = _longmemeval_record()
        record.pop("answer_session_ids")
        with pytest.raises(DatasetSchemaError, match=r"records\[0\]\.answer_session_ids"):
            load_longmemeval_official([record])

    def test_rejects_inconsistent_haystack_lengths(self) -> None:
        record = _longmemeval_record()
        record["haystack_dates"] = []
        with pytest.raises(DatasetSchemaError, match="equal lengths"):
            load_longmemeval_official([record])

    def test_rejects_unknown_answer_session(self) -> None:
        record = _longmemeval_record()
        record["answer_session_ids"] = ["missing"]
        with pytest.raises(DatasetSchemaError, match="unknown sessions"):
            load_longmemeval_official([record])

    def test_rejects_invalid_turn_role(self) -> None:
        record = _longmemeval_record()
        sessions = copy.deepcopy(record["haystack_sessions"])
        assert isinstance(sessions, list)
        session = sessions[0]
        assert isinstance(session, list)
        turn = session[0]
        assert isinstance(turn, dict)
        turn["role"] = "system"
        record["haystack_sessions"] = sessions
        with pytest.raises(DatasetSchemaError, match="role"):
            load_longmemeval_official([record])


class TestLoCoMoOfficialSchema:
    def test_flattens_nested_qa_annotations(self) -> None:
        record = _locomo_record()
        qa = record["qa"]
        assert isinstance(qa, list)
        qa.append(
            {
                "question": "What was stored?",
                "answer": "the synthetic cedar key",
                "category": "single-hop",
                "evidence": ["D1:1"],
            }
        )
        cases = load_locomo_official([record])
        assert [case.case_id for case in cases] == [
            "synthetic-conversation-001:qa-0",
            "synthetic-conversation-001:qa-1",
        ]
        assert cases[0].relevant_ids == ("D1:1",)

    def test_accepts_qa_without_optional_evidence(self) -> None:
        record = _locomo_record()
        qa = record["qa"]
        assert isinstance(qa, list)
        annotation = qa[0]
        assert isinstance(annotation, dict)
        annotation.pop("evidence")
        cases = load_locomo_official([record])
        assert cases[0].relevant_ids == ()

    def test_rejects_flattened_non_official_shape(self) -> None:
        with pytest.raises(DatasetSchemaError, match="conversation"):
            load_locomo_official(
                [
                    {
                        "sample_id": "flat",
                        "question": "Not official nested LoCoMo",
                        "evidence": ["D1:1"],
                    }
                ]
            )

    def test_rejects_evidence_outside_conversation(self) -> None:
        record = _locomo_record()
        qa = record["qa"]
        assert isinstance(qa, list)
        annotation = qa[0]
        assert isinstance(annotation, dict)
        annotation["evidence"] = ["D9:9"]
        with pytest.raises(DatasetSchemaError, match="unknown dialog ids"):
            load_locomo_official([record])

    def test_rejects_session_without_matching_date(self) -> None:
        record = _locomo_record()
        conversation = record["conversation"]
        assert isinstance(conversation, dict)
        conversation.pop("session_1_date_time")
        with pytest.raises(DatasetSchemaError, match="session_1_date_time"):
            load_locomo_official([record])
