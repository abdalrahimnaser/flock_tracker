from flask import Flask, render_template, request, jsonify
import sqlite3
from datetime import date, datetime, timedelta

app = Flask(__name__)
DB_NAME = "farm_data.db"
MATING_REVIEW_DAYS = 20

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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sheep_tag_id TEXT NOT NULL,
            mated_date TEXT NOT NULL,
            notes TEXT,
            result TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (sheep_tag_id) REFERENCES sheep(tag_id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ultrasounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mating_id INTEGER NOT NULL,
            scan_date TEXT NOT NULL,
            result TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (mating_id) REFERENCES matings(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute("UPDATE sheep SET mother_status = 'أم' WHERE mother_status = 'آم'")
    cursor.execute("UPDATE sheep SET mother_status = 'ليس أم بعد' WHERE mother_status IN ('ليس ام بعد', 'غير محدد') OR mother_status IS NULL OR mother_status = ''")
    cursor.execute("UPDATE sheep SET life_status = 'حي' WHERE life_status IS NULL OR life_status = ''")
    sync_all_mother_statuses(cursor)
    sync_all_pregnancy_statuses(cursor)
    cursor.execute(
        "DELETE FROM matings WHERE sheep_tag_id NOT IN (SELECT tag_id FROM sheep)"
    )
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

def mating_needs_review(mated_date, result):
    if result:
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
        "matings": matings or [],
        "needs_mating_review": any(m.get('needs_review') for m in (matings or [])),
    }

SHEEP_SELECT = """
    SELECT tag_id, name, breed, sheep_type, birth_date, purchase_date,
           purchase_price, purchase_location, notes,
           mother_status, mother_id, life_status,
           (SELECT COUNT(*) FROM sheep c WHERE c.mother_id = sheep.tag_id) AS child_count,
           pregnancy_status
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

def compute_pregnancy_status(cursor, tag_id):
    cursor.execute(
        "SELECT sheep_type, life_status FROM sheep WHERE tag_id = ?",
        (tag_id,),
    )
    row = cursor.fetchone()
    if not row or row[0] != 'حملة' or row[1] == 'ميت':
        return 'غير حامل'

    cursor.execute('''
        SELECT m.id, m.result
        FROM matings m
        WHERE m.sheep_tag_id = ? AND m.completed_at IS NULL
        ORDER BY m.mated_date DESC, m.id DESC
        LIMIT 1
    ''', (tag_id,))
    mating = cursor.fetchone()
    if not mating:
        return 'غير حامل'

    mating_id, mating_result = mating
    if mating_result == 'fail':
        return 'غير حامل'

    if mating_result != 'pass':
        return 'انتظار السونار'

    cursor.execute('''
        SELECT result FROM ultrasounds
        WHERE mating_id = ?
        ORDER BY scan_date DESC, id DESC
        LIMIT 1
    ''', (mating_id,))
    latest_ultrasound = cursor.fetchone()
    if not latest_ultrasound:
        return 'انتظار السونار'

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
        WHERE m.sheep_tag_id = ? AND m.result = 'pass' AND m.completed_at IS NULL
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
        SELECT id, mated_date, notes, result, completed_at, created_at
        FROM matings
        WHERE sheep_tag_id = ?
        ORDER BY mated_date DESC, id DESC
    ''', (tag_id,))
    matings = []
    for row in cursor.fetchall():
        mating_id = row[0]
        cursor.execute('''
            SELECT id, scan_date, result, notes, created_at
            FROM ultrasounds
            WHERE mating_id = ?
            ORDER BY scan_date DESC, id DESC
        ''', (mating_id,))
        ultrasounds = [
            {
                "id": u[0],
                "scan_date": u[1],
                "result": u[2],
                "notes": u[3] or '',
                "created_at": u[4],
            }
            for u in cursor.fetchall()
        ]
        matings.append({
            "id": mating_id,
            "mated_date": row[1],
            "notes": row[2] or '',
            "result": row[3],
            "completed_at": row[4],
            "created_at": row[5],
            "needs_review": mating_needs_review(row[1], row[3]),
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
        "SELECT id, sheep_tag_id, mated_date, notes, result, completed_at FROM matings WHERE id = ?",
        (mating_id,),
    )
    return cursor.fetchone()

def normalize_birth_date(birth_date):
    birth_date = (birth_date or '').strip()
    return birth_date or date.today().isoformat()

@app.route('/')
def index():
    """Renders the dashboard."""
    return render_template('index.html')

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

@app.route('/api/sheep/<tag_id>', methods=['DELETE'])
def delete_sheep(tag_id):
    """Deletes a sheep record matching the tag_id."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT mother_id FROM sheep WHERE tag_id = ?", (tag_id,))
        row = cursor.fetchone()
        mother_id = row[0] if row else None
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

    if not mated_date:
        return jsonify({"success": False, "message": "الرجاء إدخال تاريخ التلقيح."}), 400
    if not parse_date(mated_date):
        return jsonify({"success": False, "message": "تاريخ التلقيح غير صالح."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        ok, message = validate_ewe_for_mating(cursor, tag_id)
        if not ok:
            conn.close()
            return jsonify({"success": False, "message": message}), 400

        cursor.execute('''
            INSERT INTO matings (sheep_tag_id, mated_date, notes, created_at)
            VALUES (?, ?, ?, ?)
        ''', (tag_id, mated_date, notes, now_iso()))
        sync_pregnancy_status(cursor, tag_id)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم تسجيل التلقيح بنجاح!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/matings/<int:mating_id>', methods=['PATCH'])
def update_mating(mating_id):
    data = request.json or {}
    result = (data.get('result') or '').strip()
    notes = data.get('notes')

    if result not in ('pass', 'fail'):
        return jsonify({"success": False, "message": "النتيجة يجب أن تكون pass أو fail."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        row = get_mating_or_404(cursor, mating_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل التلقيح غير موجود."}), 404
        if row[4] is not None:
            conn.close()
            return jsonify({"success": False, "message": "تم تحديد نتيجة هذا التلقيح مسبقاً."}), 400

        if notes is not None:
            cursor.execute(
                "UPDATE matings SET result = ?, notes = ? WHERE id = ?",
                (result, (notes or '').strip(), mating_id),
            )
        else:
            cursor.execute(
                "UPDATE matings SET result = ? WHERE id = ?",
                (result, mating_id),
            )
        sync_pregnancy_status(cursor, row[1])
        conn.commit()
        conn.close()
        label = 'ناجح' if result == 'pass' else 'فاشل'
        return jsonify({"success": True, "message": f"تم تسجيل التلقيح كـ {label}."})
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
        row = get_mating_or_404(cursor, mating_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل التلقيح غير موجود."}), 404
        if row[4] != 'pass':
            conn.close()
            return jsonify({"success": False, "message": "يمكن إغلاق دورة حمل ناجحة فقط."}), 400
        cursor.execute(
            "UPDATE matings SET completed_at = ? WHERE id = ?",
            (now_iso(), mating_id),
        )
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

    if not scan_date:
        return jsonify({"success": False, "message": "الرجاء إدخال تاريخ السونار."}), 400
    if not parse_date(scan_date):
        return jsonify({"success": False, "message": "تاريخ السونار غير صالح."}), 400
    if result not in ('pass', 'fail'):
        return jsonify({"success": False, "message": "النتيجة يجب أن تكون pass أو fail."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        row = get_mating_or_404(cursor, mating_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل التلقيح غير موجود."}), 404
        if row[4] != 'pass':
            conn.close()
            return jsonify({"success": False, "message": "السونار متاح فقط بعد نجاح التلقيح."}), 400

        cursor.execute('''
            INSERT INTO ultrasounds (mating_id, scan_date, result, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (mating_id, scan_date, result, notes, now_iso()))
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

    if result is not None and result not in ('pass', 'fail'):
        return jsonify({"success": False, "message": "النتيجة يجب أن تكون pass أو fail."}), 400
    if scan_date is not None and not parse_date(scan_date):
        return jsonify({"success": False, "message": "تاريخ السونار غير صالح."}), 400

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

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5001)
