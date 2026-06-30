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
            status TEXT,
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
    if 'pregnancy_status' not in columns:
        cursor.execute("ALTER TABLE sheep ADD COLUMN pregnancy_status TEXT")
    if 'photo_filename' not in columns:
        cursor.execute("ALTER TABLE sheep ADD COLUMN photo_filename TEXT")
    migrate_sheep_status_column(cursor)
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS illnesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sheep_tag_id TEXT NOT NULL,
            onset_date TEXT NOT NULL,
            resolved_date TEXT,
            diagnosis TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (sheep_tag_id) REFERENCES sheep(tag_id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            illness_id INTEGER NOT NULL,
            administered_date TEXT NOT NULL,
            drug_name TEXT NOT NULL,
            dose TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (illness_id) REFERENCES illnesses(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute("UPDATE sheep SET mother_status = 'أم' WHERE mother_status = 'آم'")
    cursor.execute("UPDATE sheep SET mother_status = 'ليس أم بعد' WHERE mother_status IN ('ليس ام بعد', 'غير محدد') OR mother_status IS NULL OR mother_status = ''")
    cursor.execute("UPDATE sheep SET status = 'حي' WHERE status IS NULL OR status = ''")
    ensure_upload_folder()
    sync_all_mother_statuses(cursor)
    sync_all_pregnancy_statuses(cursor)
    cursor.execute(
        "DELETE FROM matings WHERE sheep_tag_id NOT IN (SELECT tag_id FROM sheep)"
    )
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS budget_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_system INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS budget_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_date TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            category_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (category_id) REFERENCES budget_categories(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute("PRAGMA table_info(budget_transactions)")
    budget_columns = {row[1] for row in cursor.fetchall()}
    if 'category_id' not in budget_columns:
        cursor.execute("ALTER TABLE budget_transactions ADD COLUMN category_id INTEGER REFERENCES budget_categories(id) ON DELETE SET NULL")
    if 'sold_sheep_tag_id' not in budget_columns:
        cursor.execute("ALTER TABLE budget_transactions ADD COLUMN sold_sheep_tag_id TEXT REFERENCES sheep(tag_id) ON DELETE SET NULL")
    seed_budget_categories(cursor)
    remove_budget_category_by_name(cursor, 'صيانة')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vaccination_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            interval_value INTEGER NOT NULL,
            interval_unit TEXT NOT NULL,
            applies_to_sheep_type TEXT,
            min_age_days INTEGER,
            notes TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vaccination_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vaccination_type_id INTEGER NOT NULL,
            sheep_tag_id TEXT NOT NULL,
            administered_date TEXT NOT NULL,
            dose TEXT,
            batch_number TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (vaccination_type_id) REFERENCES vaccination_types(id) ON DELETE CASCADE,
            FOREIGN KEY (sheep_tag_id) REFERENCES sheep(tag_id) ON DELETE CASCADE
        )
    ''')
    seed_vaccination_types(cursor)
    conn.commit()
    conn.close()

def migrate_sheep_status_column(cursor):
    cursor.execute("PRAGMA table_info(sheep)")
    columns = {row[1] for row in cursor.fetchall()}
    if 'status' in columns:
        cursor.execute("UPDATE sheep SET status = 'حي' WHERE status IS NULL OR status = ''")
        return
    if 'life_status' in columns:
        cursor.execute("ALTER TABLE sheep RENAME COLUMN life_status TO status")
    else:
        cursor.execute("ALTER TABLE sheep ADD COLUMN status TEXT")
    cursor.execute("UPDATE sheep SET status = 'حي' WHERE status IS NULL OR status = ''")

def parse_sheep_status(value):
    status = (value or '').strip() or 'حي'
    if status not in ('حي', 'ميت', 'مباع'):
        return None
    return status

def revert_sheep_if_sold(cursor, tag_id):
    if not tag_id:
        return
    cursor.execute(
        "UPDATE sheep SET status = 'حي' WHERE tag_id = ? AND status = 'مباع'",
        (tag_id,),
    )
    sync_pregnancy_status(cursor, tag_id)

def mark_sheep_as_sold(cursor, tag_id):
    cursor.execute("SELECT status FROM sheep WHERE tag_id = ?", (tag_id,))
    row = cursor.fetchone()
    if not row:
        return False, f"رقم الخروف '{tag_id}' غير موجود في السجل."
    if row[0] == 'مباع':
        return False, 'هذا الخروف مباع مسبقاً.'
    if row[0] == 'ميت':
        return False, 'لا يمكن تسجيل بيع لخروف ميت.'
    cursor.execute("UPDATE sheep SET status = ? WHERE tag_id = ?", ('مباع', tag_id))
    sync_pregnancy_status(cursor, tag_id)
    return True, None

def parse_income_sheep_sale(data, amount):
    if amount <= 0:
        return None, None
    if not data.get('sheep_sale'):
        return None, None
    tag_id, tag_error = normalize_tag_id(data.get('sold_sheep_tag_id', ''))
    if tag_error:
        return None, tag_error
    if not tag_id:
        return None, 'الرجاء اختيار رقم الخروف المباع.'
    return tag_id, None

def sync_budget_sheep_sale(cursor, old_tag, new_tag):
    if old_tag and old_tag != new_tag:
        revert_sheep_if_sold(cursor, old_tag)
    if not new_tag:
        return None
    ok, message = mark_sheep_as_sold(cursor, new_tag)
    if not ok:
        if old_tag and old_tag != new_tag:
            mark_sheep_as_sold(cursor, old_tag)
        return message
    return None

def now_iso():
    return datetime.now().isoformat(timespec='seconds')

def to_western_digits(value):
    if value is None:
        return ''
    eastern = '٠١٢٣٤٥٦٧٨٩'
    persian = '۰۱۲۳۴۵۶۷۸۹'
    chars = []
    for ch in str(value):
        if ch in eastern:
            chars.append(str(eastern.index(ch)))
        elif ch in persian:
            chars.append(str(persian.index(ch)))
        else:
            chars.append(ch)
    return ''.join(chars)

def normalize_tag_id(value):
    tag_id = to_western_digits(value).strip()
    if not tag_id:
        return '', None
    if not tag_id.isdigit():
        return tag_id, 'رقم الخروف يجب أن يحتوي على أرقام إنجليزية (0-9) فقط.'
    return tag_id, None

@app.url_value_preprocessor
def normalize_url_tag_id(endpoint, values):
    if values and 'tag_id' in values:
        values['tag_id'] = to_western_digits(values['tag_id']).strip()

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
        count = int(to_western_digits(value).strip())
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

def compute_health_status(illnesses):
    if any(i.get('resolved_date') is None for i in (illnesses or [])):
        return 'مريض'
    return 'سليم'

def sheep_row_to_dict(row, matings=None, illnesses=None):
    illnesses = illnesses or []
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
        "status": row[11],
        "child_count": row[12],
        "pregnancy_status": row[13] or 'غير حامل',
        "photo_url": f"/uploads/sheep/{row[14]}" if row[14] else None,
        "matings": matings or [],
        "illnesses": illnesses,
        "health_status": compute_health_status(illnesses),
        "needs_mating_review": any(
            mating_needs_review(m.get('mated_date'), m.get('completed_at'), m.get('ultrasounds'))
            for m in (matings or [])
        ),
    }

SHEEP_SELECT = """
    SELECT tag_id, name, breed, sheep_type, birth_date, purchase_date,
           purchase_price, purchase_location, notes,
           mother_status, mother_id, status,
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
        "SELECT sheep_type, status FROM sheep WHERE tag_id = ?",
        (tag_id,),
    )
    row = cursor.fetchone()
    if not row or row[0] != 'حملة' or row[1] != 'حي':
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

def fetch_illnesses_for_sheep(cursor, tag_id):
    cursor.execute('''
        SELECT id, onset_date, resolved_date, diagnosis, notes, created_at
        FROM illnesses
        WHERE sheep_tag_id = ?
        ORDER BY onset_date DESC, id DESC
    ''', (tag_id,))
    illnesses = []
    for row in cursor.fetchall():
        illness_id = row[0]
        cursor.execute('''
            SELECT id, administered_date, drug_name, dose, notes, created_at
            FROM medications
            WHERE illness_id = ?
            ORDER BY administered_date DESC, id DESC
        ''', (illness_id,))
        medications = [
            {
                "id": m[0],
                "administered_date": m[1],
                "drug_name": m[2],
                "dose": m[3] or '',
                "notes": m[4] or '',
                "created_at": m[5],
            }
            for m in cursor.fetchall()
        ]
        illnesses.append({
            "id": illness_id,
            "onset_date": row[1],
            "resolved_date": row[2],
            "diagnosis": row[3] or '',
            "notes": row[4] or '',
            "created_at": row[5],
            "medications": medications,
        })
    return illnesses

def get_sheep_or_404(cursor, tag_id):
    cursor.execute(SHEEP_SELECT + " WHERE tag_id = ?", (tag_id,))
    row = cursor.fetchone()
    return row

def validate_ewe_for_mating(cursor, tag_id):
    cursor.execute(
        "SELECT sheep_type, status FROM sheep WHERE tag_id = ?",
        (tag_id,),
    )
    row = cursor.fetchone()
    if not row:
        return False, f"رقم الخروف '{tag_id}' غير موجود في السجل."
    if row[0] != 'حملة':
        return False, "التلقيح متاح للحملات فقط."
    if row[1] != 'حي':
        return False, "لا يمكن تسجيل تلقيح إلا للحملات الأحياء."
    return True, None

def validate_sheep_for_health(cursor, tag_id):
    cursor.execute("SELECT status FROM sheep WHERE tag_id = ?", (tag_id,))
    row = cursor.fetchone()
    if not row:
        return False, f"رقم الخروف '{tag_id}' غير موجود في السجل."
    if row[0] != 'حي':
        return False, "يمكن تسجيل المرض والأدوية للحيوانات الأحياء فقط."
    return True, None

def get_illness_or_404(cursor, illness_id):
    cursor.execute(
        "SELECT id, sheep_tag_id, onset_date, resolved_date, diagnosis, notes FROM illnesses WHERE id = ?",
        (illness_id,),
    )
    return cursor.fetchone()

def get_medication_or_404(cursor, medication_id):
    cursor.execute('''
        SELECT m.id, m.illness_id, i.sheep_tag_id
        FROM medications m
        JOIN illnesses i ON i.id = m.illness_id
        WHERE m.id = ?
    ''', (medication_id,))
    return cursor.fetchone()

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

@app.route('/vaccinations')
def vaccinations():
    """Renders the flock vaccination tracker."""
    return render_template('vaccinations.html')

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
        tag_id = row[0]
        matings = fetch_matings_for_sheep(cursor, tag_id)
        illnesses = fetch_illnesses_for_sheep(cursor, tag_id)
        result.append(sheep_row_to_dict(row, matings, illnesses))
    conn.close()
    return jsonify(result)

@app.route('/api/sheep', methods=['POST'])
def add_sheep():
    """Saves a new sheep record with comprehensive metrics."""
    data = request.json
    tag_id, tag_error = normalize_tag_id(data.get('tag_id', ''))
    if tag_error:
        return jsonify({"success": False, "message": tag_error}), 400
    name = data.get('name', '').strip()
    breed = data.get('breed', '').strip()
    sheep_type = data.get('sheep_type', '').strip()
    birth_date = normalize_birth_date(data.get('birth_date', ''))
    purchase_date = data.get('purchase_date', '').strip()
    purchase_price_str = to_western_digits(data.get('purchase_price', '')).strip()
    purchase_location = data.get('purchase_location', '').strip()
    notes = data.get('notes', '').strip()
    mother_id = to_western_digits(data.get('mother_id', '')).strip()
    status = parse_sheep_status(data.get('status') or data.get('life_status'))
    if status is None:
        return jsonify({"success": False, "message": "الحالة يجب أن تكون حي أو ميت أو مباع."}), 400

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
            INSERT INTO sheep (tag_id, name, breed, sheep_type, birth_date, purchase_date, purchase_price, purchase_location, notes, mother_status, mother_id, status, pregnancy_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (tag_id, name, breed, sheep_type, birth_date, purchase_date, purchase_price, purchase_location, notes, 'ليس أم بعد', mother_id or None, status, 'غير حامل'))
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
    purchase_price_str = to_western_digits(data.get('purchase_price', '')).strip()
    purchase_location = data.get('purchase_location', '').strip()
    notes = data.get('notes', '').strip()
    mother_id = to_western_digits(data.get('mother_id', '')).strip()
    status = parse_sheep_status(data.get('status') or data.get('life_status'))
    if status is None:
        return jsonify({"success": False, "message": "الحالة يجب أن تكون حي أو ميت أو مباع."}), 400

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
            SET name=?, breed=?, sheep_type=?, birth_date=?, purchase_date=?, purchase_price=?, purchase_location=?, notes=?, mother_id=?, status=?
            WHERE tag_id=?
        ''', (name, breed, sheep_type, birth_date, purchase_date, purchase_price, purchase_location, notes, new_mother_id, status, tag_id))
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

@app.route('/api/sheep/<tag_id>/illnesses', methods=['POST'])
def add_illness(tag_id):
    data = request.json or {}
    onset_date = (data.get('onset_date') or '').strip()
    diagnosis = (data.get('diagnosis') or '').strip()
    notes = (data.get('notes') or '').strip()

    if not onset_date:
        return jsonify({"success": False, "message": "الرجاء إدخال تاريخ بداية المرض."}), 400
    if not parse_date(onset_date):
        return jsonify({"success": False, "message": "تاريخ بداية المرض غير صالح."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        ok, message = validate_sheep_for_health(cursor, tag_id)
        if not ok:
            conn.close()
            return jsonify({"success": False, "message": message}), 400

        cursor.execute('''
            INSERT INTO illnesses (sheep_tag_id, onset_date, diagnosis, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (tag_id, onset_date, diagnosis, notes, now_iso()))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم تسجيل المرض بنجاح!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/illnesses/<int:illness_id>', methods=['PATCH'])
def update_illness(illness_id):
    data = request.json or {}
    diagnosis = data.get('diagnosis')
    notes = data.get('notes')
    resolved_date = data.get('resolved_date')

    if resolved_date is not None and resolved_date != '' and not parse_date(resolved_date):
        return jsonify({"success": False, "message": "تاريخ التعافي غير صالح."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        row = get_illness_or_404(cursor, illness_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل المرض غير موجود."}), 404

        updates = []
        values = []
        if diagnosis is not None:
            updates.append("diagnosis = ?")
            values.append(diagnosis.strip())
        if notes is not None:
            updates.append("notes = ?")
            values.append(notes.strip())
        if resolved_date is not None:
            updates.append("resolved_date = ?")
            values.append(resolved_date.strip() if resolved_date else None)

        if not updates:
            conn.close()
            return jsonify({"success": False, "message": "لا توجد بيانات للتحديث."}), 400

        values.append(illness_id)
        cursor.execute(
            f"UPDATE illnesses SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        conn.commit()
        conn.close()
        if resolved_date:
            return jsonify({"success": True, "message": "تم تسجيل التعافي."})
        return jsonify({"success": True, "message": "تم حفظ التعديل."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/illnesses/<int:illness_id>', methods=['DELETE'])
def delete_illness(illness_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        row = get_illness_or_404(cursor, illness_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل المرض غير موجود."}), 404
        cursor.execute("DELETE FROM illnesses WHERE id = ?", (illness_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف سجل المرض."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/illnesses/<int:illness_id>/medications', methods=['POST'])
def add_medication(illness_id):
    data = request.json or {}
    administered_date = (data.get('administered_date') or '').strip()
    drug_name = (data.get('drug_name') or '').strip()
    dose = (data.get('dose') or '').strip()
    notes = (data.get('notes') or '').strip()

    if not administered_date:
        return jsonify({"success": False, "message": "الرجاء إدخال تاريخ إعطاء الدواء."}), 400
    if not parse_date(administered_date):
        return jsonify({"success": False, "message": "تاريخ إعطاء الدواء غير صالح."}), 400
    if not drug_name:
        return jsonify({"success": False, "message": "الرجاء إدخال اسم الدواء."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        row = get_illness_or_404(cursor, illness_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل المرض غير موجود."}), 404
        ok, message = validate_sheep_for_health(cursor, row[1])
        if not ok:
            conn.close()
            return jsonify({"success": False, "message": message}), 400

        cursor.execute('''
            INSERT INTO medications (illness_id, administered_date, drug_name, dose, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (illness_id, administered_date, drug_name, dose, notes, now_iso()))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم تسجيل الدواء بنجاح!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/medications/<int:medication_id>', methods=['PATCH'])
def update_medication(medication_id):
    data = request.json or {}
    administered_date = data.get('administered_date')
    drug_name = data.get('drug_name')
    dose = data.get('dose')
    notes = data.get('notes')

    if administered_date is not None and not parse_date(administered_date):
        return jsonify({"success": False, "message": "تاريخ إعطاء الدواء غير صالح."}), 400
    if drug_name is not None and not drug_name.strip():
        return jsonify({"success": False, "message": "اسم الدواء لا يمكن أن يكون فارغاً."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        row = get_medication_or_404(cursor, medication_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل الدواء غير موجود."}), 404

        updates = []
        values = []
        if administered_date is not None:
            updates.append("administered_date = ?")
            values.append(administered_date.strip())
        if drug_name is not None:
            updates.append("drug_name = ?")
            values.append(drug_name.strip())
        if dose is not None:
            updates.append("dose = ?")
            values.append(dose.strip())
        if notes is not None:
            updates.append("notes = ?")
            values.append(notes.strip())

        if not updates:
            conn.close()
            return jsonify({"success": False, "message": "لا توجد بيانات للتحديث."}), 400

        values.append(medication_id)
        cursor.execute(
            f"UPDATE medications SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم تحديث سجل الدواء."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/medications/<int:medication_id>', methods=['DELETE'])
def delete_medication(medication_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        row = get_medication_or_404(cursor, medication_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "سجل الدواء غير موجود."}), 404
        cursor.execute("DELETE FROM medications WHERE id = ?", (medication_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف سجل الدواء."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

def parse_amount(value):
    if value is None or value == '':
        return None
    try:
        amount = float(to_western_digits(value).strip())
    except (TypeError, ValueError):
        return 'invalid'
    if amount == 0:
        return 'invalid'
    return amount

DEFAULT_BUDGET_CATEGORIES = ('علف', 'طبي', 'أخرى')

def seed_budget_categories(cursor):
    cursor.execute("SELECT COUNT(*) FROM budget_categories")
    if cursor.fetchone()[0] > 0:
        return
    for name in DEFAULT_BUDGET_CATEGORIES:
        cursor.execute(
            "INSERT OR IGNORE INTO budget_categories (name, is_system, created_at) VALUES (?, 1, ?)",
            (name, now_iso()),
        )

def remove_budget_category_by_name(cursor, name):
    cursor.execute("DELETE FROM budget_categories WHERE name = ?", (name,))

def fetch_budget_categories(cursor):
    cursor.execute(
        "SELECT id, name, is_system FROM budget_categories ORDER BY is_system DESC, name COLLATE NOCASE ASC"
    )
    return [
        {"id": row[0], "name": row[1], "is_system": bool(row[2])}
        for row in cursor.fetchall()
    ]

def get_category_or_404(cursor, category_id):
    cursor.execute(
        "SELECT id, name, is_system FROM budget_categories WHERE id = ?",
        (category_id,),
    )
    return cursor.fetchone()

def resolve_expense_category_id(cursor, category_id_raw, amount):
    if amount >= 0:
        return None, None
    if category_id_raw in (None, ''):
        return None, 'الرجاء اختيار فئة المصروف.'
    try:
        category_id = int(category_id_raw)
    except (TypeError, ValueError):
        return None, 'فئة المصروف غير صالحة.'
    if not get_category_or_404(cursor, category_id):
        return None, 'فئة المصروف غير موجودة.'
    return category_id, None

def budget_row_to_dict(row, running_balance=None):
    return {
        "id": row[0],
        "transaction_date": row[1],
        "amount": row[2],
        "description": row[3] or '',
        "category_id": row[4],
        "category_name": row[5] or '',
        "sold_sheep_tag_id": row[6],
        "created_at": row[7],
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

@app.route('/api/budget/categories', methods=['GET'])
def get_budget_categories():
    conn = get_connection()
    cursor = conn.cursor()
    categories = fetch_budget_categories(cursor)
    conn.close()
    return jsonify(categories)

@app.route('/api/budget/categories', methods=['POST'])
def add_budget_category():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"success": False, "message": "اسم الفئة مطلوب."}), 400
    if len(name) > 40:
        return jsonify({"success": False, "message": "اسم الفئة طويل جداً."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO budget_categories (name, is_system, created_at) VALUES (?, 0, ?)",
            (name, now_iso()),
        )
        conn.commit()
        category_id = cursor.lastrowid
        conn.close()
        return jsonify({"success": True, "message": "تمت إضافة الفئة.", "id": category_id, "name": name})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "message": "هذه الفئة موجودة مسبقاً."}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/budget/categories/<int:category_id>', methods=['DELETE'])
def delete_budget_category(category_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        row = get_category_or_404(cursor, category_id)
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "الفئة غير موجودة."}), 404
        if row[2]:
            conn.close()
            return jsonify({"success": False, "message": "لا يمكن حذف الفئات الأساسية."}), 400
        cursor.execute("DELETE FROM budget_categories WHERE id = ?", (category_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف الفئة."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/budget/transactions', methods=['GET'])
def get_budget_transactions():
    conn = get_connection()
    cursor = conn.cursor()
    summary = fetch_budget_summary(cursor)
    categories = fetch_budget_categories(cursor)
    cursor.execute('''
        SELECT t.id, t.transaction_date, t.amount, t.description, t.category_id,
               c.name, t.sold_sheep_tag_id, t.created_at
        FROM budget_transactions t
        LEFT JOIN budget_categories c ON c.id = t.category_id
        ORDER BY t.transaction_date ASC, t.id ASC
    ''')
    rows = cursor.fetchall()
    running = 0.0
    with_balance = []
    for row in rows:
        running = round(running + row[2], 2)
        with_balance.append(budget_row_to_dict(row, running))
    with_balance.reverse()
    conn.close()
    return jsonify({"summary": summary, "categories": categories, "transactions": with_balance})

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

    try:
        conn = get_connection()
        cursor = conn.cursor()
        category_id, category_error = resolve_expense_category_id(cursor, data.get('category_id'), amount)
        if category_error:
            conn.close()
            return jsonify({"success": False, "message": category_error}), 400

        sold_tag, sale_error = parse_income_sheep_sale(data, amount)
        if sale_error:
            conn.close()
            return jsonify({"success": False, "message": sale_error}), 400
        if sold_tag:
            ok, message = mark_sheep_as_sold(cursor, sold_tag)
            if not ok:
                conn.close()
                return jsonify({"success": False, "message": message}), 400

        cursor.execute(
            "INSERT INTO budget_transactions (transaction_date, amount, description, category_id, sold_sheep_tag_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (transaction_date, round(amount, 2), description, category_id, sold_tag, now_iso()),
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
    category_id_raw = data.get('category_id')
    sheep_sale_raw = data.get('sheep_sale')
    sold_sheep_tag_raw = data.get('sold_sheep_tag_id')

    if transaction_date is not None:
        transaction_date = transaction_date.strip()
        if not parse_date(transaction_date):
            return jsonify({"success": False, "message": "تاريخ غير صالح."}), 400
    if description is not None:
        description = description.strip()
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

        cursor.execute(
            "SELECT amount, sold_sheep_tag_id FROM budget_transactions WHERE id = ?",
            (transaction_id,),
        )
        current_row = cursor.fetchone()
        if not current_row:
            conn.close()
            return jsonify({"success": False, "message": "الحركة غير موجودة."}), 404
        current_amount, old_sold_tag = current_row
        new_amount = amount if amount is not None else current_amount

        category_id = None
        category_touched = category_id_raw is not None or amount is not None
        if category_touched:
            if new_amount >= 0:
                category_id = None
            else:
                raw = category_id_raw if category_id_raw is not None else None
                if raw is None and amount is None:
                    cursor.execute("SELECT category_id FROM budget_transactions WHERE id = ?", (transaction_id,))
                    category_id = cursor.fetchone()[0]
                else:
                    category_id, category_error = resolve_expense_category_id(cursor, raw, new_amount)
                    if category_error:
                        conn.close()
                        return jsonify({"success": False, "message": category_error}), 400

        sale_touched = (
            sheep_sale_raw is not None
            or sold_sheep_tag_raw is not None
            or amount is not None
        )
        new_sold_tag = old_sold_tag
        if sale_touched:
            if new_amount < 0:
                if old_sold_tag:
                    revert_sheep_if_sold(cursor, old_sold_tag)
                new_sold_tag = None
            else:
                wants_sale = sheep_sale_raw if sheep_sale_raw is not None else bool(old_sold_tag)
                if wants_sale:
                    sale_data = {
                        'sheep_sale': True,
                        'sold_sheep_tag_id': sold_sheep_tag_raw if sold_sheep_tag_raw is not None else old_sold_tag,
                    }
                    parsed_tag, sale_error = parse_income_sheep_sale(sale_data, new_amount)
                    if sale_error:
                        conn.close()
                        return jsonify({"success": False, "message": sale_error}), 400
                    new_sold_tag = parsed_tag
                else:
                    new_sold_tag = None
                sale_error = sync_budget_sheep_sale(cursor, old_sold_tag, new_sold_tag)
                if sale_error:
                    conn.rollback()
                    conn.close()
                    return jsonify({"success": False, "message": sale_error}), 400

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
        if category_touched:
            updates.append("category_id = ?")
            values.append(category_id)
        if sale_touched:
            updates.append("sold_sheep_tag_id = ?")
            values.append(new_sold_tag)

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
            "SELECT sold_sheep_tag_id FROM budget_transactions WHERE id = ?",
            (transaction_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "الحركة غير موجودة."}), 404
        sold_tag = row[0]
        if sold_tag:
            revert_sheep_if_sold(cursor, sold_tag)
        cursor.execute(
            "DELETE FROM budget_transactions WHERE id = ?",
            (transaction_id,),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "message": "الحركة غير موجودة."}), 404
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف الحركة."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

VACCINATION_INTERVAL_UNITS = ('days', 'months', 'years')
VACCINATION_INTERVAL_LABELS = {
    'days': 'يوم',
    'months': 'شهر',
    'years': 'سنة',
}

DEFAULT_VACCINATION_TYPES = (
    ('طاعون المجترات الصغيرة', 1, 'years', None, None, 'تطعيم سنوي ضد طاعون المجترات الصغيرة'),
    ('تجمع معوي', 6, 'months', None, None, 'كل 6 أشهر'),
)

def seed_vaccination_types(cursor):
    cursor.execute("SELECT COUNT(*) FROM vaccination_types")
    if cursor.fetchone()[0] > 0:
        return
    for name, interval_value, interval_unit, applies_to, min_age, notes in DEFAULT_VACCINATION_TYPES:
        cursor.execute(
            "INSERT INTO vaccination_types (name, interval_value, interval_unit, applies_to_sheep_type, min_age_days, notes, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (name, interval_value, interval_unit, applies_to, min_age, notes, now_iso()),
        )

def add_interval_to_date(base_date, value, unit):
    if not base_date or value is None or not unit:
        return None
    if unit == 'days':
        return base_date + timedelta(days=value)
    if unit == 'months':
        month = base_date.month - 1 + value
        year = base_date.year + month // 12
        month = month % 12 + 1
        days_in_month = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                         31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
        day = min(base_date.day, days_in_month)
        return date(year, month, day)
    if unit == 'years':
        try:
            return base_date.replace(year=base_date.year + value)
        except ValueError:
            return base_date.replace(year=base_date.year + value, day=28)
    return None

def sheep_age_days(sheep_row):
    birth = parse_date(sheep_row.get('birth_date') if isinstance(sheep_row, dict) else sheep_row[4])
    if not birth:
        return None
    return (date.today() - birth).days

def vaccination_type_applies(vtype, sheep):
    if not vtype.get('is_active', True):
        return False
    applies_to = vtype.get('applies_to_sheep_type')
    if applies_to and sheep.get('sheep_type') != applies_to:
        return False
    min_age = vtype.get('min_age_days')
    if min_age is not None:
        age = sheep_age_days(sheep)
        if age is None or age < min_age:
            return False
    return True

def compute_vaccination_status(last_date_str, interval_value, interval_unit, reference_date=None):
    reference_date = reference_date or date.today()
    if not last_date_str:
        return 'never', None
    last_date = parse_date(last_date_str)
    if not last_date:
        return 'never', None
    next_due = add_interval_to_date(last_date, interval_value, interval_unit)
    if not next_due:
        return 'never', None
    if next_due <= reference_date:
        return 'overdue', next_due.isoformat()
    return 'ok', next_due.isoformat()

def fetch_vaccination_types(cursor, active_only=False):
    query = (
        "SELECT id, name, interval_value, interval_unit, applies_to_sheep_type, "
        "min_age_days, notes, is_active, created_at FROM vaccination_types"
    )
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY name COLLATE NOCASE ASC"
    cursor.execute(query)
    return [
        {
            "id": row[0],
            "name": row[1],
            "interval_value": row[2],
            "interval_unit": row[3],
            "applies_to_sheep_type": row[4],
            "min_age_days": row[5],
            "notes": row[6],
            "is_active": bool(row[7]),
            "created_at": row[8],
        }
        for row in cursor.fetchall()
    ]

def fetch_vaccination_type_or_404(cursor, type_id):
    cursor.execute(
        "SELECT id, name, interval_value, interval_unit, applies_to_sheep_type, min_age_days, notes, is_active "
        "FROM vaccination_types WHERE id = ?",
        (type_id,),
    )
    return cursor.fetchone()

def vaccination_type_row_to_dict(row):
    return {
        "id": row[0],
        "name": row[1],
        "interval_value": row[2],
        "interval_unit": row[3],
        "applies_to_sheep_type": row[4],
        "min_age_days": row[5],
        "notes": row[6],
        "is_active": bool(row[7]),
    }

def parse_vaccination_interval(data):
    try:
        interval_value = int(to_western_digits(data.get('interval_value', '')).strip())
    except (TypeError, ValueError):
        return None, None, 'قيمة التكرار يجب أن تكون رقماً صحيحاً.'
    if interval_value <= 0:
        return None, None, 'قيمة التكرار يجب أن تكون أكبر من صفر.'
    interval_unit = (data.get('interval_unit') or '').strip()
    if interval_unit not in VACCINATION_INTERVAL_UNITS:
        return None, None, 'وحدة التكرار غير صالحة.'
    return interval_value, interval_unit, None

def parse_min_age_days(data):
    raw = data.get('min_age_days')
    if raw is None or raw == '':
        return None, None
    try:
        days = int(to_western_digits(raw).strip())
    except (TypeError, ValueError):
        return None, 'الحد الأدنى للعمر يجب أن يكون رقماً صحيحاً.'
    if days < 0:
        return None, 'الحد الأدنى للعمر لا يمكن أن يكون سالباً.'
    return days, None

def fetch_last_vaccinations_by_sheep(cursor):
    cursor.execute('''
        SELECT vaccination_type_id, sheep_tag_id, MAX(administered_date), MAX(id)
        FROM vaccination_records
        GROUP BY vaccination_type_id, sheep_tag_id
    ''')
    last_by_key = {}
    for type_id, tag_id, last_date, record_id in cursor.fetchall():
        last_by_key[(type_id, tag_id)] = {"last_date": last_date, "record_id": record_id}
    return last_by_key

def fetch_vaccination_records(cursor, limit=None, sheep_tag_id=None):
    query = '''
        SELECT r.id, r.vaccination_type_id, t.name, r.sheep_tag_id, s.name,
               r.administered_date, r.dose, r.batch_number, r.notes, r.created_at
        FROM vaccination_records r
        JOIN vaccination_types t ON t.id = r.vaccination_type_id
        LEFT JOIN sheep s ON s.tag_id = r.sheep_tag_id
    '''
    params = []
    if sheep_tag_id:
        query += " WHERE r.sheep_tag_id = ?"
        params.append(sheep_tag_id)
    query += " ORDER BY r.administered_date DESC, r.id DESC"
    if limit:
        query += f" LIMIT {int(limit)}"
    cursor.execute(query, params)
    return [
        {
            "id": row[0],
            "vaccination_type_id": row[1],
            "type_name": row[2],
            "sheep_tag_id": row[3],
            "sheep_name": row[4],
            "administered_date": row[5],
            "dose": row[6],
            "batch_number": row[7],
            "notes": row[8],
            "created_at": row[9],
        }
        for row in cursor.fetchall()
    ]

def build_vaccination_dashboard(cursor, due_by=None):
    due_by_date = parse_date(due_by) if due_by else date.today()
    types = fetch_vaccination_types(cursor, active_only=True)
    last_by_key = fetch_last_vaccinations_by_sheep(cursor)
    cursor.execute(SHEEP_SELECT + " WHERE status = 'حي' ORDER BY CAST(tag_id AS INTEGER), tag_id")
    sheep_rows = cursor.fetchall()
    sheep_status = []
    summary = {"live": 0, "overdue": 0, "ok": 0, "never": 0}

    for row in sheep_rows:
        sheep = sheep_row_to_dict(row)
        summary["live"] += 1
        vaccinations = []
        worst = 'ok'
        priority = {'overdue': 3, 'never': 2, 'not_applicable': 0, 'ok': 1}

        for vtype in types:
            if not vaccination_type_applies(vtype, sheep):
                vaccinations.append({
                    "type_id": vtype["id"],
                    "type_name": vtype["name"],
                    "status": "not_applicable",
                    "last_date": None,
                    "next_due": None,
                })
                continue

            key = (vtype["id"], sheep["tag_id"])
            last_info = last_by_key.get(key)
            last_date = last_info["last_date"] if last_info else None
            status, next_due = compute_vaccination_status(
                last_date, vtype["interval_value"], vtype["interval_unit"], due_by_date
            )
            vaccinations.append({
                "type_id": vtype["id"],
                "type_name": vtype["name"],
                "status": status,
                "last_date": last_date,
                "next_due": next_due,
            })
            if priority.get(status, 0) > priority.get(worst, 0):
                worst = status

        if not any(v["status"] != "not_applicable" for v in vaccinations):
            worst = "not_applicable"

        if worst == 'not_applicable':
            pass
        elif worst in summary:
            summary[worst] += 1
        else:
            summary["ok"] += 1

        sheep_status.append({
            "tag_id": sheep["tag_id"],
            "name": sheep["name"],
            "breed": sheep["breed"],
            "sheep_type": sheep["sheep_type"],
            "birth_date": sheep["birth_date"],
            "mother_id": sheep["mother_id"],
            "mother_status": sheep["mother_status"],
            "status": sheep["status"],
            "overall_status": worst,
            "vaccinations": vaccinations,
        })

    return {
        "summary": summary,
        "types": types,
        "sheep": sheep_status,
        "due_by": due_by_date.isoformat(),
    }

@app.route('/api/vaccinations/types', methods=['GET'])
def get_vaccination_types():
    conn = get_connection()
    cursor = conn.cursor()
    types = fetch_vaccination_types(cursor)
    conn.close()
    return jsonify(types)

@app.route('/api/vaccinations/types', methods=['POST'])
def add_vaccination_type():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"success": False, "message": "الرجاء إدخال اسم التطعيم."}), 400
    interval_value, interval_unit, interval_error = parse_vaccination_interval(data)
    if interval_error:
        return jsonify({"success": False, "message": interval_error}), 400
    min_age_days, min_age_error = parse_min_age_days(data)
    if min_age_error:
        return jsonify({"success": False, "message": min_age_error}), 400
    applies_to = (data.get('applies_to_sheep_type') or '').strip() or None
    if applies_to and applies_to not in ('حملة', 'خروف'):
        return jsonify({"success": False, "message": "نوع الخروف غير صالح."}), 400
    notes = (data.get('notes') or '').strip()

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO vaccination_types (name, interval_value, interval_unit, applies_to_sheep_type, min_age_days, notes, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (name, interval_value, interval_unit, applies_to, min_age_days, notes, now_iso()),
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return jsonify({"success": True, "message": "تمت إضافة قاعدة التطعيم.", "id": new_id})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/vaccinations/types/<int:type_id>', methods=['PUT'])
def update_vaccination_type(type_id):
    data = request.json or {}
    conn = get_connection()
    cursor = conn.cursor()
    if not fetch_vaccination_type_or_404(cursor, type_id):
        conn.close()
        return jsonify({"success": False, "message": "قاعدة التطعيم غير موجودة."}), 404

    updates = []
    values = []
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            conn.close()
            return jsonify({"success": False, "message": "الرجاء إدخال اسم التطعيم."}), 400
        updates.append("name = ?")
        values.append(name)
    if 'interval_value' in data or 'interval_unit' in data:
        row = fetch_vaccination_type_or_404(cursor, type_id)
        merged = {
            'interval_value': data.get('interval_value', row[2]),
            'interval_unit': data.get('interval_unit', row[3]),
        }
        interval_value, interval_unit, interval_error = parse_vaccination_interval(merged)
        if interval_error:
            conn.close()
            return jsonify({"success": False, "message": interval_error}), 400
        updates.extend(["interval_value = ?", "interval_unit = ?"])
        values.extend([interval_value, interval_unit])
    if 'applies_to_sheep_type' in data:
        applies_to = (data.get('applies_to_sheep_type') or '').strip() or None
        if applies_to and applies_to not in ('حملة', 'خروف'):
            conn.close()
            return jsonify({"success": False, "message": "نوع الخروف غير صالح."}), 400
        updates.append("applies_to_sheep_type = ?")
        values.append(applies_to)
    if 'min_age_days' in data:
        min_age_days, min_age_error = parse_min_age_days(data)
        if min_age_error:
            conn.close()
            return jsonify({"success": False, "message": min_age_error}), 400
        updates.append("min_age_days = ?")
        values.append(min_age_days)
    if 'notes' in data:
        updates.append("notes = ?")
        values.append((data.get('notes') or '').strip())
    if 'is_active' in data:
        updates.append("is_active = ?")
        values.append(1 if data.get('is_active') else 0)

    if not updates:
        conn.close()
        return jsonify({"success": False, "message": "لا توجد بيانات للتحديث."}), 400

    values.append(type_id)
    cursor.execute(f"UPDATE vaccination_types SET {', '.join(updates)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "تم تحديث قاعدة التطعيم."})

@app.route('/api/vaccinations/types/<int:type_id>', methods=['DELETE'])
def delete_vaccination_type(type_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if not fetch_vaccination_type_or_404(cursor, type_id):
            conn.close()
            return jsonify({"success": False, "message": "قاعدة التطعيم غير موجودة."}), 404
        cursor.execute("DELETE FROM vaccination_types WHERE id = ?", (type_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف قاعدة التطعيم."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/vaccinations/dashboard', methods=['GET'])
def get_vaccination_dashboard():
    due_by = request.args.get('due_by', '').strip() or None
    conn = get_connection()
    cursor = conn.cursor()
    dashboard = build_vaccination_dashboard(cursor, due_by=due_by)
    dashboard["all_types"] = fetch_vaccination_types(cursor)
    dashboard["recent_records"] = fetch_vaccination_records(cursor, limit=50)
    conn.close()
    return jsonify(dashboard)

@app.route('/api/vaccinations/records', methods=['GET'])
def get_vaccination_records():
    sheep_tag_id = (request.args.get('sheep_tag_id') or '').strip() or None
    conn = get_connection()
    cursor = conn.cursor()
    if sheep_tag_id:
        cursor.execute("SELECT tag_id, name, breed, sheep_type FROM sheep WHERE tag_id = ?", (sheep_tag_id,))
        sheep_row = cursor.fetchone()
        if not sheep_row:
            conn.close()
            return jsonify({"success": False, "message": "الخروف غير موجود."}), 404
        records = fetch_vaccination_records(cursor, sheep_tag_id=sheep_tag_id)
        conn.close()
        return jsonify({
            "sheep": {
                "tag_id": sheep_row[0],
                "name": sheep_row[1],
                "breed": sheep_row[2],
                "sheep_type": sheep_row[3],
            },
            "records": records,
            "total": len(records),
        })
    records = fetch_vaccination_records(cursor, limit=100)
    conn.close()
    return jsonify({"records": records, "total": len(records)})

@app.route('/api/vaccinations/records', methods=['POST'])
def add_vaccination_record():
    data = request.json or {}
    try:
        type_id = int(data.get('vaccination_type_id'))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "نوع التطعيم غير صالح."}), 400

    administered_date = (data.get('administered_date') or '').strip()
    if not administered_date:
        administered_date = date.today().isoformat()
    elif not parse_date(administered_date):
        return jsonify({"success": False, "message": "تاريخ غير صالح."}), 400

    tag_ids = data.get('sheep_tag_ids') or []
    if not tag_ids:
        single_tag, tag_error = normalize_tag_id(data.get('sheep_tag_id', ''))
        if tag_error:
            return jsonify({"success": False, "message": tag_error}), 400
        if not single_tag:
            return jsonify({"success": False, "message": "الرجاء اختيار خروف واحد على الأقل."}), 400
        tag_ids = [single_tag]
    else:
        normalized = []
        for raw in tag_ids:
            tag_id, tag_error = normalize_tag_id(raw)
            if tag_error:
                return jsonify({"success": False, "message": tag_error}), 400
            if tag_id:
                normalized.append(tag_id)
        tag_ids = list(dict.fromkeys(normalized))
        if not tag_ids:
            return jsonify({"success": False, "message": "الرجاء اختيار خروف واحد على الأقل."}), 400

    dose = (data.get('dose') or '').strip()
    batch_number = (data.get('batch_number') or '').strip()
    notes = (data.get('notes') or '').strip()

    try:
        conn = get_connection()
        cursor = conn.cursor()
        if not fetch_vaccination_type_or_404(cursor, type_id):
            conn.close()
            return jsonify({"success": False, "message": "نوع التطعيم غير موجود."}), 404

        inserted = 0
        for tag_id in tag_ids:
            cursor.execute("SELECT status FROM sheep WHERE tag_id = ?", (tag_id,))
            sheep_row = cursor.fetchone()
            if not sheep_row:
                conn.rollback()
                conn.close()
                return jsonify({"success": False, "message": f"رقم الخروف '{tag_id}' غير موجود."}), 400
            if sheep_row[0] != 'حي':
                conn.rollback()
                conn.close()
                return jsonify({"success": False, "message": f"يمكن تطعيم الحيوانات الأحياء فقط (رقم {tag_id})."}), 400
            cursor.execute(
                "INSERT INTO vaccination_records (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (type_id, tag_id, administered_date, dose, batch_number, notes, now_iso()),
            )
            inserted += 1
        conn.commit()
        conn.close()
        message = f"تم تسجيل التطعيم لـ {inserted} رأس." if inserted > 1 else "تم تسجيل التطعيم."
        return jsonify({"success": True, "message": message, "count": inserted})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/vaccinations/records/<int:record_id>', methods=['DELETE'])
def delete_vaccination_record(record_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM vaccination_records WHERE id = ?", (record_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "message": "سجل التطعيم غير موجود."}), 404
        cursor.execute("DELETE FROM vaccination_records WHERE id = ?", (record_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف سجل التطعيم."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

def get_local_ip():
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('8.8.8.8', 80))
            return sock.getsockname()[0]
    except OSError:
        return None


init_db()

if __name__ == '__main__':
    port = 5001
    use_https = os.environ.get('USE_HTTPS', '0') == '1'
    ssl_context = 'adhoc' if use_https else None
    scheme = 'https' if use_https else 'http'
    local_ip = get_local_ip()

    print('\n--- Farm Logger ---')
    print(f'  On this Mac:  {scheme}://127.0.0.1:{port}')
    if local_ip:
        print(f'  On your phone: {scheme}://{local_ip}:{port}')
    if use_https:
        print('  HTTPS is on — accept the certificate warning on your phone.')
    else:
        print('  Use http:// (not https://) on other devices.')
        print('  For camera on phone: USE_HTTPS=1 python app.py')
    print('-------------------\n')

    app.run(debug=True, host='0.0.0.0', port=port, ssl_context=ssl_context)
