"""
Hotel Counter Manager - Flask + SQLite backend
Run: python app.py
Open: http://localhost:5000
"""

import sqlite3
import os
import time
import jwt
import json
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, g, request, jsonify, send_from_directory, render_template_string
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'hotel-counter-secret-2024-change-in-prod')
app.config['DATABASE'] = os.environ.get('DATABASE', 'counter.db')
print("Database:", os.path.abspath(app.config["DATABASE"]))
with app.app_context():
    def init_db():
    print("Initializing database...")
    db = sqlite3.connect(app.config['DATABASE'])
    ...
    init_db()
# ─── Database ─────────────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'], detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

def init_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id TEXT UNIQUE NOT NULL COLLATE NOCASE,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS varieties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            counter TEXT NOT NULL,
            name TEXT NOT NULL,
            icon TEXT NOT NULL DEFAULT 'bowl',
            sort_order INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            UNIQUE(counter, name)
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            counter TEXT NOT NULL,
            variety_name TEXT NOT NULL,
            variety_icon TEXT NOT NULL DEFAULT 'bowl',
            table_no TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            notes TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            placed_by TEXT NOT NULL,
            order_date TEXT NOT NULL,
            order_time TEXT NOT NULL,
            start_ms INTEGER NOT NULL,
            end_ms INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # Seed admin user
    existing = db.execute("SELECT id FROM users WHERE emp_id='ADMIN'").fetchone()
    if not existing:
        db.execute("INSERT INTO users (emp_id, name, password_hash, role) VALUES (?,?,?,?)",
            ('ADMIN', 'Administrator', generate_password_hash('admin123'), 'admin'))

    # Seed default varieties
    omelette_vars = [
        ('omelette', 'Plain omelette', 'egg'),
        ('omelette', 'Masala omelette', 'pepper'),
        ('omelette', 'Cheese omelette', 'cheese'),
        ('omelette', 'Mushroom omelette', 'mushroom'),
        ('omelette', 'Onion omelette', 'bowl'),
    ]
    dosa_vars = [
        ('dosa', 'Plain dosa', 'flame'),
        ('dosa', 'Masala dosa', 'pepper'),
        ('dosa', 'Ghee dosa', 'star'),
        ('dosa', 'Cheese dosa', 'cheese'),
        ('dosa', 'Rava dosa', 'leaf'),
        ('dosa', 'Onion dosa', 'bowl'),
    ]
    for i, (counter, name, icon) in enumerate(omelette_vars + dosa_vars):
        try:
            db.execute("INSERT INTO varieties (counter, name, icon, sort_order) VALUES (?,?,?,?)",
                (counter, name, icon, i))
        except sqlite3.IntegrityError:
            pass

    db.commit()
    db.close()

# ─── Auth ─────────────────────────────────────────────────────────────────────

def make_token(user):
    payload = {
        'sub': user['emp_id'],
        'name': user['name'],
        'role': user['role'],
        'exp': datetime.utcnow() + timedelta(hours=12)
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'Missing token'}), 401
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            g.user = data
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.user.get('role') != 'admin':
            return jsonify({'error': 'Admin only'}), 403
        return f(*args, **kwargs)
    return decorated

# ─── Auth Routes ──────────────────────────────────────────────────────────────

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    emp_id = (data.get('emp_id') or '').strip()
    password = data.get('password') or ''
    if not emp_id or not password:
        return jsonify({'error': 'Employee ID and password required'}), 400
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE emp_id=? COLLATE NOCASE", (emp_id,)).fetchone()
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Invalid employee ID or password'}), 401
    token = make_token(user)
    return jsonify({'token': token, 'user': {
        'emp_id': user['emp_id'], 'name': user['name'], 'role': user['role']
    }})

@app.route('/api/auth/me', methods=['GET'])
@require_auth
def me():
    return jsonify({'user': g.user})

# ─── Varieties ────────────────────────────────────────────────────────────────

@app.route('/api/varieties', methods=['GET'])
@require_auth
def get_varieties():
    counter = request.args.get('counter')
    db = get_db()
    if counter:
        rows = db.execute(
            "SELECT * FROM varieties WHERE counter=? AND active=1 ORDER BY sort_order, id",
            (counter,)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM varieties WHERE active=1 ORDER BY counter, sort_order, id").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/varieties', methods=['POST'])
@require_auth
@require_admin
def add_variety():
    data = request.get_json()
    counter = data.get('counter')
    name = (data.get('name') or '').strip()
    icon = data.get('icon', 'bowl')
    if not counter or not name:
        return jsonify({'error': 'counter and name required'}), 400
    db = get_db()
    try:
        db.execute("INSERT INTO varieties (counter, name, icon) VALUES (?,?,?)", (counter, name, icon))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Variety already exists'}), 409
    row = db.execute("SELECT * FROM varieties WHERE counter=? AND name=?", (counter, name)).fetchone()
    return jsonify(dict(row)), 201

@app.route('/api/varieties/<int:vid>', methods=['DELETE'])
@require_auth
@require_admin
def delete_variety(vid):
    db = get_db()
    db.execute("UPDATE varieties SET active=0 WHERE id=?", (vid,))
    db.commit()
    return jsonify({'ok': True})

# ─── Orders ───────────────────────────────────────────────────────────────────

@app.route('/api/orders', methods=['GET'])
@require_auth
def get_orders():
    counter = request.args.get('counter')
    status = request.args.get('status')
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    db = get_db()
    query = "SELECT * FROM orders WHERE order_date=?"
    params = [date]
    if counter and counter != 'all':
        query += " AND counter=?"
        params.append(counter)
    if status and status != 'all':
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY id DESC"
    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/orders', methods=['POST'])
@require_auth
def place_order():
    data = request.get_json()
    counter = data.get('counter')
    variety_name = data.get('variety_name')
    variety_icon = data.get('variety_icon', 'bowl')
    table_no = (data.get('table_no') or '').strip().upper()
    quantity = int(data.get('quantity', 1))
    notes = (data.get('notes') or '').strip()
    if not counter or not variety_name or not table_no:
        return jsonify({'error': 'counter, variety_name and table_no required'}), 400
    now = datetime.now()
    db = get_db()
    cur = db.execute("""
        INSERT INTO orders
          (counter, variety_name, variety_icon, table_no, quantity, notes,
           status, placed_by, order_date, order_time, start_ms)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        counter, variety_name, variety_icon, table_no, quantity, notes,
        'pending', g.user['sub'],
        now.strftime('%Y-%m-%d'), now.strftime('%I:%M %p'),
        int(time.time() * 1000)
    ))
    db.commit()
    row = db.execute("SELECT * FROM orders WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201

@app.route('/api/orders/<int:oid>/complete', methods=['PATCH'])
@require_auth
def complete_order(oid):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order:
        return jsonify({'error': 'Not found'}), 404
    end_ms = int(time.time() * 1000)
    db.execute("UPDATE orders SET status='completed', end_ms=? WHERE id=?", (end_ms, oid))
    db.commit()
    row = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/orders/<int:oid>', methods=['DELETE'])
@require_auth
def delete_order(oid):
    db = get_db()
    db.execute("DELETE FROM orders WHERE id=?", (oid,))
    db.commit()
    return jsonify({'ok': True})

# ─── Reports ──────────────────────────────────────────────────────────────────

@app.route('/api/reports/summary', methods=['GET'])
@require_auth
@require_admin
def report_summary():
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM orders WHERE order_date=?", (date,)).fetchone()[0]
    done = db.execute("SELECT COUNT(*) FROM orders WHERE order_date=? AND status='completed'", (date,)).fetchone()[0]
    pending = db.execute("SELECT COUNT(*) FROM orders WHERE order_date=? AND status='pending'", (date,)).fetchone()[0]
    by_variety = db.execute("""
        SELECT variety_name, SUM(quantity) as qty
        FROM orders WHERE order_date=?
        GROUP BY variety_name ORDER BY qty DESC LIMIT 10
    """, (date,)).fetchall()
    avg_delivery = db.execute("""
        SELECT AVG(end_ms - start_ms) FROM orders
        WHERE order_date=? AND status='completed' AND end_ms IS NOT NULL
    """, (date,)).fetchone()[0]
    return jsonify({
        'total': total, 'completed': done, 'pending': pending,
        'by_variety': [dict(r) for r in by_variety],
        'avg_delivery_ms': int(avg_delivery) if avg_delivery else None
    })

# ─── User Management (Admin) ──────────────────────────────────────────────────

@app.route('/api/users', methods=['GET'])
@require_auth
@require_admin
def get_users():
    db = get_db()
    rows = db.execute("SELECT id, emp_id, name, role, created_at FROM users ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/users', methods=['POST'])
@require_auth
@require_admin
def add_user():
    data = request.get_json()
    emp_id = (data.get('emp_id') or '').strip().upper()
    name = (data.get('name') or '').strip()
    password = data.get('password') or ''
    role = data.get('role', 'staff')
    if not emp_id or not name or not password:
        return jsonify({'error': 'emp_id, name and password required'}), 400
    if role not in ('staff', 'admin'):
        return jsonify({'error': 'role must be staff or admin'}), 400
    db = get_db()
    try:
        db.execute("INSERT INTO users (emp_id, name, password_hash, role) VALUES (?,?,?,?)",
            (emp_id, name, generate_password_hash(password), role))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Employee ID already exists'}), 409
    user = db.execute("SELECT id, emp_id, name, role FROM users WHERE emp_id=?", (emp_id,)).fetchone()
    return jsonify(dict(user)), 201

@app.route('/api/users/<int:uid>', methods=['DELETE'])
@require_auth
@require_admin
def delete_user(uid):
    db = get_db()
    user = db.execute("SELECT emp_id FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        return jsonify({'error': 'Not found'}), 404
    if user['emp_id'].upper() == 'ADMIN':
        return jsonify({'error': 'Cannot delete main admin'}), 403
    if user['emp_id'].upper() == g.user['sub'].upper():
        return jsonify({'error': 'Cannot delete yourself'}), 403
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    return jsonify({'ok': True})

# ─── Frontend (Single Page App) ───────────────────────────────────────────────

@app.route('/')
@app.route('/<path:path>')
def index(path=None):
    return render_template_string(HTML_APP)

# ─── HTML App ─────────────────────────────────────────────────────────────────

HTML_APP = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Counter Manager</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css"/>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
:root{
  --bg:#fff;--bg2:#f5f5f4;--bg3:#f0efed;
  --text:#1a1a18;--text2:#6b6b67;--text3:#a0a09c;
  --border:rgba(0,0,0,.12);--border2:rgba(0,0,0,.22);
  --accent:#D85A30;--accent-bg:#FAECE7;--accent-text:#993C1D;
  --green-bg:#EAF3DE;--green-text:#3B6D11;
  --amber-bg:#FAEEDA;--amber-text:#854F0B;
  --blue-bg:#E6F1FB;--blue-text:#185FA5;
  --red-bg:#FCEBEB;--red-text:#A32D2D;
  --radius:8px;--radius-lg:12px;
}
@media(prefers-color-scheme:dark){
  :root{
    --bg:#1c1c1a;--bg2:#252522;--bg3:#2e2e2a;
    --text:#f0efed;--text2:#a0a09c;--text3:#6b6b67;
    --border:rgba(255,255,255,.12);--border2:rgba(255,255,255,.22);
    --accent:#e8714a;--accent-bg:#3d1a0e;--accent-text:#f0997b;
    --green-bg:#1a2e10;--green-text:#97C459;
    --amber-bg:#2e1e08;--amber-text:#EF9F27;
    --blue-bg:#0d1e30;--blue-text:#85B7EB;
    --red-bg:#2e0e0e;--red-text:#F09595;
  }
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg3);color:var(--text);font-size:14px;line-height:1.5;}
a{color:inherit;text-decoration:none;}
button{cursor:pointer;font-family:inherit;}
input,select{font-family:inherit;}

/* Layout */
.app{display:flex;flex-direction:column;min-height:100vh;}
.topbar{background:var(--bg);border-bottom:0.5px solid var(--border);padding:0 1rem;display:flex;align-items:center;justify-content:space-between;height:52px;position:sticky;top:0;z-index:50;}
.logo{font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px;color:var(--text);}
.logo i{font-size:20px;color:var(--accent);}
.content{padding:1rem;flex:1;}
.card{background:var(--bg);border:0.5px solid var(--border);border-radius:var(--radius-lg);padding:1rem 1.25rem;margin-bottom:12px;}
.sec-title{font-size:10px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.07em;margin-bottom:10px;}
.split{display:grid;grid-template-columns:1fr 1fr;gap:1rem;}
@media(max-width:600px){.split{grid-template-columns:1fr;}}

/* Tabs */
.tab-bar{display:flex;gap:2px;background:var(--bg2);border-radius:var(--radius);padding:3px;}
.tab{padding:5px 13px;border-radius:6px;font-size:13px;cursor:pointer;border:none;background:transparent;color:var(--text2);transition:all .15s;}
.tab.active{background:var(--bg);color:var(--text);font-weight:500;border:0.5px solid var(--border);}
.ctab{padding:6px 16px;border-radius:20px;font-size:13px;cursor:pointer;border:0.5px solid var(--border2);background:transparent;color:var(--text2);transition:all .15s;display:flex;align-items:center;gap:5px;}
.ctab.active{background:var(--accent);color:#fff;border-color:var(--accent);}
.counter-tabs{display:flex;gap:8px;margin-bottom:1rem;flex-wrap:wrap;}

/* Forms */
.fg{display:flex;flex-direction:column;gap:5px;margin-bottom:10px;}
.fg label{font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.06em;}
.fg input,.fg select{height:38px;padding:0 10px;border-radius:var(--radius);border:0.5px solid var(--border2);background:var(--bg);color:var(--text);font-size:14px;outline:none;width:100%;transition:border-color .15s;}
.fg input:focus,.fg select:focus{border-color:var(--accent);box-shadow:0 0 0 2px rgba(216,90,48,.15);}
.btn{height:38px;padding:0 16px;border-radius:var(--radius);border:none;font-size:13px;font-weight:500;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;transition:all .15s;}
.btn-primary{background:var(--accent);color:#fff;}
.btn-primary:hover{opacity:.88;}
.btn-primary:active{transform:scale(.98);}
.btn-sm{height:30px;padding:0 11px;font-size:12px;}
.btn-ghost{background:var(--bg2);color:var(--text2);border:0.5px solid var(--border);}
.btn-ghost:hover{border-color:var(--border2);}
.btn-danger{background:var(--red-bg);color:var(--red-text);}
.btn-danger:hover{opacity:.85;}
.btn-success{background:var(--green-bg);color:var(--green-text);}
.btn-success:hover{opacity:.85;}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
@media(max-width:480px){.form-grid{grid-template-columns:1fr;}}

/* Variety grid */
.variety-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:8px;margin-bottom:1rem;}
.vcard{border:0.5px solid var(--border);border-radius:10px;padding:12px 6px;display:flex;flex-direction:column;align-items:center;gap:6px;cursor:pointer;background:var(--bg);transition:all .15s;position:relative;user-select:none;}
.vcard:hover{border-color:var(--accent);background:var(--accent-bg);}
.vcard.selected{border:1.5px solid var(--accent);background:var(--accent-bg);}
.vcard i{font-size:26px;color:var(--text3);}
.vcard.selected i{color:var(--accent);}
.vcard span{font-size:10px;text-align:center;color:var(--text2);line-height:1.3;}
.vcard.selected span{color:var(--accent-text);font-weight:500;}
.vcard .vdel{position:absolute;top:3px;right:3px;background:none;border:none;cursor:pointer;color:var(--text3);font-size:12px;padding:2px;opacity:0;line-height:1;}
.vcard:hover .vdel{opacity:1;}

/* Orders */
.order-card{background:var(--bg);border:0.5px solid var(--border);border-radius:var(--radius-lg);padding:12px 14px;display:flex;align-items:center;gap:12px;margin-bottom:8px;transition:border-color .15s;}
.order-card:hover{border-color:var(--border2);}
.tbl-badge{width:48px;height:48px;border-radius:10px;background:var(--accent-bg);display:flex;flex-direction:column;align-items:center;justify-content:center;flex-shrink:0;}
.tbl-badge span{font-size:7px;color:var(--accent-text);font-weight:600;text-transform:uppercase;letter-spacing:.06em;}
.tbl-badge strong{font-size:13px;color:var(--accent);font-weight:600;line-height:1.2;max-width:44px;text-align:center;word-break:break-all;}
.oi{flex:1;min-width:0;}
.oi .oname{font-size:13px;font-weight:500;color:var(--text);}
.oi .ometa{font-size:11px;color:var(--text2);margin-top:2px;}
.oi .otimer{font-size:11px;color:var(--amber-text);margin-top:3px;display:flex;align-items:center;gap:3px;}
.oi .otimer.fast{color:var(--green-text);}
.badge{padding:3px 9px;border-radius:20px;font-size:11px;font-weight:500;white-space:nowrap;display:inline-flex;align-items:center;gap:4px;}
.badge-pend{background:var(--amber-bg);color:var(--amber-text);}
.badge-done{background:var(--green-bg);color:var(--green-text);}
.badge-admin{background:var(--blue-bg);color:var(--blue-text);}
.pend-pill{background:var(--amber-bg);color:var(--amber-text);border-radius:10px;padding:1px 7px;font-size:11px;margin-left:5px;}

/* Stats */
.stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px;}
.stat-c{background:var(--bg2);border-radius:var(--radius);padding:12px;}
.stat-c .sl{font-size:10px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.06em;}
.stat-c .sv{font-size:26px;font-weight:500;color:var(--text);margin-top:3px;}

/* Chart bars */
.bar-row{display:flex;align-items:center;gap:8px;margin-bottom:7px;}
.bar-lbl{font-size:11px;color:var(--text2);width:110px;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.bar-track{flex:1;height:18px;background:var(--bg2);border-radius:4px;overflow:hidden;}
.bar-fill{height:100%;background:var(--accent);border-radius:4px;transition:width .4s;}
.bar-val{font-size:11px;color:var(--text2);width:24px;text-align:right;}

/* Report table */
.rtbl-wrap{overflow-x:auto;}
.rtbl{width:100%;border-collapse:collapse;font-size:12px;}
.rtbl th{text-align:left;padding:7px 10px;font-size:10px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.06em;border-bottom:0.5px solid var(--border);white-space:nowrap;}
.rtbl td{padding:9px 10px;border-bottom:0.5px solid var(--border);color:var(--text);}
.rtbl tr:last-child td{border-bottom:none;}
.rtbl tr:hover td{background:var(--bg2);}

/* Report filters */
.rpt-filters{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center;}
.rpt-filters select,.rpt-filters input[type=date]{height:32px;padding:0 8px;border-radius:var(--radius);border:0.5px solid var(--border2);background:var(--bg);color:var(--text);font-size:12px;outline:none;}

/* Icon selector */
.icon-sel{display:flex;gap:6px;flex-wrap:wrap;}
.icon-opt{width:36px;height:36px;border-radius:var(--radius);border:0.5px solid var(--border);display:flex;align-items:center;justify-content:center;cursor:pointer;background:var(--bg);transition:all .15s;font-size:18px;color:var(--text2);}
.icon-opt:hover{border-color:var(--accent);background:var(--accent-bg);}
.icon-opt.sel{border-color:var(--accent);background:var(--accent-bg);color:var(--accent);}

/* User list */
.ui-row{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:0.5px solid var(--border);}
.ui-row:last-child{border-bottom:none;}
.u-av{width:34px;height:34px;border-radius:50%;background:var(--blue-bg);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:var(--blue-text);flex-shrink:0;}
.u-info{flex:1;}
.u-name{font-size:13px;font-weight:500;color:var(--text);}
.u-id{font-size:11px;color:var(--text2);}

/* Login */
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg3);}
.login-box{background:var(--bg);border:0.5px solid var(--border);border-radius:16px;padding:2rem;width:320px;}
.login-logo{display:flex;align-items:center;gap:10px;margin-bottom:1.5rem;}
.login-logo i{font-size:28px;color:var(--accent);}
.login-logo div .t1{font-size:18px;font-weight:600;color:var(--text);}
.login-logo div .t2{font-size:11px;color:var(--text2);}
.login-err{font-size:12px;color:var(--red-text);margin-top:8px;text-align:center;min-height:16px;}
.login-hint{margin-top:16px;padding-top:12px;border-top:0.5px solid var(--border);font-size:11px;color:var(--text3);text-align:center;}

/* Toast */
#toast{position:fixed;bottom:20px;right:20px;background:#2d5a1b;color:#fff;padding:10px 16px;border-radius:var(--radius);font-size:13px;font-weight:500;opacity:0;pointer-events:none;transition:opacity .2s;display:flex;align-items:center;gap:6px;z-index:1000;max-width:300px;}
#toast.show{opacity:1;}

/* Empty state */
.empty{text-align:center;padding:2.5rem;color:var(--text3);}
.empty i{font-size:34px;display:block;margin-bottom:10px;opacity:.35;}

/* Avatar */
.top-av{width:30px;height:30px;border-radius:50%;background:var(--accent-bg);display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;color:var(--accent-text);}

/* Spinner */
.spin{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;}
@keyframes spin{to{transform:rotate(360deg);}}
</style>
</head>
<body>
<div id="root"></div>
<div id="toast"></div>

<script>
const API = '';
const ICONS = {
  egg:'ti-egg',flame:'ti-flame',leaf:'ti-leaf',star:'ti-star',
  bowl:'ti-bowl',pepper:'ti-plant-2',cheese:'ti-cheese',mushroom:'ti-mushroom',
  fish:'ti-fish',heart:'ti-heart',coffee:'ti-coffee',bread:'ti-bread'
};
const ICON_KEYS = Object.keys(ICONS);

// ── State ──────────────────────────────────────────────────────────────────
let state = {
  user: null, token: null,
  currentCounter: 'omelette', adminCounter: 'omelette',
  currentStatus: 'pending', adminSection: 'varieties',
  selectedVariety: null, selectedIcon: 'egg',
  varieties: {omelette: [], dosa: []},
  orders: [], users: [],
  reportDate: today(), reportCounter: 'all', reportStatus: 'all', reportVariety: 'all',
  reportData: [], loading: false,
};

function today() { return new Date().toISOString().split('T')[0]; }
function getInitials(name) { return name.split(' ').map(x=>x[0]).join('').slice(0,2).toUpperCase(); }
function fmsDuration(ms) {
  if (!ms) return '—';
  const s = Math.round(ms / 1000);
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60), r = s % 60;
  return m + 'm' + (r > 0 ? ' ' + r + 's' : '');
}
function elapsedMs(startMs) { return Date.now() - startMs; }

// ── API helpers ───────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (state.token) opts.headers['Authorization'] = 'Bearer ' + state.token;
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + '/api' + path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

// ── Toast ─────────────────────────────────────────────────────────────────
function toast(msg, type='success') {
  const el = document.getElementById('toast');
  el.style.background = type === 'success' ? '#2d5a1b' : '#8a2020';
  el.innerHTML = `<i class="ti ti-${type==='success'?'check':'alert-circle'}"></i> ${msg}`;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3000);
}

// ── Render helpers ────────────────────────────────────────────────────────
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el[k] = v;
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}
function icon(name, style='') {
  const i = document.createElement('i');
  i.className = 'ti ti-' + name;
  if (style) i.setAttribute('style', style);
  i.setAttribute('aria-hidden', 'true');
  return i;
}
function badge(text, cls) {
  return h('span', {class: 'badge ' + cls}, text);
}

// ── Main render ───────────────────────────────────────────────────────────
function render() {
  const root = document.getElementById('root');
  root.innerHTML = '';
  if (!state.user) { root.appendChild(renderLogin()); return; }
  root.appendChild(renderApp());
}

// ── Login ─────────────────────────────────────────────────────────────────
function renderLogin() {
  const wrap = h('div', {class: 'login-wrap'});
  const box = h('div', {class: 'login-box'});
  box.appendChild(h('div', {class:'login-logo'},
    icon('tools-kitchen-2'),
    h('div', {}, h('div', {class:'t1'}, 'Counter Manager'), h('div', {class:'t2'}, 'Hotel Kitchen System'))
  ));
  const empIn = h('input', {type:'text', placeholder:'e.g. EMP001', id:'loginId'});
  const pwIn = h('input', {type:'password', placeholder:'Enter password', id:'loginPw'});
  const err = h('div', {class:'login-err', id:'loginErr'});
  const btn = h('button', {class:'btn btn-primary', style:'width:100%;margin-top:4px;', onclick: doLogin}, icon('login'), 'Sign in');
  box.appendChild(h('div', {class:'fg'}, h('label', {}, 'Employee ID'), empIn));
  box.appendChild(h('div', {class:'fg'}, h('label', {}, 'Password'), pwIn));
  box.appendChild(btn);
  box.appendChild(err);
  box.appendChild(h('div', {class:'login-hint'}, 'Default admin: ADMIN / admin123'));
  wrap.appendChild(box);
  pwIn.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
  setTimeout(() => empIn.focus(), 50);
  return wrap;
}

async function doLogin() {
  const empId = document.getElementById('loginId').value.trim();
  const pw = document.getElementById('loginPw').value;
  const err = document.getElementById('loginErr');
  err.textContent = '';
  try {
    const data = await api('POST', '/auth/login', {emp_id: empId, password: pw});
    state.token = data.token;
    state.user = data.user;
    await Promise.all([loadVarieties(), loadOrders()]);
    render();
  } catch(e) {
    err.textContent = e.message;
  }
}

// ── App shell ─────────────────────────────────────────────────────────────
function renderApp() {
  const app = h('div', {class:'app'});
  // Topbar
  const topbar = h('div', {class:'topbar'});
  topbar.appendChild(h('div', {class:'logo'}, icon('tools-kitchen-2'), 'Counter Manager'));
  const right = h('div', {style:'display:flex;align-items:center;gap:10px;'});
  // Main tab bar
  const tabBar = h('div', {class:'tab-bar'});
  const staffTab = h('button', {class:'tab' + (state.adminSection==='staff'||!state.adminSection||state.currentMain==='staff'?'':' '), onclick: () => { state.currentMain='staff'; render(); }});
  staffTab.className = 'tab' + (state.currentMain !== 'admin' ? ' active' : '');
  staffTab.appendChild(icon('chef-hat', 'font-size:12px;margin-right:3px;'));
  staffTab.appendChild(document.createTextNode('Staff'));
  tabBar.appendChild(staffTab);
  if (state.user.role === 'admin') {
    const adminTab = h('button', {class:'tab' + (state.currentMain === 'admin' ? ' active' : ''), onclick: () => { state.currentMain='admin'; render(); }});
    adminTab.appendChild(icon('shield', 'font-size:12px;margin-right:3px;'));
    adminTab.appendChild(document.createTextNode('Admin'));
    tabBar.appendChild(adminTab);
  }
  right.appendChild(tabBar);
  // User pill
  const pill = h('div', {style:'display:flex;align-items:center;gap:8px;'});
  const av = h('div', {class:'top-av'}, getInitials(state.user.name));
  const nm = h('span', {style:'font-size:12px;color:var(--text2);'}, state.user.name.split(' ')[0]);
  const logoutBtn = h('button', {class:'btn btn-ghost', style:'height:28px;padding:0 8px;font-size:12px;', onclick: doLogout}, icon('logout', 'font-size:12px;'));
  pill.appendChild(av); pill.appendChild(nm); pill.appendChild(logoutBtn);
  right.appendChild(pill);
  topbar.appendChild(right);
  app.appendChild(topbar);

  const content = h('div', {class:'content'});
  if (state.currentMain === 'admin' && state.user.role === 'admin') {
    content.appendChild(renderAdmin());
  } else {
    content.appendChild(renderStaff());
  }
  app.appendChild(content);
  return app;
}

function doLogout() {
  state.user = null; state.token = null; state.orders = [];
  state.currentMain = 'staff'; render();
}

// ── Staff view ────────────────────────────────────────────────────────────
function renderStaff() {
  const div = h('div', {});

  // Counter tabs
  const ctabs = h('div', {class:'counter-tabs'});
  ['omelette','dosa'].forEach(c => {
    const btn = h('button', {
      class: 'ctab' + (state.currentCounter===c?' active':''),
      onclick: async () => { state.currentCounter=c; state.selectedVariety=null; await loadOrders(); render(); }
    });
    btn.appendChild(icon(c==='omelette'?'egg':'flame', 'font-size:13px;'));
    btn.appendChild(document.createTextNode(' ' + c.charAt(0).toUpperCase()+c.slice(1) + ' counter'));
    ctabs.appendChild(btn);
  });
  div.appendChild(ctabs);

  // New order form
  const formCard = h('div', {class:'card'});
  formCard.appendChild(h('div', {class:'sec-title'}, 'New order'));
  formCard.appendChild(h('div', {class:'sec-title', style:'margin-bottom:8px;font-size:9px;'}, 'Select variety'));
  formCard.appendChild(renderVarietyGrid(false));
  formCard.appendChild(renderOrderForm());
  div.appendChild(formCard);

  // Status tabs
  const stabs = h('div', {style:'display:flex;gap:10px;margin-bottom:12px;align-items:center;'});
  const statusTabBar = h('div', {class:'tab-bar'});
  const pendCount = state.orders.filter(o=>o.counter===state.currentCounter&&o.status==='pending').length;
  ['pending','completed'].forEach((s,i) => {
    const btn = h('button', {class:'tab'+(state.currentStatus===s?' active':''), onclick: () => { state.currentStatus=s; render(); }});
    btn.appendChild(document.createTextNode(s==='pending'?'Pending':'Completed'));
    if (s==='pending') {
      const pill = h('span', {class:'pend-pill'}, String(pendCount));
      btn.appendChild(pill);
    }
    statusTabBar.appendChild(btn);
  });
  stabs.appendChild(statusTabBar);
  div.appendChild(stabs);

  // Order list
  div.appendChild(renderOrderList());
  return div;
}

function renderVarietyGrid(adminMode) {
  const grid = h('div', {class:'variety-grid'});
  const vars = state.varieties[adminMode ? state.adminCounter : state.currentCounter] || [];
  if (!vars.length) {
    grid.appendChild(h('div', {style:'color:var(--text3);font-size:12px;grid-column:1/-1;padding:8px 0;'},
      adminMode ? 'No varieties yet.' : 'No varieties. Ask admin to add some.'));
    return grid;
  }
  vars.forEach((v, i) => {
    const iconCls = ICONS[v.icon] || 'ti-bowl';
    const isSelected = !adminMode && state.selectedVariety === v.id;
    const card = h('div', {class:'vcard'+(isSelected?' selected':'')});
    card.appendChild(icon(iconCls.replace('ti-',''), 'font-size:26px;'));
    card.appendChild(h('span', {}, v.name));
    if (!adminMode) {
      card.onclick = () => { state.selectedVariety = v.id; state.selectedVarietyData = v; render(); };
    } else {
      const delBtn = h('button', {class:'vdel', title:'Remove'}, '✕');
      delBtn.onclick = async (e) => { e.stopPropagation(); await deleteVariety(v.id, v.name); };
      card.appendChild(delBtn);
    }
    grid.appendChild(card);
  });
  return grid;
}

function renderOrderForm() {
  const fgrid = h('div', {class:'form-grid'});
  const tableIn = h('input', {type:'text', id:'tableInput', placeholder:'e.g. A5, VIP1, T12', maxlength:'10', style:'text-transform:uppercase;'});
  const qtyIn = h('input', {type:'number', id:'qtyInput', placeholder:'1', min:'1', max:'20', value:'1'});
  const notesIn = h('input', {type:'text', id:'notesInput', placeholder:'e.g. no onion, extra spicy'});
  fgrid.appendChild(h('div', {class:'fg'}, h('label', {}, 'Table no. (alphanumeric)'), tableIn));
  fgrid.appendChild(h('div', {class:'fg'}, h('label', {}, 'Quantity'), qtyIn));
  fgrid.appendChild(h('div', {class:'fg', style:'grid-column:1/-1;margin-bottom:0;'}, h('label', {}, 'Notes (optional)'), notesIn));
  const submitBtn = h('button', {class:'btn btn-primary', style:'grid-column:1/-1;width:100%;height:40px;font-size:14px;margin-top:4px;', onclick: placeOrder}, icon('plus'), 'Place order');
  fgrid.appendChild(submitBtn);
  return fgrid;
}

async function placeOrder() {
  if (!state.selectedVariety || !state.selectedVarietyData) { toast('Select a variety first', 'error'); return; }
  const table = (document.getElementById('tableInput').value||'').trim().toUpperCase();
  const qty = parseInt(document.getElementById('qtyInput').value) || 1;
  const notes = (document.getElementById('notesInput').value||'').trim();
  if (!table) { toast('Enter a table number', 'error'); return; }
  try {
    await api('POST', '/orders', {
      counter: state.currentCounter,
      variety_name: state.selectedVarietyData.name,
      variety_icon: state.selectedVarietyData.icon,
      table_no: table, quantity: qty, notes
    });
    state.selectedVariety = null; state.selectedVarietyData = null;
    state.currentStatus = 'pending';
    await loadOrders();
    render();
    toast(`Order placed — Table ${table}`);
  } catch(e) { toast(e.message, 'error'); }
}

function renderOrderList() {
  const div = h('div', {});
  const filtered = state.orders.filter(o => o.counter===state.currentCounter && o.status===state.currentStatus);
  if (!filtered.length) {
    const emp = h('div', {class:'empty'});
    emp.appendChild(icon('clipboard-list', 'font-size:32px;display:block;margin-bottom:8px;opacity:.3;'));
    emp.appendChild(document.createTextNode(state.currentStatus==='pending' ? 'No pending orders. Select a variety and place an order above.' : 'No completed orders yet.'));
    div.appendChild(emp);
    return div;
  }
  filtered.forEach(o => {
    const card = h('div', {class:'order-card'});
    const tbl = h('div', {class:'tbl-badge'}, h('span', {}, 'Table'), h('strong', {}, o.table_no));
    const info = h('div', {class:'oi'});
    const iconName = ICONS[o.variety_icon] ? o.variety_icon : 'bowl';
    const nameRow = h('div', {class:'oname'});
    nameRow.appendChild(icon(iconName, 'font-size:13px;margin-right:4px;'));
    nameRow.appendChild(document.createTextNode(o.variety_name + ' '));
    nameRow.appendChild(h('span', {style:'color:var(--text2);font-weight:400;'}, '× ' + o.quantity));
    info.appendChild(nameRow);
    if (o.notes) info.appendChild(h('div', {class:'ometa'}, o.notes));
    const elMs = o.status==='completed' && o.end_ms ? (o.end_ms - o.start_ms) : elapsedMs(o.start_ms);
    const isLong = elMs > 600000;
    const timerDiv = h('div', {class:'otimer' + (!isLong?' fast':'')});
    timerDiv.appendChild(icon('clock', 'font-size:11px;'));
    timerDiv.appendChild(document.createTextNode(' ' + fmsDuration(elMs) + (o.status==='pending'?' elapsed':' to deliver')));
    info.appendChild(timerDiv);
    card.appendChild(tbl); card.appendChild(info);
    if (o.status === 'pending') {
      const doneBtn = h('button', {class:'btn btn-sm btn-success', onclick: () => markDone(o.id)}, icon('check'), 'Done');
      card.appendChild(doneBtn);
    } else {
      card.appendChild(badge('✓ Done', 'badge-done'));
    }
    const delBtn = h('button', {class:'btn btn-sm btn-danger', style:'margin-left:4px;', onclick: () => removeOrder(o.id)});
    delBtn.appendChild(icon('trash'));
    card.appendChild(delBtn);
    div.appendChild(card);
  });
  return div;
}

async function markDone(id) {
  try { await api('PATCH', `/orders/${id}/complete`); await loadOrders(); render(); toast('Order completed'); }
  catch(e) { toast(e.message, 'error'); }
}

async function removeOrder(id) {
  try { await api('DELETE', `/orders/${id}`); await loadOrders(); render(); }
  catch(e) { toast(e.message, 'error'); }
}

// ── Admin view ────────────────────────────────────────────────────────────
function renderAdmin() {
  const div = h('div', {});
  // Section tabs
  const sections = ['varieties','users','reports'];
  const sTabBar = h('div', {class:'tab-bar', style:'display:inline-flex;margin-bottom:1rem;'});
  sections.forEach(s => {
    const btn = h('button', {class:'tab'+(state.adminSection===s?' active':''), onclick: async () => {
      state.adminSection = s;
      if (s==='users') await loadUsers();
      if (s==='reports') await loadReport();
      render();
    }}, s.charAt(0).toUpperCase()+s.slice(1));
    sTabBar.appendChild(btn);
  });
  div.appendChild(sTabBar);

  if (state.adminSection === 'varieties') div.appendChild(renderAdminVarieties());
  if (state.adminSection === 'users') div.appendChild(renderAdminUsers());
  if (state.adminSection === 'reports') div.appendChild(renderAdminReports());
  return div;
}

function renderAdminVarieties() {
  const split = h('div', {class:'split'});
  // Left: variety manager
  const left = h('div', {});
  const card = h('div', {class:'card'});
  card.appendChild(h('div', {class:'sec-title'}, 'Manage varieties'));
  const atabs = h('div', {class:'counter-tabs'});
  ['omelette','dosa'].forEach(c => {
    const btn = h('button', {class:'ctab'+(state.adminCounter===c?' active':''), onclick: () => { state.adminCounter=c; render(); }}, c.charAt(0).toUpperCase()+c.slice(1));
    atabs.appendChild(btn);
  });
  card.appendChild(atabs);
  card.appendChild(renderVarietyGrid(true));
  // Add variety form
  const addSec = h('div', {style:'margin-top:12px;padding-top:12px;border-top:0.5px solid var(--border);'});
  addSec.appendChild(h('div', {class:'sec-title'}, 'Add new variety'));
  const nameIn = h('input', {type:'text', id:'newVName', placeholder:'Variety name...', style:'height:34px;padding:0 10px;border-radius:var(--radius);border:0.5px solid var(--border2);background:var(--bg);color:var(--text);font-size:13px;outline:none;width:100%;margin-bottom:10px;'});
  addSec.appendChild(nameIn);
  addSec.appendChild(h('div', {class:'sec-title', style:'margin-bottom:6px;'}, 'Choose icon'));
  const iconSel = h('div', {class:'icon-sel', style:'margin-bottom:10px;'});
  ICON_KEYS.forEach(k => {
    const opt = h('div', {class:'icon-opt'+(state.selectedIcon===k?' sel':''), onclick: () => { state.selectedIcon=k; render(); }});
    opt.appendChild(icon(ICONS[k].replace('ti-',''), 'font-size:18px;'));
    opt.title = k;
    iconSel.appendChild(opt);
  });
  addSec.appendChild(iconSel);
  const addBtn = h('button', {class:'btn btn-primary', onclick: addVariety}, icon('plus'), 'Add variety');
  addSec.appendChild(addBtn);
  nameIn.addEventListener('keydown', e => { if(e.key==='Enter') addVariety(); });
  card.appendChild(addSec);
  left.appendChild(card);
  split.appendChild(left);

  // Right: summary
  const right = h('div', {});
  right.appendChild(renderSummaryCard());
  split.appendChild(right);
  return split;
}

function renderSummaryCard() {
  const card = h('div', {class:'card'});
  card.appendChild(h('div', {class:'sec-title'}, "Today's overview"));
  const todayOrders = state.orders.filter(o => o.order_date === today());
  const total = todayOrders.length;
  const done = todayOrders.filter(o=>o.status==='completed').length;
  const pend = todayOrders.filter(o=>o.status==='pending').length;
  const sg = h('div', {class:'stat-grid'});
  [['Total', total],['Done', done],['Pending', pend]].forEach(([l,v]) => {
    sg.appendChild(h('div', {class:'stat-c'}, h('div', {class:'sl'}, l), h('div', {class:'sv'}, String(v))));
  });
  card.appendChild(sg);
  card.appendChild(h('div', {class:'sec-title', style:'margin-top:4px;'}, 'Orders by variety'));
  const vc = {};
  todayOrders.forEach(o => { vc[o.variety_name] = (vc[o.variety_name]||0) + o.quantity; });
  const sorted = Object.entries(vc).sort((a,b)=>b[1]-a[1]).slice(0,6);
  const max = sorted.length ? sorted[0][1] : 1;
  if (!sorted.length) {
    card.appendChild(h('div', {style:'color:var(--text3);font-size:12px;text-align:center;padding:1rem;'}, 'No orders today'));
  } else {
    sorted.forEach(([name, count]) => {
      const row = h('div', {class:'bar-row'});
      row.appendChild(h('div', {class:'bar-lbl', title:name}, name));
      const track = h('div', {class:'bar-track'});
      const fill = h('div', {class:'bar-fill', style:`width:${Math.round(count/max*100)}%`});
      track.appendChild(fill); row.appendChild(track);
      row.appendChild(h('div', {class:'bar-val'}, String(count)));
      card.appendChild(row);
    });
  }
  return card;
}

async function addVariety() {
  const nameEl = document.getElementById('newVName');
  const name = (nameEl ? nameEl.value : '').trim();
  if (!name) { toast('Enter a variety name', 'error'); return; }
  try {
    await api('POST', '/varieties', {counter: state.adminCounter, name, icon: state.selectedIcon});
    await loadVarieties();
    render();
    toast(`"${name}" added`);
  } catch(e) { toast(e.message, 'error'); }
}

async function deleteVariety(id, name) {
  try {
    await api('DELETE', `/varieties/${id}`);
    await loadVarieties(); render();
    toast(`"${name}" removed`);
  } catch(e) { toast(e.message, 'error'); }
}

function renderAdminUsers() {
  const split = h('div', {class:'split'});
  // Create user form
  const left = h('div', {});
  const card = h('div', {class:'card'});
  card.appendChild(h('div', {class:'sec-title'}, 'Create user'));
  const fields = [
    ['newEmpId','text','Employee ID','e.g. EMP005'],
    ['newEmpName','text','Name','Full name'],
    ['newEmpPw','password','Password','Set password'],
  ];
  fields.forEach(([id,type,label,ph]) => {
    const inp = h('input', {type, id, placeholder:ph});
    card.appendChild(h('div', {class:'fg'}, h('label', {}, label), inp));
  });
  const roleSelect = h('select', {id:'newEmpRole'});
  roleSelect.appendChild(h('option', {value:'staff'}, 'Staff'));
  roleSelect.appendChild(h('option', {value:'admin'}, 'Admin'));
  card.appendChild(h('div', {class:'fg'}, h('label', {}, 'Role'), roleSelect));
  card.appendChild(h('button', {class:'btn btn-primary', style:'width:100%;', onclick: addUser}, icon('user-plus'), 'Create user'));
  left.appendChild(card);
  split.appendChild(left);

  // User list
  const right = h('div', {});
  const listCard = h('div', {class:'card'});
  listCard.appendChild(h('div', {class:'sec-title'}, 'All users'));
  const users = state.users || [];
  if (!users.length) {
    listCard.appendChild(h('div', {style:'color:var(--text3);font-size:12px;padding:8px 0;'}, 'No users found.'));
  }
  users.forEach(u => {
    const row = h('div', {class:'ui-row'});
    row.appendChild(h('div', {class:'u-av'}, getInitials(u.name)));
    const info = h('div', {class:'u-info'});
    info.appendChild(h('div', {class:'u-name'}, u.name));
    info.appendChild(h('div', {class:'u-id'}, u.emp_id + ' · ' + u.role));
    row.appendChild(info);
    if (u.emp_id !== 'ADMIN') {
      const del = h('button', {class:'btn btn-sm btn-danger', onclick: () => deleteUser(u.id, u.name)}, icon('trash'));
      row.appendChild(del);
    } else {
      row.appendChild(badge('Admin', 'badge-admin'));
    }
    listCard.appendChild(row);
  });
  right.appendChild(listCard);
  split.appendChild(right);
  return split;
}

async function addUser() {
  const id = (document.getElementById('newEmpId').value||'').trim().toUpperCase();
  const name = (document.getElementById('newEmpName').value||'').trim();
  const pw = document.getElementById('newEmpPw').value || '';
  const role = document.getElementById('newEmpRole').value;
  if (!id||!name||!pw) { toast('Fill all fields', 'error'); return; }
  try {
    await api('POST', '/users', {emp_id:id, name, password:pw, role});
    await loadUsers(); render();
    toast(`${name} created`);
  } catch(e) { toast(e.message, 'error'); }
}

async function deleteUser(uid, name) {
  try {
    await api('DELETE', `/users/${uid}`);
    await loadUsers(); render();
    toast(`${name} removed`);
  } catch(e) { toast(e.message, 'error'); }
}

function renderAdminReports() {
  const card = h('div', {class:'card'});
  card.appendChild(h('div', {class:'sec-title'}, 'Order report'));

  // Filters
  const filters = h('div', {class:'rpt-filters'});
  const dateIn = h('input', {type:'date', value:state.reportDate});
  dateIn.onchange = async () => { state.reportDate = dateIn.value; await loadReport(); render(); };
  const ctrSel = h('select');
  [['all','All counters'],['omelette','Omelette'],['dosa','Dosa']].forEach(([v,l]) => {
    const o = h('option', {value:v}, l); if(v===state.reportCounter) o.selected=true; ctrSel.appendChild(o);
  });
  ctrSel.onchange = async () => { state.reportCounter = ctrSel.value; await loadReport(); render(); };
  const stSel = h('select');
  [['all','All status'],['pending','Pending'],['completed','Completed']].forEach(([v,l]) => {
    const o = h('option', {value:v}, l); if(v===state.reportStatus) o.selected=true; stSel.appendChild(o);
  });
  stSel.onchange = async () => { state.reportStatus = stSel.value; await loadReport(); render(); };
  filters.appendChild(dateIn); filters.appendChild(ctrSel); filters.appendChild(stSel);
  card.appendChild(filters);

  // Table
  const wrap = h('div', {class:'rtbl-wrap'});
  const tbl = h('table', {class:'rtbl'});
  const thead = h('thead');
  const hrow = h('tr');
  ['Time','Counter','Variety','Table','Qty','Status','Delivery time','Notes','Staff'].forEach(t => hrow.appendChild(h('th', {}, t)));
  thead.appendChild(hrow); tbl.appendChild(thead);
  const tbody = h('tbody');
  const rows = state.reportData || [];
  if (!rows.length) {
    const tr = h('tr');
    tr.appendChild(h('td', {colspan:'9', style:'text-align:center;color:var(--text3);padding:1.5rem;'}, 'No orders match these filters.'));
    tbody.appendChild(tr);
  }
  rows.forEach(o => {
    const delivery = o.end_ms ? fmsDuration(o.end_ms - o.start_ms) : '—';
    const tr = h('tr');
    [
      o.order_time,
      o.counter.charAt(0).toUpperCase()+o.counter.slice(1),
      o.variety_name,
      o.table_no,
      String(o.quantity),
    ].forEach(t => tr.appendChild(h('td', {}, t)));
    const statusTd = h('td');
    statusTd.appendChild(badge(o.status==='pending'?'Pending':'Done', o.status==='pending'?'badge-pend':'badge-done'));
    tr.appendChild(statusTd);
    tr.appendChild(h('td', {style:'font-weight:500;'}, delivery));
    tr.appendChild(h('td', {style:'color:var(--text2);'}, o.notes||'—'));
    tr.appendChild(h('td', {style:'color:var(--text2);'}, o.placed_by||'—'));
    tbody.appendChild(tr);
  });
  tbl.appendChild(tbody); wrap.appendChild(tbl);
  card.appendChild(wrap);
  return card;
}

// ── Data loading ──────────────────────────────────────────────────────────
async function loadVarieties() {
  const data = await api('GET', '/varieties');
  state.varieties = {omelette: [], dosa: []};
  data.forEach(v => { if (state.varieties[v.counter]) state.varieties[v.counter].push(v); });
}

async function loadOrders() {
  const params = new URLSearchParams({date: today()});
  const data = await api('GET', '/orders?' + params);
  state.orders = data;
}

async function loadUsers() {
  state.users = await api('GET', '/users');
}

async function loadReport() {
  const params = new URLSearchParams({
    date: state.reportDate || today(),
    counter: state.reportCounter,
    status: state.reportStatus,
  });
  state.reportData = await api('GET', '/orders?' + params);
}

// Auto-refresh pending orders every 30s
setInterval(async () => {
  if (state.user && state.currentStatus === 'pending') {
    await loadOrders(); render();
  }
}, 30000);

render();
</script>
</body>
</html>"""

# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    print(f"\n{'='*50}")
    print(f"  Hotel Counter Manager")
    print(f"  Running at: http://localhost:{port}")
    print(f"  Default login: ADMIN / admin123")
    print(f"{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=debug)
