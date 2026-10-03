"""AI media cache: generate once, reuse many times.

Every AI generation request gets a canonical identity: media type, provider, model, the normalized
prompt and the provider settings the server actually sends (PROVIDER_SETTINGS, also used by the
generators in server.py, so the identity cannot drift from what is generated). Its SHA-256 is the
generation hash. A cache entry (models.AIGeneration) links a generation hash to the Phase 3 library
asset the generation produced: the asset is the cached media and nothing is copied.

  generation hash = "was this produced from an equivalent request?"   (this module)
  content hash    = "are these files byte-for-byte identical?"         (assets.sha256, unchanged)

Scope: entries are private to the user who generated them ("user:<id>"); entries in the "system"
scope are shared with everyone (an extension point: nothing creates them automatically). A user
never reuses another user's private generation.

A hit is valid only if its asset is ready, of the right kind, usable by the user and its file
exists and is readable; anything else is a miss (a missing file marks the asset failed, as the
library does elsewhere). Lessons that already refer to an asset are never changed.

Concurrency: identical requests take a database lock (models.AIGenerationLock) keyed by scope and
generation hash; the first generates, the others wait and then reuse its asset. Works across
threads, event loops and processes sharing the database; a lock older than AI_CACHE_LOCK_MINUTES
(default 30) is treated as abandoned.
"""
import asyncio
import datetime
import hashlib
import json
import os
import time
import unicodedata

from sqlalchemy.exc import IntegrityError

import models

IDENTITY_VERSION = 1  # bump when the identity scheme changes: older entries then simply stop matching
LOCK_STALE = datetime.timedelta(minutes=float(os.getenv("AI_CACHE_LOCK_MINUTES") or 30))
LOCK_POLL_SECONDS = 0.25

LTX_NEGATIVE_PROMPT = ("worst quality, inconsistent, blurry, deformed, text, watermark, mutated, extra limbs, "
                       "bad anatomy, low resolution, artifacts")

# What the server sends to each provider, by (media type, provider). Only settings the code really
# uses are listed; changing any of them changes the identity, so older media is not reused for it.
# The provider adapters (ai_providers.py) read these values for every call; a request for another
# model, shape or length adjusts a copy, and that copy is what both the provider and the identity get.
PROVIDER_SETTINGS = {
    ("video", "veo"): {"model": "veo-2.0-generate-001",
                       "parameters": {"aspect_ratio": "16:9", "person_generation": "ALLOW_ADULT"}},
    ("video", "ltx"): {"model": "Lightricks/LTX-Video",
                       "parameters": {"negative_prompt": LTX_NEGATIVE_PROMPT, "width": 1280, "height": 704,
                                      "num_frames": 257, "fps": 25, "num_inference_steps": 60,
                                      "guidance_scale": 4.5, "seed": 42}},
    # The user makes the video and places the file; the app only names it
    ("video", "manual"): {"model": None, "parameters": {}},
    # The seed is random per call, so it is not part of the identity (it is kept as provenance)
    ("image", "pollinations"): {"model": None, "parameters": {"width": 800, "height": 1200, "nologo": True}},
    # Gemini image model through the Gemini API (Phase 8, opt-in); portrait like the side panel
    ("image", "gemini-image"): {"model": "gemini-2.5-flash-image", "parameters": {"aspect_ratio": "2:3"}},
    # Local stand-ins used only when AI_FAKE_PROVIDER=1 (automated tests); "fake-alt" is the backup provider
    ("video", "fake"): {"model": "fake-video-1", "parameters": {"width": 640, "height": 360, "seconds": 4}},
    ("image", "fake"): {"model": "fake-image-1", "parameters": {"width": 400, "height": 600}},
    ("video", "fake-alt"): {"model": "fake-alt-video-1", "parameters": {"width": 640, "height": 360, "seconds": 4}},
    ("image", "fake-alt"): {"model": "fake-alt-image-1", "parameters": {"width": 400, "height": 600}},
    # Phase 12 presenter stand-in (tests only): a portrait clip as long as the narration
    ("presenter", "fake-presenter"): {"model": "fake-presenter-1", "parameters": {"width": 480, "height": 800, "seconds": 4}},
}

# lookups/hits/misses: generation requests; generated: provider calls that produced media;
# bypassed: explicit regenerations; waited: requests that waited for an identical one;
# plan_hits: planned AI visuals the visual router found already generated
STAT_NAMES = ("lookups", "hits", "misses", "generated", "bypassed", "waited", "plan_hits")


def normalize_text(value):
    """Unicode NFC, line endings unified, every run of whitespace one space, trimmed. Case and
    punctuation are kept: they can change what a model produces."""
    text = unicodedata.normalize("NFC", value if isinstance(value, str) else "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(text.split())


def _canonical_value(value):
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, dict):
        return {str(k): _canonical_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(v) for v in value]  # order is kept: it can matter to a provider
    return value


def generation_identity(media_type, provider, prompt, settings=None):
    """The canonical identity of a generation request. `settings` defaults to what the server sends
    this provider; raises ValueError for a media type / provider pair the server cannot generate."""
    if settings is None:
        if (media_type, provider) not in PROVIDER_SETTINGS:
            raise ValueError(f"no AI {media_type} provider called {provider!r}")
        settings = PROVIDER_SETTINGS[(media_type, provider)]
    return {
        "v": IDENTITY_VERSION,
        "media_type": media_type,
        "provider": provider,
        "model": settings.get("model"),
        "prompt": normalize_text(prompt),
        "parameters": _canonical_value(settings.get("parameters") or {}),
    }


def identity_json(identity):
    """Deterministic serialization: sorted keys, no insignificant whitespace, UTF-8 kept."""
    return json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def generation_hash(identity):
    return hashlib.sha256(identity_json(identity).encode("utf-8")).hexdigest()


def provenance(identity, digest, **extra):
    """What asset.details keeps about how the media was made (no prompt copy, no secrets)."""
    info = {"hash": digest, "provider": identity["provider"], "model": identity["model"],
            "media_type": identity["media_type"], "parameters": identity["parameters"]}
    info.update({k: v for k, v in extra.items() if v is not None})
    return info


class CacheHit:
    def __init__(self, entry, asset):
        self.entry = entry
        self.asset = asset


class AICache:
    def __init__(self, library, log=None):
        self.library = library
        self.stats = {name: 0 for name in STAT_NAMES}
        self.log = log if log is not None else bool(os.getenv("AI_CACHE_DEBUG"))

    def count(self, name, digest=None, detail=""):
        self.stats[name] += 1
        if self.log and digest:
            print(f"[AI CACHE] {digest[:12]} {name.upper()}{(' ' + detail) if detail else ''}")

    # ---- lookup -----------------------------------------------------------------------

    @staticmethod
    def scopes(user_id):
        return [f"user:{user_id}", "system"]

    def _valid(self, db, entry, asset, user_id, identity):
        kind = "video" if identity["media_type"] == "presenter" else identity["media_type"]  # a presenter clip is a video
        if asset is None or asset.status != "ready" or asset.kind != kind:
            return False
        if entry.media_type != identity["media_type"] or entry.provider != identity["provider"] \
                or entry.model != identity["model"]:
            return False
        if not self.library.usable_by(asset, user_id):
            return False
        path = self.library.file_path(asset) if self.library.file_exists(asset) else None
        if not path or not os.access(path, os.R_OK):
            self.library.mark_missing(db, asset)
            return False
        return True

    def _hits(self, db, user_id, wanted):
        """{generation hash: CacheHit} for the valid entries among `wanted` ({hash: identity}); one query."""
        if not wanted:
            return {}
        own = f"user:{user_id}"
        rows = (db.query(models.AIGeneration, models.Asset)
                .outerjoin(models.Asset, models.Asset.id == models.AIGeneration.asset_id)
                .filter(models.AIGeneration.generation_hash.in_(list(wanted)),
                        models.AIGeneration.scope_key.in_(self.scopes(user_id)))
                .order_by(models.AIGeneration.created_at.desc(), models.AIGeneration.id.desc())
                .all())
        # The user's own newest valid generation first, then a shared one
        rows.sort(key=lambda row: row[0].scope_key != own)
        found = {}
        for entry, asset in rows:
            digest = entry.generation_hash
            if digest not in found and self._valid(db, entry, asset, user_id, wanted[digest]):
                found[digest] = CacheHit(entry, asset)
        return found

    def lookup(self, db, user_id, identity, digest=None):
        """The valid cached asset for this request, or None. Never calls a provider."""
        digest = digest or generation_hash(identity)
        hit = self._hits(db, user_id, {digest: identity}).get(digest)
        self.count("lookups")
        self.count("hits" if hit else "misses", digest, f"asset {hit.asset.id[:8]}" if hit else "")
        return hit

    def peek(self, db, user_id, identity, digest=None):
        """lookup() without counting (used while waiting for an identical request)."""
        digest = digest or generation_hash(identity)
        return self._hits(db, user_id, {digest: identity}).get(digest)

    def lookup_many(self, db, user_id, identities):
        """{key: CacheHit} for a batch of requests ({key: identity}), in one query."""
        digests = {key: generation_hash(identity) for key, identity in identities.items()}
        hits = self._hits(db, user_id, {digests[key]: identities[key] for key in identities})
        found = {key: hits[digest] for key, digest in digests.items() if digest in hits}
        for key in found:
            self.count("plan_hits", digests[key], f"asset {found[key].asset.id[:8]}")
        return found

    # ---- recording --------------------------------------------------------------------

    def record(self, db, scope, asset, identity, digest=None, forced=False):
        entry = models.AIGeneration(generation_hash=digest or generation_hash(identity), scope_key=scope,
                                    asset_id=asset.id, media_type=identity["media_type"],
                                    provider=identity["provider"], model=identity["model"],
                                    identity=identity_json(identity), forced=bool(forced))
        db.add(entry)
        db.commit()
        return entry

    def record_once(self, db, scope, asset, identity, digest=None, forced=False):
        """record() that is safe to call again (recovery finishing a generation twice): one entry per
        generation hash, scope and asset."""
        digest = digest or generation_hash(identity)
        entry = db.query(models.AIGeneration).filter(models.AIGeneration.generation_hash == digest,
                                                     models.AIGeneration.scope_key == scope,
                                                     models.AIGeneration.asset_id == asset.id).first()
        return entry or self.record(db, scope, asset, identity, digest, forced)

    @staticmethod
    def adopt_lock(db, scope, digest):
        """Recovery takes over the lock of a generation whose worker died: the run it recovers is the one
        that holds it (the run's lease says so), so identical requests keep waiting for this run."""
        key = f"{scope}:{digest}"
        lock = db.get(models.AIGenerationLock, key)
        if lock:
            lock.created_at = datetime.datetime.utcnow()
        else:
            db.add(models.AIGenerationLock(key=key, created_at=datetime.datetime.utcnow()))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        return key

    @staticmethod
    def sweep_locks(db):
        """Removes abandoned locks (older than AI_CACHE_LOCK_MINUTES); returns how many."""
        cutoff = datetime.datetime.utcnow() - LOCK_STALE
        removed = db.query(models.AIGenerationLock).filter(models.AIGenerationLock.created_at < cutoff).delete()
        db.commit()
        return removed

    # ---- one generation at a time per identity ---------------------------------------

    async def acquire(self, db, scope, digest, recheck):
        """Waits until this request may generate `digest` in `scope`. Returns (hit, lock_key): a hit
        (and no lock) when another request produced it meanwhile, else the lock key to release."""
        key = f"{scope}:{digest}"
        waited = False
        while True:
            try:
                db.add(models.AIGenerationLock(key=key, created_at=datetime.datetime.utcnow()))
                db.commit()
            except IntegrityError:
                db.rollback()
            else:
                hit = recheck()  # it may have been produced just before the lock was taken
                if hit:
                    self.release(db, key)
                    return hit, None
                return None, key
            if not waited:
                waited = True
                self.count("waited", digest)
            db.expire_all()
            hit = recheck()  # the other request may have finished
            if hit:
                return hit, None
            lock = db.get(models.AIGenerationLock, key)
            if lock and datetime.datetime.utcnow() - lock.created_at > LOCK_STALE:
                db.delete(lock)  # abandoned (e.g. the process stopped mid-generation)
                db.commit()
                continue
            await asyncio.sleep(LOCK_POLL_SECONDS)

    @staticmethod
    def release(db, key):
        if not key:
            return
        try:
            db.rollback()
            db.query(models.AIGenerationLock).filter(models.AIGenerationLock.key == key).delete()
            db.commit()
        except Exception:  # noqa: BLE001 - a lock left behind expires after LOCK_STALE
            db.rollback()

    def snapshot(self):
        return dict(self.stats, time=time.time())
