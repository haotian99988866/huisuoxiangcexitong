from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from database import get_conn, init_db

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.options("/webhook")
async def opt():
    return {}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    action = data.get("action")

    if action == "get_venues":
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id,name,address,phone,business_hours FROM venues WHERE status='active' ORDER BY id")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {"list": [{"id":r[0],"name":r[1],"address":r[2],"phone":r[3],"business_hours":r[4]} for r in rows]}

    if action == "get_venue_detail":
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id,name,address,phone,business_hours,description FROM venues WHERE id=%s", (data.get("id"),))
        r = cur.fetchone()
        cur.close()
        conn.close()
        if not r: return {}
        return {"id":r[0],"name":r[1],"address":r[2],"phone":r[3],"business_hours":r[4],"description":r[5]}

    return {}

if __name__ == "__main__":
    init_db()
    import uvicorn
    print("✅ 后端：http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)