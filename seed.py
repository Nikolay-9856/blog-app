"""Заполнение базы демонстрационными данными: python seed.py"""
from werkzeug.security import generate_password_hash
from app import app, get_db, init_db, save_tags

USERS = ["anna", "dmitry", "olga"]
POSTS = [
    ("anna", "Как я перенесла пет-проект на Flask 3", "Записала шаги миграции: обновление зависимостей, замена устаревших импортов, проверка тестами.", "public", "python, flask"),
    ("dmitry", "Заметки с митапа по фронтенду", "Обсуждали Server Components, кэширование на уровне CDN и доступность форм.", "public", "frontend, митап"),
    ("olga", "Черновик статьи про SQLite", "Индексы, WAL-режим и почему внешние ключи надо включать явно.", "request", "sql, базы данных"),
    ("anna", "Чек-лист код-ревью", "Читаемость, тесты, обработка ошибок, безопасность входных данных.", "public", "python, процессы"),
]

with app.app_context():
    init_db()
    db = get_db()
    for u in USERS:
        db.execute("INSERT INTO users(username, password_hash) VALUES (?,?)", (u, generate_password_hash("secret1")))
    ids = {r["username"]: r["id"] for r in db.execute("SELECT id, username FROM users")}
    for author, title, body, vis, tags in POSTS:
        cur = db.execute("INSERT INTO posts(author_id,title,body,visibility) VALUES (?,?,?,?)", (ids[author], title, body, vis))
        save_tags(cur.lastrowid, tags)
    db.execute("INSERT INTO subscriptions VALUES (?,?,CURRENT_TIMESTAMP)", (ids["dmitry"], ids["anna"]))
    db.execute("INSERT INTO comments(post_id,author_id,text) VALUES (1,?,?)", (ids["dmitry"], "Полезно, спасибо! А как решила вопрос с асинхронными view?"))
    db.commit()
    print("Демо-данные загружены. Пароль у всех пользователей: secret1")
