from __future__ import annotations

import pytest

from app.domain.models.cdha_clinical_summary import (
    CDHAClinicalSummary,
    ClinicalSummaryValidationError,
)


def test_summary_removes_labels_but_preserves_nested_values_and_measurements() -> None:
    summary = CDHAClinicalSummary.from_values(
        key_findings=[
            "Key findings:\n• Cấu trúc dạng ống, đường kính 19.60 mm.",
            "• Thành dày.",
        ],
        impression="Impression:\nHình ảnh gợi ý viêm, cần đối chiếu lâm sàng.",
        analysis_url="https://cdha.ai/dash?view=result-123",
        source_language="vi",
    )

    assert summary.key_findings == [
        "Cấu trúc dạng ống, đường kính 19.60 mm.",
        "Thành dày.",
    ]
    assert summary.impression == "Hình ảnh gợi ý viêm, cần đối chiếu lâm sàng."
    assert summary.raw_key_findings.startswith("Key findings:")
    assert summary.raw_impression.startswith("Impression:")


@pytest.mark.parametrize(
    ("findings", "impression"),
    [
        (["Key findings:"], "Impression:"),
        ([], "Nhận định:"),
        (["Phát hiện chính"], ""),
    ],
)
def test_summary_rejects_empty_or_label_only_required_fields(
    findings: list[str], impression: str
) -> None:
    with pytest.raises(ClinicalSummaryValidationError):
        CDHAClinicalSummary.from_values(
            key_findings=findings,
            impression=impression,
            analysis_url="https://cdha.ai/dash?view=result-123",
        )


@pytest.mark.parametrize("url", ["", "not-a-url", "http://cdha.ai/result"])
def test_summary_requires_exact_https_analysis_url(url: str) -> None:
    with pytest.raises(ClinicalSummaryValidationError, match="analysis URL"):
        CDHAClinicalSummary.from_values(
            key_findings=["Ghi nhận tổn thương khu trú."],
            impression="Hình ảnh gợi ý tổn thương, cần đối chiếu.",
            analysis_url=url,
        )
