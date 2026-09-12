import sqlite3
from datetime import datetime

DB_PATH = "taxi.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            full_name TEXT,
            phone TEXT,
            role TEXT DEFAULT NULL,   -- 'client' or 'driver'
            is_online INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            from_location TEXT,
            from_lat REAL,
            from_lon REAL,
            to_location TEXT,
            to_lat REAL,
            to_lon REAL,
            ride_time TEXT,
            price TEXT,
            status TEXT DEFAULT 'open',  -- open / accepted / arrived / completed / cancelled
            driver_id INTEGER,
            rating INTEGER,
            review TEXT,
            created_at TEXT,
            service_type TEXT DEFAULT 'taxi',  -- 'taxi' or 'delivery'
            route TEXT DEFAULT 'local',        -- 'local' (Жанақала ішінде) or 'intercity' (Жанақала — Орал)
            item_note TEXT,                    -- what to deliver (delivery orders only)
            FOREIGN KEY (client_id) REFERENCES users(id),
            FOREIGN KEY (driver_id) REFERENCES users(id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    # migration guard: add is_online if upgrading an older db
    cur.execute("PRAGMA table_info(users)")
    cols = [c["name"] for c in cur.fetchall()]
    if "is_online" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN is_online INTEGER DEFAULT 0")
        conn.commit()
    if "is_blocked" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
        conn.commit()
    if "intercity_access" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN intercity_access INTEGER DEFAULT 0")
        conn.commit()

    cur.execute("PRAGMA table_info(rides)")
    ride_cols = [c["name"] for c in cur.fetchall()]
    for col in ("from_lat", "from_lon", "to_lat", "to_lon"):
        if col not in ride_cols:
            cur.execute(f"ALTER TABLE rides ADD COLUMN {col} REAL")
            conn.commit()
    if "rating" not in ride_cols:
        cur.execute("ALTER TABLE rides ADD COLUMN rating INTEGER")
        conn.commit()
    if "review" not in ride_cols:
        cur.execute("ALTER TABLE rides ADD COLUMN review TEXT")
        conn.commit()
    if "service_type" not in ride_cols:
        cur.execute("ALTER TABLE rides ADD COLUMN service_type TEXT DEFAULT 'taxi'")
        conn.commit()
    if "route" not in ride_cols:
        cur.execute("ALTER TABLE rides ADD COLUMN route TEXT DEFAULT 'local'")
        conn.commit()
    if "item_note" not in ride_cols:
        cur.execute("ALTER TABLE rides ADD COLUMN item_note TEXT")
        conn.commit()
    conn.close()


def get_or_create_user(telegram_id, username=None, full_name=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
    user = cur.fetchone()
    if user is None:
        cur.execute(
            "INSERT INTO users (telegram_id, username, full_name, created_at) VALUES (?,?,?,?)",
            (telegram_id, username, full_name, datetime.now().isoformat()),
        )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        user = cur.fetchone()
    conn.close()
    return dict(user)


def set_role(telegram_id, role):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET role=? WHERE telegram_id=?", (role, telegram_id))
    conn.commit()
    conn.close()


def set_phone(telegram_id, phone):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET phone=? WHERE telegram_id=?", (phone, telegram_id))
    conn.commit()
    conn.close()


def set_online(telegram_id, is_online):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_online=? WHERE telegram_id=?", (1 if is_online else 0, telegram_id))
    conn.commit()
    conn.close()


def get_online_driver_telegram_ids():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM users WHERE role='driver' AND is_online=1")
    rows = [r["telegram_id"] for r in cur.fetchall()]
    conn.close()
    return rows


def create_ride(client_id, from_location, to_location, ride_time, price,
                 from_lat=None, from_lon=None, to_lat=None, to_lon=None,
                 service_type="taxi", route="local", item_note=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO rides (client_id, from_location, from_lat, from_lon, to_location, to_lat, to_lon,
                               ride_time, price, status, created_at, service_type, route, item_note)
           VALUES (?,?,?,?,?,?,?,?,?, 'open', ?, ?, ?, ?)""",
        (client_id, from_location, from_lat, from_lon, to_location, to_lat, to_lon,
         ride_time, price, datetime.now().isoformat(), service_type, route, item_note),
    )
    conn.commit()
    ride_id = cur.lastrowid
    conn.close()
    return ride_id


# ---------- Intercity (Жанақала — Орал) access control ----------

def set_intercity_access(user_id, allowed):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET intercity_access=? WHERE id=?", (1 if allowed else 0, user_id))
    conn.commit()
    conn.close()


def has_intercity_access(telegram_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT intercity_access FROM users WHERE telegram_id=?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row["intercity_access"]) if row else False


def get_user_by_telegram_id(telegram_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_open_rides(limit=20):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT rides.*, users.full_name as client_name, users.phone as client_phone, users.username as client_username
           FROM rides JOIN users ON rides.client_id = users.id
           WHERE rides.status='open' ORDER BY rides.created_at DESC LIMIT ?""",
        (limit,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_ride(ride_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rides WHERE id=?", (ride_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def accept_ride(ride_id, driver_id):
    """Driver accepts an open order -> status 'accepted' (driver is en route to pick up client)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT status FROM rides WHERE id=?", (ride_id,))
    row = cur.fetchone()
    if row is None or row["status"] != "open":
        conn.close()
        return False
    cur.execute("UPDATE rides SET status='accepted', driver_id=? WHERE id=?", (driver_id, ride_id))
    conn.commit()
    conn.close()
    return True


def mark_arrived(ride_id, driver_id):
    """Driver has arrived at the pickup point -> status 'arrived'."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT status, driver_id FROM rides WHERE id=?", (ride_id,))
    row = cur.fetchone()
    if row is None or row["status"] != "accepted" or row["driver_id"] != driver_id:
        conn.close()
        return False
    cur.execute("UPDATE rides SET status='arrived' WHERE id=?", (ride_id,))
    conn.commit()
    conn.close()
    return True


def complete_ride(ride_id):
    """Force-complete, used by admin panel regardless of current stage."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE rides SET status='completed' WHERE id=?", (ride_id,))
    conn.commit()
    conn.close()


def complete_ride_by_driver(ride_id, driver_id):
    """Driver finishes the trip -> status 'completed'. Only from 'arrived'."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT status, driver_id FROM rides WHERE id=?", (ride_id,))
    row = cur.fetchone()
    if row is None or row["status"] != "arrived" or row["driver_id"] != driver_id:
        conn.close()
        return False
    cur.execute("UPDATE rides SET status='completed' WHERE id=?", (ride_id,))
    conn.commit()
    conn.close()
    return True


def rate_ride(ride_id, client_id, rating, review=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT status, client_id, rating FROM rides WHERE id=?", (ride_id,))
    row = cur.fetchone()
    if row is None or row["client_id"] != client_id or row["status"] != "completed" or row["rating"] is not None:
        conn.close()
        return False
    cur.execute("UPDATE rides SET rating=?, review=? WHERE id=?", (rating, review, ride_id))
    conn.commit()
    conn.close()
    return True


def get_driver_rating(driver_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT rating FROM rides WHERE driver_id=? AND rating IS NOT NULL", (driver_id,))
    rows = [r["rating"] for r in cur.fetchall()]
    conn.close()
    if not rows:
        return {"avg": None, "count": 0}
    return {"avg": round(sum(rows) / len(rows), 1), "count": len(rows)}


def get_driver_history(driver_id, limit=10):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT rides.*, c.full_name as client_name FROM rides
           LEFT JOIN users c ON rides.client_id = c.id
           WHERE rides.driver_id=? AND rides.status='completed'
           ORDER BY rides.created_at DESC LIMIT ?""",
        (driver_id, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def set_blocked(user_id, blocked):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_blocked=? WHERE id=?", (1 if blocked else 0, user_id))
    conn.commit()
    conn.close()


def is_blocked(telegram_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT is_blocked FROM users WHERE telegram_id=?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row["is_blocked"]) if row else False


def get_setting(key, default=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
    conn.close()


def get_commission_percent():
    val = get_setting("commission_percent", "10")
    try:
        return float(val)
    except (TypeError, ValueError):
        return 10.0


def get_total_commission():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT price FROM rides WHERE status='completed'")
    rows = cur.fetchall()
    conn.close()
    percent = get_commission_percent()
    total_price = 0
    for r in rows:
        digits = "".join(ch for ch in (r["price"] or "") if ch.isdigit())
        if digits:
            total_price += int(digits)
    return round(total_price * percent / 100, 2)


def get_broadcast_recipients(role_filter=None):
    """role_filter: None (everyone), 'client', or 'driver'. Excludes blocked users."""
    conn = get_conn()
    cur = conn.cursor()
    if role_filter:
        cur.execute("SELECT telegram_id FROM users WHERE role=? AND is_blocked=0", (role_filter,))
    else:
        cur.execute("SELECT telegram_id FROM users WHERE is_blocked=0")
    rows = [r["telegram_id"] for r in cur.fetchall()]
    conn.close()
    return rows


def get_driver_active_ride(driver_id):
    """Return the driver's current accepted/arrived ride, if any."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM rides WHERE driver_id=? AND status IN ('accepted','arrived') ORDER BY created_at DESC LIMIT 1",
        (driver_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_driver_earnings(driver_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT price FROM rides WHERE driver_id=? AND status='completed'", (driver_id,))
    rows = cur.fetchall()
    conn.close()
    total = 0
    count = 0
    for r in rows:
        count += 1
        digits = "".join(ch for ch in (r["price"] or "") if ch.isdigit())
        if digits:
            total += int(digits)
    return {"total": total, "count": count}


def cancel_ride(ride_id):
    """Force-cancel, used by admin panel."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE rides SET status='cancelled' WHERE id=?", (ride_id,))
    conn.commit()
    conn.close()


def cancel_ride_by_client(ride_id, client_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT status, client_id FROM rides WHERE id=?", (ride_id,))
    row = cur.fetchone()
    if row is None or row["client_id"] != client_id or row["status"] not in ("open", "accepted"):
        conn.close()
        return False
    cur.execute("UPDATE rides SET status='cancelled' WHERE id=?", (ride_id,))
    conn.commit()
    conn.close()
    return True


def get_user_by_id(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_rides(limit=200):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT rides.*, c.full_name as client_name, d.full_name as driver_name
           FROM rides
           LEFT JOIN users c ON rides.client_id = c.id
           LEFT JOIN users d ON rides.driver_id = d.id
           ORDER BY rides.created_at DESC LIMIT ?""",
        (limit,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_all_users(limit=200):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_stats():
    conn = get_conn()
    cur = conn.cursor()
    stats = {}
    cur.execute("SELECT COUNT(*) as c FROM users")
    stats["total_users"] = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM users WHERE role='driver'")
    stats["total_drivers"] = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM users WHERE role='driver' AND is_online=1")
    stats["online_drivers"] = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM users WHERE role='client'")
    stats["total_clients"] = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM rides")
    stats["total_rides"] = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM rides WHERE status='open'")
    stats["open_rides"] = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM rides WHERE status='completed'")
    stats["completed_rides"] = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM rides WHERE service_type='delivery'")
    stats["total_deliveries"] = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM rides WHERE route='intercity'")
    stats["total_intercity"] = cur.fetchone()["c"]
    cur.execute("SELECT AVG(rating) as a, COUNT(rating) as c FROM rides WHERE rating IS NOT NULL")
    row = cur.fetchone()
    stats["avg_rating"] = round(row["a"], 1) if row["a"] else None
    stats["rating_count"] = row["c"]
    conn.close()
    return stats
