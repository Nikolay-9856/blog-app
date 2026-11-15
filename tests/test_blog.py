import os
import tempfile

import pytest

from app import app, init_db


@pytest.fixture
def client():
    fd, path = tempfile.mkstemp()
    app.config.update(TESTING=True, DATABASE=path)
    with app.app_context():
        init_db()
    with app.test_client() as c:
        yield c
    os.close(fd)
    os.unlink(path)


def register(c, name, pwd="secret1"):
    c.post("/register", data={"username": name, "password": pwd})


def login(c, name, pwd="secret1"):
    return c.post("/login", data={"username": name, "password": pwd})


def logout(c):
    c.get("/logout")


def test_register_and_login(client):
    register(client, "anna")
    r = login(client, "anna")
    assert r.status_code == 302
    assert "@anna" in client.get("/").get_data(as_text=True)


def test_wrong_password(client):
    register(client, "anna")
    r = login(client, "anna", "badpass")
    assert "Неверный логин" in r.get_data(as_text=True)


def test_duplicate_user(client):
    register(client, "anna")
    r = client.post("/register", data={"username": "anna", "password": "secret1"})
    assert "уже существует" in r.get_data(as_text=True)


def test_create_edit_delete_post(client):
    register(client, "anna"); login(client, "anna")
    client.post("/post/new", data={"title": "Первый", "body": "Текст", "tags": "python, Flask", "visibility": "public"})
    page = client.get("/post/1").get_data(as_text=True)
    assert "Первый" in page and "#flask" in page
    client.post("/post/1/edit", data={"title": "Исправленный", "body": "Новый", "tags": "sql", "visibility": "public"})
    assert "Исправленный" in client.get("/post/1").get_data(as_text=True)
    client.post("/post/1/delete")
    assert client.get("/post/1").status_code == 404


def test_only_author_can_edit(client):
    register(client, "anna"); register(client, "boris")
    login(client, "anna")
    client.post("/post/new", data={"title": "A", "body": "B", "visibility": "public"})
    logout(client); login(client, "boris")
    assert client.post("/post/1/edit", data={"title": "X", "body": "Y"}).status_code == 403
    assert client.post("/post/1/delete").status_code == 403


def test_hidden_post_access_flow(client):
    register(client, "anna"); register(client, "boris")
    login(client, "anna")
    client.post("/post/new", data={"title": "Секрет", "body": "Скрытый текст", "visibility": "request"})
    logout(client); login(client, "boris")
    assert "Скрытый текст" not in client.get("/post/1").get_data(as_text=True)
    client.post("/post/1/request")
    logout(client); login(client, "anna")
    client.post("/request/1/approved")
    logout(client); login(client, "boris")
    assert "Скрытый текст" in client.get("/post/1").get_data(as_text=True)


def test_feed_by_subscriptions(client):
    register(client, "anna"); register(client, "boris"); register(client, "vera")
    login(client, "anna"); client.post("/post/new", data={"title": "От Анны", "body": "1", "visibility": "public"}); logout(client)
    login(client, "vera"); client.post("/post/new", data={"title": "От Веры", "body": "2", "visibility": "public"}); logout(client)
    login(client, "boris"); client.post("/u/anna/follow")
    feed = client.get("/feed").get_data(as_text=True)
    assert "От Анны" in feed and "От Веры" not in feed


def test_tag_filter_and_comments(client):
    register(client, "anna"); login(client, "anna")
    client.post("/post/new", data={"title": "Про SQL", "body": "1", "tags": "sql", "visibility": "public"})
    client.post("/post/new", data={"title": "Про CSS", "body": "2", "tags": "css", "visibility": "public"})
    page = client.get("/?tag=sql").get_data(as_text=True)
    assert "Про SQL" in page and "Про CSS" not in page
    client.post("/post/1/comment", data={"text": "Отличный пост"})
    assert "Отличный пост" in client.get("/post/1").get_data(as_text=True)


def test_cannot_follow_self(client):
    register(client, "anna"); login(client, "anna")
    r = client.post("/u/anna/follow", follow_redirects=True)
    assert "Нельзя подписаться" in r.get_data(as_text=True)
