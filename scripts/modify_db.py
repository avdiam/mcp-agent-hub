import re

with open('mcp_hub/db.py', 'r') as f:
    code = f.read()

# 1. Update init_db table schema and add ALTER TABLE
schema_old = """                updated_at REAL
            )"""
schema_new = """                updated_at REAL,
                subject TEXT
            )"""
code = code.replace(schema_old, schema_new)

alter_table_code = """
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN subject TEXT")
        except sqlite3.OperationalError:
            pass
        
        await db.execute("CREATE INDEX IF NOT EXISTS"""
code = code.replace('        await db.execute("CREATE INDEX IF NOT EXISTS', alter_table_code, 1)

# 2. Update enqueue_message signature and INSERT
code = code.replace(
    'kind="task", response=None):',
    'kind="task", response=None, subject=None):'
)

insert_old = """                payload, context, response, status, flagged_stale, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        \", (message_id, session_id, parent_id, kind, sender_id, recipient_id, payload, context, response, is_stale, now, now))"""

insert_new = """                payload, context, response, status, flagged_stale, created_at, updated_at, subject
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        \", (message_id, session_id, parent_id, kind, sender_id, recipient_id, payload, context, response, is_stale, now, now, subject))"""

code = code.replace(insert_old, insert_new)

# 3. Add delete_old function
delete_old_code = """
@retry_on_lock()
async def delete_old(db_path):
    async with _connect(db_path) as db:
        cursor = await db.execute(\"\"\"
            DELETE FROM messages 
            WHERE status IN ('completed', 'failed', 'expired')
        \"\"\")
        rowcount = cursor.rowcount
        await db.commit()
        return rowcount
"""

if "async def delete_old" not in code:
    code += delete_old_code

with open('mcp_hub/db.py', 'w') as f:
    f.write(code)
