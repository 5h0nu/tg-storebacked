import io, sqlite3, os, secrets, string, asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from telethon import TelegramClient
from telethon.sessions import StringSession
import uvicorn

# --- CONFIG ---
API_ID = 29052019 
API_HASH = 'fe36df2c48d5e425d64933c282a8658d'
CHANNEL_ID = -1003710481657
BOT_TOKENS = [
    "6286138732:AAFOReXueXxjONbE_V92MKNx5iIz62jYfEo",
    "8504092007:AAFN4YW3OzH9qEQ3zpHZ5f8TPLkGWzMa6sc",
    "8742088475:AAF79jwhc3T23WBFUhjLEERBxHlHKh960LE"
]

class BotPool:
    def __init__(self, tokens):
        # FIXED: Changed _init_ to __init__
        self.tokens = tokens
        self.clients = [TelegramClient(StringSession(), API_ID, API_HASH) for _ in tokens]
        self.entities = {} 
        self._cursor = 0

    async def start(self):
        for i, client in enumerate(self.clients):
            try:
                # Use the specific token for each client
                await client.start(bot_token=self.tokens[i])
                self.entities[client] = await client.get_input_entity(CHANNEL_ID)
                print(f"✅ Bot {i+1} Ready")
            except Exception as e:
                print(f"⚠️ Bot {i+1} Sync Error: {e}")

    def get_next(self):
        # Round-robin selection of an active bot
        for _ in range(len(self.clients)):
            c = self.clients[self._cursor]
            e = self.entities.get(c)
            self._cursor = (self._cursor + 1) % len(self.clients)
            if e: return c, e
        return None, None

# Initialize the pool
pool = BotPool(BOT_TOKENS)

def generate_key(length=8):
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length))

def init_db():
    conn = sqlite3.connect("storage.db")
    conn.execute('''CREATE TABLE IF NOT EXISTS files 
                 (file_key TEXT PRIMARY KEY, msg_id INTEGER, name TEXT, type TEXT, size TEXT)''')
    conn.commit()
    conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Attempt to start bots
    await pool.start()
    yield
    # Cleanup on shutdown
    for c in pool.clients: 
        if c.is_connected():
            await c.disconnect()

app = FastAPI(lifespan=lifespan)

# Standard CORS - Change allow_origins to your frontend domain in production
app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_methods=["*"], 
    allow_headers=["*"]
)

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    temp_path = f"temp_{generate_key(5)}_{file.filename}"
    try:
        content = await file.read()
        with open(temp_path, "wb") as f: 
            f.write(content)
        
        worker, entity = pool.get_next()
        if not worker: 
            raise HTTPException(status_code=503, detail="All bots are currently offline or rate-limited.")

        # Upload to Telegram
        msg = await worker.send_file(entity, temp_path, caption=file.filename)
        file_key = generate_key().upper()
        
        # Save metadata to local SQLite
        conn = sqlite3.connect("storage.db")
        conn.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?)", 
                     (file_key, msg.id, file.filename, file.content_type, f"{len(content)/1048576:.2f} MB"))
        conn.commit()
        conn.close()

        return {"file_id": file_key, "name": file.filename}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        if os.path.exists(temp_path): 
            os.remove(temp_path)

@app.get("/download/{file_key}")
async def download(file_key: str):
    conn = sqlite3.connect("storage.db")
    res = conn.execute("SELECT msg_id, type FROM files WHERE file_key = ?", (file_key.upper(),)).fetchone()
    conn.close()
    
    if not res: 
        raise HTTPException(status_code=404, detail="File not found")
    
    msg_id, mime = res
    worker, entity = pool.get_next()
    
    if not worker:
        raise HTTPException(status_code=503, detail="Bots offline")
        
    msg = await worker.get_messages(entity, ids=msg_id)
    
    if not msg or not msg.media:
        raise HTTPException(status_code=404, detail="Message or media no longer exists on Telegram")

    async def stream():
        async for chunk in worker.iter_download(msg.media): 
            yield chunk

    return StreamingResponse(stream(), media_type=mime)

# FIXED: Changed _name_ to __name__ and _main_ to __main__
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
