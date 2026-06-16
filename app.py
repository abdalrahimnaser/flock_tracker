from flask import Flask, render_template, request, jsonify
import sqlite3
from datetime import date

app = Flask(__name__)
DB_NAME = "farm_data.db"

def init_db():
    """Initializes the database with the expanded farm metrics spec sheet."""
    conn = sqlite3.connect(DB_NAME)
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
            life_status TEXT
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
    cursor.execute("UPDATE sheep SET mother_status = 'أم' WHERE mother_status = 'آم'")
    cursor.execute("UPDATE sheep SET mother_status = 'ليس أم بعد' WHERE mother_status IN ('ليس ام بعد', 'غير محدد') OR mother_status IS NULL OR mother_status = ''")
    cursor.execute("UPDATE sheep SET life_status = 'حي' WHERE life_status IS NULL OR life_status = ''")
    sync_all_mother_statuses(cursor)
    conn.commit()
    conn.close()

def sheep_row_to_dict(row):
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
    }

SHEEP_SELECT = """
    SELECT tag_id, name, breed, sheep_type, birth_date, purchase_date,
           purchase_price, purchase_location, notes,
           mother_status, mother_id, life_status,
           (SELECT COUNT(*) FROM sheep c WHERE c.mother_id = sheep.tag_id) AS child_count
    FROM sheep
"""

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
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT breed FROM sheep WHERE breed IS NOT NULL AND breed != ''")
    breeds = [row[0] for row in cursor.fetchall()]
    conn.close()
    return jsonify(breeds)

@app.route('/api/sheep', methods=['GET'])
def get_sheep():
    """Fetches all sheep records with the updated spec sheet fields."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(SHEEP_SELECT)
    rows = cursor.fetchall()
    conn.close()
    
    return jsonify([sheep_row_to_dict(row) for row in rows])

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
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        if mother_id:
            cursor.execute("SELECT tag_id FROM sheep WHERE tag_id = ?", (mother_id,))
            if not cursor.fetchone():
                conn.close()
                return jsonify({"success": False, "message": f"رقم الأم '{mother_id}' غير موجود في السجل."}), 400

        cursor.execute('''
            INSERT INTO sheep (tag_id, name, breed, sheep_type, birth_date, purchase_date, purchase_price, purchase_location, notes, mother_status, mother_id, life_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (tag_id, name, breed, sheep_type, birth_date, purchase_date, purchase_price, purchase_location, notes, 'ليس أم بعد', mother_id or None, life_status))
        sync_mother_status(cursor, tag_id)
        sync_mother_status(cursor, mother_id)
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
        conn = sqlite3.connect(DB_NAME)
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

        cursor.execute('''
            UPDATE sheep
            SET name=?, breed=?, sheep_type=?, birth_date=?, purchase_date=?, purchase_price=?, purchase_location=?, notes=?, mother_id=?, life_status=?
            WHERE tag_id=?
        ''', (name, breed, sheep_type, birth_date, purchase_date, purchase_price, purchase_location, notes, mother_id or None, life_status, tag_id))
        sync_mother_status(cursor, tag_id)
        sync_mother_status(cursor, mother_id)
        if old_mother_id and old_mother_id != (mother_id or None):
            sync_mother_status(cursor, old_mother_id)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم تعديل بيانات الخروف بنجاح!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/sheep/<tag_id>', methods=['DELETE'])
def delete_sheep(tag_id):
    """Deletes a sheep record matching the tag_id."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT mother_id FROM sheep WHERE tag_id = ?", (tag_id,))
        row = cursor.fetchone()
        mother_id = row[0] if row else None
        cursor.execute("DELETE FROM sheep WHERE tag_id=?", (tag_id,))
        sync_mother_status(cursor, mother_id)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف رأس الماشية بنجاح!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5001)