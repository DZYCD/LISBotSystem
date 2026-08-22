"""BGE Embedding 微服务 —— 用 ultralytics conda 环境（已有 torch）跑。

主项目（Python 3.14）无法直接 import ultralytics 环境的 torch（C 扩展版本
绑定 Python 3.11），因此把 embedding 抽成独立 HTTP 服务，用 ultralytics 的
python 启动，主项目通过 HTTP 调用做向量化。

模型缓存到 D 盘（HF_HOME 指向 D:/models_hf），避免占用 C 盘。

用法（用 ultralytics 的 python 启动）：
    E:\\miniconda\\envs\\ultralytics\\python.exe embedding_server.py [port]

端点：
    POST /embed  {"texts": ["...", ...]}  ->  {"vectors": [[...], ...]}
"""

import os
import json
import sys
import argparse

# 模型缓存到 D 盘（避免 C 盘空间不足），须在 import torch 前设置
os.environ["HF_HOME"] = os.environ.get("HF_HOME", "D:/models_hf")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

_MODEL = None


def get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    return _MODEL


def do_embed(texts):
    model = get_model()
    vecs = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vecs]


def handle_request(body: dict) -> dict:
    texts = body.get("texts") or []
    if not isinstance(texts, list):
        texts = [texts]
    texts = [str(t) for t in texts]
    if not texts:
        return {"error": "texts required"}
    vectors = do_embed(texts)
    return {"vectors": vectors, "dim": len(vectors[0]) if vectors else 0}


def main():
    # 支持 HTTP 服务（用标准库，避免额外依赖）
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8677
    if os.environ.get("EMBED_ONCE"):
        # 单次调用模式（命令行测试）：读 stdin JSON，写 stdout JSON
        data = json.loads(sys.stdin.read())
        print(json.dumps(handle_request(data)))
        return
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                out = handle_request(body)
                resp = json.dumps(out, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                resp = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp)

        def log_message(self, *a):
            pass

    print(f"[embedding] BGE service on :{port} (cache {os.environ['HF_HOME']})", flush=True)
    HTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
