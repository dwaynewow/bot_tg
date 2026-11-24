import json
import sqlite3

class Sessions:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (chat INTEGER PRIMARY KEY, data TEXT NOT NULL)")

    def load(self, chat):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT data FROM sessions WHERE chat=?", (chat,)).fetchone()
        return json.loads(row[0]) if row else {"answers": {}}

    def save(self, chat, record):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO sessions VALUES(?, ?) ON CONFLICT(chat) DO UPDATE SET data=excluded.data",
                       (chat, json.dumps(record, ensure_ascii=False)))

    def delete(self, chat):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM sessions WHERE chat=?", (chat,))


def chunks(text, limit=3900):
    # Telegram counts UTF-16 units. Splitting by Unicode characters preserves emojis.
    pending = []
    size = 0
    for line in text.split('\n'):
        while len(line.encode("utf-16-le")) // 2 > limit - 1:
            end = (limit - 1) // 2
            if pending:
                yield "\n".join(pending)
            pending, size = [], 0
            yield line[:end]
            line = line[end:]
        length = len(line.encode("utf-16-le")) // 2 + 1
        if pending and size + length > limit:
            yield "\n".join(pending)
            pending, size = [], 0
        pending.append(line)
        size += length
    if pending:
        yield "\n".join(pending)


