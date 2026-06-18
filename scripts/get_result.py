import sqlite3

def get_result():
    db_path = "hub.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Search for the message referred to as 50dd6e66
    cursor = conn.execute("SELECT id, response FROM messages WHERE id LIKE '50dd6e66%'")
    row = cursor.fetchone()
    if row and row["response"]:
        response_text = row["response"]
        with open("scripts/wiki_result_full.txt", "w", encoding="utf-8") as f:
            f.write(response_text)
        print(f"Success: Wrote full response for message {row['id']} to scripts/wiki_result_full.txt")
    else:
        # Search for any message containing the term A2A or ACP in the response, to find the full answer
        cursor = conn.execute("SELECT id, response FROM messages WHERE response LIKE '%A2A%' AND response NOT LIKE '%Duplicate%' LIMIT 1")
        row = cursor.fetchone()
        if row and row["response"]:
            response_text = row["response"]
            with open("scripts/wiki_result_full.txt", "w", encoding="utf-8") as f:
                f.write(response_text)
            print(f"Success: Wrote full response for message {row['id']} via search to scripts/wiki_result_full.txt")
        else:
            print("Fully-cited response not found in database.")
            
    conn.close()

if __name__ == "__main__":
    get_result()
