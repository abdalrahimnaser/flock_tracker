from flask import Flask, render_template, request, jsonify
import sqlite3

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
            birth_date TEXT,
            purchase_date TEXT,
            purchase_price REAL,
            purchase_location TEXT,
            breeding_date TEXT,
            breeding_type TEXT,
            notes TEXT
        )
    ''')
    conn.commit()
    conn.close()

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
    cursor.execute("SELECT * FROM sheep")
    rows = cursor.fetchall()
    conn.close()
    
    sheep_list = []
    for row in rows:
        sheep_list.append({
            "tag_id": row[0],
            "name": row[1],
            "breed": row[2],
            "birth_date": row[3],
            "purchase_date": row[4],
            "purchase_price": row[5],
            "purchase_location": row[6],
            "breeding_date": row[7],
            "breeding_type": row[8],
            "notes": row[9]
        })
    return jsonify(sheep_list)

@app.route('/api/sheep', methods=['POST'])
def add_sheep():
    """Saves a new sheep record with comprehensive metrics."""
    data = request.json
    tag_id = data.get('tag_id', '').strip()
    name = data.get('name', '').strip()
    breed = data.get('breed', '').strip()
    birth_date = data.get('birth_date', '').strip()
    purchase_date = data.get('purchase_date', '').strip()
    purchase_price_str = data.get('purchase_price', '').strip()
    purchase_location = data.get('purchase_location', '').strip()
    breeding_date = data.get('breeding_date', '').strip()
    breeding_type = data.get('breeding_type', '').strip()
    notes = data.get('notes', '').strip()

    if not tag_id:
        return jsonify({"success": False, "message": "الرجاء إدخال رقم الخروف الحتمي."}), 400

    purchase_price = None
    if purchase_price_str:
        try:
            purchase_price = float(purchase_price_str)
        except ValueError:
            return jsonify({"success": False, "message": "يجب أن يكون سعر الشراء رقماً صحيحاً."}), 400

    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sheep (tag_id, name, breed, birth_date, purchase_date, purchase_price, purchase_location, breeding_date, breeding_type, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (tag_id, name, breed, birth_date, purchase_date, purchase_price, purchase_location, breeding_date, breeding_type, notes))
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
    birth_date = data.get('birth_date', '').strip()
    purchase_date = data.get('purchase_date', '').strip()
    purchase_price_str = data.get('purchase_price', '').strip()
    purchase_location = data.get('purchase_location', '').strip()
    breeding_date = data.get('breeding_date', '').strip()
    breeding_type = data.get('breeding_type', '').strip()
    notes = data.get('notes', '').strip()

    purchase_price = None
    if purchase_price_str:
        try:
            purchase_price = float(purchase_price_str)
        except ValueError:
            return jsonify({"success": False, "message": "يجب أن يكون سعر الشراء رقماً صحيحاً."}), 400

    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE sheep
            SET name=?, breed=?, birth_date=?, purchase_date=?, purchase_price=?, purchase_location=?, breeding_date=?, breeding_type=?, notes=?
            WHERE tag_id=?
        ''', (name, breed, birth_date, purchase_date, purchase_price, purchase_location, breeding_date, breeding_type, notes, tag_id))
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
        cursor.execute("DELETE FROM sheep WHERE tag_id=?", (tag_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "تم حذف رأس الماشية بنجاح!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5001)