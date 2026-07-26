from __future__ import annotations

from app.models.results import DownloadComment
from app.services.clinical_factors_service import ClinicalFactorsService, REQUIRED_HEADINGS
from app.services.privacy_service import (
    EMAIL_TOKEN,
    IDENTITY_TOKEN,
    PATIENT_ID_TOKEN,
    PHONE_TOKEN,
    PrivacyService,
)


def valid_response() -> str:
    return "\n".join(
        [
            "Cơ quan/vùng khảo sát: Gan",
            "Triệu chứng chính: Đau hạ sườn phải",
            "Thời gian xuất hiện triệu chứng: 2 ngày",
            "Chỉ định hoặc nghi ngờ lâm sàng: Khảo sát gan; chưa xác minh",
            "Tiền sử liên quan: Không được cung cấp",
            "Kết quả xét nghiệm liên quan: Không được cung cấp",
            "Thông tin bổ sung: Bình luận công khai chưa được xác minh",
            "Thông tin chưa được cung cấp: Tuổi, giới, tiền sử",
        ]
    )


def test_gemini_prompt_construction_contains_required_contract() -> None:
    prompt = ClinicalFactorsService().build_prompt(
        "Đau hạ sườn phải", [DownloadComment(None, "Siêu âm gan")]
    )

    assert "Do not make a final medical diagnosis" in prompt
    assert "Facebook caption:\n\nĐau hạ sườn phải" in prompt
    assert "Visible public comments:\n\n- Siêu âm gan" in prompt
    assert all(heading in prompt for heading in REQUIRED_HEADINGS)


def test_prompt_removes_exact_duplicate_and_empty_comments() -> None:
    prompt = ClinicalFactorsService().build_prompt(
        "Caption", ["Nhận xét", "", "Nhận xét", {"content": "Khác"}]
    )

    comments = prompt.split("Visible public comments:\n\n", 1)[1].split("\n</UNTRUSTED_FACEBOOK_CONTENT>", 1)[0]
    assert comments.count("Nhận xét") == 1
    assert comments.count("- ") == 2


def test_prompt_truncates_excessive_comment_length_and_count() -> None:
    service = ClinicalFactorsService(max_comment_chars=20, max_comments=1)
    prompt = service.build_prompt("Caption", ["x" * 80, "second"])
    comments = prompt.split("Visible public comments:\n\n", 1)[1].split("\n</UNTRUSTED_FACEBOOK_CONTENT>", 1)[0]

    assert comments == "- " + "x" * 19 + "…"
    assert "second" not in comments


def test_empty_caption_and_comments_are_explicitly_unavailable() -> None:
    prompt = ClinicalFactorsService().build_prompt("", [])

    assert prompt.count("Không được cung cấp") >= 2


def test_required_headings_are_accepted() -> None:
    result = ClinicalFactorsService().validate(valid_response(), job_id="job")

    assert result.success
    assert result.missing_fields == []


def test_empty_gemini_response_is_rejected() -> None:
    result = ClinicalFactorsService().validate("", job_id="job")

    assert not result.success
    assert "empty" in result.error


def test_still_generating_response_is_rejected() -> None:
    result = ClinicalFactorsService().validate(
        valid_response() + "\nĐang tạo câu trả lời", job_id="job"
    )

    assert not result.success
    assert "still generating" in result.error


def test_markdown_table_is_rejected() -> None:
    result = ClinicalFactorsService().validate(
        valid_response() + "\n| Cột | Giá trị |\n| --- | --- |", job_id="job"
    )

    assert not result.success
    assert "Markdown" in result.error


def test_unsupported_definite_diagnosis_is_rejected() -> None:
    result = ClinicalFactorsService().validate(
        valid_response() + "\nChẩn đoán xác định: ung thư", job_id="job"
    )

    assert not result.success
    assert "unsupported definite diagnosis" in result.error


def test_privacy_masks_phone_email_patient_id_handle_and_explicit_name() -> None:
    masked = PrivacyService().mask(
        "Bệnh nhân: Nguyễn Văn An; mã bệnh nhân BN-12345; 0901 234 567; "
        "doctor@example.com; @public_account"
    )

    assert PHONE_TOKEN in masked
    assert EMAIL_TOKEN in masked
    assert PATIENT_ID_TOKEN in masked
    assert IDENTITY_TOKEN in masked
    assert "Nguyễn Văn An" not in masked
    assert "@public_account" not in masked


def test_privacy_masks_national_id_and_unrelated_url() -> None:
    masked = PrivacyService().mask("CCCD: 012345678901 https://example.org/patient")

    assert masked.count(IDENTITY_TOKEN) == 2


def test_privacy_preserves_medical_measurements_and_timing() -> None:
    source = "Nang gan 12.5 mm, đau 2 ngày, nhiệt độ 38.2 °C, huyết áp 120/80 mmHg"

    assert PrivacyService().mask(source) == source


def test_prompt_masks_identifiers_before_sending_to_gemini() -> None:
    prompt = ClinicalFactorsService().build_prompt(
        "Liên hệ 0901234567", ["Email a@b.com", "MRN: AB-123"]
    )

    assert "0901234567" not in prompt
    assert "a@b.com" not in prompt
    assert "AB-123" not in prompt
