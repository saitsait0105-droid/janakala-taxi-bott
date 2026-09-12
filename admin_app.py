import os
import urllib.request
import urllib.parse
import json
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
import database as db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")


def send_telegram_message(chat_id, text):
    if not BOT_TOKEN:
        return False, "BOT_TOKEN орнатылмаған"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:
            body = json.loads(resp.read().decode())
            return body.get("ok", False), body.get("description", "")
    except Exception as e:
        return False, str(e)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        flash("Қате құпия сөз")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    stats = db.get_stats()
    stats["commission_percent"] = db.get_commission_percent()
    stats["total_commission"] = db.get_total_commission()
    rides = db.get_all_rides(limit=20)
    return render_template("dashboard.html", stats=stats, rides=rides)


@app.route("/rides")
@login_required
def rides():
    status_filter = request.args.get("status", "")
    all_rides = db.get_all_rides()
    if status_filter:
        all_rides = [r for r in all_rides if r["status"] == status_filter]
    return render_template("rides.html", rides=all_rides, status_filter=status_filter)


@app.route("/rides/<int:ride_id>/complete", methods=["POST"])
@login_required
def complete_ride(ride_id):
    db.complete_ride(ride_id)
    return redirect(url_for("rides"))


@app.route("/rides/<int:ride_id>/cancel", methods=["POST"])
@login_required
def cancel_ride(ride_id):
    db.cancel_ride(ride_id)
    return redirect(url_for("rides"))


@app.route("/users")
@login_required
def users():
    all_users = db.get_all_users()
    for u in all_users:
        if u["role"] == "driver":
            r = db.get_driver_rating(u["id"])
            u["rating_display"] = f"⭐{r['avg']} ({r['count']})" if r["count"] else "—"
    return render_template("users.html", users=all_users)


@app.route("/users/<int:user_id>/block", methods=["POST"])
@login_required
def block_user(user_id):
    db.set_blocked(user_id, True)
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/unblock", methods=["POST"])
@login_required
def unblock_user(user_id):
    db.set_blocked(user_id, False)
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/grant_intercity", methods=["POST"])
@login_required
def grant_intercity(user_id):
    db.set_intercity_access(user_id, True)
    user = db.get_user_by_id(user_id)
    if user:
        send_telegram_message(
            user["telegram_id"],
            "✅ Сізге 'Жанақала — Орал' бағыты бойынша тапсырыс беруге рұқсат берілді! Тапсырысты қайта бастап көріңіз.",
        )
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/revoke_intercity", methods=["POST"])
@login_required
def revoke_intercity(user_id):
    db.set_intercity_access(user_id, False)
    return redirect(url_for("users"))


@app.route("/broadcast", methods=["GET", "POST"])
@login_required
def broadcast():
    result = None
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        role_filter = request.form.get("role") or None
        if not text:
            flash("Хабарлама мәтінін жаз")
        else:
            recipients = db.get_broadcast_recipients(role_filter)
            sent, failed = 0, 0
            for tg_id in recipients:
                ok, _ = send_telegram_message(tg_id, text)
                if ok:
                    sent += 1
                else:
                    failed += 1
            result = {"sent": sent, "failed": failed, "total": len(recipients)}
    return render_template("broadcast.html", result=result, bot_token_set=bool(BOT_TOKEN))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        percent = request.form.get("commission_percent", "10")
        try:
            float(percent)
            db.set_setting("commission_percent", percent)
            flash("Комиссия сақталды")
        except ValueError:
            flash("Дұрыс сан жаз")
    current = db.get_commission_percent()
    total = db.get_total_commission()
    return render_template("settings.html", commission_percent=current, total_commission=total)


if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("PORT", "5000"))
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
