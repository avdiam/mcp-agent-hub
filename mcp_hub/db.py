import asyncio
from functools import wraps
from contextlib import asynccontextmanager
import sqlite3
import aiosqlite
import json
import time
import uuid

if sqlite3.sqlite_version_info < (3, 35):
    raise RuntimeError(f"SQLite >= 3.35 is required. Found: {sqlite3.sqlite_version}")

# Kinds that auto-complete the instant they are claimed (ack-less): the recipient never
# reply/fail's them, so claim_pending marks them 'completed' on the way out.
#   result       = a task YOU sent succeeded (D20)
#   failure      = a task YOU sent failed    (AHB-13 #3)
#   announcement = a broadcast delivered to everyone (AHB-1 BD2 — informational, no reply)
#   offer_update = a job-board state change that concerns you (AHB-2 — the offer row is the
#                  source of truth, so a missed notification never strands anything)
NO_ACK_KINDS = ("result", "failure", "announcement", "offer_update")

# Tunable queue constants — the single source of truth. hub.py imports these (rather than
# redefining them) so tuning one here can't silently desync the DB logic from the server
# config; each is also overridable per-call via the matching keyword arg. (Q5; AHB-14)
STALE_THRESHOLD = 90       # seconds since last_seen before a queued recipient is flagged stale
VISIBILITY_TIMEOUT = 600   # seconds an in_progress claim is held before it's eligible for redelivery
MESSAGE_TTL = 86400        # seconds a pending task lingers before the sweep marks it expired

# Broadcast flood caps (AHB-1 P1). A violation raises ValueError and inserts NOTHING
# (all-or-nothing). Enforced inside db.broadcast so the DB stays the single source of truth
# (per the D32/AHB-14 precedent); each is overridable per-call via the matching keyword arg.
BROADCAST_MAX_PAYLOAD = 4096    # bytes (UTF-8) — max broadcast body
BROADCAST_MAX_SUBJECT = 120     # characters — max broadcast subject
BROADCAST_MIN_INTERVAL = 30     # seconds — per-sender cooldown between broadcasts
BROADCAST_HOURLY_CAP = 10       # broadcasts per sender per rolling hour
BROADCAST_MAX_RECIPIENTS = 200  # safety ceiling on fan-out size per broadcast
BROADCAST_CATCHUP_WINDOW = 86400  # seconds a broadcast stays deliverable to late joiners (AHB-1 P2)

# Job-offer board caps (AHB-2). Same philosophy as the broadcast caps: enforced inside the
# db functions so the DB stays the single source of truth; a violation raises ValueError and
# inserts NOTHING. Posting an offer also broadcasts an advert under the poster's own
# broadcast flood caps, so those apply on top of these.
OFFER_MAX_PAYLOAD = 4096       # bytes (UTF-8) — max offer body (the full work description)
OFFER_MAX_SUBJECT = 120        # characters — max offer subject
OFFER_MAX_NOTE = 1024          # bytes (UTF-8) — max claim note
OFFER_MAX_OPEN_PER_POSTER = 5  # open offers one poster may have on the board at once
OFFER_DEFAULT_TTL = 86400      # seconds an offer stays open before the sweep expires it (24h)
OFFER_MIN_TTL = 60             # floor for a caller-supplied TTL
OFFER_MAX_TTL = 259200         # ceiling for a caller-supplied TTL (72h)
OFFER_ANNOUNCE_SNIPPET = 500   # characters of the offer body quoted in the advert broadcast



def retry_on_lock(retries=5, backoff=0.01):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return await func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e) and attempt < retries - 1:
                        await asyncio.sleep(backoff * (2 ** attempt))
                    else:
                        raise
        return wrapper
    return decorator

@asynccontextmanager
async def _connect(db_path):
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = aiosqlite.Row
        yield conn
    finally:
        await conn.close()

@retry_on_lock()
async def init_db(db_path="hub.db"):
    async with _connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                description TEXT,
                skills TEXT,
                status TEXT,
                last_seen REAL
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                parent_id TEXT,
                kind TEXT DEFAULT 'task',
                sender_id TEXT,
                recipient_id TEXT,
                payload TEXT,
                context TEXT,
                response TEXT,
                status TEXT DEFAULT 'pending',
                flagged_stale INTEGER DEFAULT 0,
                claimed_at REAL,
                created_at REAL,
                updated_at REAL,
                subject TEXT
            )
        """)
        

        try:
            await db.execute("ALTER TABLE messages ADD COLUMN subject TEXT")
        except sqlite3.OperationalError:
            pass
        
        await db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_recipient_status ON messages(recipient_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_session ON messages(session_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_created_at ON messages(created_at)")

        # Broadcast audit log (AHB-1 P1). Doubles as the durable rate-limit source (survives
        # restarts, unlike an in-memory bucket): db.broadcast reads it to enforce the caps.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id TEXT PRIMARY KEY,
                sender_id TEXT,
                subject TEXT,
                payload TEXT,
                context TEXT,
                recipient_count INTEGER,
                created_at REAL
            )
        """)
        # P2 migration: pre-P2 broadcasts tables lack the context column (AHB-1 P2).
        try:
            await db.execute("ALTER TABLE broadcasts ADD COLUMN context TEXT")
        except sqlite3.OperationalError:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_sender_created ON broadcasts(sender_id, created_at)")

        # Job-offer board (AHB-2): claimable work items with a lifecycle
        # (open → assigned → completed | withdrawn | expired; re-opened on assignment failure).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS job_offers (
                id TEXT PRIMARY KEY,
                poster_id TEXT,
                subject TEXT,
                payload TEXT,
                required_skills TEXT,
                status TEXT DEFAULT 'open',
                claimant_id TEXT,
                task_message_id TEXT,
                broadcast_id TEXT,
                created_at REAL,
                updated_at REAL,
                expires_at REAL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_offers_status ON job_offers(status)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS job_claims (
                id TEXT PRIMARY KEY,
                offer_id TEXT,
                claimant_id TEXT,
                note TEXT,
                status TEXT DEFAULT 'pending',
                created_at REAL,
                updated_at REAL
            )
        """)
        # One PENDING claim per (offer, claimant) — a partial unique index rather than a table
        # UNIQUE, so an agent whose claim was rejected (or whose assignment failed and
        # re-opened the offer) may claim again later.
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_claims_pending_unique
            ON job_claims(offer_id, claimant_id) WHERE status='pending'
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_claims_offer ON job_claims(offer_id)")

        await db.commit()

@retry_on_lock()
async def upsert_agent(db_path, agent_id, skills_json, description=None):
    async with _connect(db_path) as db:
        await db.execute("""
            INSERT INTO agents (id, description, skills, status, last_seen)
            VALUES (?, ?, ?, 'online', ?)
            ON CONFLICT(id) DO UPDATE SET
                description=excluded.description,
                skills=excluded.skills,
                status='online',
                last_seen=excluded.last_seen
        """, (agent_id, description, skills_json, time.time()))
        await db.commit()

def derive_status(stored_status, last_seen, now=None, stale_threshold=STALE_THRESHOLD):
    """Liveness as surfaced to consumers: the stored `status` column is only the sticky
    online/offline intent flag; actual presence is derived from `last_seen` age (AHB-15).
    Explicit `offline` is preserved; anything else reads `online` or `stale` by age."""
    if stored_status == "offline":
        return "offline"
    if now is None:
        now = time.time()
    if last_seen is None or now - last_seen > stale_threshold:
        return "stale"
    return "online"

@retry_on_lock()
async def get_all_agents(db_path):
    """Registry rows with `status` already liveness-derived (AHB-15) — every consumer
    (MCP `list_agents`, `/api/state`) shares this one derivation so they can't diverge."""
    now = time.time()
    async with _connect(db_path) as db:
        async with db.execute("SELECT * FROM agents") as cursor:
            rows = await cursor.fetchall()
            agents = [dict(row) for row in rows]
    for a in agents:
        a["status"] = derive_status(a["status"], a["last_seen"], now)
    return agents

@retry_on_lock()
async def set_agent_offline(db_path, agent_id):
    async with _connect(db_path) as db:
        await db.execute("UPDATE agents SET status='offline' WHERE id=?", (agent_id,))
        await db.commit()

@retry_on_lock()
async def delete_agent(db_path, agent_id, purge_messages=False):
    """Permanently remove an agent from the registry.

    Option A (default): deletes the agent row only; its historical messages are
    left intact. Option B (purge_messages=True): also deletes every message the
    agent sent or received, its job-board footprint (offers it posted — and
    their claims — and claims it made on others' offers; AHB-2), and its
    `broadcasts` audit rows (AHB-16: purging deletes recipients' copies of its
    adverts, so leaving the audit row would make register-time catch-up re-queue
    ghost adverts for offers that no longer exist; the sender is gone, so its
    rate-limit history is moot). Returns a dict with the rows removed from each
    table.
    """
    async with _connect(db_path) as db:
        messages_deleted = 0
        offers_deleted = 0
        broadcasts_deleted = 0
        if purge_messages:
            cursor = await db.execute(
                "DELETE FROM messages WHERE sender_id=? OR recipient_id=?",
                (agent_id, agent_id),
            )
            messages_deleted = cursor.rowcount
            await db.execute(
                "DELETE FROM job_claims WHERE claimant_id=? OR offer_id IN (SELECT id FROM job_offers WHERE poster_id=?)",
                (agent_id, agent_id),
            )
            cursor = await db.execute("DELETE FROM job_offers WHERE poster_id=?", (agent_id,))
            offers_deleted = cursor.rowcount
            cursor = await db.execute("DELETE FROM broadcasts WHERE sender_id=?", (agent_id,))
            broadcasts_deleted = cursor.rowcount
        cursor = await db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        agents_deleted = cursor.rowcount
        await db.commit()
        return {"agents_deleted": agents_deleted, "messages_deleted": messages_deleted,
                "offers_deleted": offers_deleted, "broadcasts_deleted": broadcasts_deleted}

@retry_on_lock()
async def touch_last_seen(db_path, agent_id):
    async with _connect(db_path) as db:
        await db.execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), agent_id))
        await db.commit()

@retry_on_lock()
async def enqueue_message(db_path, sender_id, recipient_id, payload, context=None, session_id=None, parent_id=None, kind="task", response=None, subject=None, internal=False, stale_threshold=STALE_THRESHOLD):
    message_id = str(uuid.uuid4())
    if not session_id:
        session_id = str(uuid.uuid4())

    async with _connect(db_path) as db:
        async with db.execute("SELECT status, last_seen FROM agents WHERE id=?", (recipient_id,)) as cursor:
            row = await cursor.fetchone()
            # Point-to-point sends reject unknown/offline recipients. Internal deliveries
            # (result / input_request fan-out) MUST bypass that guard: the originating task
            # already completed, so raising here would surface a spurious error to the
            # worker AND drop the fan-out. Mirrors the AHB-1 BD3 broadcast rule. (AHB-11)
            if not row:
                if not internal:
                    raise ValueError(f"Recipient {recipient_id} is unknown")
                is_stale = 0
            else:
                if row["status"] == "offline" and not internal:
                    raise ValueError(f"Recipient {recipient_id} is offline")
                is_stale = 0
                if time.time() - row["last_seen"] > stale_threshold:
                    is_stale = 1

        now = time.time()
        await db.execute("""
            INSERT INTO messages (
                id, session_id, parent_id, kind, sender_id, recipient_id,
                payload, context, response, status, flagged_stale, created_at, updated_at, subject
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """, (message_id, session_id, parent_id, kind, sender_id, recipient_id, payload, context, response, is_stale, now, now, subject))
        await db.commit()
    
    return {"message_id": message_id, "session_id": session_id}

@retry_on_lock()
async def broadcast(db_path, sender_id, payload, subject=None, context=None,
                    max_payload=BROADCAST_MAX_PAYLOAD, max_subject=BROADCAST_MAX_SUBJECT,
                    min_interval=BROADCAST_MIN_INTERVAL, hourly_cap=BROADCAST_HOURLY_CAP,
                    max_recipients=BROADCAST_MAX_RECIPIENTS, stale_threshold=STALE_THRESHOLD):
    """Fan one announcement out to every connected agent (AHB-1 P1).

    Delivers a kind='announcement' message (ack-less; auto-completed on claim, NO_ACK_KINDS)
    to all non-offline agents INCLUDING the sender (BD5 echo), skipping explicitly-offline
    peers (BD3). Flood-capped per sender via the durable `broadcasts` audit table; a cap
    violation raises ValueError and inserts NOTHING (all-or-nothing). Returns delivery counts
    and the broadcast_id. One multi-row transaction under @retry_on_lock.
    """
    # Size caps first — cheap, and reject before opening a connection.
    if payload is not None and len(payload.encode("utf-8")) > max_payload:
        raise ValueError(f"Broadcast payload exceeds the {max_payload}-byte limit")
    if subject is not None and len(subject) > max_subject:
        raise ValueError(f"Broadcast subject exceeds the {max_subject}-character limit")

    broadcast_id = str(uuid.uuid4())
    now = time.time()

    async with _connect(db_path) as db:
        # Rate limits, read from the durable audit log (survives restarts).
        async with db.execute(
            "SELECT COUNT(*) AS n, MAX(created_at) AS last_at FROM broadcasts WHERE sender_id=? AND created_at > ?",
            (sender_id, now - 3600),
        ) as cursor:
            rl = await cursor.fetchone()
        if rl["last_at"] is not None and (now - rl["last_at"]) < min_interval:
            wait = int(min_interval - (now - rl["last_at"])) + 1
            raise ValueError(f"Broadcast cooldown active — wait ~{wait}s before broadcasting again")
        if (rl["n"] or 0) >= hourly_cap:
            raise ValueError(f"Broadcast hourly cap reached ({hourly_cap}/hour) — try again later")

        # Eligible recipients: every non-offline agent (online + stale), the sender included (BD5).
        async with db.execute("SELECT id, last_seen, status FROM agents") as cursor:
            agents = await cursor.fetchall()
        skipped_offline = sum(1 for a in agents if a["status"] == "offline")
        eligible = [a for a in agents if a["status"] != "offline"]

        # Safety ceiling: never fan out to more than max_recipients. Realistically unreachable
        # on a localhost hub; if hit, serve the most-recently-active first and report the drop
        # (never silently — AHB-1 caps note).
        skipped_over_cap = 0
        if len(eligible) > max_recipients:
            eligible.sort(key=lambda a: a["last_seen"] or 0, reverse=True)
            skipped_over_cap = len(eligible) - max_recipients
            eligible = eligible[:max_recipients]

        rows = [
            (
                str(uuid.uuid4()), broadcast_id, None, "announcement", sender_id, a["id"],
                payload, context, None, "pending",
                1 if (a["last_seen"] is not None and now - a["last_seen"] > stale_threshold) else 0,
                now, now, subject,
            )
            for a in eligible
        ]
        if rows:
            await db.executemany("""
                INSERT INTO messages (
                    id, session_id, parent_id, kind, sender_id, recipient_id,
                    payload, context, response, status, flagged_stale, created_at, updated_at, subject
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

        # Audit row (also the rate-limit source AND the durable late-joiner catch-up source,
        # AHB-1 P2) — recorded even on a zero-recipient broadcast, so broadcasting into an
        # empty hub still counts against the caps and still reaches whoever registers later.
        await db.execute(
            "INSERT INTO broadcasts (id, sender_id, subject, payload, context, recipient_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (broadcast_id, sender_id, subject, payload, context, len(rows), now),
        )
        await db.commit()

    return {
        "broadcast_id": broadcast_id,
        "delivered": len(rows),
        "recipients": [a["id"] for a in eligible],
        "skipped_offline": skipped_offline,
        "skipped_over_cap": skipped_over_cap,
    }

@retry_on_lock()
async def deliver_missed_broadcasts(db_path, agent_id, window=BROADCAST_CATCHUP_WINDOW):
    """Late-joiner catch-up (AHB-1 P2): queue every broadcast from the last `window` seconds
    that `agent_id` never received, as normal pending kind='announcement' rows.

    Dedupe is structural, not cursor-based: a P1 fan-out row (and any prior catch-up row)
    carries session_id = broadcast_id, so "already received" = "a messages row exists for
    (session_id, recipient_id)" — in ANY status, including claimed/completed/expired. That
    makes this idempotent across re-registers with no read-cursor column to maintain.
    Catch-up rows get created_at = now, so the D24 TTL sweep gives them a full fresh life.
    Returns the number of announcements queued.
    """
    now = time.time()
    async with _connect(db_path) as db:
        async with db.execute(
            """
            SELECT b.id, b.sender_id, b.subject, b.payload, b.context
            FROM broadcasts b
            WHERE b.created_at > ?
              AND NOT EXISTS (
                  SELECT 1 FROM messages m
                  WHERE m.session_id = b.id AND m.recipient_id = ?
              )
            ORDER BY b.created_at
            """,
            (now - window, agent_id),
        ) as cursor:
            missed = await cursor.fetchall()

        if missed:
            await db.executemany("""
                INSERT INTO messages (
                    id, session_id, parent_id, kind, sender_id, recipient_id,
                    payload, context, response, status, flagged_stale, created_at, updated_at, subject
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    str(uuid.uuid4()), b["id"], None, "announcement", b["sender_id"], agent_id,
                    b["payload"], b["context"], None, "pending",
                    0,  # the recipient is registering right now — by definition fresh
                    now, now, b["subject"],
                )
                for b in missed
            ])
            await db.commit()

    return len(missed)

@retry_on_lock()
async def post_offer(db_path, poster_id, payload, subject=None, required_skills=None,
                     ttl=OFFER_DEFAULT_TTL, max_payload=OFFER_MAX_PAYLOAD,
                     max_subject=OFFER_MAX_SUBJECT, max_open=OFFER_MAX_OPEN_PER_POSTER):
    """Post a job offer to the board (AHB-2): work open for ANY agent to claim, not
    addressed to a specific recipient.

    Validates the offer caps, announces the offer through db.broadcast (the poster's own
    broadcast flood caps apply — posting an offer IS a broadcast, so offer spam and
    broadcast spam share one budget), then inserts the board row. Any cap violation raises
    ValueError and posts nothing. The advert carries a snippet + claim instructions; the
    board row holds the full payload — the advert is the ad, the row is the contract.

    Authoring convention (AHB-17 #2): `payload` should be the PURE WORK STATEMENT — it is
    delivered verbatim as the winner's task on selection. The hub already appends claim
    instructions to the advert, so recruitment copy in the payload just becomes boilerplate
    inside the eventual assignment.
    Returns {"offer_id", "expires_at", "broadcast": {...}}.
    """
    if payload is None or not payload.strip():
        raise ValueError("Offer payload must not be empty")
    if len(payload.encode("utf-8")) > max_payload:
        raise ValueError(f"Offer payload exceeds the {max_payload}-byte limit")
    if subject is not None and len(subject) > max_subject:
        raise ValueError(f"Offer subject exceeds the {max_subject}-character limit")
    ttl = max(OFFER_MIN_TTL, min(int(ttl), OFFER_MAX_TTL))
    skills_list = list(required_skills or [])

    async with _connect(db_path) as db:
        async with db.execute("SELECT 1 FROM agents WHERE id=?", (poster_id,)) as cursor:
            if not await cursor.fetchone():
                raise ValueError(f"Poster {poster_id} is not a registered agent")
        async with db.execute(
            "SELECT COUNT(*) AS n FROM job_offers WHERE poster_id=? AND status='open'",
            (poster_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row["n"] >= max_open:
            raise ValueError(
                f"Open-offer cap reached ({max_open} per poster) — resolve or withdraw one first"
            )

    offer_id = str(uuid.uuid4())
    snippet = payload if len(payload) <= OFFER_ANNOUNCE_SNIPPET else payload[:OFFER_ANNOUNCE_SNIPPET] + "…"
    announce = (
        f"Job offer {offer_id} from {poster_id}"
        + (f"\nRequired skills: {', '.join(skills_list)}" if skills_list else "")
        + f"\n\n{snippet}\n\n"
        + f"To claim it: claim_offer(agent_id=<you>, offer_id='{offer_id}'). "
        + "Browse the board with list_offers()."
    )
    # Broadcast first: if the poster's flood caps reject it, no offer row exists either
    # (all-or-nothing from the caller's view). context carries a machine-parseable tag.
    bcast = await broadcast(
        db_path, poster_id, announce,
        subject=(f"[job] {subject}" if subject else "[job] New offer")[:max_subject],
        context=f"job_offer:{offer_id}",
    )

    now = time.time()
    async with _connect(db_path) as db:
        await db.execute("""
            INSERT INTO job_offers (id, poster_id, subject, payload, required_skills, status,
                                    broadcast_id, created_at, updated_at, expires_at)
            VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
        """, (offer_id, poster_id, subject, payload, json.dumps(skills_list),
              bcast["broadcast_id"], now, now, now + ttl))
        await db.commit()

    return {"offer_id": offer_id, "expires_at": now + ttl, "broadcast": bcast}

@retry_on_lock()
async def claim_offer(db_path, agent_id, offer_id, note=None, max_note=OFFER_MAX_NOTE):
    """Express intent to take an open offer (AHB-2). Claims accumulate — there is no
    enforced claim window; the poster picks one with resolve_offer whenever ready, bounded
    by the offer TTL. The poster is notified via an ack-less kind='offer_update' message
    (session_id = offer_id, so the whole offer thread shares one session). At most one
    PENDING claim per (offer, claimant); re-claiming after a rejection or a failed
    assignment is allowed."""
    if note is not None and len(note.encode("utf-8")) > max_note:
        raise ValueError(f"Claim note exceeds the {max_note}-byte limit")
    now = time.time()
    async with _connect(db_path) as db:
        async with db.execute("SELECT * FROM job_offers WHERE id=?", (offer_id,)) as cursor:
            offer = await cursor.fetchone()
        if not offer:
            raise ValueError(f"Offer {offer_id} not found")
        if offer["status"] != "open" or offer["expires_at"] < now:
            shown = "expired" if offer["status"] == "open" else offer["status"]
            raise ValueError(f"Offer {offer_id} is not open (status: {shown})")
        if offer["poster_id"] == agent_id:
            raise ValueError("You cannot claim your own offer")
        async with db.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)) as cursor:
            if not await cursor.fetchone():
                raise ValueError(f"Claimant {agent_id} is not a registered agent")
        async with db.execute(
            "SELECT 1 FROM job_claims WHERE offer_id=? AND claimant_id=? AND status='pending'",
            (offer_id, agent_id),
        ) as cursor:
            if await cursor.fetchone():
                raise ValueError("You already have a pending claim on this offer")

        claim_id = str(uuid.uuid4())
        await db.execute("""
            INSERT INTO job_claims (id, offer_id, claimant_id, note, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, (claim_id, offer_id, agent_id, note, now, now))
        async with db.execute(
            "SELECT COUNT(*) AS n FROM job_claims WHERE offer_id=? AND status='pending'",
            (offer_id,),
        ) as cursor:
            pending = (await cursor.fetchone())["n"]
        await db.commit()

    # Notify the poster (ack-less; internal=True so a stale/offline poster can't strand the
    # claim — the board row is the source of truth either way).
    await enqueue_message(
        db_path, sender_id=agent_id, recipient_id=offer["poster_id"],
        payload=(
            f"{agent_id} claimed your offer {offer_id}"
            + (f" — note: {note}" if note else "")
            + f". {pending} pending claim(s). Pick one with resolve_offer(poster_id='{offer['poster_id']}', "
            + f"offer_id='{offer_id}', action='select', claimant_id=...) or action='withdraw'."
        ),
        session_id=offer_id, kind="offer_update", internal=True,
        subject=f"Claim on offer: {offer['subject'] or offer_id[:8]}"[:OFFER_MAX_SUBJECT],
    )
    # AHB-17 #1: state the outcomes contract in the return so a claimant knows it never needs
    # to poll — every terminal outcome pushes to its inbox (the task itself if selected; an
    # offer_update if rejected / withdrawn / expired), bounded by expires_at.
    return {
        "claim_id": claim_id,
        "offer_id": offer_id,
        "pending_claims": pending,
        "expires_at": offer["expires_at"],
        "next": ("No polling needed: every outcome arrives in your inbox — the work as a normal "
                 "task if you're selected, or an offer_update if you're not selected / the offer "
                 "is withdrawn / it expires (by expires_at at the latest)."),
    }

@retry_on_lock()
async def resolve_offer(db_path, poster_id, offer_id, action, claimant_id=None):
    """Poster decision on an open offer — the confirmation leg of the AHB-2 two-way verify
    (posting and claiming are the two intents; this closes the handshake).

    action='select': pick one pending claimant → offer 'assigned', the winner's claim
    'selected', every other pending claim 'rejected' (each rejected claimant notified with
    an ack-less offer_update). The hub then auto-sends the offer payload as a NORMAL
    kind='task' message (poster → winner, session_id = offer_id), so the existing
    ack/redeliver/result/failure machinery drives execution — the winner's "you got it"
    signal IS the task itself.
    action='withdraw': offer → 'withdrawn'; pending claims rejected + claimants notified.
    """
    now = time.time()
    async with _connect(db_path) as db:
        async with db.execute("SELECT * FROM job_offers WHERE id=?", (offer_id,)) as cursor:
            offer = await cursor.fetchone()
        if not offer:
            raise ValueError(f"Offer {offer_id} not found")
        if offer["poster_id"] != poster_id:
            raise ValueError(f"Only the poster ({offer['poster_id']}) can resolve this offer")
        if offer["status"] != "open":
            raise ValueError(f"Offer {offer_id} is not open (status: {offer['status']})")
        if action == "select":
            if offer["expires_at"] < now:
                raise ValueError(f"Offer {offer_id} has expired")
            if not claimant_id:
                raise ValueError("action='select' requires claimant_id")
            async with db.execute(
                "SELECT id FROM job_claims WHERE offer_id=? AND claimant_id=? AND status='pending'",
                (offer_id, claimant_id),
            ) as cursor:
                claim = await cursor.fetchone()
            if not claim:
                raise ValueError(f"{claimant_id} has no pending claim on offer {offer_id}")
        elif action != "withdraw":
            raise ValueError("action must be 'select' or 'withdraw'")

        loser_filter = "AND claimant_id != ?" if action == "select" else ""
        params = (offer_id, claimant_id) if action == "select" else (offer_id,)
        async with db.execute(
            f"SELECT claimant_id FROM job_claims WHERE offer_id=? AND status='pending' {loser_filter}",
            params,
        ) as cursor:
            losers = [r["claimant_id"] for r in await cursor.fetchall()]
        await db.execute(
            f"UPDATE job_claims SET status='rejected', updated_at=? WHERE offer_id=? AND status='pending' {loser_filter}",
            (now,) + params,
        )
        if action == "select":
            await db.execute(
                "UPDATE job_claims SET status='selected', updated_at=? WHERE id=?",
                (now, claim["id"]),
            )
            await db.execute(
                "UPDATE job_offers SET status='assigned', claimant_id=?, updated_at=? WHERE id=? AND status='open'",
                (claimant_id, now, offer_id),
            )
        else:
            await db.execute(
                "UPDATE job_offers SET status='withdrawn', updated_at=? WHERE id=? AND status='open'",
                (now, offer_id),
            )
        await db.commit()

    task_message_id = None
    if action == "select":
        # internal=True: the winner explicitly asked for this work — a stale (or briefly
        # offline) claimant must still receive the assignment.
        res = await enqueue_message(
            db_path, sender_id=poster_id, recipient_id=claimant_id,
            payload=offer["payload"],
            context=f"Assigned from job offer {offer_id}: you claimed it and the poster selected you.",
            session_id=offer_id, kind="task", internal=True,
            subject=offer["subject"],
        )
        task_message_id = res["message_id"]
        async with _connect(db_path) as db:
            # Linkage for the failure re-open hook (_reopen_offer_on_task_failure).
            await db.execute(
                "UPDATE job_offers SET task_message_id=? WHERE id=?", (task_message_id, offer_id)
            )
            await db.commit()

    for loser in losers:
        await enqueue_message(
            db_path, sender_id=poster_id, recipient_id=loser,
            payload=(
                (f"Your claim on offer {offer_id} was not selected"
                 if action == "select"
                 else f"Offer {offer_id} was withdrawn by the poster")
                + " — no action needed."
            ),
            session_id=offer_id, kind="offer_update", internal=True,
            subject=(
                (f"Not selected: {offer['subject'] or offer_id[:8]}"
                 if action == "select"
                 else f"Withdrawn: {offer['subject'] or offer_id[:8]}")
            )[:OFFER_MAX_SUBJECT],
        )

    return {
        "offer_id": offer_id,
        "status": "assigned" if action == "select" else "withdrawn",
        "claimant_id": claimant_id if action == "select" else None,
        "task_message_id": task_message_id,
        "rejected_claims": len(losers),
    }

@retry_on_lock()
async def list_offers(db_path, status=None, limit=100):
    """Browse the job board (AHB-2): offers newest-first with their claims attached and
    required_skills parsed back to a list. `status` filters ('open'|'assigned'|'completed'|
    'withdrawn'|'expired'); None = all. An open offer past expires_at reads 'expired' here
    even before the sweep persists that transition."""
    now = time.time()
    async with _connect(db_path) as db:
        if status:
            query = "SELECT * FROM job_offers WHERE status=? ORDER BY created_at DESC LIMIT ?"
            params = (status, limit)
        else:
            query = "SELECT * FROM job_offers ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        async with db.execute(query, params) as cursor:
            offers = [dict(r) for r in await cursor.fetchall()]
        for o in offers:
            async with db.execute(
                "SELECT claimant_id, note, status, created_at FROM job_claims WHERE offer_id=? ORDER BY created_at",
                (o["id"],),
            ) as cursor:
                o["claims"] = [dict(r) for r in await cursor.fetchall()]
            try:
                o["required_skills"] = json.loads(o["required_skills"]) if o["required_skills"] else []
            except (ValueError, TypeError):
                o["required_skills"] = []
            if o["status"] == "open" and o["expires_at"] is not None and o["expires_at"] < now:
                o["status"] = "expired"
    if status:
        offers = [o for o in offers if o["status"] == status]
    return offers

@retry_on_lock()
async def expire_offers(db_path):
    """Sweep open offers past expires_at → 'expired'; reject their pending claims and
    notify each pending claimant (ack-less offer_update). Companion to expire_messages
    (D24), run from the same background sweeper. Returns the number of offers expired."""
    now = time.time()
    async with _connect(db_path) as db:
        async with db.execute(
            "UPDATE job_offers SET status='expired', updated_at=? WHERE status='open' AND expires_at < ? RETURNING id, poster_id, subject",
            (now, now),
        ) as cursor:
            expired = [dict(r) for r in await cursor.fetchall()]
        notify = []
        for o in expired:
            async with db.execute(
                "SELECT claimant_id FROM job_claims WHERE offer_id=? AND status='pending'", (o["id"],)
            ) as cursor:
                claimants = [r["claimant_id"] for r in await cursor.fetchall()]
            await db.execute(
                "UPDATE job_claims SET status='rejected', updated_at=? WHERE offer_id=? AND status='pending'",
                (now, o["id"]),
            )
            notify.extend((o, c) for c in claimants)
        await db.commit()
    for o, claimant in notify:
        await enqueue_message(
            db_path, sender_id=o["poster_id"], recipient_id=claimant,
            payload=f"Offer {o['id']} expired before the poster selected a claimant — no action needed.",
            session_id=o["id"], kind="offer_update", internal=True,
            subject=f"Expired: {o['subject'] or o['id'][:8]}"[:OFFER_MAX_SUBJECT],
        )
    return len(expired)

@retry_on_lock()
async def _complete_offer_on_task_success(db_path, task_message_id):
    """AHB-17 #3: completing a job-board assignment task flips its offer to the terminal
    'completed' state — the success mirror of _reopen_offer_on_task_failure. Without this a
    fulfilled job sat 'assigned' on the board forever. Guarded on status='assigned' so a
    duplicate/late completion is a no-op; no notifications (the poster just received the
    result on the same session). No-op for ordinary tasks."""
    now = time.time()
    async with _connect(db_path) as db:
        cursor = await db.execute(
            "UPDATE job_offers SET status='completed', updated_at=? WHERE task_message_id=? AND status='assigned'",
            (now, task_message_id),
        )
        completed = cursor.rowcount > 0
        await db.commit()
    return completed

@retry_on_lock()
async def _reopen_offer_on_task_failure(db_path, task_message_id):
    """AHB-2 lifecycle: a failed assignment hands the offer back to the board (within TTL)
    so other agents can claim it; past TTL it expires instead. The winning claim flips
    'selected'→'failed', and the pending-only unique index lets the same agent re-claim
    later. No extra poster notification: the normal D31 failure fan-out already reaches the
    poster with session_id = offer_id, and the board shows the re-open."""
    now = time.time()
    async with _connect(db_path) as db:
        async with db.execute(
            "SELECT id, expires_at FROM job_offers WHERE task_message_id=? AND status='assigned'",
            (task_message_id,),
        ) as cursor:
            offer = await cursor.fetchone()
        if not offer:
            return False
        new_status = "open" if (offer["expires_at"] is not None and offer["expires_at"] > now) else "expired"
        await db.execute(
            "UPDATE job_offers SET status=?, claimant_id=NULL, task_message_id=NULL, updated_at=? WHERE id=?",
            (new_status, now, offer["id"]),
        )
        await db.execute(
            "UPDATE job_claims SET status='failed', updated_at=? WHERE offer_id=? AND status='selected'",
            (now, offer["id"]),
        )
        await db.commit()
    return True

@retry_on_lock()
async def claim_pending(db_path, agent_id, visibility_timeout=VISIBILITY_TIMEOUT):
    async with _connect(db_path) as db:
        now = time.time()
        cutoff = now - visibility_timeout
        
        query = """
            UPDATE messages 
            SET status = 'in_progress', claimed_at = ?, updated_at = ?
            WHERE recipient_id = ? AND (
                status = 'pending' OR 
                (status = 'in_progress' AND claimed_at < ?)
            )
            RETURNING *
        """
        async with db.execute(query, (now, now, agent_id, cutoff)) as cursor:
            rows = await cursor.fetchall()
            claimed = [dict(r) for r in rows]
            
        noack_ids = [r["id"] for r in claimed if r["kind"] in NO_ACK_KINDS]
        if noack_ids:
            placeholders = ",".join("?" for _ in noack_ids)
            await db.execute(f"UPDATE messages SET status='completed' WHERE id IN ({placeholders})", noack_ids)
            
        await db.commit()
        return claimed

@retry_on_lock()
async def reclaim_stale(db_path, visibility_timeout=VISIBILITY_TIMEOUT):
    async with _connect(db_path) as db:
        now = time.time()
        cutoff = now - visibility_timeout
        await db.execute("""
            UPDATE messages 
            SET status = 'pending', claimed_at = NULL 
            WHERE status = 'in_progress' AND claimed_at < ?
        """, (cutoff,))
        await db.commit()

@retry_on_lock()
async def reset_stuck(db_path):
    async with _connect(db_path) as db:
        cursor = await db.execute("""
            UPDATE messages 
            SET status = 'pending', claimed_at = NULL 
            WHERE status = 'in_progress'
        """)
        rowcount = cursor.rowcount
        await db.commit()
        return rowcount

@retry_on_lock()
async def request_input(db_path, message_id, question):
    async with _connect(db_path) as db:
        async with db.execute("SELECT session_id, sender_id, recipient_id FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Message not found")
                
        now = time.time()
        await db.execute("UPDATE messages SET status='input_required', updated_at=? WHERE id=?", (now, message_id))
        await db.commit()
        
    res = await enqueue_message(
        db_path, 
        sender_id=row["recipient_id"],
        recipient_id=row["sender_id"],
        payload=question,
        session_id=row["session_id"],
        parent_id=message_id,
        kind="input_request",
        internal=True,
    )
    return {"request_message_id": res["message_id"], "session_id": res["session_id"]}

@retry_on_lock()
async def complete_message(db_path, message_id, response):
    async with _connect(db_path) as db:
        async with db.execute("SELECT session_id, parent_id, kind, sender_id, recipient_id FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Message not found")
                
        now = time.time()
        await db.execute("UPDATE messages SET status='completed', response=?, updated_at=? WHERE id=?", (response, now, message_id))
            
        if row["kind"] == "input_request" and row["parent_id"]:
            # Un-park the parent task ONLY if it is still awaiting this clarification.
            # A duplicate/late reply (at-least-once redelivery, or the requester answering
            # twice) must not revive a parent that already moved on — that would reopen a
            # completed task as duplicate work. (AHB-12)
            async with db.execute("SELECT status, context FROM messages WHERE id=?", (row["parent_id"],)) as cursor:
                parent = await cursor.fetchone()
            if parent and parent["status"] == "input_required":
                new_context = (parent["context"] or "") + f"\n[Clarification Answer]: {response}"
                await db.execute("UPDATE messages SET status='pending', claimed_at=NULL, context=?, updated_at=? WHERE id=?", (new_context, now, row["parent_id"]))
            
        await db.commit()
        
    if row["kind"] == "task":
        # Result fan-out
        await enqueue_message(
            db_path,
            sender_id=row["recipient_id"],
            recipient_id=row["sender_id"],
            payload="",
            response=response,
            session_id=row["session_id"],
            parent_id=message_id,
            kind="result",
            internal=True,
        )
        # AHB-17 #3: if this task was a job-board assignment, mark the offer fulfilled.
        await _complete_offer_on_task_success(db_path, message_id)

@retry_on_lock()
async def fail_message(db_path, message_id, error):
    async with _connect(db_path) as db:
        async with db.execute("SELECT session_id, parent_id, kind, sender_id, recipient_id FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Message not found")

        now = time.time()
        await db.execute("UPDATE messages SET status='failed', response=?, updated_at=? WHERE id=?", (error, now, message_id))

        if row["kind"] == "input_request" and row["parent_id"]:
            # Failing a clarification (the sender can't/won't answer) must NOT strand the
            # parent in input_required forever — the TTL sweep only touches pending kind='task'
            # (D24). Hand the parent back to the worker as pending, with the refusal noted in
            # context, so the worker re-claims it and decides whether to proceed best-effort or
            # fail the task itself. Symmetric to complete_message's un-park, and idempotent:
            # only if the parent is still parked (a duplicate/late fail is a no-op). (AHB-13 #4)
            async with db.execute("SELECT status, context FROM messages WHERE id=?", (row["parent_id"],)) as cursor:
                parent = await cursor.fetchone()
            if parent and parent["status"] == "input_required":
                new_context = (parent["context"] or "") + f"\n[Clarification Failed]: {error}"
                await db.execute("UPDATE messages SET status='pending', claimed_at=NULL, context=?, updated_at=? WHERE id=?", (new_context, now, row["parent_id"]))

        await db.commit()

    if row["kind"] == "task":
        # Failure fan-out (mirror of the D20 result fan-out for success): surface the task's
        # terminal failure to the sender's live inbox so a peer long-polling check_inbox gets a
        # signal instead of waiting to its idle cap — the live loop never falls back to
        # check_status. Ack-less kind='failure' (auto-completed on claim, NO_ACK_KINDS);
        # internal=True so it survives an offline/unknown/departed sender (D30/AHB-11). (AHB-13 #3)
        await enqueue_message(
            db_path,
            sender_id=row["recipient_id"],
            recipient_id=row["sender_id"],
            payload="",
            response=error,
            session_id=row["session_id"],
            parent_id=message_id,
            kind="failure",
            internal=True,
        )
        # AHB-2: if this task was a job-board assignment, hand the offer back to the board
        # (or expire it if past TTL). No-op for ordinary tasks.
        await _reopen_offer_on_task_failure(db_path, message_id)

@retry_on_lock()
async def get_message_endpoints(db_path, message_id):
    """Return {'sender_id', 'recipient_id'} for a message, or None if it doesn't exist.

    Used by the activity-feed middleware to attribute the message-id-only tools
    (reply/fail/request_input/check_status) to a real agent for display, since those
    calls carry no agent_id/sender_id arg (AHB-14). Does NOT touch last_seen (D23).
    """
    async with _connect(db_path) as db:
        async with db.execute("SELECT sender_id, recipient_id FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

@retry_on_lock()
async def get_status(db_path, message_id):
    async with _connect(db_path) as db:
        async with db.execute("SELECT * FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Message not found")
            return dict(row)

@retry_on_lock()
async def peek_inbox(db_path, agent_id, visibility_timeout=VISIBILITY_TIMEOUT):
    async with _connect(db_path) as db:
        now = time.time()
        cutoff = now - visibility_timeout
        
        query = """
            SELECT sender_id FROM messages 
            WHERE recipient_id = ? AND (
                status = 'pending' OR 
                (status = 'in_progress' AND claimed_at < ?)
            )
        """
        async with db.execute(query, (agent_id, cutoff)) as cursor:
            rows = await cursor.fetchall()
            senders = list(set([r["sender_id"] for r in rows]))
            return {"count": len(rows), "senders": senders}

@retry_on_lock()
async def expire_messages(db_path, message_ttl=MESSAGE_TTL):
    async with _connect(db_path) as db:
        now = time.time()
        cutoff = now - message_ttl
        # Sweep unclaimed 'task', 'announcement' AND 'offer_update' rows (D24, extended by
        # AHB-1/AHB-2). None has a dependent parent, so expiring them strands nothing; a
        # never-claimed notification must not linger forever. input_request/result/failure
        # stay excluded (D24 carve-out).
        await db.execute("""
            UPDATE messages
            SET status = 'expired', updated_at = ?
            WHERE status = 'pending' AND kind IN ('task', 'announcement', 'offer_update') AND created_at < ?
        """, (now, cutoff))
        await db.commit()

@retry_on_lock()
async def get_recent_messages(db_path, limit=100):
    async with _connect(db_path) as db:
        async with db.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

@retry_on_lock()
async def get_stats(db_path):
    async with _connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM messages") as cursor:
            row = await cursor.fetchone()
            total_messages = row[0]
            return {"total_messages": total_messages}

@retry_on_lock()
async def delete_old(db_path):
    async with _connect(db_path) as db:
        cursor = await db.execute("""
            DELETE FROM messages 
            WHERE status IN ('completed', 'failed', 'expired')
        """)
        rowcount = cursor.rowcount
        await db.commit()
        return rowcount
