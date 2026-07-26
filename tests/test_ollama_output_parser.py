from __future__ import annotations

import json

from app.ai.output_parser import OllamaOutputParser


def test_structured_clinical_factors_include_extended_analysis_fields() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0",
            "analysis_mode": "VISION_FRAMES",
            "visual_analysis_performed": True,
            "requires_human_review": True,
            "clinical_factors": {
                "organ_region": "Vùng đùi",
                "laterality": "Phải",
                "target_structure": "Mô mềm vùng đùi",
                "scan_plane": "Mặt cắt ngang",
                "clinical_indication": "Không được nguồn cung cấp rõ",
                "direct_observations": "Quan sát thấy cấu trúc mô mềm trên ảnh siêu âm",
                "source_stated_indication": "Không có chỉ định rõ trong nguồn",
                "image_inference": "Có thể là khảo sát mô mềm; chưa xác minh",
                "confidence": "LOW",
                "evidence": "Frame 2, mốc 00:08",
            },
        },
        ensure_ascii=False,
    )

    result = OllamaOutputParser().parse(
        raw,
        job_id="job",
        frames_were_sent=True,
        model="vision-model",
    )

    assert result.success
    assert "Bên khảo sát:\nPhải" in result.clinical_factors_text
    assert "Cấu trúc đích:\nMô mềm vùng đùi" in result.clinical_factors_text
    assert "Mặt cắt:\nMặt cắt ngang" in result.clinical_factors_text
    assert (
        "Dấu hiệu quan sát trực tiếp:\nQuan sát thấy cấu trúc mô mềm trên ảnh siêu âm"
        in result.clinical_factors_text
    )
    assert (
        "Chỉ định được nguồn nêu rõ:\nKhông có chỉ định rõ trong nguồn"
        in result.clinical_factors_text
    )
    assert (
        "Nhận định suy ra từ hình ảnh:\nCó thể là khảo sát mô mềm; chưa xác minh"
        in result.clinical_factors_text
    )
    assert "Mức độ chắc chắn:\nLOW" in result.clinical_factors_text
    assert "Bằng chứng:\nFrame 2, mốc 00:08" in result.clinical_factors_text


def test_empty_extended_fields_are_omitted() -> None:
    text = OllamaOutputParser._dict_to_cf_text(
        {
            "organ_region": "Ổ bụng",
            "laterality": None,
            "target_structure": "",
            "scan_plane": "Không được cung cấp",
            "evidence": "null",
        }
    )

    assert text == "Cơ quan/vùng khảo sát:\nỔ bụng"
