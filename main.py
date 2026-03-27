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
    def _init_(self, tokens):
        self.clients = [TelegramClient(StringSession(), API_ID, API_HASH) for _ in tokens]
        self.entities = {} 
        self._cursor = 0

    async def start(self):
        for i, client in enumerate(self.clients):
            try:
                await client.start(bot_token=BOT_TOKENS[i])
                self.entities[client] = await client.get_input_entity(CHANNEL_ID)
                print(f"✅ Bot {i+1} Ready")
            except Exception as e:
                print(f"⚠️ Bot {i+1} Sync Error: {e}")

    def get_next(self):
        for _ in range(len(self.clients)):
            c = self.clients[self._cursor]
            e = self.entities.get(c)
            self._cursor = (self._cursor + 1) % len(self.clients)
            if e: return c, e
        return None, None

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
    await pool.start()
    yield
    for c in pool.clients: await c.disconnect()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[""], allow_methods=[""], allow_headers=["*"])

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    temp_path = f"temp_{generate_key(5)}_{file.filename}"
    try:
        content = await file.read()
        with open(temp_path, "wb") as f: f.write(content)
        
        worker, entity = pool.get_next()
        if not worker: raise HTTPException(status_code=503, detail="Bots offline")

        # Upload to TG
        msg = await worker.send_file(entity, temp_path, caption=file.filename)
        file_key = generate_key().upper()
        
        # Save to Railway Local DB
        conn = sqlite3.connect("storage.db")
        conn.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?)", 
                     (file_key, msg.id, file.filename, file.content_type, f"{len(content)/1048576:.2f} MB"))
        conn.commit()
        conn.close()

        return {"file_id": file_key, "name": file.filename}
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

@app.get("/download/{file_key}")
async def download(file_key: str):
    conn = sqlite3.connect("storage.db")
    res = conn.execute("SELECT msg_id, type FROM files WHERE file_key = ?", (file_key.upper(),)).fetchone()
    conn.close()
    if not res: raise HTTPException(status_code=404)
    
    msg_id, mime = res
    worker, entity = pool.get_next()
    msg = await worker.get_messages(entity, ids=msg_id)
    
    async def stream():
        async for chunk in worker.iter_download(msg.media): yield chunk
    return StreamingResponse(stream(), media_type=mime)

if _name_ == "_main_":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
