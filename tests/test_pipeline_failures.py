import pytest
from app.config import Settings
from app.limits import Deadline, InputError, JobTimeout
from app.llm import LLMError, MockLLM
from app.pipeline import GenerationRequest, RenderError, generate_document


def run(tmp_path, llm=None, prompt='Create an Excel workbook with sales data', **kw):
    return generate_document(GenerationRequest(prompt, fmt='xlsx', **kw), Settings(), llm or MockLLM(), tmp_path)


def test_bad_llm_response(tmp_path):
    with pytest.raises(Exception):
        run(tmp_path, MockLLM(canned='this is not JSON'))


def test_llm_timeout(tmp_path):
    class TimeoutLLM:
        def complete_json(self, system, user):
            raise LLMError('The model timed out')
    with pytest.raises(LLMError):
        run(tmp_path, TimeoutLLM())


def test_deadline(tmp_path):
    with pytest.raises(JobTimeout):
        generate_document(GenerationRequest('Create an Excel report', 'xlsx'), Settings(), MockLLM(), tmp_path, deadline=Deadline(0))


def test_malformed_csv(tmp_path):
    with pytest.raises(InputError):
        run(tmp_path, csv_bytes=b'\x00\x00\x00', csv_name='bad.csv')


def test_render_crash(tmp_path, monkeypatch):
    import app.pipeline as pipeline
    def crash(*args):
        raise RuntimeError('unexpected renderer problem')
    monkeypatch.setattr(pipeline, 'render_xlsx', crash)
    with pytest.raises(RenderError):
        run(tmp_path)


def test_mock_xlsx_pipeline(tmp_path):
    result = run(tmp_path)
    assert result.ok
    assert result.path.exists()

@pytest.mark.parametrize('fmt,prompt', [
    ('pdf', 'Create a project proposal PDF with budget and risks'),
    ('pptx', 'Create a 5-slide presentation for executives')
])
def test_other_formats_end_to_end(tmp_path, fmt, prompt):
    result = generate_document(GenerationRequest(prompt, fmt), Settings(), MockLLM(), tmp_path)
    assert result.ok, result.issues
    assert result.path and result.path.suffix == '.' + fmt


def test_tampered_pdf_is_rejected(tmp_path):
    from app.planner import parse_plan
    from app.llm import _mock_pdf
    from renderers.pdf_renderer import render_pdf
    from validators.pdf_validator import validate_pdf
    import json
    doc = parse_plan(json.dumps(_mock_pdf(['Executive Summary'], 'Test Proposal', 'clients', None)), 'pdf')
    path = render_pdf(doc, tmp_path / 'proposal.pdf')
    path.write_bytes(b'not a PDF')
    assert validate_pdf(path, doc).status == 'failed'


def test_tampered_pptx_is_rejected(tmp_path):
    from app.planner import parse_plan
    from app.llm import _mock_deck
    from renderers.pptx_renderer import render_pptx
    from validators.pptx_validator import validate_pptx
    import json
    doc = parse_plan(json.dumps(_mock_deck(3, 'Demo', 'executives')), 'pptx')
    path = render_pptx(doc, tmp_path / 'demo.pptx')
    path.write_bytes(b'not a PowerPoint')
    assert validate_pptx(path, doc).status == 'failed'

def test_csv_analysis_end_to_end(tmp_path):
    csv = b'Month,Revenue,Expenses\nJan,100,60\nFeb,140,80\nMar,160,100\n'
    result = generate_document(
        GenerationRequest('Analyze the uploaded sales CSV and create an Excel report', 'xlsx', csv, 'sales.csv'),
        Settings(), MockLLM(), tmp_path)
    assert result.ok, result.issues
    assert result.path.exists()

def test_invalid_plan_is_retried(tmp_path):
    import json
    from app.llm import _mock_deck

    class InvalidThenValidLLM:
        def __init__(self):
            self.calls = 0
            self.feedback_received = False

        def complete_json(self, system, user):
            self.calls += 1

            if self.calls == 1:
                # Simulate Gemini returning invalid JSON
                return "This is not JSON"

            # Check that the planner sends feedback
            self.feedback_received = (
                "valid JSON" in user
                or "retry" in user.lower()
            )

            return json.dumps(
                _mock_deck(3, "Mercury", "students")
            )

    fake_llm = InvalidThenValidLLM()

    result = generate_document(
        GenerationRequest(
            "Create a 3-slide presentation about Mercury",
            "pptx"
        ),
        Settings(),
        fake_llm,
        tmp_path
    )

    assert fake_llm.calls == 2
    assert result.ok
    assert result.path is not None
    assert result.path.exists()

