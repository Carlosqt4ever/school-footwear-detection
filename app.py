import csv
import functools
import io
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from detection import Detector

ROOT = Path(__file__).resolve().parent


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict', PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=1024 * 1024)
    if test_config:
        app.config.update(test_config)
    storage = Path(app.config.get('STORAGE', app.instance_path))
    storage.mkdir(parents=True, exist_ok=True)
    (storage / 'snapshots').mkdir(exist_ok=True)
    os.environ.setdefault('YOLO_CONFIG_DIR', str(storage / 'yolo'))
    key_file = storage / 'secret.key'
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))
    app.secret_key = key_file.read_text().strip()

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(storage / 'footwear.sqlite', timeout=15)
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close_db(error=None):
        connection = g.pop('db', None)
        if connection:
            connection.close()

    with app.app_context():
        db().executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS detections (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, label TEXT NOT NULL, confidence REAL NOT NULL, image TEXT, review TEXT, reviewer TEXT);
        ''')
        policy = json.loads((ROOT / 'footwear_model_package' / 'entry_policy.json').read_text())
        defaults = dict(camera_index=0, confidence_threshold=policy.get('confidence_threshold', 0.5), save_images=False, retention_days=7)
        db().execute('INSERT OR IGNORE INTO settings VALUES (1, ?)', (json.dumps(defaults),))
        db().commit()

    def get_settings():
        with app.app_context():
            return json.loads(db().execute('SELECT data FROM settings WHERE id=1').fetchone()[0])

    def prune_images():
        cutoff = (datetime.now() - timedelta(days=get_settings()['retention_days'])).isoformat(timespec='seconds')
        rows = db().execute('SELECT id, image FROM detections WHERE image IS NOT NULL AND created_at < ?', (cutoff,)).fetchall()
        for row in rows:
            (storage / 'snapshots' / row['image']).unlink(missing_ok=True)
            db().execute('UPDATE detections SET image=NULL WHERE id=?', (row['id'],))
        db().commit()

    def record(label, confidence, image):
        with app.app_context():
            name = None
            if image:
                name = secrets.token_hex(16) + '.jpg'
                (storage / 'snapshots' / name).write_bytes(image)
            db().execute('INSERT INTO detections(created_at,label,confidence,image) VALUES (?,?,?,?)', (datetime.now().isoformat(timespec='seconds'), label, confidence, name))
            db().commit()
            prune_images()

    detector = Detector(ROOT / 'footwear_model_package' / 'best.pt', get_settings, record)
    app.extensions['detector'] = detector
    attempts = {}
    attempt_lock = threading.Lock()

    @app.before_request
    def protect():
        session.setdefault('csrf', secrets.token_hex(24))
        g.user = db().execute('SELECT id,username,role FROM users WHERE id=?', (session.get('user_id'),)).fetchone()
        if request.method == 'POST' and not secrets.compare_digest(str(request.form.get('csrf', request.headers.get('X-CSRF-Token', ''))), session['csrf']):
            abort(400, 'Invalid form token. Reload the page and try again.')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        return response

    def login_required(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not g.user:
                if request.path.startswith('/api/'):
                    return jsonify(error='Please sign in again.'), 401
                return redirect(url_for('login'))
            return view(*args, **kwargs)
        return wrapped

    def admin_required(view):
        @login_required
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if g.user['role'] != 'admin':
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    @app.route('/setup', methods=['GET', 'POST'])
    def setup():
        if db().execute('SELECT 1 FROM users LIMIT 1').fetchone():
            return redirect(url_for('login'))
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            if len(username) < 3 or len(username) > 50 or len(password) < 10:
                flash('Use a username of 3–50 characters and a password of at least 10 characters.', 'error')
            else:
                # Serialize first-run setup so two requests cannot create administrators.
                db().execute('BEGIN IMMEDIATE')
                if db().execute('SELECT 1 FROM users LIMIT 1').fetchone():
                    db().rollback()
                    return redirect(url_for('login'))
                db().execute('INSERT INTO users(username,password,role) VALUES (?,?,?)', (username, generate_password_hash(password), 'admin'))
                db().commit()
                flash('Administrator created. Sign in to begin.', 'success')
                return redirect(url_for('login'))
        return render_template('auth.html', setup=True)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if not db().execute('SELECT 1 FROM users LIMIT 1').fetchone():
            return redirect(url_for('setup'))
        if g.user:
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            address = request.remote_addr
            now = time.monotonic()
            with attempt_lock:
                recent = [t for t in attempts.get(address, []) if now - t < 300]
                attempts[address] = recent
                if len(recent) >= 8:
                    flash('Too many attempts. Try again in five minutes.', 'error')
                    return render_template('auth.html', setup=False), 429
                recent.append(now)
            user = db().execute('SELECT * FROM users WHERE username=?', (request.form.get('username', '').strip(),)).fetchone()
            if user and check_password_hash(user['password'], request.form.get('password', '')):
                with attempt_lock:
                    attempts.pop(address, None)
                session.clear()
                session.update(user_id=user['id'], csrf=secrets.token_hex(24))
                session.permanent = True
                return redirect(url_for('dashboard'))
            flash('Incorrect username or password.', 'error')
        return render_template('auth.html', setup=False)

    @app.post('/logout')
    @login_required
    def logout():
        detector.stop()
        session.clear()
        return redirect(url_for('login'))

    def stats():
        today = datetime.now().date().isoformat()
        counts = {'valid': 0, 'not_valid': 0, 'pending': 0}
        for row in db().execute('SELECT label, COUNT(*) count FROM detections WHERE created_at>=? GROUP BY label', (today,)):
            counts[row['label']] = row['count']
        counts['pending'] = db().execute('SELECT COUNT(*) FROM detections WHERE created_at>=? AND review IS NULL', (today,)).fetchone()[0]
        return counts

    @app.get('/')
    @login_required
    def dashboard():
        prune_images()
        rows = db().execute('SELECT * FROM detections ORDER BY id DESC LIMIT 6').fetchall()
        return render_template('dashboard.html', counts=stats(), rows=rows)

    @app.get('/live')
    @login_required
    def live():
        return render_template('live.html')

    @app.post('/api/camera/<action>')
    @login_required
    def camera(action):
        if action == 'start':
            detector.start()
        elif action == 'stop':
            detector.stop()
        else:
            abort(404)
        return jsonify(detector.status())

    @app.get('/api/status')
    @login_required
    def status():
        return jsonify(detector.status())

    @app.get('/video')
    @login_required
    def video():
        return Response(detector.stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

    def filtered_records():
        clauses, params = [], []
        label = request.args.get('label', '')
        if label in ('valid', 'not_valid'):
            clauses.append('label=?')
            params.append(label)
        date = request.args.get('date', '')
        if date:
            try:
                datetime.strptime(date, '%Y-%m-%d')
            except ValueError:
                abort(400, 'Invalid date')
            clauses.append('substr(created_at,1,10)=?')
            params.append(date)
        return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), params

    @app.get('/history')
    @login_required
    def history():
        prune_images()
        where, params = filtered_records()
        page = max(1, request.args.get('page', 1, type=int))
        total = db().execute('SELECT COUNT(*) FROM detections' + where, params).fetchone()[0]
        rows = db().execute('SELECT * FROM detections' + where + ' ORDER BY id DESC LIMIT 25 OFFSET ?', params + [(page - 1) * 25]).fetchall()
        return render_template('history.html', rows=rows, page=page, total=total)

    @app.get('/history/export')
    @login_required
    def export():
        where, params = filtered_records()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'Local date/time', 'Model result', 'Confidence', 'Staff review', 'Reviewer'])
        for row in db().execute('SELECT * FROM detections' + where + ' ORDER BY id DESC', params):
            # Prefix spreadsheet formulas in user-controlled reviewer names.
            reviewer = row['reviewer'] or ''
            if reviewer.startswith(('=', '+', '-', '@', '\t', '\r')):
                reviewer = "'" + reviewer
            writer.writerow([row['id'], row['created_at'], row['label'], f"{row['confidence']:.1%}", row['review'] or 'Pending', reviewer])
        return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=footwear-history.csv'})

    @app.post('/history/<int:record_id>/review')
    @login_required
    def review(record_id):
        decision = request.form.get('decision')
        if decision not in ('valid', 'not_valid', 'dismissed'):
            abort(400)
        changed = db().execute('UPDATE detections SET review=?, reviewer=? WHERE id=?', (decision, g.user['username'], record_id))
        db().commit()
        if not changed.rowcount:
            abort(404)
        flash('Staff review saved.', 'success')
        return redirect(url_for('history'))

    @app.get('/snapshots/<name>')
    @login_required
    def snapshot(name):
        prune_images()
        if not db().execute('SELECT 1 FROM detections WHERE image=?', (name,)).fetchone():
            abort(404)
        return send_from_directory(storage / 'snapshots', name)

    @app.route('/settings', methods=['GET', 'POST'])
    @admin_required
    def settings():
        config = get_settings()
        if request.method == 'POST':
            try:
                index = int(request.form.get('camera_index', ''))
                threshold = float(request.form.get('confidence_threshold', '')) / 100
                retention = int(request.form.get('retention_days', ''))
                if not (0 <= index <= 10 and 0.1 <= threshold <= 0.99 and 1 <= retention <= 90):
                    raise ValueError()
                config = dict(camera_index=index, confidence_threshold=threshold, save_images=request.form.get('save_images') == 'on', retention_days=retention)
                db().execute('UPDATE settings SET data=? WHERE id=1', (json.dumps(config),))
                db().commit()
                prune_images()
                flash('Settings saved. Restart the camera to apply detection changes.', 'success')
                return redirect(url_for('settings'))
            except (TypeError, ValueError):
                flash('Enter a camera from 0–10, confidence from 10–99%, and retention from 1–90 days.', 'error')
        return render_template('settings.html', config=config)

    @app.route('/users', methods=['GET', 'POST'])
    @admin_required
    def users():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            role = request.form.get('role', '')
            if not 3 <= len(username) <= 50 or len(password) < 10 or role not in ('staff', 'admin'):
                flash('Use a username of 3–50 characters, a password of at least 10 characters, and a valid role.', 'error')
            else:
                try:
                    db().execute('INSERT INTO users(username,password,role) VALUES (?,?,?)', (username, generate_password_hash(password), role))
                    db().commit()
                    flash('Account created.', 'success')
                    return redirect(url_for('users'))
                except sqlite3.IntegrityError:
                    db().rollback()
                    flash('That username is already taken.', 'error')
        return render_template('users.html', users=db().execute('SELECT id,username,role FROM users ORDER BY id').fetchall())

    @app.route('/account', methods=['GET', 'POST'])
    @login_required
    def account():
        if request.method == 'POST':
            user = db().execute('SELECT * FROM users WHERE id=?', (g.user['id'],)).fetchone()
            password = request.form.get('password', '')
            if not check_password_hash(user['password'], request.form.get('current_password', '')):
                flash('Current password is incorrect.', 'error')
            elif len(password) < 10:
                flash('Use at least 10 characters for the new password.', 'error')
            else:
                db().execute('UPDATE users SET password=? WHERE id=?', (generate_password_hash(password), user['id']))
                db().commit()
                flash('Password updated.', 'success')
                return redirect(url_for('account'))
        return render_template('account.html')

    return app


if __name__ == '__main__':
    from waitress import serve
    print('Footwear Check is running at http://127.0.0.1:5000')
    serve(create_app(), host='127.0.0.1', port=5000, threads=8)
