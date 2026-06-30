#!/usr/bin/env python3
"""Populate farm_data.db with realistic dummy flock data for manual testing."""

import random
import sqlite3
from datetime import date, timedelta

from app import (
    DB_NAME,
    get_connection,
    init_db,
    now_iso,
    sync_all_mother_statuses,
    sync_all_pregnancy_statuses,
    sync_pregnancy_status,
)

random.seed(42)
TODAY = date.today()
NOW = now_iso()

BREEDS = ['دروبر', 'سفلك', 'عساف', 'بلدي', 'نجدي']
EWES_NAMES = ['شمس', 'قمر', 'زهرة', 'لولو', 'نورة', 'سلمى', 'هناء', 'ريم', 'دانة', 'مها',
              'فاطمة', 'عائشة', 'خديجة', 'مريم', 'سارة', 'أمل', 'بسمة', 'جنى', 'تالا', 'ياسمين']
RAM_NAMES = ['فهد', 'أسد', 'سهم', 'رعد', 'برق', 'صقر', 'نجم', 'كريم']
LAMB_NAMES = ['بيسان', 'وسام', 'رغد', 'لين', 'تيم', 'رامي', 'جود', 'لينا', 'مازن', 'رنا',
              'عمر', 'ليان', 'زين', 'نادر', 'هند', 'وليد', 'سند', 'غزل', 'بدر', 'لمى']


def d(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


def insert_sheep(cursor, tag_id, name, breed, sheep_type, birth_date, status='حي',
                 mother_id=None, purchase_date=None, purchase_price=None,
                 purchase_location=None, notes=None):
    cursor.execute('''
        INSERT INTO sheep (tag_id, name, breed, sheep_type, birth_date, purchase_date,
                           purchase_price, purchase_location, notes, mother_status, mother_id,
                           status, pregnancy_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ليس أم بعد', ?, ?, 'غير حامل')
    ''', (tag_id, name, breed, sheep_type, birth_date, purchase_date, purchase_price,
          purchase_location, notes, mother_id, status))


def seed_sheep(cursor):
    """~50 sheep: breeding flock, lambs, purchased stock, mixed statuses."""
    ewes = []
    for i in range(1, 9):
        tag = str(100 + i)
        ewes.append(tag)
        insert_sheep(
            cursor, tag, EWES_NAMES[i - 1], random.choice(BREEDS), 'حملة',
            d(random.randint(900, 1600)), notes='أمهات القطيع الأساسية'
        )

    for i in range(4):
        tag = str(200 + i)
        insert_sheep(
            cursor, tag, RAM_NAMES[i], random.choice(BREEDS), 'خروف',
            d(random.randint(700, 1200)), notes='كباش تربية'
        )

    lamb_idx = 0
    for mother in ewes[:6]:
        for _ in range(random.randint(2, 3)):
            tag = str(300 + lamb_idx)
            lamb_idx += 1
            age_days = random.randint(30, 400)
            insert_sheep(
                cursor, tag, LAMB_NAMES[lamb_idx % len(LAMB_NAMES)],
                random.choice(BREEDS), 'حملة' if random.random() < 0.55 else 'خروف',
                d(age_days), mother_id=mother,
                notes='مولود في المزرعة' if age_days > 60 else 'حمل حديث'
            )

    for i in range(8):
        tag = str(400 + i)
        breed = BREEDS[i % len(BREEDS)]
        bought_days = random.randint(60, 500)
        insert_sheep(
            cursor, tag, f'مشتري {i + 1}', breed,
            'حملة' if i < 5 else 'خروف',
            d(bought_days + random.randint(200, 800)),
            purchase_date=d(bought_days),
            purchase_price=round(random.uniform(800, 2200), 0),
            purchase_location=random.choice(['سوق الجمعة', 'مزارع الشمال', 'تاجر أبو خالد']),
            notes='مشتري من الخارج'
        )

    extra_ewes = 50 - (8 + 4 + lamb_idx + 8)
    for i in range(extra_ewes):
        tag = str(500 + i)
        insert_sheep(
            cursor, tag, EWES_NAMES[(8 + i) % len(EWES_NAMES)], random.choice(BREEDS), 'حملة',
            d(random.randint(400, 1100))
        )

    cursor.execute("UPDATE sheep SET status = 'ميت', notes = 'نفوق — إسهال حاد' WHERE tag_id = '303'")
    cursor.execute("UPDATE sheep SET status = 'ميت', notes = 'نفوق — حادث' WHERE tag_id = '405'")
    cursor.execute("UPDATE sheep SET status = 'مباع', notes = 'بيع في المزاد' WHERE tag_id = '408'")
    cursor.execute("UPDATE sheep SET status = 'مباع', notes = 'بيع لجار' WHERE tag_id = '410'")


def seed_matings_and_scans(cursor):
    pregnant_ewes = ['101', '102', '105', '107', '501']
    for tag in pregnant_ewes:
        mated = d(random.randint(40, 70))
        cursor.execute('''
            INSERT INTO matings (sheep_tag_id, mated_date, notes, mating_type, created_at)
            VALUES (?, ?, ?, 'natural', ?)
        ''', (tag, mated, 'تزاوج طبيعي — موسم الربيع', NOW))
        mating_id = cursor.lastrowid
        scan_date = d(random.randint(15, 35))
        result = 'حامل'
        fetus_count = random.choice([1, 1, 2])
        cursor.execute('''
            INSERT INTO ultrasounds (mating_id, scan_date, result, fetus_count, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (mating_id, scan_date, result, fetus_count, 'سونار تأكيد', NOW))
        sync_pregnancy_status(cursor, tag)

    for tag in ['103', '104', '106']:
        cursor.execute('''
            INSERT INTO matings (sheep_tag_id, mated_date, notes, mating_type, created_at)
            VALUES (?, ?, ?, 'natural', ?)
        ''', (tag, d(random.randint(10, 18)), 'تزاوج حديث — بانتظار السونار', NOW))


def seed_illnesses(cursor):
    cases = [
        ('301', d(25), d(18), 'إسهال', 'علاج بالأملاح والمضاد'),
        ('302', d(12), None, 'التهاب رئوي خفيف', 'تحت المتابعة'),
        ('104', d(40), d(30), 'قدم ساخنة', 'غسول وضمادات'),
        ('203', d(60), d(52), 'طفيليات داخلية', 'جرعة ديدان'),
    ]
    for tag, onset, resolved, diagnosis, notes in cases:
        cursor.execute('''
            INSERT INTO illnesses (sheep_tag_id, onset_date, resolved_date, diagnosis, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (tag, onset, resolved, diagnosis, notes, NOW))
        illness_id = cursor.lastrowid
        cursor.execute('''
            INSERT INTO medications (illness_id, administered_date, drug_name, dose, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (illness_id, onset, 'أوكسي تتراسيكلين', '5 مل', 'جرعة أولى', NOW))
        if resolved:
            cursor.execute('''
                INSERT INTO medications (illness_id, administered_date, drug_name, dose, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (illness_id, resolved, 'أوكسي تتراسيكلين', '5 مل', 'جرعة متابعة', NOW))


def seed_vaccination_rules(cursor):
    """Rules covering all filter/status scenarios for manual testing."""
    cursor.execute('DELETE FROM vaccination_records')
    cursor.execute('DELETE FROM vaccination_types')

    rules = [
        ('طاعون المجترات الصغيرة', 1, 'years', None, None,
         'تطعيم سنوي إلزامي لكل القطيع — اختبر الحالة «متأخر» و«ضمن الجدول»'),
        ('تجمع معوي', 6, 'months', None, None,
         'كل 6 أشهر — معظم الأغنام يجب أن تكون ضمن الجدول أو متأخرة'),
        ('جدري الأغنام', 1, 'years', 'حملة', 60,
         'للحملات فقط من عمر 60 يوم — الخراف يظهرون «لا ينطبق»'),
        ('التوكسوپلازما (كباش)', 6, 'months', 'خروف', 90,
         'للكباش فقط من عمر 90 يوم — الحملات تظهر «لا ينطبق»'),
        ('enterotoxemia — حملان', 3, 'months', None, 45,
         'للحملان فوق 45 يوم — الأغنام الأصغر «لا ينطبق»'),
        ('جداء الأقدام', 1, 'years', None, 120,
         'سنوي بعد 4 أشهر — اختبر فلتر السلالة والجنس'),
        ('حمى الوادي (موقوف)', 1, 'years', None, None,
         'قاعدة موقوفة للاختبار — لا يجب أن تظهر في لوحة المتابعة', 0),
    ]
    type_ids = {}
    for row in rules:
        is_active = row[6] if len(row) > 6 else 1
        cursor.execute('''
            INSERT INTO vaccination_types
            (name, interval_value, interval_unit, applies_to_sheep_type, min_age_days, notes, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (*row[:6], is_active, NOW))
        type_ids[row[0]] = cursor.lastrowid
    return type_ids


def seed_vaccination_records(cursor, type_ids):
    cursor.execute("SELECT tag_id, sheep_type, birth_date, status FROM sheep WHERE status = 'حي'")
    live = cursor.fetchall()

    pox_id = type_ids['طاعون المجترات الصغيرة']
    enter_id = type_ids['تجمع معوي']
    scab_id = type_ids['جدري الأغنام']
    toxo_id = type_ids['التوكسوپلازما (كباش)']
    entero_id = type_ids['enterotoxemia — حملان']
    foot_id = type_ids['جداء الأقدام']

    for i, (tag, sheep_type, birth_date, _) in enumerate(live):
        bucket = i % 5
        if bucket == 0:
            continue
        if bucket == 1:
            cursor.execute('''
                INSERT INTO vaccination_records
                (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
                VALUES (?, ?, ?, '2 مل', 'LOT-2024-A', 'تطعيم سنوي', ?)
            ''', (pox_id, tag, d(400), NOW))
            cursor.execute('''
                INSERT INTO vaccination_records
                (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
                VALUES (?, ?, ?, '2 مل', 'LOT-2024-B', 'تجمع — متأخر', ?)
            ''', (enter_id, tag, d(220), NOW))
        elif bucket == 2:
            cursor.execute('''
                INSERT INTO vaccination_records
                (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
                VALUES (?, ?, ?, '2 مل', 'LOT-2025-01', 'حديث — ضمن الجدول', ?)
            ''', (pox_id, tag, d(60), NOW))
            cursor.execute('''
                INSERT INTO vaccination_records
                (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
                VALUES (?, ?, ?, '2 مل', 'LOT-2025-02', 'تجمع — ضمن الجدول', ?)
            ''', (enter_id, tag, d(45), NOW))
        elif bucket == 3:
            cursor.execute('''
                INSERT INTO vaccination_records
                (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
                VALUES (?, ?, ?, '2 مل', 'LOT-2023-X', 'متأخر — يحتاج تطعيم', ?)
            ''', (pox_id, tag, d(500), NOW))
        else:
            if sheep_type == 'حملة':
                cursor.execute('''
                    INSERT INTO vaccination_records
                    (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
                    VALUES (?, ?, ?, '1 مل', 'SCAB-01', 'جدري — حملة', ?)
                ''', (scab_id, tag, d(90), NOW))
            elif sheep_type == 'خروف':
                cursor.execute('''
                    INSERT INTO vaccination_records
                    (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
                    VALUES (?, ?, ?, '1.5 مل', 'TOXO-12', 'توكسو — كبش', ?)
                ''', (toxo_id, tag, d(30), NOW))

    for tag in ['101', '102', '201', '202', '301']:
        cursor.execute('''
            INSERT INTO vaccination_records
            (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
            VALUES (?, ?, ?, '2 مل', 'ENT-2025', 'enterotoxemia', ?)
        ''', (entero_id, tag, d(20), NOW))
        cursor.execute('''
            INSERT INTO vaccination_records
            (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
            VALUES (?, ?, ?, '1 مل', 'FOOT-99', 'جداء أقدام', ?)
        ''', (foot_id, tag, d(15), NOW))

    def full_vax(tag, sheep_type):
        """All applicable vaccines recent — shows «ضمن الجدول» in flock tracker."""
        recent = d(30)
        for vid in (pox_id, enter_id, entero_id, foot_id):
            cursor.execute('''
                INSERT OR IGNORE INTO vaccination_records
                (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
                VALUES (?, ?, ?, '2 مل', 'FULL-OK', 'مكتمل — ضمن الجدول', ?)
            ''', (vid, tag, recent, NOW))
        if sheep_type == 'حملة':
            cursor.execute('''
                INSERT OR IGNORE INTO vaccination_records
                (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
                VALUES (?, ?, ?, '1 مل', 'SCAB-OK', 'جدري — مكتمل', ?)
            ''', (scab_id, tag, recent, NOW))
        elif sheep_type == 'خروف':
            cursor.execute('''
                INSERT OR IGNORE INTO vaccination_records
                (vaccination_type_id, sheep_tag_id, administered_date, dose, batch_number, notes, created_at)
                VALUES (?, ?, ?, '1.5 مل', 'TOXO-OK', 'توكسو — مكتمل', ?)
            ''', (toxo_id, tag, recent, NOW))

    full_vax('101', 'حملة')
    full_vax('102', 'حملة')
    full_vax('201', 'خروف')


def seed_budget(cursor):
    cursor.execute('DELETE FROM budget_transactions')
    cursor.execute("SELECT id, name FROM budget_categories")
    cats = {name: cid for cid, name in cursor.fetchall()}

    txns = [
        (d(180), 15000, 'رصيد افتتاحي — موسم جديد', None),
        (d(150), -3200, 'علف شعير — طنين', cats.get('علف')),
        (d(120), -450, 'أدوية ومضادات حيوية', cats.get('طبي')),
        (d(90), -1800, 'علف مركز', cats.get('علف')),
        (d(60), 5500, 'بيع خروف 408', None),
        (d(45), -220, 'فيتامينات ومكملات', cats.get('طبي')),
        (d(30), -950, 'علف تبن', cats.get('علف')),
        (d(15), 4800, 'بيع خروف 410', None),
        (d(5), -150, 'مستلزمات أخرى', cats.get('أخرى')),
    ]
    for txn_date, amount, desc, cat_id in txns:
        cursor.execute('''
            INSERT INTO budget_transactions (transaction_date, amount, description, category_id, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (txn_date, amount, desc, cat_id, NOW))


def main():
    init_db()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM medications')
    cursor.execute('DELETE FROM illnesses')
    cursor.execute('DELETE FROM ultrasounds')
    cursor.execute('DELETE FROM matings')
    cursor.execute('DELETE FROM vaccination_records')
    cursor.execute('DELETE FROM sheep')

    seed_sheep(cursor)
    sync_all_mother_statuses(cursor)
    seed_matings_and_scans(cursor)
    sync_all_pregnancy_statuses(cursor)
    seed_illnesses(cursor)
    type_ids = seed_vaccination_rules(cursor)
    seed_vaccination_records(cursor, type_ids)
    seed_budget(cursor)

    cursor.execute("SELECT COUNT(*) FROM sheep")
    sheep_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM vaccination_types")
    rules_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM vaccination_records")
    records_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM matings")
    matings_count = cursor.fetchone()[0]

    conn.commit()
    conn.close()

    print('Dummy data seeded successfully.')
    print(f'  Sheep: {sheep_count}')
    print(f'  Vaccination rules: {rules_count} (6 active + 1 inactive)')
    print(f'  Vaccination records: {records_count}')
    print(f'  Matings: {matings_count}')
    print()
    print('Test scenarios in vaccinations tab:')
    print('  • متأخر — sheep with old pox/entero shots (e.g. tags in 300s bucket 1 & 3)')
    print('  • لم يُطعَّم — ~20% of flock with no records at all')
    print('  • ضمن الجدول — recent vaccinations (tags 101, 102, 201, etc.)')
    print('  • لا ينطبق — gender/age rules (جدري for rams, توكسو for ewes, young lambs)')
    print('  • Rules tab — edit «حمى الوادي (موقوف)» to verify inactive rules')
    print()
    print('Refresh the app in your browser to see the data.')


if __name__ == '__main__':
    main()
