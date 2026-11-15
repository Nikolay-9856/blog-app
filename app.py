"""Блог-платформа «Заметки»: посты, теги, подписки, комментарии,
скрытые посты с доступом по запросу.

Запуск:  python app.py   (по умолчанию http://127.0.0.1:5000)
"""
import os
import sqlite3
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("BLOG_SECRET", "dev-only-change-me"),
    SESSION_COOKIE_NAME="blog_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    DATABASE=os.environ.get("BLOG_DB", os.path.join(BASE_DIR, "blog.sqlite3")),
)

VISIBILITY = {"public": "Публичный", "request": "Только по запросу"}


# ---------------------------------------------------------------- БД
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as f:
        get_db().executescript(f.read())


@app.cli.command("init-db")
def init_db_command():
    init_db()
    print("База данных создана")


# ---------------------------------------------------------- доступ
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Сначала войдите в систему")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def load_user():
    uid = session.get("uid")
    g.user = None
    if uid is not None:
        g.user = get_db().execute(
            "SELECT id, username, about FROM users WHERE id = ?", (uid,)
        ).fetchone()


def can_read(post):
    """Публичный пост доступен всем; скрытый — автору и тем,
    чей запрос на доступ одобрен."""
    if post["visibility"] == "public":
        return True
    if g.user is None:
        return False
    if post["author_id"] == g.user["id"]:
        return True
    row = get_db().execute(
        "SELECT status FROM access_requests WHERE post_id=? AND user_id=?",
        (post["id"], g.user["id"]),
    ).fetchone()
    return row is not None and row["status"] == "approved"


def get_post(post_id):
    post = get_db().execute(
        """SELECT p.*, u.username FROM posts p
           JOIN users u ON u.id = p.author_id WHERE p.id = ?""",
        (post_id,),
    ).fetchone()
    if post is None:
        abort(404)
    return post


def tags_of(post_id):
    rows = get_db().execute(
        """SELECT t.name FROM tags t JOIN post_tags pt ON pt.tag_id = t.id
           WHERE pt.post_id = ? ORDER BY t.name""",
        (post_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def save_tags(post_id, raw):
    db = get_db()
    db.execute("DELETE FROM post_tags WHERE post_id = ?", (post_id,))
    names = {t.strip().lower().lstrip("#") for t in raw.split(",")}
    for name in sorted(n for n in names if n):
        db.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name[:40],))
        tag_id = db.execute("SELECT id FROM tags WHERE name=?", (name[:40],)).fetchone()["id"]
        db.execute("INSERT INTO post_tags(post_id, tag_id) VALUES (?, ?)", (post_id, tag_id))


def decorate(rows):
    """Добавляет к выборке постов список тегов и число комментариев."""
    db = get_db()
    result = []
    for r in rows:
        item = dict(r)
        item["tags"] = tags_of(r["id"])
        item["comments"] = db.execute(
            "SELECT COUNT(*) FROM comments WHERE post_id=?", (r["id"],)
        ).fetchone()[0]
        item["readable"] = can_read(r)
        result.append(item)
    return result


ORDERS = {
    "new": "p.created_at DESC",
    "old": "p.created_at ASC",
    "title": "p.title COLLATE NOCASE ASC",
}


# ------------------------------------------------------ регистрация
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        error = None
        if not (3 <= len(username) <= 30) or not username.replace("_", "").isalnum():
            error = "Логин: 3–30 символов, буквы, цифры и «_»"
        elif len(password) < 6:
            error = "Пароль должен быть не короче 6 символов"
        if error is None:
            try:
                get_db().execute(
                    "INSERT INTO users(username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
                get_db().commit()
            except sqlite3.IntegrityError:
                error = f"Пользователь «{username}» уже существует"
            else:
                flash("Регистрация прошла успешно, войдите")
                return redirect(url_for("login"))
        flash(error)
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?",
            (request.form.get("username", "").strip(),),
        ).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = user["id"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") else url_for("index"))
        flash("Неверный логин или пароль")
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ------------------------------------------------------------ ленты
@app.route("/")
def index():
    """Публичная лента с фильтром по тегу и сортировкой."""
    tag = request.args.get("tag", "").strip().lower()
    order = ORDERS.get(request.args.get("sort", "new"), ORDERS["new"])
    sql = "SELECT p.*, u.username FROM posts p JOIN users u ON u.id = p.author_id"
    params = []
    if tag:
        sql += (" JOIN post_tags pt ON pt.post_id = p.id"
                " JOIN tags t ON t.id = pt.tag_id AND t.name = ?")
        params.append(tag)
    sql += f" ORDER BY {order}"
    posts = decorate(get_db().execute(sql, params).fetchall())
    all_tags = get_db().execute(
        """SELECT t.name, COUNT(*) n FROM tags t JOIN post_tags pt ON pt.tag_id=t.id
           GROUP BY t.id ORDER BY n DESC, t.name"""
    ).fetchall()
    return render_template("index.html", posts=posts, tag=tag, all_tags=all_tags,
                           sort=request.args.get("sort", "new"), title="Все публикации")


@app.route("/feed")
@login_required
def feed():
    """Лента, сформированная по подпискам пользователя."""
    rows = get_db().execute(
        """SELECT p.*, u.username FROM posts p
           JOIN users u ON u.id = p.author_id
           JOIN subscriptions s ON s.author_id = p.author_id AND s.follower_id = ?
           ORDER BY p.created_at DESC""",
        (g.user["id"],),
    ).fetchall()
    return render_template("index.html", posts=decorate(rows), tag="", all_tags=[],
                           sort="new", title="Моя лента подписок", is_feed=True)


# ------------------------------------------------------------ посты
@app.route("/post/new", methods=["GET", "POST"])
@login_required
def create_post():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        vis = request.form.get("visibility", "public")
        if not title or not body or vis not in VISIBILITY:
            flash("Заполните заголовок и текст")
        else:
            db = get_db()
            cur = db.execute(
                "INSERT INTO posts(author_id, title, body, visibility) VALUES (?,?,?,?)",
                (g.user["id"], title[:200], body, vis),
            )
            save_tags(cur.lastrowid, request.form.get("tags", ""))
            db.commit()
            return redirect(url_for("view_post", post_id=cur.lastrowid))
    return render_template("edit.html", post=None, tags="", visibility=VISIBILITY)


@app.route("/post/<int:post_id>")
def view_post(post_id):
    post = get_post(post_id)
    readable = can_read(post)
    comments, requests_list, my_request = [], [], None
    db = get_db()
    if readable:
        comments = db.execute(
            """SELECT c.*, u.username FROM comments c JOIN users u ON u.id=c.author_id
               WHERE c.post_id=? ORDER BY c.created_at""",
            (post_id,),
        ).fetchall()
    if g.user and post["author_id"] == g.user["id"]:
        requests_list = db.execute(
            """SELECT r.*, u.username FROM access_requests r JOIN users u ON u.id=r.user_id
               WHERE r.post_id=? ORDER BY r.status, r.created_at""",
            (post_id,),
        ).fetchall()
    elif g.user:
        my_request = db.execute(
            "SELECT status FROM access_requests WHERE post_id=? AND user_id=?",
            (post_id, g.user["id"]),
        ).fetchone()
    return render_template("post.html", post=post, readable=readable, tags=tags_of(post_id),
                           comments=comments, requests_list=requests_list,
                           my_request=my_request, visibility=VISIBILITY)


@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = get_post(post_id)
    if post["author_id"] != g.user["id"]:
        abort(403)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        vis = request.form.get("visibility", "public")
        if not title or not body or vis not in VISIBILITY:
            flash("Заполните заголовок и текст")
        else:
            db = get_db()
            db.execute(
                """UPDATE posts SET title=?, body=?, visibility=?,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (title[:200], body, vis, post_id),
            )
            save_tags(post_id, request.form.get("tags", ""))
            db.commit()
            flash("Пост обновлён")
            return redirect(url_for("view_post", post_id=post_id))
    return render_template("edit.html", post=post, tags=", ".join(tags_of(post_id)),
                           visibility=VISIBILITY)


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    post = get_post(post_id)
    if post["author_id"] != g.user["id"]:
        abort(403)
    db = get_db()
    db.execute("DELETE FROM posts WHERE id=?", (post_id,))
    db.commit()
    flash("Пост удалён")
    return redirect(url_for("profile", username=g.user["username"]))


# ------------------------------------------- доступ к скрытым постам
@app.route("/post/<int:post_id>/request", methods=["POST"])
@login_required
def request_access(post_id):
    post = get_post(post_id)
    if post["visibility"] != "request" or post["author_id"] == g.user["id"]:
        abort(400)
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO access_requests(post_id, user_id) VALUES (?, ?)",
        (post_id, g.user["id"]),
    )
    db.commit()
    flash("Запрос отправлен автору")
    return redirect(url_for("view_post", post_id=post_id))


@app.route("/request/<int:req_id>/<decision>", methods=["POST"])
@login_required
def decide_request(req_id, decision):
    if decision not in ("approved", "rejected"):
        abort(400)
    db = get_db()
    req = db.execute(
        """SELECT r.*, p.author_id FROM access_requests r JOIN posts p ON p.id=r.post_id
           WHERE r.id=?""",
        (req_id,),
    ).fetchone()
    if req is None or req["author_id"] != g.user["id"]:
        abort(403)
    db.execute("UPDATE access_requests SET status=? WHERE id=?", (decision, req_id))
    db.commit()
    return redirect(url_for("view_post", post_id=req["post_id"]))


# -------------------------------------------------------- комментарии
@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    post = get_post(post_id)
    if not can_read(post):
        abort(403)
    text = request.form.get("text", "").strip()
    if text:
        db = get_db()
        db.execute(
            "INSERT INTO comments(post_id, author_id, text) VALUES (?,?,?)",
            (post_id, g.user["id"], text[:2000]),
        )
        db.commit()
    return redirect(url_for("view_post", post_id=post_id) + "#comments")


@app.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    db = get_db()
    c = db.execute(
        """SELECT c.*, p.author_id AS post_author FROM comments c
           JOIN posts p ON p.id=c.post_id WHERE c.id=?""",
        (comment_id,),
    ).fetchone()
    if c is None:
        abort(404)
    if g.user["id"] not in (c["author_id"], c["post_author"]):
        abort(403)
    db.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    db.commit()
    return redirect(url_for("view_post", post_id=c["post_id"]) + "#comments")


# ------------------------------------------------- профили и подписки
@app.route("/u/<username>")
def profile(username):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if user is None:
        abort(404)
    posts = decorate(db.execute(
        """SELECT p.*, u.username FROM posts p JOIN users u ON u.id=p.author_id
           WHERE p.author_id=? ORDER BY p.created_at DESC""",
        (user["id"],),
    ).fetchall())
    followers = db.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE author_id=?", (user["id"],)).fetchone()[0]
    following = db.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE follower_id=?", (user["id"],)).fetchone()[0]
    is_following = False
    if g.user:
        is_following = db.execute(
            "SELECT 1 FROM subscriptions WHERE follower_id=? AND author_id=?",
            (g.user["id"], user["id"]),
        ).fetchone() is not None
    return render_template("profile.html", profile=user, posts=posts, followers=followers,
                           following=following, is_following=is_following)


@app.route("/u/<username>/follow", methods=["POST"])
@login_required
def toggle_follow(username):
    db = get_db()
    user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if user is None:
        abort(404)
    if user["id"] == g.user["id"]:
        flash("Нельзя подписаться на себя")
        return redirect(url_for("profile", username=username))
    exists = db.execute(
        "SELECT 1 FROM subscriptions WHERE follower_id=? AND author_id=?",
        (g.user["id"], user["id"]),
    ).fetchone()
    if exists:
        db.execute("DELETE FROM subscriptions WHERE follower_id=? AND author_id=?",
                   (g.user["id"], user["id"]))
    else:
        db.execute("INSERT INTO subscriptions(follower_id, author_id) VALUES (?,?)",
                   (g.user["id"], user["id"]))
    db.commit()
    return redirect(url_for("profile", username=username))


@app.errorhandler(403)
@app.errorhandler(404)
def http_error(err):
    return render_template("error.html", err=err), err.code


if __name__ == "__main__":
    with app.app_context():
        if not os.path.exists(app.config["DATABASE"]):
            init_db()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
