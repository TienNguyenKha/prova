import sqlite3


def export_users(db_path: str, out_path: str) -> int:
    conn = sqlite3.connect(db_path)
    out = open(out_path, "w")
    count = 0
    for row in conn.execute("SELECT id, email FROM users"):
        out.write(f"{row[0]},{row[1]}\n")
        count += 1
    return count
