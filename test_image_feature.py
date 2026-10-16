"""Standalone tests for the image-generation feature (no server deps needed).

Run: python3 test_image_feature.py
Covers:
  - dola_client: quality hint threading (DolaClient body, pool signature/dedupe)
  - server.py: image model/ratio/quality validators (extracted via AST, real code)
  - store.py: migration adds image_* columns; image_tasks_to_resume() filtering
"""
import ast
import inspect
import os
import sqlite3
import sys
import tempfile
import types

WORKDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKDIR)

# ---- stub aiohttp so dola_client imports without network deps ----
aiohttp = types.ModuleType("aiohttp")


class _ClientSession:
    def __init__(self, *a, **k):
        pass


class _ClientTimeout:
    def __init__(self, total=None):
        self.total = total


class _ClientResponse:
    pass


aiohttp.ClientSession = _ClientSession
aiohttp.ClientTimeout = _ClientTimeout
aiohttp.ClientResponse = _ClientResponse
sys.modules["aiohttp"] = aiohttp

import dola_client  # noqa: E402
import store  # noqa: E402

passed = []


def check(name, cond):
    assert cond, f"FAILED: {name}"
    passed.append(name)


# ============ 1. dola_client quality threading ============
c = dola_client.DolaClient("sessionid=abc; msToken=m; s_v_web_id=f")
b1 = c._build_image_body("a cat", "1:1", "auto")
t1 = b1["messages"][0]["content_block"][0]["content"]["text_block"]["text"]
check("default quality sends no quality hint", "画质" not in t1)
check("ability still type 16", b1["chat_ability"] == {"ability_type": 16, "ability_param": "{}"})

b2 = c._build_image_body("a cat", "16:9", "写实照片", "hd")
t2 = b2["messages"][0]["content_block"][0]["content"]["text_block"]["text"]
check("hd appends 画质：高清 hint", "画质：高清" in t2)
check("style/ratio preserved with hd", "风格：写实照片" in t2 and "比例：16:9" in t2)

sig = inspect.signature(dola_client.DolaPool.generate_image)
check("DolaPool.generate_image accepts quality", "quality" in sig.parameters)
sig2 = inspect.signature(dola_client.DoubaoCNClient.generate_image)
check("DoubaoCNClient.generate_image accepts quality", "quality" in sig2.parameters)

pool = dola_client.DolaPool(
    ["sessionid=a; msToken=m", "sessionid=a; msToken=m", "", "   ",
     "sessionid=" + "b" * 24],
    api_base="https://www.dola.com",
)
check("DolaPool dedupes cookie lines", len(pool.clients) == 2)
check("DolaPool.available", pool.available)

empty_pool = dola_client.DolaPool([], api_base="https://www.dola.com")
check("empty pool not available", not empty_pool.available)

# ============ 2. server.py validators (real code via AST) ============
src = open(os.path.join(WORKDIR, "server.py"), encoding="utf-8").read()
tree = ast.parse(src)
wanted = {
    "IMAGE_MODELS", "IMAGE_RATIOS", "IMAGE_QUALITIES", "SIZE_TO_RATIO",
    "_resolve_ratio", "_normalize_image_model", "_normalize_image_ratio",
    "_normalize_image_quality",
}


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


ns = {"HTTPException": HTTPException}


def _node_name(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and len(node.targets) == 1 \
            and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


# constants first, then functions (dependency order)
ordered = sorted(
    [n for n in tree.body if _node_name(n) in wanted],
    key=lambda n: 0 if isinstance(n, (ast.Assign, ast.AnnAssign)) else 1,
)
for node in ordered:
    exec(compile(ast.Module(body=[node], type_ignores=[]), "server.py", "exec"), ns)

nm = ns["_normalize_image_model"]
check("model default -> dola-image", nm(None) == "dola-image")
check("model case/sep-insensitive", nm("DOLA_IMAGE") == "dola-image" and nm("Dola-Image") == "dola-image")
try:
    nm("seedream-4")
    check("unknown model raises 422", False)
except HTTPException as e:
    check("unknown model raises 422", e.status_code == 422 and "dola-image" in str(e.detail))

nr = ns["_normalize_image_ratio"]
check("ratio passthrough", nr(None, "16:9") == "16:9")
check("size resolves to ratio", nr("1024x1024", None) == "1:1")
check("size 720x1280 -> 9:16", nr("720x1280", None) == "9:16")
check("default ratio 1:1", nr(None, None) == "1:1")
try:
    nr(None, "2:3")
    check("bad ratio raises 422", False)
except HTTPException as e:
    check("bad ratio raises 422", e.status_code == 422)

nq = ns["_normalize_image_quality"]
check("quality default standard", nq(None) == "standard")
check("quality HD -> hd", nq("HD") == "hd")
try:
    nq("ultra")
    check("bad quality raises 422", False)
except HTTPException as e:
    check("bad quality raises 422", e.status_code == 422)

# endpoint + task runner presence (static check on real source)
for token in ("@app.post(\"/v1/images/generations\", response_model=ImageTaskResponse)",
              "@app.get(\"/v1/images/{task_id}\", response_model=ImageTaskResponse)",
              "async def _run_image_task", "def _get_image_pool",
              "store.image_tasks_to_resume()", "class ImageGenRequest",
              "class ImageTaskResponse", "task_id = \"img_\" + uuid.uuid4().hex"):
    check(f"server.py contains {token[:45]}", token in src)

# ============ 3. store.py migration + resume query ============
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "t.db")
conn = sqlite3.connect(db)
conn.execute(
    "CREATE TABLE tasks (id TEXT PRIMARY KEY, model TEXT, prompt TEXT, ratio TEXT, "
    "duration INTEGER, status TEXT, video_url TEXT, error TEXT, created_at REAL, updated_at REAL)"
)
conn.commit()
conn.close()

s = store.TaskStore(db)
cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(tasks)")]
for col in ("image_url", "image_urls", "image_style", "image_quality", "image_count"):
    check(f"migration added column {col}", col in cols)

s.create("img_abc123", "dola-image", "a cat", "1:1", None,
         api_key_hash=None, api_key_name=None, daily_limit=0,
         concurrency_limit=0, max_pending=0)
s.update("img_abc123", image_style="auto", image_quality="hd", image_count=2)
s.create("video_x9", "seedance-2.5", "a dog", "16:9", 10,
         api_key_hash=None, api_key_name=None, daily_limit=0,
         concurrency_limit=0, max_pending=0)
rows = s.image_tasks_to_resume()
check("resume picks only img_ tasks", len(rows) == 1 and rows[0]["id"] == "img_abc123")
check("resume row carries style/quality/count",
      rows[0]["image_quality"] == "hd" and rows[0]["image_count"] == 2
      and rows[0]["image_style"] == "auto")

row = s.get("img_abc123")
check("duration NULL ok", row["duration"] is None)

print(f"\nALL {len(passed)} TESTS PASSED")
