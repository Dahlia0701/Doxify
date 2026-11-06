""" testing api endpoints ,idempotent retries and atomic job claiming"""
from concurrent.futures import Future
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.llm import MockLLM
from app.pipeline import GenerationRequest
from app.service import JobService
from app.main import app


class ImmediateExecutor:
    def submit(self, fn, *args):
        f = Future()
        try:
            f.set_result(fn(*args))
        except Exception as exc:
            f.set_exception(exc)
        return f
    def shutdown(self, wait=False):
        pass


def test_service_retry_same_job(tmp_path):
    llm = MockLLM()
    svc = JobService(Settings(data_dir=str(tmp_path)), llm=llm, executor=ImmediateExecutor())
    req = GenerationRequest('Create an Excel sales workbook', 'xlsx')
    first = svc.submit(req, 'abc')
    second = svc.submit(req, 'abc')
    assert first['id'] == second['id']
    assert llm.calls == 1
    assert svc.download(first['id']).is_file()


def test_atomic_claim(tmp_path):
    svc = JobService(Settings(data_dir=str(tmp_path)), llm=MockLLM(), executor=ImmediateExecutor())
    job_id, _ = svc.store.create('new-job')
    assert svc.store.claim(job_id)
    assert not svc.store.claim(job_id)


def test_api_endpoints(tmp_path):
    with TestClient(app) as client:
        app.state.service = JobService(Settings(data_dir=str(tmp_path)), llm=MockLLM(), executor=ImmediateExecutor())
        assert client.get('/health').json()['status'] == 'ok'
        response = client.post('/jobs', data={'prompt':'Create an Excel sales workbook','fmt':'xlsx'})
        assert response.status_code == 202
        job_id = response.json()['id']
        status = client.get('/jobs/'+job_id)
        assert status.status_code == 200
        assert status.json()['status'] in ('completed','completed_with_warnings')
        file_response = client.get('/jobs/'+job_id+'/download')
        assert file_response.status_code == 200
        assert file_response.content[:2] == b'PK'
        assert client.get('/jobs/unknown').status_code == 404
        assert client.post('/jobs',data={'prompt':'x','fmt':'xlsx'}).status_code == 400
