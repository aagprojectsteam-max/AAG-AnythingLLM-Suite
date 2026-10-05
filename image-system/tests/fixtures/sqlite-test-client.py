"""Test-only SQLite transport for the server's small Prisma raw-SQL interface."""
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1], isolation_level=None, timeout=10)
db.row_factory = sqlite3.Row
for line in sys.stdin:
    try:
        item = json.loads(line)
        rows = db.execute(item['sql'], item.get('params', []))
        value = [dict(row) for row in rows] if item['query'] else rows.rowcount
        print(json.dumps({'value': value}), flush=True)
    except Exception as error:
        print(json.dumps({'error': str(error)}), flush=True)
