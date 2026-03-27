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
        self.clients = [TelegramClient(StringSession(), API_ID, API_HASH) for _ in tokens]
        self.entities = {} 
        self._cursor = 0

    async def start(self):
        for i, client in enumerate(self.clients):
            try:
                await client.start(bot_token=BOT_TOKENS[i])
                self.entities[client] = await client.get_input_entity(CHANNEL_ID)
                print(f"✅ Bot {i+1} Connected to Channel")
            except Exception as e:
                print(f"⚠️ Bot {i+1} Error: {e}")

    def get_next(self):
        for _ in range(len(self.clients)):
            c = self.clients[self._cursor]
            e = self.entities.get(c)
            self._cursor = (self._cursor + 1) % len(self.clients)
            if e: return c, e
        return None, None

pool = BotPool(BOT_TOKENS)

def init_db():
    conn = sqlite3.connect("storage.db")
    conn.execute("CREATE TABLE IF NOT EXISTS files (file_key TEXT PRIMARY KEY, msg_id INTEGER, name TEXT, type TEXT)")
    conn.commit()
    conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await pool.start()
    yield
    for c in pool.clients: await c.disconnect()

app = FastAPI(lifespan=lifespan)

# CRITICAL: Allow CORS so the browser accepts the "Success" signal
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    key = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    temp_name = f"tmp_{key}_{file.filename}"
    
    try:
        print(f"📥 Receiving: {file.filename}")
        with open(temp_name, "wb") as f: 
            f.write(await file.read())
        
        worker, entity = pool.get_next()
        if not worker: raise HTTPException(503, "No bots available")

        print(f"📤 Uploading to Telegram via Bot Pool...")
        # This is where it usually hangs if bots aren't Admins
        msg = await worker.send_file(entity, temp_name)
        
        print(f"💾 Saving to Database: {key}")
        conn = sqlite3.connect("storage.db")
        conn.execute("INSERT INTO files VALUES (?, ?, ?, ?)", (key, msg.id, file.filename, file.content_type))
        conn.commit()
        conn.close()
        
        print(f"✨ Done! Returning Key: {key}")
        return {"file_id": key, "name": file.filename}
        
    except Exception as e:
        print(f"❌ Server Error: {e}")
        raise HTTPException(500, str(e))
    finally:
        if os.path.exists(temp_name): 
            os.remove(temp_name)

@app.get("/download/{key}")
async def download(key: str):
    conn = sqlite3.connect("storage.db")
    res = conn.execute("SELECT msg_id, type FROM files WHERE file_key = ?", (key.upper(),)).fetchone()
    conn.close()
    if not res: raise HTTPException(404)
    
    worker, entity = pool.get_next()
    msg = await worker.get_messages(entity, ids=res[0])
    return StreamingResponse(worker.iter_download(msg.media), media_type=res[1])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
