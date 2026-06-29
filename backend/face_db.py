"""人脸特征库：{label: [embedding, ...]}，numpy 存储 + 余弦比对。

ArcFace embedding 已 L2 归一化，余弦相似度 = 点积。
线程安全（OpenCV 采集线程 + FastAPI 线程池并发访问）。
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

DEFAULT_DB_PATH = Path(__file__).parent / "face_db.npz"


class FaceDB:
    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        self._lock = threading.Lock()
        self._path = path
        # label -> list[np.ndarray(512)]
        self._data: dict[str, list[np.ndarray]] = {}
        self._load()

    # ---- 持久化 ----
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with np.load(self._path, allow_pickle=True) as f:
                for key in f.files:
                    arrs = f[key]
                    self._data[key] = [np.asarray(a, dtype=np.float32) for a in arrs]
        except Exception as e:  # pragma: no cover
            print(f"[FaceDB] load failed: {e}")

    def _save(self) -> None:
        try:
            np.savez(
                self._path,
                **{label: np.stack(embs) for label, embs in self._data.items()},
            )
        except Exception as e:  # pragma: no cover
            print(f"[FaceDB] save failed: {e}")

    # ---- 写 ----
    def register(self, label: str, embedding: np.ndarray) -> int:
        emb = np.asarray(embedding, dtype=np.float32)
        with self._lock:
            self._data.setdefault(label, []).append(emb)
            self._save()
            return len(self._data[label])

    def delete(self, label: str) -> bool:
        with self._lock:
            existed = label in self._data
            if existed:
                del self._data[label]
                self._save()
            return existed

    # ---- 读 / 识别 ----
    def recognize(
        self, embedding: np.ndarray, threshold: float
    ) -> tuple[str | None, float]:
        """返回 (label or None, best_score)。score < threshold 视为陌生人。"""
        emb = np.asarray(embedding, dtype=np.float32)
        with self._lock:
            if not self._data:
                return None, 0.0
            best_label: str | None = None
            best_score = -1.0
            for label, embs in self._data.items():
                mat = np.stack(embs)  # (n, 512)
                sims = mat @ emb  # (n,)
                m = float(sims.max())
                if m > best_score:
                    best_score = m
                    best_label = label
        if best_score < threshold:
            return None, best_score
        return best_label, best_score

    def list_entries(self) -> list[dict]:
        with self._lock:
            return [{"name": k, "count": len(v)} for k, v in self._data.items()]
