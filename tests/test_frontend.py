import pytest
from app import create_app

@pytest.fixture
def client():
    app = create_app({"TESTING": True, "TASKS_FILE": "tests/test_tasks.csv"})
    with app.test_client() as client:
        yield client

def test_index_page_loads(client):
    response = client.get('/')
    assert response.status_code == 200

def test_index_contains_keywords(client):
    response = client.get('/')
    assert '添加任务' in response.data.decode('utf-8')