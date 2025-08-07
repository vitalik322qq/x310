import sqlite3

conn = sqlite3.connect("n3l0x_users.db")
c = conn.cursor()

try:
    c.execute("ALTER TABLE users ADD COLUMN username TEXT DEFAULT ''")
    print("Добавлена колонка username")
except sqlite3.OperationalError:
    print("Колонка username уже существует")

try:
    c.execute("ALTER TABLE users ADD COLUMN requests_left INTEGER DEFAULT 0")
    print("Добавлена колонка requests_left")
except sqlite3.OperationalError:
    print("Колонка requests_left уже существует")

try:
    c.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
    print("Добавлена колонка is_blocked")
except sqlite3.OperationalError:
    print("Колонка is_blocked уже существует")

conn.commit()
conn.close()
