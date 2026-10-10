"""Folder-aware incremental indexing with durable upload intents."""

import hashlib
import json
from .extract import extract
from .mailbox import SnapshotError
from .config import Settings
from .index import PendingIndexError


from .catalog import Catalog as Catalog


def _ingest(catalog, backend, folder, validity, uid, raw, existing, synthetic, settings):
    digest = hashlib.sha256(raw).hexdigest()
    identity = json.dumps(
        [catalog.namespace, folder, validity, uid, digest], ensure_ascii=False
    ).encode()
    key = "proton-mail-" + hashlib.sha256(identity).hexdigest()
    if existing.get(key, {}).get("active"):
        return key, 0
    content = catalog.content(digest, synthetic)
    if content and content["phase"] == "deleting":
        raise PendingIndexError("Pending deletion requires reconciliation")
    cached = catalog.get_text(content["key"]) if content else None
    if content and cached is not None:
        catalog.intent(
            key, folder, validity, uid, digest, synthetic, json.loads(content["metadata"])
        )
        if content["phase"] == "active":
            catalog.activate(key, json.loads(content["paths"]))
            return key, 0
        text = cached["text"]
        skipped = json.loads(cached["skipped"])
    else:
        parsed = extract(raw, settings=settings) if settings else extract(raw)
        if set(parsed["skipped"]) & {"parse_failed", "parse_timeout_or_resource_limit"}:
            raise SnapshotError("Parser failed; preserve prior retrieval data")
        canonical = catalog.intent(
            key, folder, validity, uid, digest, synthetic, parsed.get("metadata", {})
        )
        catalog.store_text(
            canonical, parsed["text"], parsed["skipped"], parsed.get("text_truncated", False)
        )
        text, skipped = parsed["text"], parsed["skipped"]
        content = catalog.content(digest, synthetic)
    canonical = content["key"]
    if content["phase"] == "active":
        catalog.activate(key, json.loads(content["paths"]))
        return key, len(skipped)
    ingest = (
        backend.recover
        if content["phase"] == "uploading" and hasattr(backend, "recover")
        else backend.ensure
    )
    paths = ingest(canonical, text, before_upload=lambda: catalog.dispatched(canonical))
    catalog.activate(key, paths)
    return key, len(skipped)


def _remove(catalog, backend, rows, keep):
    for key in rows:
        if key not in keep:
            catalog.forget(key, retain_message=True)


def collect_orphans(
    catalog, backend, *, interrupted_only=False, stop=None, before_each=None, defer_new=False
):
    """Run after a complete stable cycle, so folder moves can attach before deletion."""
    removed = 0
    for content in catalog.orphan_messages():
        if content["phase"] == "uploading":
            continue  # Preserve uncertain collector outcomes even after the UID disappears.
        if interrupted_only and content["phase"] != "deleting":
            continue
        if stop and stop.is_set():
            break
        if before_each:
            before_each()
        key = content["key"]
        if defer_new and content["phase"] != "deleting" and not catalog.seen_orphan(key):
            continue
        catalog.mark_deleting(key)
        paths = backend.find(key) if hasattr(backend, "find") else json.loads(content["paths"])
        backend.remove(paths)
        catalog.forget_content(key)
        removed += 1
    return removed


def cleanup_duplicates(catalog, backend, limit=25):
    """Delete only obsolete remote copies; canonical content and aliases are retained."""
    queued = catalog.cleanup_queue(limit)
    keys, paths = [], set()
    for row in queued:
        found = backend.find(row["key"]) if hasattr(backend, "find") else json.loads(row["paths"])
        if not found and row["phase"] == "uploading":
            continue  # An uncertain collector could still commit; keep reconciliation durable.
        keys.append(row["key"])
        protected = set(json.loads(row["canonical_paths"] or "[]"))
        paths.update(p for p in found if p not in protected)
    if paths:
        backend.remove(sorted(paths))
    catalog.cleanup_finished(keys)
    return len(keys)


def _dataset(catalog, synthetic):
    if any(bool(row["synthetic"]) != synthetic for row in catalog.rows().values()):
        raise SnapshotError("Use separate catalogs for fixtures and real mail")


def synchronize(catalog, backend, snapshot, synthetic=False):
    """Small explicit snapshots for fixtures and import tests."""
    if not snapshot.complete:
        raise SnapshotError("Incomplete snapshot cannot synchronize")
    if not snapshot.validity.isdigit() or any(not uid.isdigit() for uid in snapshot.messages):
        raise SnapshotError("Invalid snapshot identities")
    _dataset(catalog, synthetic)
    if hasattr(backend, "workspace"):
        catalog.bind_workspace(backend.workspace)
    collect_orphans(catalog, backend, interrupted_only=True)
    existing = catalog.rows(snapshot.folder)
    keep, skipped = set(), 0
    for uid, raw in snapshot.messages.items():
        key, count = _ingest(
            catalog,
            backend,
            snapshot.folder,
            snapshot.validity,
            uid,
            raw,
            existing,
            synthetic,
            None,
        )
        keep.add(key)
        skipped += count
    _remove(catalog, backend, existing, keep)
    collect_orphans(catalog, backend)
    return {"messages": len(keep), "skipped_parts": skipped}


def synchronize_folder(
    catalog,
    backend,
    mailbox,
    folder,
    settings=None,
    stop=None,
    progress=None,
    before_batch=None,
    on_error=None,
):
    settings = settings or Settings()
    _dataset(catalog, False)
    if hasattr(backend, "workspace"):
        catalog.bind_workspace(backend.workspace)
    inventory = mailbox.inventory(folder)
    catalog.observe(inventory)
    existing = catalog.rows(folder)
    active = {
        r["uid"]: key
        for key, r in existing.items()
        if r["validity"] == inventory.validity and r["active"]
    }
    keep, skipped, fetched, failed = set(), 0, 0, 0
    ordered = tuple(sorted(inventory.uids, key=int, reverse=True))
    for offset in range(0, len(ordered), settings.batch_size):
        if before_batch:
            before_batch()
        if stop and stop.is_set():
            raise SnapshotError("Synchronization interrupted")
        for uid in ordered[offset : offset + settings.batch_size]:
            if stop and stop.is_set():
                raise SnapshotError("Synchronization interrupted")
            if uid in active:
                key = active[uid]
                if catalog.get_text(key) is None:
                    raw = mailbox.fetch(uid)
                    if raw is None:
                        failed += 1
                        skipped += 1
                    else:
                        if hashlib.sha256(raw).hexdigest() != existing[key]["digest"]:
                            raise SnapshotError("Message content changed during text backfill")
                        parsed = extract(raw, settings=settings)
                        if set(parsed["skipped"]) & {
                            "parse_failed",
                            "parse_timeout_or_resource_limit",
                        }:
                            failed += 1
                        else:
                            catalog.store_text(
                                key,
                                parsed["text"],
                                parsed["skipped"],
                                parsed.get("text_truncated", False),
                            )
                keep.add(active[uid])
                continue
            raw = mailbox.fetch(uid)
            if raw is None:
                skipped += 1
                # Keep any earlier version while its body cannot be fetched.
                keep.update(
                    k
                    for k, r in existing.items()
                    if r["uid"] == uid and r["validity"] == inventory.validity
                )
                continue
            try:
                key, count = _ingest(
                    catalog,
                    backend,
                    folder,
                    inventory.validity,
                    uid,
                    raw,
                    existing,
                    False,
                    settings,
                )
            except (SnapshotError, PendingIndexError) as error:
                failed += 1
                if on_error:
                    on_error(error)
                continue
            keep.add(key)
            skipped += count
            fetched += 1
        if progress:
            progress(
                {
                    "processed": min(offset + settings.batch_size, len(ordered)),
                    "total": len(ordered),
                    "fetched": fetched,
                    "failed_messages": failed,
                }
            )
    mailbox.verify(inventory)
    if before_batch:
        before_batch()
    if not failed:
        _remove(catalog, backend, existing, keep)
    catalog.folder_finished(folder, successful=not failed)
    return {
        "messages": len(inventory.uids),
        "fetched": fetched,
        "skipped_parts": skipped,
        "failed_messages": failed,
    }
