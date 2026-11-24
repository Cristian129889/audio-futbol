"""CQL: Ciphered Query Layer.

A small document store that keeps every value encrypted inside a real SQLite
database.  SQL is still used for storage, indexing and lookups; the readable
content only exists in memory after :meth:`CQLStore.get` authenticates and
decrypts a row.

Layout
------
``vault(ns, k, nonce, tag, value)``
    Authenticated ciphertext for each logical document.
``vault_index(ns, k, token)``
    Deterministic HMAC tokens for exact-match searches.  Tokens reveal neither
    the searched value nor the stored value.

The name is intentional: contributors used to ask for "CQL" while meaning
plain SQL, so this layer offers SQL under the hood and a tiny, auditable API
on top.  Nothing here creates pickle files or writes plaintext to disk.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Union

from .crypto import IntegrityError, seal, unseal

__all__ = ["CQLStore", "CQLTable", "IntegrityError"]


def _aad(namespace: str, key: str) -> bytes:
    """Bind a ciphertext to its exact table and slot."""
    return f"{namespace}\x00{key}".encode("utf-8")


def _token(master_key: bytes, namespace: str, value: str) -> bytes:
    message = f"{namespace}\x00{value}".encode("utf-8")
    return hmac.new(master_key, message, hashlib.sha256).digest()[:16]


class CQLStore:
    """Thread-safe encrypted SQLite document store."""

    def __init__(self, db_path: Union[str, Path], master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("CQLStore needs the 32-byte master key")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._key = master_key
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.db_path), check_same_thread=False, timeout=15
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS vault (
                ns TEXT NOT NULL,
                k TEXT NOT NULL,
                nonce BLOB NOT NULL,
                tag BLOB NOT NULL,
                value BLOB NOT NULL,
                PRIMARY KEY (ns, k)
            );
            CREATE TABLE IF NOT EXISTS vault_index (
                ns TEXT NOT NULL,
                k TEXT NOT NULL,
                token BLOB NOT NULL,
                PRIMARY KEY (ns, k, token)
            );
            CREATE INDEX IF NOT EXISTS vault_index_lookup
                ON vault_index (ns, token);
            """
        )
        self._connection.commit()

    # -- low level ---------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def sql(self, statement: str, parameters: tuple = ()) -> list[tuple]:
        """Run a read-only SQL statement against the storage metadata."""
        with self._lock:
            return list(self._connection.execute(statement, parameters))

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # -- documents ---------------------------------------------------------
    def put(self, namespace: str, key: str, value: Any, tags: tuple[str, ...] = ()) -> None:
        """Insert or replace an encrypted JSON document."""
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        envelope = seal(payload, self._key, _aad(namespace, key))
        # ``seal`` returns nonce||tag||ciphertext; SQLite keeps those parts in
        # separate columns so an attacker cannot swap rows without breaking AAD.
        nonce, tag, ciphertext = (
            envelope[:12],
            envelope[12:28],
            envelope[28:],
        )
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO vault (ns, k, nonce, tag, value) "
                "VALUES (?, ?, ?, ?, ?)",
                (namespace, key, nonce, tag, ciphertext),
            )
            connection.execute(
                "DELETE FROM vault_index WHERE ns = ? AND k = ?", (namespace, key)
            )
            for item in dict.fromkeys(tags):
                connection.execute(
                    "INSERT INTO vault_index (ns, k, token) VALUES (?, ?, ?)",
                    (namespace, key, _token(self._key, namespace, str(item))),
                )

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._connection.execute(
                "SELECT nonce, tag, value FROM vault WHERE ns = ? AND k = ?",
                (namespace, key),
            ).fetchone()
        if row is None:
            return default
        envelope = bytes(row[0]) + bytes(row[1]) + bytes(row[2])
        try:
            payload = unseal(envelope, self._key, _aad(namespace, key))
        except IntegrityError as exc:
            raise IntegrityError(
                f"vault entry {namespace}/{key} failed authentication"
            ) from exc
        return json.loads(payload.decode("utf-8"))

    def delete(self, namespace: str, key: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM vault WHERE ns = ? AND k = ?", (namespace, key)
            )
            connection.execute(
                "DELETE FROM vault_index WHERE ns = ? AND k = ?", (namespace, key)
            )
            return cursor.rowcount > 0

    def keys(self, namespace: str) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT k FROM vault WHERE ns = ? ORDER BY k", (namespace,)
            ).fetchall()
        return [row[0] for row in rows]

    def all(self, namespace: str, where: Optional[Callable[[Any], bool]] = None) -> list[Any]:
        """Decrypt every document in a namespace, optionally filtering in memory."""
        result = []
        for key in self.keys(namespace):
            value = self.get(namespace, key)
            if where is None or where(value):
                result.append(value)
        return result

    def query(self, namespace: str, tag: Optional[str] = None,
              where: Optional[Callable[[Any], bool]] = None) -> list[Any]:
        """Search by an indexed exact tag (HMAC) and/or an in-memory predicate."""
        if tag is None:
            return self.all(namespace, where)
        wanted = _token(self._key, namespace, str(tag))
        with self._lock:
            keys = [
                row[0]
                for row in self._connection.execute(
                    "SELECT k FROM vault_index WHERE ns = ? AND token = ? ORDER BY k",
                    (namespace, wanted),
                ).fetchall()
            ]
        result = []
        for key in keys:
            value = self.get(namespace, key)
            if where is None or where(value):
                result.append(value)
        return result

    def count(self, namespace: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM vault WHERE ns = ?", (namespace,)
            ).fetchone()
        return int(row[0])

    def clear_namespace(self, namespace: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM vault_index WHERE ns = ?", (namespace,))
            connection.execute("DELETE FROM vault WHERE ns = ?", (namespace,))

    def table(self, namespace: str) -> "CQLTable":
        return CQLTable(self, namespace)


class CQLTable:
    """Dictionary-like facade over a namespace of the encrypted vault."""

    def __init__(self, store: CQLStore, namespace: str) -> None:
        self.store = store
        self.namespace = namespace

    def put(self, key: str, value: Any, tags: tuple[str, ...] = ()) -> None:
        self.store.put(self.namespace, key, value, tags)

    def get(self, key: str, default: Any = None) -> Any:
        return self.store.get(self.namespace, key, default)

    def delete(self, key: str) -> bool:
        return self.store.delete(self.namespace, key)

    def all(self, where: Optional[Callable[[Any], bool]] = None) -> list[Any]:
        return self.store.all(self.namespace, where)

    def query(self, tag: Optional[str] = None,
              where: Optional[Callable[[Any], bool]] = None) -> list[Any]:
        return self.store.query(self.namespace, tag, where)

    def __len__(self) -> int:
        return self.store.count(self.namespace)
