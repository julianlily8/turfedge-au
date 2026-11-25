from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class FirestoreStore:
    """Thin Cloud Firestore Admin wrapper with seamless local dry-run JSON fallback."""

    def __init__(self, dry_run: bool = False, local_dir: Optional[str] = None) -> None:
        self.dry_run = dry_run
        self.local_dir = Path(local_dir or "pipeline/output/local_store")
        self.db = None

        if not self.dry_run:
            self._init_firebase()
        else:
            self.local_dir.mkdir(parents=True, exist_ok=True)
            logger.info("FirestoreStore running in DRY-RUN local JSON mode at %s", self.local_dir)

    def _init_firebase(self) -> None:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            sa_env = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
            project_id = os.environ.get("FIREBASE_PROJECT_ID", "").strip() or None

            if sa_env:
                if sa_env.startswith("{"):
                    cred_dict = json.loads(sa_env)
                    cred = credentials.Certificate(cred_dict)
                    project_id = project_id or cred_dict.get("project_id")
                else:
                    cred = credentials.Certificate(sa_env)
            elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                cred = credentials.Certificate(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
            else:
                raise RuntimeError(
                    "FIREBASE_SERVICE_ACCOUNT env var (JSON string or file path) is required unless --dry-run is used."
                )

            options = {"projectId": project_id} if project_id else None
            firebase_admin.initialize_app(cred, options=options)

        self.db = firestore.client()

    def _local_path(self, collection: str, doc_id: str) -> Path:
        col_dir = self.local_dir / collection
        col_dir.mkdir(parents=True, exist_ok=True)
        safe_id = doc_id.replace("/", "_")
        return col_dir / f"{safe_id}.json"

    def get_doc(self, collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
        if self.dry_run:
            path = self._local_path(collection, doc_id)
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        snap = self.db.collection(collection).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def set_doc(self, collection: str, doc_id: str, data: Dict[str, Any], merge: bool = True) -> None:
        if self.dry_run:
            path = self._local_path(collection, doc_id)
            if merge and path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                existing.update(data)
                payload = existing
            else:
                payload = data
            path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            return
        self.db.collection(collection).document(doc_id).set(data, merge=merge)

    def delete_doc(self, collection: str, doc_id: str) -> None:
        if self.dry_run:
            path = self._local_path(collection, doc_id)
            if path.exists():
                path.unlink()
            return
        self.db.collection(collection).document(doc_id).delete()

    def query_by_field(self, collection: str, field: str, value: Any) -> List[Dict[str, Any]]:
        if self.dry_run:
            col_dir = self.local_dir / collection
            if not col_dir.exists():
                return []
            results: List[Dict[str, Any]] = []
            for p in sorted(col_dir.glob("*.json")):
                doc = json.loads(p.read_text(encoding="utf-8"))
                if doc.get(field) == value:
                    doc.setdefault("_doc_id", p.stem)
                    results.append(doc)
            return results

        from google.cloud.firestore_v1.base_query import FieldFilter

        docs = self.db.collection(collection).where(filter=FieldFilter(field, "==", value)).stream()
        results = []
        for snap in docs:
            d = snap.to_dict() or {}
            d["_doc_id"] = snap.id
            results.append(d)
        return results

    def list_collection(self, collection: str, limit: int = 500) -> List[Dict[str, Any]]:
        if self.dry_run:
            col_dir = self.local_dir / collection
            if not col_dir.exists():
                return []
            out = []
            for p in sorted(col_dir.glob("*.json"))[:limit]:
                d = json.loads(p.read_text(encoding="utf-8"))
                d.setdefault("_doc_id", p.stem)
                out.append(d)
            return out

        docs = self.db.collection(collection).limit(limit).stream()
        out = []
        for snap in docs:
            d = snap.to_dict() or {}
            d["_doc_id"] = snap.id
            out.append(d)
        return out
