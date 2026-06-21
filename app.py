from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import sqlite3
from datetime import date, datetime, timedelta
from werkzeug.utils import secure_filename

app = Flask(__name__)
DB_NAME = "farm_data.db"
MATING_REVIEW_DAYS = 20
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'sheep')
ALLOWED_PHOTO_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

def ensure_upload_folder():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_photo(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_PHOTO_EXTENSIONS

def delete_sheep_photo_file(filename):
    if not filename:
        return
    path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.isfile(path):
        os.remove(path)

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """Initializes the database with the expanded farm metrics spec sheet."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sheep (
            tag_id TEXT PRIMARY KEY,
            name TEXT,
            breed TEXT,
            sheep_type TEXT,
            birth_date TEXT,
            purchase_date TEXT,
            purchase_price REAL,
            purchase_location TEXT,
            breeding_date TEXT,
            breeding_type TEXT,
            notes TEXT,
            mother_status TEXT,
            mother_id TEXT,
            life_status TEXT,
            pregnancy_status TEXT
        )
    ''')
    cursor.execute("PRAGMA table_info(sheep)")
    columns = {row[1] for row in cursor.fetchall()}
    if 'sheep_type' not in columns:
        cursor.execute("ALTER TABLE sheep ADD COLUMN sheep_type TEXT")
    if 'mother_status' not in columns:
        cursor.execute("ALTER TABLE sheep ADD COLUMN mother_status TEXT")
    if 'mother_id' not in columns:
        cursor.execute("ALTER TABLE sheep ADD COLUMN mother_id TEXT")
    if 'life_status' not in columns:
        cursor.execute("ALTER TABLE sheep ADD COLUMN life_status TEXT")
    if 'pregnancy_status' not in columns:
        cursor.execute("ALTER TABLE sheep ADD COLUMN pregnancy_status TEXT")
    if 'photo_filename' not in columns:
        cursor.execute("ALTER TABLE sheep ADD COLUMN photo_filename TEXT")
    ensure_upload_folder()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sheep_tag_id TEXT NOT NULL,
            mated_date TEXT NOT NULL,
            notes TEXT,
            mating_type TEXT,
            result TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (sheep_tag_id) REFERENCES sheep(tag_id) ON DELETE CASCADE
        )
    ''')
    cursor.execute("PRAGMA table_info(matings)")
    mating_columns = {row[1] for row in cursor.fetchall()}
    if 'mating_type' not in mating_columns:
        cursor.execute("ALTER TABLE matings ADD COLUMN mating_type TEXT")
    cursor.execute(
        "UPDATE matings SET mating_type = 'natural' WHERE mating_type IS NULL OR mating_type = ''"
    )
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ultrasounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mating_id INTEGER NOT NULL,
            scan_date TEXT NOT NULL,
            result TEXT NOT NULL,
            fetus_count INTEGER,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (mating_id) REFERENCES matings(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute("PRAGMA table_info(ultrasounds)")
    ultrasound_columns = {row[1] for row in cursor.fetchall()}
    if 'fetus_count' not in ultrasound_columns:
        cursor.execute("ALTER TABLE ultrasounds ADD COLUMN fetus_count INTEGER")
    cursor.execute("UPDATE sheep SET mother_status = 'أم' WHERE mother_status = 'آم'")
    cursor.execute("UPDATE sheep SET mother_status = 'ليس أم بعد' WHERE mother_status IN ('ليس ام بعد', 'غير محدد') OR mother_status IS NULL OR mother_status = ''")
    cursor.execute("UPDATE sheep SET life_status = 'حي' WHERE life_status IS NULL OR life_status = ''")
    sync_all_mother_statuses(cursor)
    sync_all_pregnancy_statuses(cursor)
    cursor.execute(
        "DELETE FROM matings WHERE sheep_tag_id NOT IN (SELECT tag_id FROM sheep)"
    )
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS budget_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_date TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def now_iso():
    return datetime.now().isoformat(timespec='seconds')

def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        return None

def parse_fetus_count(value):
    if value is None or value == '':
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 'invalid'
    if count < 0:
        return 'invalid'
    return count

def mating_needs_review(mated_date, completed_at, ultrasounds):
    if completed_at or ultrasounds:
        return False
    mated = parse_date(mated_date)
    if not mated:
        return False
    return (date.today() - mated).days >= MATING_REVIEW_DAYS

def sheep_row_to_dict(row, matings=None):
    return {
        "tag_id": row[0],
        "name": row[1],
        "breed": row[2],
        "sheep_type": row[3],
        "birth_date": row[4],
        "purchase_date": row[5],
        "purchase_price": row[6],
        "purchase_location": row[7],
        "notes": row[8],
        "mother_status": row[9],
        "mother_id": row[10],
        "life_status": row[11],
        "child_count": row[12],
        "pregnancy_status": row[13] or 'غير حامل',
        "photo_url": f"/uploads/sheep/{row[14]}" if row[14] else None,
        "matings": matings or [],
        "needs_mating_review": any(
            mating_needs_review(m.get('mated_date'), m.get('completed_at'), m.get('ultrasounds'))
            for m in (matings or [])
        ),
    }

SHEEP_SELECT = """
    SELECT tag_id, name, breed, sheep_type, birth_date, purchase_date,
           purchase_price, purchase_location, notes,
           mother_status, mother_id, life_status,
           (SELECT COUNT(*) FROM sheep c WHERE c.mother_id = sheep.tag_id) AS child_count,
           pregnancy_status, photo_filename
    FROM sheep
"""

def delete_reproduction_for_sheep(cursor, tag_id):
    """Remove mating/ultrasound history for a sheep."""
    if not tag_id:
        return
    cursor.execute("DELETE FROM matings WHERE sheep_tag_id = ?", (tag_id,))

def sync_mother_status(cursor, tag_id):
    """Set mother_status from whether this sheep actually has children."""
    if not tag_id:
        return
    cursor.execute(
        "SELECT COUNT(*) FROM sheep WHERE mother_id = ?",
        (tag_id,),
    )
    count = cursor.fetchone()[0]
    cursor.execute(
        "UPDATE sheep SET mother_status = ? WHERE tag_id = ?",
        ("أم" if count > 0 else "ليس أم بعد", tag_id),
    )

def sync_all_mother_statuses(cursor):
    cursor.execute("SELECT tag_id FROM sheep")
    for (tag_id,) in cursor.fetchall():
        sync_mother_status(cursor, tag_id)

def get_latest_mating_id(cursor, sheep_tag_id):
    cursor.execute('''
        SELECT id FROM matings
        WHERE sheep_tag_id = ?
        ORDER BY mated_date DESC, id DESC
        LIMIT 1
    ''', (sheep_tag_id,))
    row = cursor.fetchone()
    return row[0] if row else None

def validate_latest_active_mating(cursor, mating_id):
    row = get_mating_or_404(cursor, mating_id)
    if not row:
        return None, "سجل التلقيح غير موجود."
    if row[5] is not None:
        return None, "تم إغلاق دورة هذا التلقيح."
    if get_latest_mating_id(cursor, row[1]) != mating_id:
        return None, "يمكن التعديل على آخر سجل تلقيح فقط."
    return row, None

def compute_pregnancy_status(cursor, tag_id):
    cursor.execute(
        "SELECT sheep_type, life_status FROM sheep WHERE tag_id = ?",
        (tag_id,),
    )
    row = cursor.fetchone()
    if not row or row[0] != 'حملة' or row[1] == 'ميت':
        return 'غير حامل'

    cursor.execute('''
        SELECT m.id, m.completed_at
        FROM matings m
        WHERE m.sheep_tag_id = ?
        ORDER BY m.mated_date DESC, m.id DESC
        LIMIT 1
    ''', (tag_id,))
    mating = cursor.fetchone()
    if not mating or mating[1] is not None:
        return 'غير حامل'

    mating_id = mating[0]

    cursor.execute('''
        SELECT result FROM ultrasounds
        WHERE mating_id = ?
        ORDER BY scan_date DESC, id DESC
        LIMIT 1
    ''', (mating_id,))
    latest_ultrasound = cursor.fetchone()
    if not latest_ultrasound:
        return 'غير حامل'

    if latest_ultrasound[0] == 'pass':
        return 'حامل'

    return 'غير حامل'

def sync_pregnancy_status(cursor, tag_id):
    if not tag_id:
        return
    status = compute_pregnancy_status(cursor, tag_id)
    cursor.execute(
        "UPDATE sheep SET pregnancy_status = ? WHERE tag_id = ?",
        (status, tag_id),
    )

def sync_all_pregnancy_statuses(cursor):
    cursor.execute("SELECT tag_id FROM sheep")
    for (tag_id,) in cursor.fetchall():
        sync_pregnancy_status(cursor, tag_id)

def close_active_pregnancy(cursor, tag_id, completed_at=None):
    """Mark the active pregnant mating cycle as completed (e.g. after lambing)."""
    if not tag_id:
        return
    completed_at = completed_at or now_iso()
    cursor.execute('''
        SELECT m.id FROM matings m
        WHERE m.sheep_tag_id = ? AND m.completed_at IS NULL
          AND EXISTS (
              SELECT 1 FROM ultrasounds u
              WHERE u.mating_id = m.id AND u.result = 'pass'
          )
        ORDER BY m.mated_date DESC
        LIMIT 1
    ''', (tag_id,))
    row = cursor.fetchone()
    if row:
        cursor.execute(
            "UPDATE matings SET completed_at = ? WHERE id = ?",
            (completed_at, row[0]),
        )
    sync_pregnancy_status(cursor, tag_id)

def fetch_matings_for_sheep(cursor, tag_id):
    cursor.execute('''
        SELECT id, mated_date, notes, mating_type, completed_at, created_at
        FROM matings
        WHERE sheep_tag_id = ?
        ORDER BY mated_date DESC, id DESC
    ''', (tag_id,))
    matings = []
    for row in cursor.fetchall():
        mating_id = row[0]
        cursor.execute('''
            SELECT id, scan_date, result, fetus_count, notes, created_at
            FROM ultrasounds
            WHERE mating_id = ?
            ORDER BY scan_date DESC, id DESC
        ''', (mating_id,))
        ultrasounds = [
            {
                "id": u[0],
                "scan_date": u[1],
                "result": u[2],
                "fetus_count": u[3],
                "notes": u[4] or '',
                "created_at": u[5],
            }
            for u in cursor.fetchall()
        ]
        matings.append({
            "id": mating_id,
            "mated_date": row[1],
            "notes": row[2] or '',
            "mating_type": row[3] or 'natural',
            "completed_at": row[4],
            "created_at": row[5],
            "ultrasounds": ultrasounds,
        })
    return matings

def get_sheep_or_404(cursor, tag_id):
    cursor.execute(SHEEP_SELECT + " WHERE tag_id = ?", (tag_id,))
    row = cursor.fetchone()
    return row

def validate_ewe_for_mating(cursor, tag_id):
    cursor.execute(
        "SELECT sheep_type, life_status FROM sheep WHERE tag_id = ?",
        (tag_id,),
    )
    row = cursor.fetchone()
    if not row:
        return False, f"رقم الخروف '{tag_id}' غير موجود في السجل."
    if row[0] != 'حملة':
        return False, "التلقيح متاح للحملات فقط."
    if row[1] == 'ميت':
        return False, "لا يمكن تسجيل تلقيح لخروف ميت."
    return True, None

def get_mating_or_404(cursor, mating_id):
    cursor.execute(
        "SELECT id, sheep_tag_id, mated_date, notes, mating_type, completed_at FROM matings WHERE id = ?",
        (mating_id,),
    )
    return cursor.fetchone()

def normalize_birth_date(birth_date):
    birth_date = (birth_date or '').strip()
    return birth_date or date.today().isoformat()

@app.route('/')
def index():
    """Renders the sheep logger dashboard."""
    return render_template('index.html')

@app.route('/budget')
def budget():
    """Renders the farm budget tracker."""
    return render_template('budget.html')

@app.route('/api/breeds', methods=['GET'])
def get_breeds():
    """Fetches unique breeds currently in the database to populate the auto-suggest list."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT breed FROM sheep WHERE breed IS NOT NULL AND breed != ''")
    breeds = [row[0] for row in cursor.fetchall()]
    conn.close()
    return jsonify(breeds)

@app.route('/api/sheep', methods=['GET'])
def get_sheep():
    """Fetches all sheep records with reproduction history."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(SHEEP_SELECT)
    rows = cursor.fetchall()
    result = []
    for row in rows:
        matings = fetch_matings_for_sheep(cursor, row[0])
        result.append(sheep_row_to_dict(row, matings))
    conn.close()
    return jsonify(result)

@app.route('/api/sheep', methods=['POST'])
def add_sheep():
    """Saves a new sheep record with comprehensive metrics."""
    data = request.json
    tag_id = data.get('tag_id', '').strip()
    name = data.get('name', '').strip()
    breed = data.get('breed', '').strip()
    sheep_type = data.get('sheep_type', '').strip()
    birth_date = normalize_birth_date(data.get('birth_date', ''))
    purchase_date = data.get('purchase_date', '').strip()
    purchase_price_str = data.get('purchase_price', '').strip()
    purchase_location = data.get('purchase_location', '').strip()
    notes = data.get('notes', '').strip()
    mother_id = data.get('mother_id', '').strip()
    life_status = data.get('life_status', '').strip() or 'حي'

    if not tag_id:
        return jsonify({"success": False, "message": "الرجاء إدخال رقم الخروف الحتمي."}), 400

    if mother_id == tag_id:
        return jsonify({"success": False, "message": "لا يمكن أن يكون الخروف أمّاً لنفسه."}), 400

    purchase_price = None
    if purchase_price_str:
        try:
            purchase_price = float(purchase_price_str)
        except ValueError:
            return jsonify({"success": False, "message": "يجب أن يكون سعر الشراء رقماً صحيحاً."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()

        if mother_id:
            cursor.execute("SELECT tag_id FROM sheep WHERE tag_id = ?", (mother_id,))
            if not cursor.fetchone():
                conn.close()
                return jsonify({"success": False, "message": f"رقم الأم '{mother_id}' غير موجود في السجل."}), 400

        cursor.execute('''
            INSERT INTO sheep (tag_id, name, breed, sheep_type, birth_date, purchase_date, purchase_price, purchase_location, notes, mother_status, mother_id, life_status, pregnancy_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (tag_id, name, breed, sheep_type, birth_date, purchase_date, purchase_price, purchase_location, notes, 'ليس أم بعد', mother_id or None, life_status, 'غير حامل'))
        sync_mother_status(cursor, tag_id)
        sync_mother_status(cursor, mother_id)
        if mother_id:
            close_active_pregnancy(cursor, mother_id)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حفظ بيانات الخروف بنجاح!"})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "message": f"خطأ: رقم الخروف '{tag_id}' مسجل مسبقاً."}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/sheep/<tag_id>', methods=['PUT'])
def update_sheep(tag_id):
    """Updates an existing sheep record matching the tag_id."""
    data = request.json
    name = data.get('name', '').strip()
    breed = data.get('breed', '').strip()
    sheep_type = data.get('sheep_type', '').strip()
    birth_date = normalize_birth_date(data.get('birth_date', ''))
    purchase_date = data.get('purchase_date', '').strip()
    purchase_price_str = data.get('purchase_price', '').strip()
    purchase_location = data.get('purchase_location', '').strip()
    notes = data.get('notes', '').strip()
    mother_id = data.get('mother_id', '').strip()
    life_status = data.get('life_status', '').strip() or 'حي'

    if mother_id == tag_id:
        return jsonify({"success": False, "message": "لا يمكن أن يكون الخروف أمّاً لنفسه."}), 400

    purchase_price = None
    if purchase_price_str:
        try:
            purchase_price = float(purchase_price_str)
        except ValueError:
            return jsonify({"success": False, "message": "يجب أن يكون سعر الشراء رقماً صحيحاً."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()

        if mother_id:
            cursor.execute("SELECT tag_id FROM sheep WHERE tag_id = ?", (mother_id,))
            if not cursor.fetchone():
                conn.close()
                return jsonify({"success": False, "message": f"رقم الأم '{mother_id}' غير موجود في السجل."}), 400

        cursor.execute("SELECT mother_id FROM sheep WHERE tag_id = ?", (tag_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": f"رقم الخروف '{tag_id}' غير موجود في السجل."}), 404
        old_mother_id = row[0]
        new_mother_id = mother_id or None
        mother_assigned = new_mother_id and new_mother_id != old_mother_id

        cursor.execute('''
            UPDATE sheep
            SET name=?, breed=?, sheep_type=?, birth_date=?, purchase_date=?, purchase_price=?, purchase_location=?, notes=?, mother_id=?, life_status=?
            WHERE tag_id=?
        ''', (name, breed, sheep_type, birth_date, purchase_date, purchase_price, purchase_location, notes, new_mother_id, life_status, tag_id))
        sync_mother_status(cursor, tag_id)
        sync_mother_status(cursor, new_mother_id)
        if old_mother_id and old_mother_id != new_mother_id:
            sync_mother_status(cursor, old_mother_id)
        if mother_assigned:
            close_active_pregnancy(cursor, new_mother_id)
        sync_pregnancy_status(cursor, tag_id)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم تعديل بيانات الخروف بنجاح!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/uploads/sheep/<path:filename>')
def serve_sheep_photo(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/sheep/<tag_id>/photo', methods=['POST'])
def upload_sheep_photo(tag_id):
    """Upload or replace a sheep photo."""
    file = request.files.get('photo')
    if not file or not file.filename:
        return jsonify({"success": False, "message": "الرجاء اختيار صورة."}), 400
    if not allowed_photo(file.filename):
        return jsonify({"success": False, "message": "نوع الملف غير مدعوم. استخدم JPG أو PNG أو WEBP."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT photo_filename FROM sheep WHERE tag_id = ?", (tag_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": f"رقم الخروف '{tag_id}' غير موجود في السجل."}), 404

        ext = file.filename.rsplit('.', 1)[1].lower()
        safe_tag = secure_filename(tag_id) or 'sheep'
        filename = f"{safe_tag}.{ext}"
        delete_sheep_photo_file(row[0])
        ensure_upload_folder()
        file.save(os.path.join(UPLOAD_FOLDER, filename))
        cursor.execute("UPDATE sheep SET photo_filename = ? WHERE tag_id = ?", (filename, tag_id))
        conn.commit()
        conn.close()
        return jsonify({
            "success": True,
            "message": "تم رفع الصورة بنجاح!",
            "photo_url": f"/uploads/sheep/{filename}",
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/sheep/<tag_id>/photo', methods=['DELETE'])
def delete_sheep_photo(tag_id):
    """Remove a sheep photo."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT photo_filename FROM sheep WHERE tag_id = ?", (tag_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": f"رقم الخروف '{tag_id}' غير موجود في السجل."}), 404
        delete_sheep_photo_file(row[0])
        cursor.execute("UPDATE sheep SET photo_filename = NULL WHERE tag_id = ?", (tag_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف الصورة."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/sheep/<tag_id>', methods=['DELETE'])
def delete_sheep(tag_id):
    """Deletes a sheep record matching the tag_id."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT mother_id, photo_filename FROM sheep WHERE tag_id = ?", (tag_id,))
        row = cursor.fetchone()
        mother_id = row[0] if row else None
        if row and row[1]:
            delete_sheep_photo_file(row[1])
        delete_reproduction_for_sheep(cursor, tag_id)
        cursor.execute("DELETE FROM sheep WHERE tag_id=?", (tag_id,))
        sync_mother_status(cursor, mother_id)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف رأس الماشية بنجاح!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/sheep/<tag_id>/matings', methods=['POST'])
def add_mating(tag_id):
    data = request.json or {}
    mated_date = (data.get('mated_date') or '').strip()
    notes = (data.get('notes') or '').strip()
    mating_type = (data.get('mating_type') or '').strip()

    if not mated_date:
        return jsonify({"success": False, "message": "الرجاء إدخال تاريخ التلقيح."}), 400
    if not parse_date(mated_date):
        return jsonify({"success": False, "message": "تاريخ التلقيح غير صالح."}), 400
    if mating_type not in ('natural', 'hormone'):
        return jsonify({"success": False, "message": "الرجاء اختيار نوع التلقيح (طبيعي أو هرمون)."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        ok, message = validate_ewe_for_mating(cursor, tag_id)
        if not ok:
            conn.close()
            return jsonify({"success": False, "message": message}), 400

        cursor.execute('''
            UPDATE matings SET completed_at = ?
            WHERE sheep_tag_id = ? AND completed_at IS NULL
        ''', (now_iso(), tag_id))

        cursor.execute('''
            INSERT INTO matings (sheep_tag_id, mated_date, notes, mating_type, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (tag_id, mated_date, notes, mating_type, now_iso()))
        sync_pregnancy_status(cursor, tag_id)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم تسجيل التلقيح بنجاح!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/matings/<int:mating_id>', methods=['PATCH'])
def update_mating(mating_id):
    data = request.json or {}
    notes = data.get('notes')

    if notes is None:
        return jsonify({"success": False, "message": "لا توجد بيانات للتحديث."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        row = get_mating_or_404(cursor, mating_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل التلقيح غير موجود."}), 404

        cursor.execute(
            "UPDATE matings SET notes = ? WHERE id = ?",
            ((notes or '').strip(), mating_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حفظ الملاحظة."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/matings/<int:mating_id>', methods=['DELETE'])
def delete_mating(mating_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        row = get_mating_or_404(cursor, mating_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل التلقيح غير موجود."}), 404
        sheep_tag_id = row[1]
        cursor.execute("DELETE FROM matings WHERE id = ?", (mating_id,))
        sync_pregnancy_status(cursor, sheep_tag_id)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف سجل التلقيح."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/matings/<int:mating_id>/complete', methods=['POST'])
def complete_mating(mating_id):
    """Manually close a pregnancy cycle."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        row, error = validate_latest_active_mating(cursor, mating_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": error}), 404
        if error:
            conn.close()
            return jsonify({"success": False, "message": error}), 400
        cursor.execute(
            "SELECT 1 FROM ultrasounds WHERE mating_id = ? AND result = 'pass' LIMIT 1",
            (mating_id,),
        )
        if not cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "message": "يمكن إغلاق دورة حمل ناجحة فقط."}), 400
        completed_at = now_iso()
        cursor.execute(
            "UPDATE matings SET completed_at = ? WHERE id = ?",
            (completed_at, mating_id),
        )
        cursor.execute('''
            UPDATE matings SET completed_at = ?
            WHERE sheep_tag_id = ? AND completed_at IS NULL AND id != ?
        ''', (completed_at, row[1], mating_id))
        sync_pregnancy_status(cursor, row[1])
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم إغلاق دورة الحمل."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/matings/<int:mating_id>/ultrasounds', methods=['POST'])
def add_ultrasound(mating_id):
    data = request.json or {}
    scan_date = (data.get('scan_date') or '').strip()
    result = (data.get('result') or '').strip()
    notes = (data.get('notes') or '').strip()
    fetus_count = parse_fetus_count(data.get('fetus_count'))

    if not scan_date:
        return jsonify({"success": False, "message": "الرجاء إدخال تاريخ السونار."}), 400
    if not parse_date(scan_date):
        return jsonify({"success": False, "message": "تاريخ السونار غير صالح."}), 400
    if result not in ('pass', 'fail'):
        return jsonify({"success": False, "message": "النتيجة يجب أن تكون pass أو fail."}), 400
    if fetus_count == 'invalid':
        return jsonify({"success": False, "message": "عدد الأجنة يجب أن يكون رقماً صحيحاً (٠ أو أكثر)."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        row, error = validate_latest_active_mating(cursor, mating_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": error}), 404
        if error:
            conn.close()
            return jsonify({"success": False, "message": error}), 400

        cursor.execute('''
            INSERT INTO ultrasounds (mating_id, scan_date, result, fetus_count, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (mating_id, scan_date, result, fetus_count, notes, now_iso()))
        sync_pregnancy_status(cursor, row[1])
        conn.commit()
        conn.close()
        label = 'ناجح' if result == 'pass' else 'فاشل'
        return jsonify({"success": True, "message": f"تم تسجيل السونار كـ {label}."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/ultrasounds/<int:ultrasound_id>', methods=['PATCH'])
def update_ultrasound(ultrasound_id):
    data = request.json or {}
    scan_date = data.get('scan_date')
    result = data.get('result')
    notes = data.get('notes')
    fetus_count_raw = data.get('fetus_count')

    if result is not None and result not in ('pass', 'fail'):
        return jsonify({"success": False, "message": "النتيجة يجب أن تكون pass أو fail."}), 400
    if scan_date is not None and not parse_date(scan_date):
        return jsonify({"success": False, "message": "تاريخ السونار غير صالح."}), 400
    if fetus_count_raw is not None:
        fetus_count = parse_fetus_count(fetus_count_raw)
        if fetus_count == 'invalid':
            return jsonify({"success": False, "message": "عدد الأجنة يجب أن يكون رقماً صحيحاً (٠ أو أكثر)."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.id, u.mating_id, m.sheep_tag_id
            FROM ultrasounds u
            JOIN matings m ON m.id = u.mating_id
            WHERE u.id = ?
        ''', (ultrasound_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل السونار غير موجود."}), 404

        updates = []
        values = []
        if scan_date is not None:
            updates.append("scan_date = ?")
            values.append(scan_date.strip())
        if result is not None:
            updates.append("result = ?")
            values.append(result.strip())
        if notes is not None:
            updates.append("notes = ?")
            values.append(notes.strip())
        if fetus_count_raw is not None:
            updates.append("fetus_count = ?")
            values.append(parse_fetus_count(fetus_count_raw))

        if not updates:
            conn.close()
            return jsonify({"success": False, "message": "لا توجد بيانات للتحديث."}), 400

        values.append(ultrasound_id)
        cursor.execute(
            f"UPDATE ultrasounds SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        sync_pregnancy_status(cursor, row[2])
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم تحديث سجل السونار."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/ultrasounds/<int:ultrasound_id>', methods=['DELETE'])
def delete_ultrasound(ultrasound_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.id, m.sheep_tag_id
            FROM ultrasounds u
            JOIN matings m ON m.id = u.mating_id
            WHERE u.id = ?
        ''', (ultrasound_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل السونار غير موجود."}), 404
        cursor.execute("DELETE FROM ultrasounds WHERE id = ?", (ultrasound_id,))
        sync_pregnancy_status(cursor, row[1])
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف سجل السونار."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

def parse_amount(value):
    if value is None or value == '':
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 'invalid'
    if amount == 0:
        return 'invalid'
    return amount

def budget_row_to_dict(row, running_balance=None):
    return {
        "id": row[0],
        "transaction_date": row[1],
        "amount": row[2],
        "description": row[3] or '',
        "created_at": row[4],
        "running_balance": running_balance,
    }

def fetch_budget_summary(cursor):
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM budget_transactions"
    )
    balance = round(cursor.fetchone()[0], 2)

    month_start = date.today().replace(day=1).isoformat()
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM budget_transactions WHERE amount > 0 AND transaction_date >= ?",
        (month_start,),
    )
    income_month = round(cursor.fetchone()[0], 2)
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM budget_transactions WHERE amount < 0 AND transaction_date >= ?",
        (month_start,),
    )
    expenses_month = round(abs(cursor.fetchone()[0]), 2)

    return {
        "balance": balance,
        "income_month": income_month,
        "expenses_month": expenses_month,
    }

@app.route('/api/budget/transactions', methods=['GET'])
def get_budget_transactions():
    conn = get_connection()
    cursor = conn.cursor()
    summary = fetch_budget_summary(cursor)
    cursor.execute(
        "SELECT id, transaction_date, amount, description, created_at "
        "FROM budget_transactions ORDER BY transaction_date ASC, id ASC"
    )
    rows = cursor.fetchall()
    running = 0.0
    with_balance = []
    for row in rows:
        running = round(running + row[2], 2)
        with_balance.append(budget_row_to_dict(row, running))
    with_balance.reverse()
    conn.close()
    return jsonify({"summary": summary, "transactions": with_balance})

@app.route('/api/budget/transactions', methods=['POST'])
def add_budget_transaction():
    data = request.json or {}
    transaction_date = (data.get('transaction_date') or '').strip()
    description = (data.get('description') or '').strip()
    amount = parse_amount(data.get('amount'))

    if not transaction_date:
        transaction_date = date.today().isoformat()
    elif not parse_date(transaction_date):
        return jsonify({"success": False, "message": "تاريخ غير صالح."}), 400
    if amount == 'invalid':
        return jsonify({"success": False, "message": "المبلغ يجب أن يكون رقماً غير صفري."}), 400
    if not description:
        return jsonify({"success": False, "message": "الوصف مطلوب."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO budget_transactions (transaction_date, amount, description, created_at) "
            "VALUES (?, ?, ?, ?)",
            (transaction_date, round(amount, 2), description, now_iso()),
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return jsonify({"success": True, "message": "تم تسجيل الحركة.", "id": new_id})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/budget/transactions/<int:transaction_id>', methods=['PUT'])
def update_budget_transaction(transaction_id):
    data = request.json or {}
    transaction_date = data.get('transaction_date')
    description = data.get('description')
    amount_raw = data.get('amount')

    if transaction_date is not None:
        transaction_date = transaction_date.strip()
        if not parse_date(transaction_date):
            return jsonify({"success": False, "message": "تاريخ غير صالح."}), 400
    if description is not None:
        description = description.strip()
        if not description:
            return jsonify({"success": False, "message": "الوصف مطلوب."}), 400
    amount = None
    if amount_raw is not None:
        amount = parse_amount(amount_raw)
        if amount == 'invalid':
            return jsonify({"success": False, "message": "المبلغ يجب أن يكون رقماً غير صفري."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM budget_transactions WHERE id = ?",
            (transaction_id,),
        )
        if not cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "message": "الحركة غير موجودة."}), 404

        updates = []
        values = []
        if transaction_date is not None:
            updates.append("transaction_date = ?")
            values.append(transaction_date)
        if description is not None:
            updates.append("description = ?")
            values.append(description)
        if amount is not None:
            updates.append("amount = ?")
            values.append(round(amount, 2))

        if not updates:
            conn.close()
            return jsonify({"success": False, "message": "لا توجد بيانات للتحديث."}), 400

        values.append(transaction_id)
        cursor.execute(
            f"UPDATE budget_transactions SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم تحديث الحركة."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/budget/transactions/<int:transaction_id>', methods=['DELETE'])
def delete_budget_transaction(transaction_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM budget_transactions WHERE id = ?",
            (transaction_id,),
        )
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({"success": False, "message": "الحركة غير موجودة."}), 404
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف الحركة."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5001)
